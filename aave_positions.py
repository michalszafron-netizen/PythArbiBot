"""
AAVE V3 Position Monitor — Health Factor Tracking.

Fetches AAVE V3 borrower positions via subgraph + on-chain reads,
calculates health factors, and identifies liquidation candidates.

Run standalone:
    python aave_positions.py

Or import into aave_main.py for continuous monitoring.
"""
import asyncio
import csv
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import aiohttp
from web3 import Web3

from aave_config import (
    AAVE_SUBGRAPH_URL,
    AAVE_TOKENS,
    AAVE_V3_DATA_PROVIDER,
    AAVE_V3_ORACLE,
    AAVE_V3_POOL,
    ARBITRUM_RPC_URL,
    HF_EARLY_WARNING,
    HF_LIQUIDATABLE,
    HF_MONITOR,
    LOG_LEVEL,
    MIN_POSITION_USD,
    STABLECOINS,
    MULTICALL3_ADDRESS,
)

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("aave_positions")

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(DATA_DIR, exist_ok=True)

# ---------------------------------------------------------------------------
# ABIs
# ---------------------------------------------------------------------------

POOL_ABI = [
    {
        "name": "getUserAccountData",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "user", "type": "address"}],
        "outputs": [
            {"name": "totalCollateralBase", "type": "uint256"},
            {"name": "totalDebtBase", "type": "uint256"},
            {"name": "availableBorrowsBase", "type": "uint256"},
            {"name": "currentLiquidationThreshold", "type": "uint256"},
            {"name": "ltv", "type": "uint256"},
            {"name": "healthFactor", "type": "uint256"},
        ],
    },
    {
        "name": "getReservesList",
        "type": "function",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{"name": "", "type": "address[]"}],
    },
]

DATA_PROVIDER_ABI = [
    {
        "name": "getUserReserveData",
        "type": "function",
        "stateMutability": "view",
        "inputs": [
            {"name": "asset", "type": "address"},
            {"name": "user", "type": "address"},
        ],
        "outputs": [
            {"name": "currentATokenBalance", "type": "uint256"},
            {"name": "currentStableDebt", "type": "uint256"},
            {"name": "currentVariableDebt", "type": "uint256"},
            {"name": "principalStableDebt", "type": "uint256"},
            {"name": "scaledVariableDebt", "type": "uint256"},
            {"name": "stableBorrowRate", "type": "uint256"},
            {"name": "liquidityRate", "type": "uint256"},
            {"name": "stableRateLastUpdated", "type": "uint40"},
            {"name": "usageAsCollateralEnabled", "type": "bool"},
        ],
    },
    {
        "name": "getReserveConfigurationData",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "asset", "type": "address"}],
        "outputs": [
            {"name": "decimals", "type": "uint256"},
            {"name": "ltv", "type": "uint256"},
            {"name": "liquidationThreshold", "type": "uint256"},
            {"name": "liquidationBonus", "type": "uint256"},
            {"name": "reserveFactor", "type": "uint256"},
            {"name": "usageAsCollateralEnabled", "type": "bool"},
            {"name": "borrowingEnabled", "type": "bool"},
            {"name": "stableBorrowRateEnabled", "type": "bool"},
            {"name": "isActive", "type": "bool"},
            {"name": "isFrozen", "type": "bool"},
        ],
    },
]

ORACLE_ABI = [
    {
        "name": "getAssetPrice",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "asset", "type": "address"}],
        "outputs": [{"name": "", "type": "uint256"}],
    },
    {
        "name": "BASE_CURRENCY_UNIT",
        "type": "function",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{"name": "", "type": "uint256"}],
    },
]

