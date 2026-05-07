"""
PythOracle MEV Bot — Main Orchestrator.

Combines:
  - Hermes WebSocket (live off-chain prices)
  - On-chain Pyth price polling (staleness/delta detection)
  - GMX V2 position snapshots (liquidation candidates)

The orchestrator runs all three concurrently and cross-references
price deltas with position liquidation distances to find actionable
liquidation opportunities.

Run:
    python main.py
    python main.py --snapshot-interval 60  (custom interval)
"""
import argparse
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
import websockets
from web3 import Web3

from config import (
    ARBITRUM_RPC_URL,
    LOG_LEVEL,
    PYTH_CONTRACT,
    PYTH_FEEDS,
    PYTH_HERMES_HTTP,
    PYTH_HERMES_WS,
)
from executor import Executor
from gmx_positions import (
    INDEX_TOKEN_DECIMALS,
    MARKET_TO_FEED,
    MAX_POSITIONS,
    MIN_COLLATERAL_FACTOR,
    STABLES,
    TOKEN_DECIMALS,
    USD_PRECISION,
    Position,
    calc_liquidation_price,
    fetch_hermes_prices,
    fetch_positions_datastore,
    fetch_positions_subgraph,
    parse_onchain_position,
    parse_subgraph_position,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("main")

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(DATA_DIR, exist_ok=True)

# Minimal ABI for on-chain Pyth price reads
PYTH_ABI = [
    {
        "name": "getPriceUnsafe",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "id", "type": "bytes32"}],
        "outputs": [
            {
                "name": "price",
                "type": "tuple",
                "components": [
                    {"name": "price", "type": "int64"},
                    {"name": "conf", "type": "uint64"},
                    {"name": "expo", "type": "int32"},
                    {"name": "publishTime", "type": "uint256"},
                ],
            }
        ],
    }
]


def _raw_to_float(raw_price: int, expo: int) -> float:
    return raw_price * (10 ** expo)


def _hermes_to_float(price_obj: dict) -> tuple[float, int]:
    raw = int(price_obj["price"])
    expo = int(price_obj["expo"])
    publish_time = int(price_obj["publish_time"])
    return _raw_to_float(raw, expo), publish_time


# ---------------------------------------------------------------------------
# Price state (shared between tasks)
# ---------------------------------------------------------------------------

@dataclass
class PriceSample:
    price: float
    publish_time: int
    source: str
    received_at: float = field(default_factory=time.time)


@dataclass
class LiquidationCandidate:
    """A position that is liquidatable or very close to it."""
    position: Position
    delta_pct: float          # hermes vs on-chain price delta
    staleness_s: float        # on-chain price age
    timestamp: str


