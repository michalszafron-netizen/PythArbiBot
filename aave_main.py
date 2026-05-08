"""
AAVE V3 Flash Liquidation Bot — Main Orchestrator.

Combines:
  - Pyth Hermes WS (live off-chain prices — early warning system)
  - AAVE V3 position monitoring (health factor tracking)
  - Flash Loan Executor (auto-liquidation via deployed contract)

The Pyth Hermes feed provides a 10-30s early warning before Chainlink
updates on-chain, giving us time to prepare liquidation transactions.

Run:
    python aave_main.py
    python aave_main.py --scan-interval 30
    python aave_main.py --dry-run              (simulation only)
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

from aave_config import (
    AAVE_V3_ORACLE,
    AAVE_V3_POOL,
    ARBITRUM_RPC_URL,
    HF_EARLY_WARNING,
    HF_LIQUIDATABLE,
    HF_MONITOR,
    LOG_LEVEL,
    MIN_POSITION_USD,
    PYTH_FEEDS,
    PYTH_HERMES_WS,
    AAVE_TOKENS,
    FLASH_LIQUIDATOR_ADDRESS,
)
from aave_positions import (
    AaveBorrower,
    fetch_borrower_details,
    fetch_borrowers_subgraph,
    fetch_health_factors,
)
from aave_executor import AaveExecutor

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(DATA_DIR, exist_ok=True)

log_filename = os.path.join(DATA_DIR, f"bot_run_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    force=True,  # Overrides any other logging setup
    handlers=[
        logging.FileHandler(log_filename, encoding="utf-8"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger("aave_main")


# ---------------------------------------------------------------------------
# Helpers — reused from original main.py
# ---------------------------------------------------------------------------

def _raw_to_float(raw_price: int, expo: int) -> float:
    return raw_price * (10 ** expo)


def _hermes_to_float(price_obj: dict) -> tuple[float, int]:
    raw = int(price_obj["price"])
    expo = int(price_obj["expo"])
    publish_time = int(price_obj["publish_time"])
    return _raw_to_float(raw, expo), publish_time


@dataclass
class PriceSample:
    price: float
    publish_time: int
    source: str
    received_at: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# AAVE Oracle ABI (Chainlink prices on-chain)
# ---------------------------------------------------------------------------

ORACLE_ABI = [
    {
        "name": "getAssetPrice",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "asset", "type": "address"}],
        "outputs": [{"name": "", "type": "uint256"}],
    },
]


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

class AaveOrchestrator:
    """
    Central coordinator for AAVE V3 Flash Liquidation Bot.

    1. Hermes WS → live off-chain prices (early warning)
    2. AAVE Oracle poll → on-chain Chainlink prices
    3. Borrower scan → health factor tracking
    4. Cross-reference → find actionable liquidation candidates
    5. Executor → flash loan liquidation

    Strategy:
      - Pyth gives us 10-30s advance notice of price moves
      - We pre-calculate which positions WILL become liquidatable
      - When AAVE's Chainlink oracle catches up, HF drops below 1.0
      - We fire the flash loan liquidation immediately
    """

    def __init__(
        self,
        scan_interval: int = 60,
        dry_run: bool = True,
    ) -> None:
        self.w3 = Web3(Web3.HTTPProvider(ARBITRUM_RPC_URL))
        if not self.w3.is_connected():
            raise RuntimeError(f"Cannot connect to RPC: {ARBITRUM_RPC_URL}")
        block = self.w3.eth.block_number
        log.info("Connected to Arbitrum, block #%d", block)

        self.oracle = self.w3.eth.contract(
            address=Web3.to_checksum_address(AAVE_V3_ORACLE),
            abi=ORACLE_ABI,
        )

        # Config
        self.scan_interval = scan_interval
        self.dry_run = dry_run

        # Price state
        self.hermes_prices: dict[str, PriceSample] = {}
        self.chainlink_prices: dict[str, float] = {}  # token_addr → USD price

        # Feed ID → name mapping (for Hermes WS)
        self.id_to_name = {
            fid.lower().removeprefix("0x"): name
            for name, fid in PYTH_FEEDS.items()
        }

        # Borrower state
        self.borrowers: list[AaveBorrower] = []
        self.known_addresses: list[str] = []  # cached borrower addresses

        # Executor (only if not dry-run and contract is deployed)
        self.executor: Optional[AaveExecutor] = None
        if not dry_run:
            try:
                self.executor = AaveExecutor()
                log.info("✅ Executor initialized — LIVE MODE")
            except Exception as e:
                log.warning("⚠️ Executor failed to init: %s. Running in read-only mode.", e)
        else:
            log.info("🔵 Running in DRY-RUN mode (no transactions)")

        # Session CSV for candidates
        session_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.csv_path = os.path.join(DATA_DIR, f"aave_candidates_{session_ts}.csv")
        self._csv_file = open(self.csv_path, "w", newline="", encoding="utf-8")
        self._csv = csv.writer(self._csv_file)
        self._csv.writerow([
            "timestamp", "address", "health_factor", "collateral_usd",
            "debt_usd", "liq_threshold", "status", "action",
        ])
        self._csv_file.flush()

        self.running = True
        self._stats = {
            "scans": 0,
            "borrowers_tracked": 0,
            "liquidatable_found": 0,
            "hot_found": 0,
            "executions_attempted": 0,
            "executions_success": 0,
        }

    # ------------------------------------------------------------------ #
    # 1. Hermes WebSocket (Pyth off-chain prices — early warning)         #
    # ------------------------------------------------------------------ #

    async def _hermes_loop(self) -> None:
        """
        Stream live Pyth prices via WebSocket.
        These arrive ~10-30s BEFORE Chainlink updates on-chain.
        This is our key competitive advantage.
        """
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
    # 2. AAVE Chainlink Oracle polling (on-chain prices)                  #
    # ------------------------------------------------------------------ #

    async def _oracle_poll_loop(self) -> None:
        """Poll AAVE's Chainlink oracle for current on-chain prices."""
        loop = asyncio.get_event_loop()

        while self.running:
            for token_addr, (symbol, decimals, pyth_feed) in AAVE_TOKENS.items():
                try:
                    price = await loop.run_in_executor(
                        None,
                        lambda addr=token_addr: self.oracle.functions.getAssetPrice(
                            Web3.to_checksum_address(addr)
                        ).call(),
                    )
                    self.chainlink_prices[token_addr] = price / 1e8  # AAVE oracle = 8 decimals
                except Exception as e:
                    log.debug("Oracle read failed for %s: %s", symbol, e)

            await asyncio.sleep(3.0)  # poll every 3s

    # ------------------------------------------------------------------ #
    # 3. Borrower scan loop                                               #
    # ------------------------------------------------------------------ #

    async def _scan_loop(self) -> None:
        """
        Periodically scan AAVE borrowers for low health factors.
        Combines subgraph discovery with on-chain HF verification.
        """
        await asyncio.sleep(8)  # wait for Hermes + oracle to fill

        while self.running:
            try:
                t0 = time.time()
                self._stats["scans"] += 1

                # Step 1: Get borrower addresses (refresh every 5 scans)
                if not self.known_addresses or self._stats["scans"] % 5 == 1:
                    async with aiohttp.ClientSession() as session:
                        self.known_addresses = await fetch_borrowers_subgraph(session)
                    log.info(
                        "Refreshed borrower list: %d addresses",
                        len(self.known_addresses),
                    )

                if not self.known_addresses:
                    log.warning("No borrowers found — retrying next scan")
                    await asyncio.sleep(self.scan_interval)
                    continue

                # Step 2: Read health factors on-chain
                self.borrowers = await fetch_health_factors(
                    self.w3, self.known_addresses
                )
                elapsed = time.time() - t0

                # Step 3: Categorize
                liquidatable = [b for b in self.borrowers if b.is_liquidatable]
                hot = [b for b in self.borrowers if b.is_hot]
                warning = [b for b in self.borrowers if b.is_early_warning]

                self._stats["borrowers_tracked"] = len(self.borrowers)
                self._stats["liquidatable_found"] += len(liquidatable)
                self._stats["hot_found"] += len(hot)

                log.info(
                    "Scan #%d: %d borrowers | 🔴 %d liquidatable | 🟡 %d HOT | 🟠 %d warning [%.1fs]",
                    self._stats["scans"],
                    len(self.borrowers),
                    len(liquidatable),
                    len(hot),
                    len(warning),
                    elapsed,
                )

                # Step 4: Process liquidatable positions
                for b in liquidatable:
                    await self._process_candidate(b, "LIQUIDATABLE")

                # Step 5: Log HOT positions (Pyth early warning)
                for b in hot[:5]:
                    self._check_pyth_early_warning(b)

                # Log top 5 closest
                for b in self.borrowers[:5]:
                    log.info(
                        "  %s HF=%.4f | coll=$%.0f | debt=$%.0f | %s...",
                        b.status,
                        b.health_factor,
                        b.total_collateral_usd,
                        b.total_debt_usd,
                        b.address[:14],
                    )

            except Exception as e:
                log.error("Scan error: %s", e, exc_info=True)

            await asyncio.sleep(self.scan_interval)

    # ------------------------------------------------------------------ #
    # 4. Candidate processing and execution                               #
    # ------------------------------------------------------------------ #

    async def _process_candidate(self, borrower: AaveBorrower, label: str) -> None:
        """Process a liquidation candidate — enrich details and execute."""
        ts = datetime.utcnow().isoformat(timespec="seconds")

        log.warning(
            "🔴 %s: %s | HF=%.4f | debt=$%.0f | coll=$%.0f",
            label,
            borrower.address[:14],
            borrower.health_factor,
            borrower.total_debt_usd,
            borrower.total_collateral_usd,
        )

        # Get detailed per-asset breakdown
        try:
            borrower = await asyncio.get_event_loop().run_in_executor(
                None, fetch_borrower_details, self.w3, borrower
            )
        except Exception as e:
            log.error("Failed to get details for %s: %s", borrower.address[:14], e)
            return

        # Log breakdown
        if borrower.collateral_assets:
            for _, sym, usd, amt, _ in borrower.collateral_assets:
                log.info("    Collateral: %s = %.4f ($%.2f)", sym, amt, usd)
        if borrower.debt_assets:
            for _, sym, usd, amt, _ in borrower.debt_assets:
                log.info("    Debt:       %s = %.4f ($%.2f)", sym, amt, usd)

        action = "MONITOR"

        # Execute if not dry-run
        if self.executor and not self.dry_run:
            self._stats["executions_attempted"] += 1
            tx_hash = self.executor.execute_liquidation(borrower)
            if tx_hash:
                self._stats["executions_success"] += 1
                action = f"EXECUTED:{tx_hash[:18]}"
                log.warning("💰 LIQUIDATION EXECUTED: %s", tx_hash)
            else:
                action = "EXEC_FAILED"
        else:
            action = "DRY_RUN" if self.dry_run else "NO_EXECUTOR"

        # Write to CSV
        self._csv.writerow([
            ts, borrower.address, f"{borrower.health_factor:.6f}",
            f"{borrower.total_collateral_usd:.2f}",
            f"{borrower.total_debt_usd:.2f}",
            f"{borrower.liquidation_threshold:.2f}",
            label, action,
        ])
        self._csv_file.flush()

    def _check_pyth_early_warning(self, borrower: AaveBorrower) -> None:
        """
        Use Pyth prices to estimate if a HOT position will become liquidatable
        when Chainlink catches up. This gives us a ~10-30s head start.
        """
        # For now, just log. Full implementation would re-calculate HF
        # using Pyth prices instead of Chainlink.
        for feed_name, sample in self.hermes_prices.items():
            # Find corresponding token
            for addr, (sym, _, pyth_feed) in AAVE_TOKENS.items():
                if pyth_feed == feed_name:
                    chainlink_price = self.chainlink_prices.get(addr, 0)
                    if chainlink_price > 0:
                        delta_pct = (sample.price - chainlink_price) / chainlink_price * 100
                        if abs(delta_pct) > 0.5:
                            log.info(
                                "  ⚡ EARLY WARNING: %s Pyth=%.2f vs Chainlink=%.2f (Δ=%+.2f%%) | %s HF=%.4f",
                                sym, sample.price, chainlink_price, delta_pct,
                                borrower.address[:10], borrower.health_factor,
                            )

    # ------------------------------------------------------------------ #
    # 5. Status loop                                                      #
    # ------------------------------------------------------------------ #

    async def _status_loop(self, interval: float = 60.0) -> None:
        await asyncio.sleep(15)
        while self.running:
            hermes_count = len(self.hermes_prices)
            chainlink_count = len(self.chainlink_prices)

            # Price comparison: Pyth vs Chainlink
            comparisons = []
            for feed_name, sample in self.hermes_prices.items():
                for addr, (sym, _, pyth_feed) in AAVE_TOKENS.items():
                    if pyth_feed == feed_name:
                        cl_price = self.chainlink_prices.get(addr, 0)
                        if cl_price > 0:
                            delta = (sample.price - cl_price) / cl_price * 100
                            comparisons.append(
                                f"  {sym:8s} Pyth={sample.price:>12.2f}  Chainlink={cl_price:>12.2f}  Δ={delta:+.3f}%"
                            )
                        break

            log.info(
                "\n=== AAVE BOT STATUS ===\n"
                "  Mode: %s\n"
                "  Pyth feeds: %d  |  Chainlink prices: %d  |  Borrowers: %d\n"
                "  Scans: %d  |  Liquidatable found: %d  |  HOT found: %d\n"
                "  Executions: %d attempted / %d success\n"
                "  Price comparison (Pyth vs Chainlink):\n%s",
                "DRY-RUN" if self.dry_run else "LIVE",
                hermes_count, chainlink_count, self._stats["borrowers_tracked"],
                self._stats["scans"],
                self._stats["liquidatable_found"],
                self._stats["hot_found"],
                self._stats["executions_attempted"],
                self._stats["executions_success"],
                "\n".join(comparisons) if comparisons else "  (waiting for data...)",
            )
            await asyncio.sleep(interval)

    # ------------------------------------------------------------------ #
    # Run / Stop                                                           #
    # ------------------------------------------------------------------ #

    async def run(self) -> None:
        log.info(
            "╔══════════════════════════════════════════╗\n"
            "║  AAVE V3 Flash Liquidation Bot v1.0      ║\n"
            "║  Network: Arbitrum One                    ║\n"
            "║  Mode: %s                          ║\n"
            "╚══════════════════════════════════════════╝",
            "DRY-RUN " if self.dry_run else "LIVE    ",
        )
        log.info("Scan interval: %ds", self.scan_interval)
        log.info("Candidates log: %s", self.csv_path)

        if not self.dry_run and not self.executor:
            log.error(
                "LIVE mode requested but executor failed to init. "
                "Set FLASH_LIQUIDATOR_ADDRESS in .env after deploying contract!"
            )

        try:
            await asyncio.gather(
                self._hermes_loop(),
                self._oracle_poll_loop(),
                self._scan_loop(),
                self._status_loop(),
            )
        except asyncio.CancelledError:
            pass
        finally:
            self._csv_file.close()
            if self.executor:
                self.executor.print_stats()
            log.info(
                "\n=== FINAL SUMMARY ===\n"
                "  Scans: %d\n"
                "  Borrowers tracked: %d\n"
                "  Liquidatable found: %d\n"
                "  Executions: %d/%d (success/attempted)\n"
                "  Data saved to: %s",
                self._stats["scans"],
                self._stats["borrowers_tracked"],
                self._stats["liquidatable_found"],
                self._stats["executions_success"],
                self._stats["executions_attempted"],
                self.csv_path,
            )

    def stop(self) -> None:
        self.running = False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="AAVE V3 Flash Liquidation Bot"
    )
    parser.add_argument(
        "--scan-interval", type=int, default=60,
        help="Seconds between borrower scans (default: 60)",
    )
    parser.add_argument(
        "--dry-run", action="store_true", default=True,
        help="Simulation mode — no real transactions (default: True)",
    )
    parser.add_argument(
        "--live", action="store_true", default=False,
        help="Enable LIVE mode — will send real transactions!",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    dry_run = not args.live  # --live flag overrides --dry-run
    orchestrator = AaveOrchestrator(
        scan_interval=args.scan_interval,
        dry_run=dry_run,
    )
    try:
        await orchestrator.run()
    except KeyboardInterrupt:
        orchestrator.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
