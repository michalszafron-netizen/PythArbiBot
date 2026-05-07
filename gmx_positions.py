"""
MVP-3: GMX V2 Position Reader + Liquidation Price Calculator.

Data sources (tried in order):
  1. GMX subgraph (The Graph / Satsuma) — fast, bulk
  2. DataStore + Reader contracts (pure on-chain, no DNS needed) — reliable fallback

Run:
    python gmx_positions.py

Output: console table + data/positions_<timestamp>.csv
"""
import asyncio
import csv
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import aiohttp
from eth_abi import encode as abi_encode
from web3 import Web3

from config import ARBITRUM_RPC_URL, GMX_V2, LOG_LEVEL, PYTH_FEEDS, PYTH_HERMES_HTTP

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("gmx_positions")

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(DATA_DIR, exist_ok=True)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

USD_PRECISION = 10 ** 30
MIN_COLLATERAL_FACTOR = 0.015 # 1.5% — closer to GMX V2 reality

# Collateral token decimals
TOKEN_DECIMALS = {
    "0x82af49447d8a07e3bd95bd0d56f35241523fbab1": 18,  # WETH
    "0x2f2a2543b76a4166549f7aab2e75bef0aefc5b0f": 8,   # WBTC
    "0x912ce59144191c1204e64559fe8253a0e49e6548": 18,  # ARB
    "0xf97f4df75117a78c1a5a0dbb814af92458539fb4": 18,  # LINK
    "0xaf88d065e77c8cc2239327c5edb3a432268e5831": 6,   # USDC
    "0xff970a61a04b1ca14834a43f5de4533ebddb5cc8": 6,   # USDC.e
    "0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9": 6,   # USDT
}

STABLES = {
    "0xaf88d065e77c8cc2239327c5edb3a432268e5831",
    "0xff970a61a04b1ca14834a43f5de4533ebddb5cc8",
    "0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9",
}

# Market address → Pyth feed name  (lower-case keys)
MARKET_TO_FEED = {
    "0x70d95587d40a2caf56bd97485ab3eec10bee6336": "ETH/USD",
    "0x47c031236e19d024b42f8ae6780e44a573170703": "BTC/USD",
    "0xc25cef6061cf5de5eb761b50e4743c1f5d7e5407": "ARB/USD",
    "0x7f1fa204bb700853d36994da19f830b6ad18d233": "LINK/USD",
    "0x09400d9db990d5ed3f35d7be61dfaeb900af03c9": "SOL/USD",
}

# Index token decimals per market (sizeInTokens is denominated in the index token)
# WETH=18, WBTC=8, ARB=18, LINK=18, SOL=9
INDEX_TOKEN_DECIMALS = {
    "0x70d95587d40a2caf56bd97485ab3eec10bee6336": 18,  # ETH market → WETH
    "0x47c031236e19d024b42f8ae6780e44a573170703": 8,   # BTC market → WBTC
    "0xc25cef6061cf5de5eb761b50e4743c1f5d7e5407": 18,  # ARB market → ARB
    "0x7f1fa204bb700853d36994da19f830b6ad18d233": 18,  # LINK market → LINK
    "0x09400d9db990d5ed3f35d7be61dfaeb900af03c9": 9,   # SOL market → SOL
}

# Global position list key in DataStore:
# Keys.POSITION_LIST = keccak256(abi.encode("POSITION_LIST"))
POSITION_LIST_KEY = Web3.keccak(abi_encode(["string"], ["POSITION_LIST"]))

# Maximum positions to read in one run (raise after confirming performance)
MAX_POSITIONS = 500
# Concurrent RPC calls when fetching individual positions
CONCURRENCY = 20

# ---------------------------------------------------------------------------
# ABIs
# ---------------------------------------------------------------------------