class Orchestrator:
    """
    Central coordinator:
      1. Hermes WS  → live off-chain prices
      2. On-chain poll → staleness + delta detection
      3. GMX snapshot → position liquidation distance
      4. Cross-reference → find actionable liquidation candidates
    """

    # Thresholds
    DELTA_ALERT_PCT = 0.30
    STALENESS_ALERT_S = 5.0
    ONCHAIN_POLL_INTERVAL = 2.0
    DRY_RUN = False  # Set to False only when ready to lose/gain real money!

    def __init__(self, snapshot_interval: int = 120) -> None:
        self.w3 = Web3(Web3.HTTPProvider(ARBITRUM_RPC_URL))
        if not self.w3.is_connected():
            raise RuntimeError(f"Cannot connect to RPC: {ARBITRUM_RPC_URL}")
        block = self.w3.eth.block_number
        log.info("Connected to Arbitrum, block #%d", block)

        self.pyth = self.w3.eth.contract(
            address=Web3.to_checksum_address(PYTH_CONTRACT),
            abi=PYTH_ABI,
        )

        # Shared price state
        self.hermes_prices: dict[str, PriceSample] = {}
        self.onchain_prices: dict[str, PriceSample] = {}

        # Feed ID → name mapping
        self.id_to_name = {
            fid.lower().removeprefix("0x"): name
            for name, fid in PYTH_FEEDS.items()
        }

        # GMX positions (refreshed periodically)
        self.positions: list[Position] = []
        self.snapshot_interval = snapshot_interval

        # Executor
        self.executor = Executor()

        # Candidates log
        self.candidates: list[LiquidationCandidate] = []

        # Session CSV for candidates
        session_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.csv_path = os.path.join(DATA_DIR, f"candidates_{session_ts}.csv")
        self._csv_file = open(self.csv_path, "w", newline="", encoding="utf-8")
        self._csv = csv.writer(self._csv_file)
        self._csv.writerow([
            "timestamp", "account", "market", "feed", "is_long",
            "size_usd", "liq_price", "hermes_price", "distance_pct",
            "delta_pct", "staleness_s", "status",
        ])
        self._csv_file.flush()

        self.running = True
        self._stats = {
            "snapshots": 0,
            "candidates_found": 0,
            "liquidatable_found": 0,
        }

    # ------------------------------------------------------------------ #
    # 1. Hermes WebSocket (live off-chain prices)                         #
    # ------------------------------------------------------------------ #

    async def _hermes_loop(self) -> None:
        backoff = 1.0
        ids = [fid.lower().removeprefix("0x") for fid in PYTH_FEEDS.values()]
        sub_msg = json.dumps({"type": "subscribe", "ids": ids})

        while self.running:
            try:
                log.info("Hermes: connecting...")
                async with websockets.connect(
                    PYTH_HERMES_WS,
                    ping_interval=20,
                    ping_timeout=10,
                    max_size=2**22,
                ) as ws:
                    await ws.send(sub_msg)
                    log.info("Hermes: subscribed to %d feeds", len(ids))
                    backoff = 1.0
                    async for raw in ws:
                        if not self.running:
                            break
                        try:
                            msg = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        if msg.get("type") != "price_update":
                            continue
                        feed = msg.get("price_feed", {})
                        feed_id = feed.get("id", "").lower().removeprefix("0x")
                        name = self.id_to_name.get(feed_id)
                        if not name:
                            continue
                        price_obj = feed.get("price")
                        if not price_obj:
                            continue
                        price, publish_time = _hermes_to_float(price_obj)
                        self.hermes_prices[name] = PriceSample(
                            price=price, publish_time=publish_time, source="hermes"
                        )
            except (websockets.ConnectionClosed, OSError) as e:
                log.warning("Hermes disconnected: %s. Retry in %.1fs", e, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    # ------------------------------------------------------------------ #
    # 2. On-chain Pyth price polling                                      #
    # ------------------------------------------------------------------ #

    def _read_onchain_price(self, name: str, feed_id: str) -> Optional[PriceSample]:
        try:
            id_bytes = bytes.fromhex(feed_id.lower().removeprefix("0x"))
            result = self.pyth.functions.getPriceUnsafe(id_bytes).call()
            price = _raw_to_float(result[0], result[2])
            publish_time = int(result[3])
            return PriceSample(price=price, publish_time=publish_time, source="onchain")
        except Exception as e:
            log.debug("onchain read failed for %s: %s", name, e)
            return None

    async def _onchain_poll_loop(self) -> None:
        while self.running:
            for name, feed_id in PYTH_FEEDS.items():
                sample = await asyncio.get_event_loop().run_in_executor(
                    None, self._read_onchain_price, name, feed_id
                )
                if sample:
                    self.onchain_prices[name] = sample
            await asyncio.sleep(self.ONCHAIN_POLL_INTERVAL)

    # ------------------------------------------------------------------ #
    # 3. GMX V2 position snapshots                                        #
    # ------------------------------------------------------------------ #

    async def _position_snapshot(self) -> list[Position]:
        """Fetch positions, enrich with current Hermes prices, calculate liq prices."""
        hermes = {name: s.price for name, s in self.hermes_prices.items() if s}
        if not hermes:
            log.warning("Snapshot: no Hermes prices available yet, skipping")
            return []

        async with aiohttp.ClientSession() as session:
            raw_positions = await fetch_positions_subgraph(session)

        use_onchain = not raw_positions
        if use_onchain:
            log.info("Snapshot: using on-chain DataStore fallback...")
            raw_positions = await fetch_positions_datastore(self.w3)

        if not raw_positions:
            log.warning("Snapshot: no positions retrieved")
            return []

        parse_fn = parse_onchain_position if use_onchain else parse_subgraph_position

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
                pos.distance_pct = (
                    (pos.current_hermes_price - pos.liquidation_price)
                    / pos.liquidation_price * 100.0
                )
            else:
                pos.distance_pct = (
                    (pos.liquidation_price - pos.current_hermes_price)
                    / pos.current_hermes_price * 100.0
                )
            positions.append(pos)

        positions.sort(key=lambda p: p.distance_pct)
        return positions

    async def _snapshot_loop(self) -> None:
        # Wait for Hermes to fill up first
        await asyncio.sleep(5)
        while self.running:
            try:
                t0 = time.time()
                self.positions = await self._position_snapshot()
                elapsed = time.time() - t0
                self._stats["snapshots"] += 1

                if self.positions:
                    liquidatable = [p for p in self.positions if p.already_liquidatable]
                    hot = [p for p in self.positions if p.hot]

                    log.info(
                        "Snapshot #%d: %d positions, %d liquidatable, %d HOT (≤5%%) [%.1fs]",
                        self._stats["snapshots"],
                        len(self.positions),
                        len(liquidatable),
                        len(hot),
                        elapsed,
                    )

                    # Cross-reference with delta data
                    self._evaluate_candidates(liquidatable + hot)

                    # Print top 5 closest
                    for p in self.positions[:5]:
                        side = "LONG" if p.is_long else "SHORT"
                        flag = "🔴" if p.already_liquidatable else ("🟡" if p.hot else "  ")
                        log.info(
                            "  %s %s %s $%.0f  liq=%.2f  hermes=%.2f  dist=%+.2f%%",
                            flag, p.feed, side, p.size_in_usd,
                            p.liquidation_price, p.current_hermes_price, p.distance_pct,
                        )
                else:
                    log.info("Snapshot #%d: no positions (network issue?)", self._stats["snapshots"])

            except Exception as e:
                log.error("Snapshot error: %s", e, exc_info=True)

            await asyncio.sleep(self.snapshot_interval)

    # ------------------------------------------------------------------ #
    # 4. Cross-reference: delta + positions → candidates                  #
    # ------------------------------------------------------------------ #

    def _evaluate_candidates(self, close_positions: list[Position]) -> None:
        """Check positions near liquidation against current delta state."""
        now = time.time()
        ts = datetime.utcnow().isoformat(timespec="seconds")

        for pos in close_positions:
            hermes = self.hermes_prices.get(pos.feed)
            onchain = self.onchain_prices.get(pos.feed)

            if not hermes or not onchain:
                continue

            delta_pct = (hermes.price - onchain.price) / onchain.price * 100.0
            staleness_s = now - onchain.publish_time

            # Determine status
            if pos.already_liquidatable and abs(delta_pct) >= self.DELTA_ALERT_PCT:
                status = "!!! ACTIONABLE !!!"
                self._stats["liquidatable_found"] += 1
            elif pos.already_liquidatable:
                status = "[!] LIQUIDATABLE (low delta)"
            elif pos.hot and abs(delta_pct) >= self.DELTA_ALERT_PCT:
                status = "[+] HOT+DELTA"
            elif pos.hot:
                status = "[+] HOT"
            else:
                status = "MONITOR"

            self._stats["candidates_found"] += 1

            candidate = LiquidationCandidate(
                position=pos,
                delta_pct=delta_pct,
                staleness_s=staleness_s,
                timestamp=ts,
            )
            self.candidates.append(candidate)

            # Log actionable ones prominently
            if "ACTIONABLE" in status:
                log.warning(
                    "!!! ACTIONABLE !!!: %s %s $%.0f  dist=%+.2f%%  delta=%+.3f%%  stale=%.0fs  %s",
                    pos.feed,
                    "LONG" if pos.is_long else "SHORT",
                    pos.size_in_usd,
                    pos.distance_pct,
                    delta_pct,
                    staleness_s,
                    pos.account[:12] + "...",
                )
                # FIRE EXECUTOR
                asyncio.create_task(self.executor.execute_liquidation(pos, dry_run=self.DRY_RUN))

            # Write to CSV
            self._csv.writerow([
                ts, pos.account, pos.market, pos.feed, pos.is_long,
                f"{pos.size_in_usd:.2f}",
                f"{pos.liquidation_price:.6f}",
                f"{pos.current_hermes_price:.6f}",
                f"{pos.distance_pct:.4f}",
                f"{delta_pct:.4f}",
                f"{staleness_s:.2f}",
                status,
            ])
            self._csv_file.flush()

    # ------------------------------------------------------------------ #
    # 5. Status reporting                                                  #
    # ------------------------------------------------------------------ #

    async def _status_loop(self, interval: float = 60.0) -> None:
        await asyncio.sleep(10)
        while self.running:
            hermes_count = len(self.hermes_prices)
            onchain_count = len(self.onchain_prices)
            pos_count = len(self.positions)

            # Delta summary
            deltas = []
            now = time.time()
            for name in PYTH_FEEDS:
                h = self.hermes_prices.get(name)
                o = self.onchain_prices.get(name)
                if h and o:
                    d = (h.price - o.price) / o.price * 100.0
                    age = now - o.publish_time
                    deltas.append(f"  {name:10s} Δ={d:+.3f}%  age={age:.0f}s")

            log.info(
                "\n=== STATUS ===\n"
                "  Hermes feeds: %d  |  On-chain feeds: %d  |  Positions: %d\n"
                "  Snapshots: %d  |  Candidates: %d  |  Actionable: %d\n"
                "  Deltas:\n%s",
                hermes_count, onchain_count, pos_count,
                self._stats["snapshots"],
                self._stats["candidates_found"],
                self._stats["liquidatable_found"],
                "\n".join(deltas) if deltas else "  (waiting for data...)",
            )
            await asyncio.sleep(interval)

    # ------------------------------------------------------------------ #
    # Run / Stop                                                           #
    # ------------------------------------------------------------------ #

    async def run(self) -> None:
        log.info(
            "PythOracle starting — snapshot every %ds, delta alert ≥%.2f%%, staleness ≥%.1fs",
            self.snapshot_interval,
            self.DELTA_ALERT_PCT,
            self.STALENESS_ALERT_S,
        )
        log.info("Candidates log: %s", self.csv_path)

        try:
            await asyncio.gather(
                self._hermes_loop(),
                self._onchain_poll_loop(),
                self._snapshot_loop(),
                self._status_loop(),
            )
        except asyncio.CancelledError:
            pass
        finally:
            self._print_final_summary()
            self._csv_file.close()
            log.info("Stopped.")

    def stop(self) -> None:
        self.running = False

    def _print_final_summary(self) -> None:
        log.info(
            "\n=== FINAL SUMMARY ===\n"
            "  Snapshots: %d\n"
            "  Total candidates evaluated: %d\n"
            "  Actionable liquidations found: %d\n"
            "  Data saved to: %s",
            self._stats["snapshots"],
            self._stats["candidates_found"],
            self._stats["liquidatable_found"],
            self.csv_path,
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="PythOracle MEV Bot — Orchestrator")
    parser.add_argument(
        "--snapshot-interval", type=int, default=120,
        help="Seconds between GMX position snapshots (default: 120)",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    orchestrator = Orchestrator(snapshot_interval=args.snapshot_interval)
    try:
        await orchestrator.run()
    except KeyboardInterrupt:
        orchestrator.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