MULTICALL3_ABI = [
    {
        "inputs": [
            {
                "components": [
                    {"name": "target", "type": "address"},
                    {"name": "allowFailure", "type": "bool"},
                    {"name": "callData", "type": "bytes"}
                ],
                "name": "calls",
                "type": "tuple[]"
            }
        ],
        "name": "aggregate3",
        "outputs": [
            {
                "components": [
                    {"name": "success", "type": "bool"},
                    {"name": "returnData", "type": "bytes"}
                ],
                "name": "returnData",
                "type": "tuple[]"
            }
        ],
        "stateMutability": "payable",
        "type": "function"
    }
]


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class AaveBorrower:
    """Represents an AAVE V3 borrower with health factor data."""
    address: str
    health_factor: float        # 1e18 precision → float (< 1.0 = liquidatable)
    total_collateral_usd: float
    total_debt_usd: float
    liquidation_threshold: float  # weighted average, in %
    
    # Details per asset (filled on-demand for actionable candidates)
    collateral_assets: list = field(default_factory=list)  # [(token_addr, symbol, amount_usd)]
    debt_assets: list = field(default_factory=list)          # [(token_addr, symbol, amount_usd)]
    
    # Tracking
    timestamp: str = ""
    source: str = "subgraph"  # or "onchain"

    @property
    def is_liquidatable(self) -> bool:
        return self.health_factor < HF_LIQUIDATABLE

    @property
    def is_hot(self) -> bool:
        return HF_LIQUIDATABLE <= self.health_factor < HF_MONITOR

    @property
    def is_early_warning(self) -> bool:
        return HF_MONITOR <= self.health_factor < HF_EARLY_WARNING

    @property
    def status(self) -> str:
        if self.is_liquidatable:
            return "🔴 LIQUIDATABLE"
        elif self.is_hot:
            return "🟡 HOT"
        elif self.is_early_warning:
            return "🟠 WARNING"
        return "🟢 SAFE"


# ---------------------------------------------------------------------------
# Source 1: AAVE Subgraph (bulk fetch borrowers)
# ---------------------------------------------------------------------------

BORROWERS_QUERY = """
query GetBorrowers($lastId: String!) {
  positions(
    first: 1000
    where: { 
      side: BORROWER, 
      id_gt: $lastId,
      balance_gt: "0"
    }
    orderBy: id
    orderDirection: asc
  ) {
    id
    account {
      id
    }
    balance
  }
}
"""

# Query for users with active borrows — we then check HF on-chain
async def fetch_borrowers_subgraph(session: aiohttp.ClientSession, max_borrowers: int = 12000) -> list[str]:
    """Fetch list of borrower addresses from AAVE subgraph using pagination."""
    addresses = set()
    last_id = ""
    
    log.info("Subgraph: Fetching active borrowers (balance > 0)...")
    
    while len(addresses) < max_borrowers:
        try:
            async with session.post(
                AAVE_SUBGRAPH_URL,
                json={
                    "query": BORROWERS_QUERY,
                    "variables": {"lastId": last_id}
                },
                timeout=aiohttp.ClientTimeout(total=20),
            ) as r:
                if r.status != 200:
                    log.warning("Subgraph returned status %d: %s", r.status, await r.text())
                    break
                data = await r.json()
                if "errors" in data:
                    log.error("Subgraph errors: %s", json.dumps(data["errors"], indent=2))
                    # If the filter failed, try to fallback to a simpler query or stop
                    break
                
                positions = data.get("data", {}).get("positions", [])
                if not positions:
                    log.info("Subgraph: reached end of results")
                    break
                
                for p in positions:
                    last_id = p["id"]
                    if p.get("account") and p["account"].get("id"):
                        addresses.add(p["account"]["id"])
                
                log.info("Subgraph: %d unique borrowers found...", len(addresses))
                
                # If we received less than 1000 items, we've reached the end
                if len(positions) < 1000:
                    break
                    
        except asyncio.TimeoutError:
            log.warning("Subgraph timeout at id %s", last_id)
            break
        except Exception as e:
            log.warning("Subgraph fetch failed: %s", e)
            break
            
    log.info("Subgraph: Total unique borrowers for on-chain check: %d", len(addresses))
    return list(addresses)


# ---------------------------------------------------------------------------
# Source 2: On-chain health factor reads (batch)
# ---------------------------------------------------------------------------

def chunked_iterable(iterable, size):
    for i in range(0, len(iterable), size):
        yield iterable[i:i + size]