DATASTORE_ABI = [
    {
        "name": "getBytes32Count",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "setKey", "type": "bytes32"}],
        "outputs": [{"name": "", "type": "uint256"}],
    },
    {
        "name": "getBytes32ValuesAt",
        "type": "function",
        "stateMutability": "view",
        "inputs": [
            {"name": "setKey", "type": "bytes32"},
            {"name": "start", "type": "uint256"},
            {"name": "end", "type": "uint256"},
        ],
        "outputs": [{"name": "", "type": "bytes32[]"}],
    },
    {
        "name": "getKeysCount",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "setKey", "type": "bytes32"}],
        "outputs": [{"name": "", "type": "uint256"}],
    },
    {
        "name": "getKeysAt",
        "type": "function",
        "stateMutability": "view",
        "inputs": [
            {"name": "setKey", "type": "bytes32"},
            {"name": "start", "type": "uint256"},
            {"name": "end", "type": "uint256"},
        ],
        "outputs": [{"name": "", "type": "bytes32[]"}],
    },
]

READER_ABI = [
    {
        "name": "getPosition",
        "type": "function",
        "stateMutability": "view",
        "inputs": [
            {"name": "dataStore", "type": "address"},
            {"name": "key", "type": "bytes32"},
        ],
        "outputs": [
            {
                "name": "",
                "type": "tuple",
                "components": [
                    {
                        "name": "addresses",
                        "type": "tuple",
                        "components": [
                            {"name": "account", "type": "address"},
                            {"name": "market", "type": "address"},
                            {"name": "collateralToken", "type": "address"},
                        ],
                    },
                    {
                        "name": "numbers",
                        "type": "tuple",
                        "components": [
                            {"name": "sizeInUsd", "type": "uint256"},
                            {"name": "sizeInTokens", "type": "uint256"},
                            {"name": "collateralAmount", "type": "uint256"},
                            {"name": "borrowingFactor", "type": "uint256"},
                            {"name": "fundingFeeAmountPerSize", "type": "uint256"},
                            {"name": "longTokenClaimableFundingAmountPerSize", "type": "uint256"},
                            {"name": "shortTokenClaimableFundingAmountPerSize", "type": "uint256"},
                            {"name": "increasedAtTime", "type": "uint256"},
                            {"name": "decreasedAtTime", "type": "uint256"},
                        ],
                    },
                    {
                        "name": "flags",
                        "type": "tuple",
                        "components": [{"name": "isLong", "type": "bool"}],
                    },
                ],
            }
        ],
    }
]

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Position:
    key: str
    account: str
    market: str
    collateral_token: str
    size_in_usd: float
    size_in_tokens: float
    collateral_amount: float
    is_long: bool
    feed: str = ""
    liquidation_price: float = 0.0
    current_hermes_price: float = 0.0
    distance_pct: float = 0.0

    @property
    def hot(self) -> bool:
        return 0.0 <= self.distance_pct <= 5.0

    @property
    def already_liquidatable(self) -> bool:
        return self.distance_pct < 0.0


# ---------------------------------------------------------------------------
# Liquidation price (simplified — no pending fees/funding)
# ---------------------------------------------------------------------------

def calc_liquidation_price(pos: Position) -> float:
    if pos.size_in_tokens == 0:
        return 0.0
    collateral_price = 1.0 if pos.collateral_token in STABLES else pos.current_hermes_price
    collateral_usd = pos.collateral_amount * collateral_price
    mcf = MIN_COLLATERAL_FACTOR
    if pos.is_long:
        numerator = pos.size_in_usd - collateral_usd + pos.size_in_usd * mcf
    else:
        numerator = pos.size_in_usd + collateral_usd - pos.size_in_usd * mcf
    return max(numerator / pos.size_in_tokens, 0.0)


# ---------------------------------------------------------------------------
# Hermes price fetch (REST, one-shot)
# ---------------------------------------------------------------------------

async def fetch_hermes_prices(session: aiohttp.ClientSession) -> dict[str, float]:
    ids = [fid.lower().removeprefix("0x") for fid in PYTH_FEEDS.values()]
    url = f"{PYTH_HERMES_HTTP}/v2/updates/price/latest"
    params = [("ids[]", fid) for fid in ids]
    try:
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as r:
            data = await r.json()
            prices: dict[str, float] = {}
            for item in data.get("parsed", []):
                fid = item["id"].lower().removeprefix("0x")
                for name, cfg_id in PYTH_FEEDS.items():
                    if cfg_id.lower().removeprefix("0x") == fid:
                        raw = int(item["price"]["price"])
                        expo = int(item["price"]["expo"])
                        prices[name] = raw * (10 ** expo)
            return prices
    except Exception as e:
        log.warning("Hermes REST failed: %s", e)
        return {}


