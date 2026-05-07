"""
MVP-1: Pyth Hermes WebSocket monitor.

Connects to Pyth's Hermes streaming endpoint, subscribes to a list of price
feeds, and prints each update with latency stats.

Run:
    python pyth_monitor.py
"""
import asyncio
import json
import logging
import signal
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Deque

import websockets

from config import LOG_LEVEL, PYTH_FEEDS, PYTH_HERMES_WS

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("pyth_monitor")


@dataclass
class FeedStats:
    """Per-feed running statistics."""
    name: str
    updates: int = 0
    last_price: float = 0.0
    last_publish_time: int = 0
    latencies_ms: Deque[float] = field(default_factory=lambda: deque(maxlen=200))

    def record(self, price: float, publish_time: int, received_at: float) -> float:
        latency_ms = (received_at - publish_time) * 1000.0
        self.updates += 1
        self.last_price = price
        self.last_publish_time = publish_time
        self.latencies_ms.append(latency_ms)
        return latency_ms

    def mean_latency(self) -> float:
        return sum(self.latencies_ms) / len(self.latencies_ms) if self.latencies_ms else 0.0

    def p95_latency(self) -> float:
        if not self.latencies_ms:
            return 0.0
        s = sorted(self.latencies_ms)
        return s[int(len(s) * 0.95)]


def _parse_price(price_obj: dict) -> float:
    """Pyth price format: price * 10^expo. expo is negative (e.g. -8)."""
    raw = int(price_obj["price"])
    expo = int(price_obj["expo"])
    return raw * (10 ** expo)


class PythMonitor:
    def __init__(self, feeds: dict[str, str]):
        # Reverse map: feed_id -> human name
        self.feed_to_name: dict[str, str] = {}
        for name, fid in feeds.items():
            normalized = fid.lower().removeprefix("0x")
            self.feed_to_name[normalized] = name

        self.stats: dict[str, FeedStats] = {
            name: FeedStats(name=name) for name in feeds
        }
        self.running = True
        self.start_time = time.time()
        self.total_updates = 0

    def _summary(self) -> str:
        runtime = time.time() - self.start_time
        ups = self.total_updates / runtime if runtime > 0 else 0
        lines = [f"\n=== STATS (runtime {runtime:.0f}s, {ups:.1f} updates/s) ==="]
        for name, st in self.stats.items():
            if st.updates == 0:
                lines.append(f"  {name:10s} no updates yet")
                continue
            lines.append(
                f"  {name:10s} price={st.last_price:>12.4f} "
                f"updates={st.updates:>5d} "
                f"mean_latency={st.mean_latency():>6.0f}ms "
                f"p95={st.p95_latency():>6.0f}ms"
            )
        return "\n".join(lines)

    async def _stats_loop(self, interval_s: float = 15.0) -> None:
        while self.running:
            await asyncio.sleep(interval_s)
            log.info(self._summary())

    async def _consume(self, ws) -> None:
        async for raw in ws:
            received_at = time.time()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                log.warning("Non-JSON message: %s", raw[:200])
                continue

            if msg.get("type") != "price_update":
                # Hermes also sends 'response' acks for subscribe
                log.debug("Non-price message: %s", msg)
                continue

            update = msg.get("price_feed", {})
            feed_id = update.get("id", "").lower().removeprefix("0x")
            name = self.feed_to_name.get(feed_id)
            if not name:
                continue

            price_obj = update.get("price", {})
            if not price_obj:
                continue

            price = _parse_price(price_obj)
            publish_time = int(price_obj.get("publish_time", 0))

            stats = self.stats[name]
            latency_ms = stats.record(price, publish_time, received_at)
            self.total_updates += 1

            log.debug(
                "%-10s %.6f  publish_time=%s  latency=%.0fms",
                name,
                price,
                datetime.utcfromtimestamp(publish_time).isoformat(timespec="seconds"),
                latency_ms,
            )

    async def _subscribe(self, ws) -> None:
        # Hermes subscribe message - subscribe to all feeds at once
        sub_msg = {
            "type": "subscribe",
            "ids": list(self.feed_to_name.keys()),
        }
        await ws.send(json.dumps(sub_msg))
        log.info("Subscribed to %d feeds: %s", len(self.feed_to_name), list(self.stats.keys()))

    async def run(self) -> None:
        stats_task = asyncio.create_task(self._stats_loop())
        backoff = 1.0
        try:
            while self.running:
                try:
                    log.info("Connecting to %s ...", PYTH_HERMES_WS)
                    async with websockets.connect(
                        PYTH_HERMES_WS,
                        ping_interval=20,
                        ping_timeout=10,
                        max_size=2**22,
                    ) as ws:
                        log.info("Connected.")
                        backoff = 1.0
                        await self._subscribe(ws)
                        await self._consume(ws)
                except (websockets.ConnectionClosed, OSError) as e:
                    log.warning("Connection lost: %s. Reconnecting in %.1fs", e, backoff)
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 30.0)
                except Exception as e:
                    log.exception("Unexpected error: %s", e)
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 30.0)
        finally:
            stats_task.cancel()
            log.info(self._summary())
            log.info("Monitor stopped.")

    def stop(self) -> None:
        self.running = False


async def main() -> None:
    monitor = PythMonitor(PYTH_FEEDS)

    loop = asyncio.get_running_loop()
    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, monitor.stop)
    except NotImplementedError:
        # Windows: signal handlers not supported on ProactorEventLoop
        pass

    await monitor.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