async def fetch_health_factors(
    w3: Web3, 
    addresses: list[str],
    concurrency: int = 40,
) -> list[AaveBorrower]:
    """
    Read getUserAccountData for each address using Multicall3 in batches.
    Returns sorted list of AaveBorrower by health_factor ascending.
    """
    pool_address = Web3.to_checksum_address(AAVE_V3_POOL)
    pool = w3.eth.contract(address=pool_address, abi=POOL_ABI)
    multicall = w3.eth.contract(
        address=Web3.to_checksum_address(MULTICALL3_ADDRESS), 
        abi=MULTICALL3_ABI
    )
    
    # 50 users per chunk ensures we stay well below computation/gas timeouts
    chunk_size = 50
    address_chunks = list(chunked_iterable(addresses, chunk_size))
    
    semaphore = asyncio.Semaphore(concurrency)
    loop = asyncio.get_event_loop()
    
    def _fetch_chunk(chunk: list[str]) -> list[AaveBorrower]:
        calls = []
        for addr in chunk:
            try:
                # Web3 v6: positional argument for fn_name
                call_data = pool.encode_abi("getUserAccountData", args=[Web3.to_checksum_address(addr)])
            except AttributeError:
                # Web3 v5: keyword argument for fn_name
                call_data = pool.encodeABI(fn_name="getUserAccountData", args=[Web3.to_checksum_address(addr)])
            
            calls.append((pool_address, True, call_data))
            
        try:
            results = multicall.functions.aggregate3(calls).call()
        except Exception as e:
            log.error("Multicall failed: %s", e)
            return []
            
        borrowers = []
        for addr, (success, return_data) in zip(chunk, results):
            if not success or len(return_data) == 0:
                continue
                
            try:
                # Use w3.codec.decode (Web3 v6) or w3.codec.decode_abi (Web3 v5)
                try:
                    decoded = w3.codec.decode(["uint256", "uint256", "uint256", "uint256", "uint256", "uint256"], return_data)
                except AttributeError:
                    decoded = w3.codec.decode_abi(["uint256", "uint256", "uint256", "uint256", "uint256", "uint256"], return_data)
                
                total_collateral = decoded[0] / 1e8
                total_debt = decoded[1] / 1e8
                liq_threshold = decoded[3] / 100
                health_factor = decoded[5] / 1e18
                
                if total_debt < MIN_POSITION_USD:
                    continue
                    
                borrowers.append(AaveBorrower(
                    address=addr,
                    health_factor=health_factor,
                    total_collateral_usd=total_collateral,
                    total_debt_usd=total_debt,
                    liquidation_threshold=liq_threshold,
                    timestamp=datetime.utcnow().isoformat(timespec="seconds"),
                    source="onchain"
                ))
            except Exception as e:
                log.debug("Failed to decode for %s: %s", addr, e)
                
        return borrowers

    async def _process_chunk(chunk: list[str]) -> list[AaveBorrower]:
        async with semaphore:
            return await loop.run_in_executor(None, _fetch_chunk, chunk)

    t0 = time.time()
    results = await asyncio.gather(*[_process_chunk(chunk) for chunk in address_chunks])
    elapsed = time.time() - t0
    
    borrowers = []
    for r in results:
        borrowers.extend(r)
        
    borrowers.sort(key=lambda b: b.health_factor)
    
    log.info(
        "On-chain (Multicall): read %d/%d borrowers in %.2fs",
        len(borrowers), len(addresses), elapsed
    )
    return borrowers


# ---------------------------------------------------------------------------
# Detailed position data (for actionable candidates only)
# ---------------------------------------------------------------------------

def fetch_borrower_details(w3: Web3, borrower: AaveBorrower) -> AaveBorrower:
    """
    Enrich borrower with per-asset collateral and debt breakdown.
    Only call this for positions we want to actually liquidate.
    """
    pool = w3.eth.contract(
        address=Web3.to_checksum_address(AAVE_V3_POOL),
        abi=POOL_ABI
    )
    data_provider = w3.eth.contract(
        address=Web3.to_checksum_address(AAVE_V3_DATA_PROVIDER),
        abi=DATA_PROVIDER_ABI,
    )
    oracle = w3.eth.contract(
        address=Web3.to_checksum_address(AAVE_V3_ORACLE),
        abi=ORACLE_ABI,
    )

    try:
        reserves = pool.functions.getReservesList().call()
    except Exception as e:
        log.error("Failed to get reserves list: %s", e)
        return borrower

    user_addr = Web3.to_checksum_address(borrower.address)
    collateral_list = []
    debt_list = []

    for reserve in reserves:
        reserve_lower = reserve.lower()
        token_info = AAVE_TOKENS.get(reserve)
        if token_info is None:
            # Try case-insensitive match
            for k, v in AAVE_TOKENS.items():
                if k.lower() == reserve_lower:
                    token_info = v
                    break
        
        symbol = token_info[0] if token_info else reserve[:8]
        decimals = token_info[1] if token_info else 18

        try:
            user_data = data_provider.functions.getUserReserveData(reserve, user_addr).call()
            a_token_balance = user_data[0]   # collateral
            variable_debt = user_data[2]      # variable debt
            
            if a_token_balance == 0 and variable_debt == 0:
                continue

            # Get price from AAVE oracle
            price = oracle.functions.getAssetPrice(reserve).call() / 1e8
            
            if a_token_balance > 0:
                amount = a_token_balance / (10 ** decimals)
                usd_value = amount * price
                collateral_list.append((reserve, symbol, usd_value, amount, decimals))
            
            if variable_debt > 0:
                amount = variable_debt / (10 ** decimals)
                usd_value = amount * price
                debt_list.append((reserve, symbol, usd_value, amount, decimals))

        except Exception as e:
            log.debug("Failed to read reserve %s for %s: %s", symbol, borrower.address[:10], e)

    borrower.collateral_assets = collateral_list
    borrower.debt_assets = debt_list
    return borrower