# ---------------------------------------------------------------------------
# Source 1: Subgraph (multiple URLs tried in order)
# ---------------------------------------------------------------------------

SUBGRAPH_URLS = [
    "https://subgraph.satsuma-prod.com/3b2ced13c81a/gmx/synthetics-arbitrum-stats/api",
    "https://api.thegraph.com/subgraphs/name/gmx-io/gmx-synthetics-arbitrum",
]

POSITIONS_QUERY = """
{
  positions(
    first: 1000
    where: { sizeInUsd_gt: "0" }
    orderBy: sizeInUsd
    orderDirection: desc
  ) {
    id
    account
    market
    collateralToken
    sizeInUsd
    sizeInTokens
    collateralAmount
    isLong
  }
}
"""


async def fetch_positions_subgraph(session: aiohttp.ClientSession) -> list[dict]:
    for url in SUBGRAPH_URLS:
        try:
            async with session.post(
                url,
                json={"query": POSITIONS_QUERY},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as r:
                data = await r.json()
                if "errors" in data:
                    log.debug("Subgraph %s errors: %s", url, data["errors"])
                    continue
                positions = data.get("data", {}).get("positions", [])
                if positions:
                    log.info("Subgraph (%s): fetched %d positions", url, len(positions))
                    return positions
        except Exception as e:
            log.debug("Subgraph %s failed: %s", url, e)
    log.warning("All subgraph endpoints failed — switching to on-chain DataStore fallback")
    return []


def parse_subgraph_position(raw: dict) -> Optional[Position]:
    market = raw.get("market", "").lower()
    feed = MARKET_TO_FEED.get(market)
    if not feed:
        return None
    collateral_token = raw.get("collateralToken", "").lower()
    collateral_decimals = TOKEN_DECIMALS.get(collateral_token, 18)
    index_decimals = INDEX_TOKEN_DECIMALS.get(market, 18)
    try:
        size_usd = int(raw["sizeInUsd"]) / USD_PRECISION
        size_tokens = int(raw["sizeInTokens"]) / (10 ** index_decimals)
        collateral_amount = int(raw["collateralAmount"]) / (10 ** collateral_decimals)
    except (KeyError, ValueError):
        return None
    if size_usd < 1.0 or size_tokens == 0:
        return None
    return Position(
        key=raw.get("id", ""),
        account=raw.get("account", ""),
        market=market,
        collateral_token=collateral_token,
        size_in_usd=size_usd,
        size_in_tokens=size_tokens,
        collateral_amount=collateral_amount,
        is_long=raw.get("isLong", True),
        feed=feed,
    )


# ---------------------------------------------------------------------------
# Source 2: DataStore + Reader (pure on-chain — no external DNS needed)
# ---------------------------------------------------------------------------

def _get_position_from_chain(reader, datastore_addr: str, key: bytes) -> Optional[dict]:
    """Synchronous single-position fetch (run in executor for async use)."""
    try:
        result = reader.functions.getPosition(datastore_addr, key).call()
        addresses, numbers, flags = result
        return {
            "id": "0x" + key.hex(),
            "account": addresses[0],
            "market": addresses[1],
            "collateralToken": addresses[2],
            "sizeInUsd": numbers[0],
            "sizeInTokens": numbers[1],
            "collateralAmount": numbers[2],
            "isLong": flags[0],
        }
    except Exception:
        return None


async def fetch_positions_datastore(w3: Web3) -> list[dict]:
    datastore = w3.eth.contract(
        address=Web3.to_checksum_address(GMX_V2["DataStore"]),
        abi=DATASTORE_ABI,
    )
    reader = w3.eth.contract(
        address=Web3.to_checksum_address(GMX_V2["Reader"]),
        abi=READER_ABI,
    )
    datastore_addr = Web3.to_checksum_address(GMX_V2["DataStore"])

    # Step 1: get total position count
    total = 0
    max_retries = 3
    for i in range(max_retries):
        try:
            total = datastore.functions.getBytes32Count(POSITION_LIST_KEY).call()
            log.info("DataStore: %d total positions on-chain", total)
            break
        except Exception as e:
            if i == max_retries - 1:
                log.error("DataStore.getBytes32Count failed after %d retries: %s", max_retries, e)
                return []
            log.warning("DataStore.getBytes32Count failed (try %d/%d): %s", i+1, max_retries, e)
            time.sleep(2)

    if total == 0:
        log.warning("DataStore reports 0 positions — check POSITION_LIST_KEY")
        return []

    # Step 2: fetch position keys (last 100 — most recently touched)
    start = max(0, total - 100)
    keys_raw = []
    for i in range(max_retries):
        try:
            keys_raw = datastore.functions.getBytes32ValuesAt(
                POSITION_LIST_KEY, start, total
            ).call()
            log.info("DataStore: fetched %d position keys (range %d-%d)", len(keys_raw), start, total)
            break
        except Exception as e:
            if i == max_retries - 1:
                log.error("DataStore.getBytes32ValuesAt failed after %d retries: %s", max_retries, e)
                return []
            log.warning("DataStore.getBytes32ValuesAt failed (try %d/%d): %s", i+1, max_retries, e)
            time.sleep(2)

    # Step 3: fetch each position concurrently
    semaphore = asyncio.Semaphore(CONCURRENCY)
    loop = asyncio.get_event_loop()

    async def fetch_one(key_bytes: bytes) -> Optional[dict]:
        async with semaphore:
            return await loop.run_in_executor(
                None, _get_position_from_chain, reader, datastore_addr, key_bytes
            )

    t0 = time.time()
    results = await asyncio.gather(*[fetch_one(k) for k in keys_raw])
    elapsed = time.time() - t0
    positions = [r for r in results if r is not None]
    log.info(
        "DataStore: loaded %d/%d positions in %.1fs",
        len(positions), len(keys_raw), elapsed,
    )
    return positions


def parse_onchain_position(raw: dict) -> Optional[Position]:
    market = raw.get("market", "").lower()
    feed = MARKET_TO_FEED.get(market)
    if not feed:
        return None
    collateral_token = raw.get("collateralToken", "").lower()
    collateral_decimals = TOKEN_DECIMALS.get(collateral_token, 18)
    index_decimals = INDEX_TOKEN_DECIMALS.get(market, 18)
    try:
        size_usd = int(raw["sizeInUsd"]) / USD_PRECISION
        size_tokens = int(raw["sizeInTokens"]) / (10 ** index_decimals)
        collateral_amount = int(raw["collateralAmount"]) / (10 ** collateral_decimals)
    except (KeyError, ValueError):
        return None
    if size_usd < 1.0 or size_tokens == 0:
        return None
    return Position(
        key=raw.get("id", ""),
        account=str(raw.get("account", "")),
        market=market,
        collateral_token=collateral_token,
        size_in_usd=size_usd,
        size_in_tokens=size_tokens,
        collateral_amount=collateral_amount,
        is_long=bool(raw.get("isLong", True)),
        feed=feed,
    )


# ---------------------------------------------------------------------------
# Display + CSV
# ---------------------------------------------------------------------------

def _print_table(positions: list[Position], title: str) -> None:
    if not positions:
        return
    print(f"\n{title}")
    header = f"  {'Feed':10s} {'Side':5s} {'Size USD':>13s} {'Liq Price':>12s} {'Hermes':>12s} {'Dist%':>8s}  Account"
    print(header)
    print("  " + "-" * (len(header) - 2))
    for p in positions:
        side = "LONG " if p.is_long else "SHORT"
        flag = "🔴" if p.already_liquidatable else ("🟡" if p.hot else "  ")
        print(
            f"  {p.feed:10s} {side} "
            f"{p.size_in_usd:>13,.1f} "
            f"{p.liquidation_price:>12.4f} "
            f"{p.current_hermes_price:>12.4f} "
            f"{p.distance_pct:>+7.2f}%  "
            f"{flag} {p.account[:12]}..."
        )


def _save_csv(positions: list[Position]) -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(DATA_DIR, f"positions_{ts}.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "account", "market", "feed", "is_long",
            "size_in_usd", "size_in_tokens", "collateral_amount",
            "liquidation_price", "hermes_price", "distance_pct",
            "hot", "already_liquidatable",
        ])
        for p in positions:
            writer.writerow([
                p.account, p.market, p.feed, p.is_long,
                f"{p.size_in_usd:.2f}", f"{p.size_in_tokens:.8f}",
                f"{p.collateral_amount:.8f}",
                f"{p.liquidation_price:.6f}", f"{p.current_hermes_price:.6f}",
                f"{p.distance_pct:.4f}", p.hot, p.already_liquidatable,
            ])
    return path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def run_analysis() -> None:
    w3 = Web3(Web3.HTTPProvider(ARBITRUM_RPC_URL))
    if not w3.is_connected():
        log.error("Cannot connect to Arbitrum RPC: %s", ARBITRUM_RPC_URL)
        return
    log.info("Connected to Arbitrum, block #%d", w3.eth.block_number)

    async with aiohttp.ClientSession() as session:
        log.info("Fetching Hermes prices...")
        hermes = await fetch_hermes_prices(session)
        if not hermes:
            log.error("No Hermes prices — cannot continue.")
            return
        for name, price in hermes.items():
            log.info("  %-10s = %.4f", name, price)

        log.info("Trying subgraph sources...")
        raw_positions = await fetch_positions_subgraph(session)

    # Fallback to DataStore if subgraph failed
    use_onchain = not raw_positions
    if use_onchain:
        log.info("Using on-chain DataStore (this takes ~15-30s)...")
        raw_positions = await fetch_positions_datastore(w3)

    if not raw_positions:
        log.error("No positions retrieved from any source.")
        return

    parse_fn = parse_onchain_position if use_onchain else parse_subgraph_position

    # Parse + enrich
    positions: list[Position] = []
    for raw in raw_positions:
        pos = parse_fn(raw)
        if not pos:
            continue
        pos.current_hermes_price = hermes.get(pos.feed, 0.0)
        if not pos.current_hermes_price:
            continue
        pos.liquidation_price = calc_liquidation_price(pos)
        if not pos.liquidation_price:
            continue
        if pos.is_long:
            pos.distance_pct = (pos.current_hermes_price - pos.liquidation_price) / pos.liquidation_price * 100.0
        else:
            pos.distance_pct = (pos.liquidation_price - pos.current_hermes_price) / pos.current_hermes_price * 100.0
        positions.append(pos)

    positions.sort(key=lambda p: p.distance_pct)

    liquidatable = [p for p in positions if p.already_liquidatable]
    hot = [p for p in positions if p.hot]

    print("\n" + "=" * 95)
    print(f"GMX V2 POSITION ANALYSIS  [{datetime.utcnow().isoformat(timespec='seconds')} UTC]  source={'onchain' if use_onchain else 'subgraph'}")
    print("=" * 95)
    print(f"  Positions parsed : {len(positions)}")
    print(f"  🔴 Liquidatable  : {len(liquidatable)}")
    print(f"  🟡 HOT (≤5%)    : {len(hot)}")

    if liquidatable:
        _print_table(liquidatable[:25], "🔴 ALREADY LIQUIDATABLE:")
    if hot:
        _print_table(hot[:25], "🟡 HOT — within 5% of liquidation:")
    if not liquidatable and not hot:
        _print_table(positions[:20], "Closest 20 positions to liquidation:")

    csv_path = _save_csv(positions)
    log.info("Saved %d positions to %s", len(positions), csv_path)


if __name__ == "__main__":
    asyncio.run(run_analysis())
