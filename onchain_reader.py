"""
MVP-2: On-chain Pyth reader + Hermes delta detector.

Reads the Pyth price on Arbitrum every few seconds and compares
it with the live Hermes WebSocket price.  Logs the delta % and
the staleness of the on-chain price — this is our core "edge" metric.

Run:
    python onchain_reader.py
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

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(DATA_DIR, exist_ok=True)
_SESSION_TS = datetime.now().strftime("%Y%m%d_%H%M%S")
CSV_PATH = os.path.join(DATA_DIR, f"deltas_{_SESSION_TS}.csv")
CSV_HEADER = ["timestamp", "feed", "hermes_price", "onchain_price", "delta_pct", "age_s", "alert"]

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("onchain_reader")

# Minimal ABI — only the functions we call
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


@dataclass
class PriceSample:
    price: float
    publish_time: int           # unix seconds, from on-chain or Hermes
    source: str                 # "onchain" | "hermes"
    received_at: float = field(default_factory=time.time)


@dataclass
class DeltaStats:
    feed: str
    samples: int = 0
    max_delta_pct: float = 0.0
    max_staleness_s: float = 0.0
    alerts_triggered: int = 0

    def record(self, delta_pct: float, staleness_s: float) -> None:
        self.samples += 1
        if abs(delta_pct) > abs(self.max_delta_pct):
            self.max_delta_pct = delta_pct
        if staleness_s > self.max_staleness_s:
            self.max_staleness_s = staleness_s


def _raw_to_float(raw_price: int, expo: int) -> float:
    return raw_price * (10 ** expo)


def _hermes_to_float(price_obj: dict) -> tuple[float, int]:
    """Returns (price, publish_time) from a Hermes price dict."""
    raw = int(price_obj["price"])
    expo = int(price_obj["expo"])
    publish_time = int(price_obj["publish_time"])
    return _raw_to_float(raw, expo), publish_time


class OnChainDeltaDetector:
    # Alert when on-chain price deviates ≥ this % from Hermes
    DELTA_ALERT_PCT = 0.30
    # Alert when on-chain price hasn't been updated for ≥ this many seconds
    STALENESS_ALERT_S = 5.0
    # How often to poll on-chain prices (seconds)
    ONCHAIN_POLL_INTERVAL = 2.0

    def __init__(self) -> None:
        self.w3 = Web3(Web3.HTTPProvider(ARBITRUM_RPC_URL))
        if not self.w3.is_connected():
            raise RuntimeError(f"Cannot connect to RPC: {ARBITRUM_RPC_URL}")
        log.info("Connected to Arbitrum, block #%d", self.w3.eth.block_number)

        self.pyth = self.w3.eth.contract(
            address=Web3.to_checksum_address(PYTH_CONTRACT),
            abi=PYTH_ABI,
        )

        # name -> latest Hermes price (updated by WS loop)
        self.hermes_prices: dict[str, PriceSample] = {}

        # name -> on-chain price (updated by poll loop)
        self.onchain_prices: dict[str, PriceSample] = {}

        # feed_id (no 0x) -> name
        self.id_to_name = {
            fid.lower().removeprefix("0x"): name
            for name, fid in PYTH_FEEDS.items()
        }

        self.delta_stats: dict[str, DeltaStats] = {
            name: DeltaStats(feed=name) for name in PYTH_FEEDS
        }

        self.running = True

        # CSV session file
        self._csv_file = open(CSV_PATH, "w", newline="", encoding="utf-8")
        self._csv = csv.writer(self._csv_file)
        self._csv.writerow(CSV_HEADER)
        self._csv_file.flush()
        log.info("Saving deltas to: %s", CSV_PATH)

    # ------------------------------------------------------------------ #
    # On-chain polling                                                     #
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
            now = time.time()
            for name, feed_id in PYTH_FEEDS.items():
                sample = await asyncio.get_event_loop().run_in_executor(
                    None, self._read_onchain_price, name, feed_id
                )
                if sample:
                    self.onchain_prices[name] = sample

            self._compare_and_log(now)
            await asyncio.sleep(self.ONCHAIN_POLL_INTERVAL)

    # ------------------------------------------------------------------ #
    # Comparison logic                                                     #
    # ------------------------------------------------------------------ #

    def _compare_and_log(self, now: float) -> None:
        ts = datetime.utcnow().isoformat(timespec="milliseconds")
        rows = []
        for name in PYTH_FEEDS:
            hermes = self.hermes_prices.get(name)
            onchain = self.onchain_prices.get(name)

            if not hermes or not onchain:
                rows.append(f"  {name:10s}  waiting for data...")
                continue

            delta_pct = (hermes.price - onchain.price) / onchain.price * 100.0
            staleness_s = now - onchain.publish_time

            self.delta_stats[name].record(delta_pct, staleness_s)

            alert = ""
            if abs(delta_pct) >= self.DELTA_ALERT_PCT:
                self.delta_stats[name].alerts_triggered += 1
                alert = "DELTA_ALERT"
            elif staleness_s >= self.STALENESS_ALERT_S:
                alert = "STALE"

            rows.append(
                f"  {name:10s}  hermes={hermes.price:>12.4f}  "
                f"onchain={onchain.price:>12.4f}  "
                f"Δ={delta_pct:>+7.3f}%  "
                f"age={staleness_s:>5.1f}s"
                + (f"  ⚡ {alert}" if alert else "")
            )

            self._csv.writerow([
                ts, name,
                f"{hermes.price:.6f}", f"{onchain.price:.6f}",
                f"{delta_pct:.4f}", f"{staleness_s:.2f}",
                alert,
            ])

        self._csv_file.flush()
        log.info("\n=== DELTA SNAPSHOT ===\n%s", "\n".join(rows))

    # ------------------------------------------------------------------ #
    # Hermes WebSocket                                                     #
    # ------------------------------------------------------------------ #

    async def _hermes_consume(self, ws) -> None:
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

    async def _hermes_loop(self) -> None:
        backoff = 1.0
        ids = [fid.lower().removeprefix("0x") for fid in PYTH_FEEDS.values()]
        sub_msg = json.dumps({"type": "subscribe", "ids": ids})

        while self.running:
            try:
                log.info("Connecting to Hermes WS...")
                async with websockets.connect(
                    PYTH_HERMES_WS,
                    ping_interval=20,
                    ping_timeout=10,
                    max_size=2**22,
                ) as ws:
                    await ws.send(sub_msg)
                    log.info("Hermes subscribed.")
                    backoff = 1.0
                    await self._hermes_consume(ws)
            except (websockets.ConnectionClosed, OSError) as e:
                log.warning("Hermes disconnected: %s. Retry in %.1fs", e, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    # ------------------------------------------------------------------ #
    # Summary                                                              #
    # ------------------------------------------------------------------ #

    def _print_summary(self) -> None:
        lines = ["\n=== SESSION SUMMARY ==="]
        for name, st in self.delta_stats.items():
            if st.samples == 0:
                lines.append(f"  {name:10s}  no data")
                continue
            lines.append(
                f"  {name:10s}  "
                f"samples={st.samples:>4d}  "
                f"max_delta={st.max_delta_pct:>+7.3f}%  "
                f"max_age={st.max_staleness_s:>5.1f}s  "
                f"alerts={st.alerts_triggered}"
            )
        log.info("\n".join(lines))

    # ------------------------------------------------------------------ #
    # Main entry                                                           #
    # ------------------------------------------------------------------ #

    async def run(self) -> None:
        log.info(
            "Starting — delta alert at ≥%.2f%%, staleness alert at ≥%.1fs",
            self.DELTA_ALERT_PCT,
            self.STALENESS_ALERT_S,
        )
        try:
            await asyncio.gather(
                self._hermes_loop(),
                self._onchain_poll_loop(),
            )
        except asyncio.CancelledError:
            pass
        finally:
            self._print_summary()
            self._csv_file.close()
            log.info("Data saved to: %s", CSV_PATH)
            log.info("Stopped.")

    def stop(self) -> None:
        self.running = False


async def main() -> None:
    detector = OnChainDeltaDetector()
    try:
        await detector.run()
    except KeyboardInterrupt:
        detector.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