# ---------------------------------------------------------------------------
# Display + CSV
# ---------------------------------------------------------------------------

def print_borrowers(borrowers: list[AaveBorrower], title: str, limit: int = 25) -> None:
    if not borrowers:
        return
    print(f"\n{title}")
    header = f"  {'HF':>8s} {'Collateral':>14s} {'Debt':>14s} {'Liq.Thr':>8s} {'Status':16s} Address"
    print(header)
    print("  " + "-" * (len(header) - 2))
    for b in borrowers[:limit]:
        print(
            f"  {b.health_factor:>8.4f} "
            f"${b.total_collateral_usd:>13,.0f} "
            f"${b.total_debt_usd:>13,.0f} "
            f"{b.liquidation_threshold:>7.1f}% "
            f"{b.status:16s} "
            f"{b.address[:14]}..."
        )


def save_borrowers_csv(borrowers: list[AaveBorrower]) -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(DATA_DIR, f"aave_borrowers_{ts}.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "address", "health_factor", "total_collateral_usd",
            "total_debt_usd", "liq_threshold", "status", "timestamp",
        ])
        for b in borrowers:
            writer.writerow([
                b.address, f"{b.health_factor:.6f}",
                f"{b.total_collateral_usd:.2f}", f"{b.total_debt_usd:.2f}",
                f"{b.liquidation_threshold:.2f}", b.status, b.timestamp,
            ])
    return path


# ---------------------------------------------------------------------------
# Main (standalone analysis)
# ---------------------------------------------------------------------------

async def run_analysis() -> None:
    w3 = Web3(Web3.HTTPProvider(ARBITRUM_RPC_URL))
    if not w3.is_connected():
        log.error("Cannot connect to Arbitrum RPC: %s", ARBITRUM_RPC_URL)
        return
    log.info("Connected to Arbitrum, block #%d", w3.eth.block_number)

    # Step 1: Get borrower addresses
    async with aiohttp.ClientSession() as session:
        addresses = await fetch_borrowers_subgraph(session)

    if not addresses:
        log.warning("No borrowers found via subgraph")
        return

    # Step 2: Read health factors on-chain
    borrowers = await fetch_health_factors(w3, addresses)

    # Step 3: Categorize
    liquidatable = [b for b in borrowers if b.is_liquidatable]
    hot = [b for b in borrowers if b.is_hot]
    warning = [b for b in borrowers if b.is_early_warning]

    print("\n" + "=" * 100)
    print(f"AAVE V3 BORROWER ANALYSIS  [{datetime.utcnow().isoformat(timespec='seconds')} UTC]")
    print("=" * 100)
    print(f"  Total borrowers scanned : {len(borrowers)}")
    print(f"  🔴 Liquidatable (HF<1)  : {len(liquidatable)}")
    print(f"  🟡 HOT (HF 1.0-1.05)   : {len(hot)}")
    print(f"  🟠 Warning (HF 1.05-1.1): {len(warning)}")

    if liquidatable:
        print_borrowers(liquidatable, "🔴 LIQUIDATABLE POSITIONS:")
        # Get detailed breakdown for top candidates
        for b in liquidatable[:3]:
            b = fetch_borrower_details(w3, b)
            if b.collateral_assets:
                print(f"\n    Collateral breakdown for {b.address[:14]}...")
                for _, sym, usd, amt, _ in b.collateral_assets:
                    print(f"      {sym:8s}: {amt:>14.6f}  (${usd:>12,.2f})")
            if b.debt_assets:
                print(f"    Debt breakdown:")
                for _, sym, usd, amt, _ in b.debt_assets:
                    print(f"      {sym:8s}: {amt:>14.6f}  (${usd:>12,.2f})")

    if hot:
        print_borrowers(hot, "🟡 HOT — HF between 1.0 and 1.05:")

    if not liquidatable and not hot:
        print_borrowers(borrowers[:20], "Top 20 closest to liquidation:")

    csv_path = save_borrowers_csv(borrowers)
    log.info("Saved %d borrowers to %s", len(borrowers), csv_path)


if __name__ == "__main__":
    asyncio.run(run_analysis())
