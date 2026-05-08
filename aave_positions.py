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
{
  positions(
    first: 1000
    where: { side: BORROWER }
    orderBy: id
    orderDirection: asc
  ) {
    account {
      id
    }
  }
}
"""

# Query for users with active borrows — we then check HF on-chain
async def fetch_borrowers_subgraph(session: aiohttp.ClientSession) -> list[str]:
    """Fetch list of borrower addresses from AAVE subgraph."""
    try:
        async with session.post(
            AAVE_SUBGRAPH_URL,
            json={"query": BORROWERS_QUERY},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as r:
            if r.status != 200:
                log.warning("Subgraph returned status %d", r.status)
                return []
            data = await r.json()
            if "errors" in data:
                log.warning("Subgraph errors: %s", data["errors"])
                return []
            positions = data.get("data", {}).get("positions", [])
            addresses = list({p.get("account", {}).get("id") for p in positions if p.get("account") and p["account"].get("id")})
            log.info("Subgraph: found %d active borrowers", len(addresses))
            return addresses
    except Exception as e:
        log.warning("Subgraph fetch failed: %s", e)
        return []


# ---------------------------------------------------------------------------
# Source 2: On-chain health factor reads (batch)
# ---------------------------------------------------------------------------

async def fetch_health_factors(
    w3: Web3, 
    addresses: list[str],
    concurrency: int = 30,
) -> list[AaveBorrower]:
    """
    Read getUserAccountData for each address.
    Returns sorted list of AaveBorrower by health_factor ascending.
    """
    pool = w3.eth.contract(
        address=Web3.to_checksum_address(AAVE_V3_POOL), 
        abi=POOL_ABI
    )
    semaphore = asyncio.Semaphore(concurrency)
    loop = asyncio.get_event_loop()

    def _read_one(addr: str) -> Optional[AaveBorrower]:
        try:
            result = pool.functions.getUserAccountData(
                Web3.to_checksum_address(addr)
            ).call()
            
            total_collateral = result[0] / 1e8  # AAVE base currency = 8 decimals (USD)
            total_debt = result[1] / 1e8
            liq_threshold = result[3] / 100       # bps → %
            health_factor = result[5] / 1e18
            
            if total_debt < MIN_POSITION_USD:
                return None
            
            return AaveBorrower(
                address=addr,
                health_factor=health_factor,
                total_collateral_usd=total_collateral,
                total_debt_usd=total_debt,
                liquidation_threshold=liq_threshold,
                timestamp=datetime.utcnow().isoformat(timespec="seconds"),
                source="onchain",
            )
        except Exception as e:
            log.debug("Failed to read HF for %s: %s", addr[:10], e)
            return None

    async def _fetch_one(addr: str) -> Optional[AaveBorrower]:
        async with semaphore:
            return await loop.run_in_executor(None, _read_one, addr)

    t0 = time.time()
    results = await asyncio.gather(*[_fetch_one(a) for a in addresses])
    elapsed = time.time() - t0
    
    borrowers = [r for r in results if r is not None]
    borrowers.sort(key=lambda b: b.health_factor)
    
    log.info(
        "On-chain: read %d/%d borrowers in %.1fs",
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
