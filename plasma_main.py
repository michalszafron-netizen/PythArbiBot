import os
import time
import logging
import json
import asyncio
from datetime import datetime
from web3 import Web3
from dotenv import load_dotenv

# Local imports
import plasma_config as config
import plasma_positions as positions
from plasma_executor import PlasmaAaveExecutor
from plasma_positions import AaveBorrower

load_dotenv()

# Logging Configuration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("plasma_bot.log", encoding='utf-8')
    ]
)
log = logging.getLogger("AaveBot.Plasma")

DB_FILE = "plasma_borrowers.json"

def load_borrowers():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r") as f:
                return set(json.load(f))
        except: return set()
    return set()

def save_borrowers(borrowers):
    with open(DB_FILE, "w") as f:
        json.dump(list(borrowers), f)

async def main():
    log.info("=== STARTUJĘ AAVE V3 BOT (PLASMA NETWORK) - INTELLIGENCE MODE ===")
    
    w3 = Web3(Web3.HTTPProvider(config.RPC_URL))
    if not w3.is_connected():
        log.error("Błąd połączenia z RPC Plasma!")
        return
    
    # Initialize POOL address
    provider_abi = [{"inputs":[],"name":"getPool","outputs":[{"internalType":"address","name":"","type":"address"}],"stateMutability":"view","type":"function"}]
    provider = w3.eth.contract(address=Web3.to_checksum_address(config.POOL_ADDRESSES_PROVIDER), abi=provider_abi)
    config.POOL = provider.functions.getPool().call()
    log.info(f"Adres POOL: {config.POOL}")

    known_borrowers = load_borrowers()
    log.info(f"Załadowano {len(known_borrowers)} dłużników.")

    last_scanned_file = "plasma_last_block.txt"
    start_block = 20000000
    if os.path.exists(last_scanned_file):
        with open(last_scanned_file, "r") as f:
            try: start_block = int(f.read().strip())
            except: pass

    # Execution Setup
    executor_address = os.getenv("PLASMA_EXECUTOR_ADDRESS")
    executor = PlasmaAaveExecutor() if executor_address else None
    dry_run = not bool(executor_address)

    stats = {"scans": 0, "attempts": 0, "successes": 0, "known": len(known_borrowers)}

    while True:
        try:
            t0 = time.time()
            stats["scans"] += 1
            current_block = w3.eth.block_number
            
            # 1. Quick log scan for new users
            new_list = positions.get_borrowers_from_logs(w3, current_block - 100, current_block)
            if new_list:
                known_borrowers.update(new_list)
                save_borrowers(known_borrowers)
                stats["known"] = len(known_borrowers)

            # 2. Check Health Factors
            borrowers_list = list(known_borrowers)
            all_borrowers = await positions.check_health_factors_multicall(w3, borrowers_list)
            
            # 3. Enrich TOP risks
            PT_TOKENS = {sym for sym in config.TOKEN_MAP if sym.startswith("PT-")}
            reverse_token_map = {addr.lower(): sym for sym, addr in config.TOKEN_MAP.items()}

            all_borrowers.sort(key=lambda b: b.health_factor)

            # Wzbogacamy top 50 zeby po odfiltraniu PT zostalo min 30
            enrich_pool = all_borrowers[:50]

            oracle_abi = [{"inputs":[{"internalType":"address","name":"asset","type":"address"}],"name":"getAssetPrice","outputs":[{"internalType":"uint256","name":"","type":"uint256"}],"stateMutability":"view","type":"function"}]
            oracle = w3.eth.contract(address=Web3.to_checksum_address(config.AAVE_ORACLE), abi=oracle_abi)

            current_prices = {}
            for sym, addr in config.TOKEN_MAP.items():
                try:
                    p = oracle.functions.getAssetPrice(Web3.to_checksum_address(addr)).call()
                    current_prices[sym] = p / 1e8
                except: continue

            await positions.fetch_detailed_data(w3, enrich_pool, current_prices)

            # Dopiero teraz filtrujemy PT — symbole sa juz znane po fetch_detailed_data
            hot_candidates = [
                b for b in enrich_pool
                if b.main_collateral_symbol not in PT_TOKENS
                and b.main_debt_symbol not in PT_TOKENS
            ][:30]

            # 4. UI Dashboard (Intelligence)
            W = 130
            print("\n" + "=" * W)
            header = f"PLASMA INTELLIGENCE: {stats['scans']} | KNOWN: {stats['known']} | ATTEMPTS: {stats['attempts']} | SUCCESS: {stats['successes']}"
            print(f"{header:^{W}}")
            print("-" * W)
            print(f"{'UŻYTKOWNIK':<20} | {'HF':<7} | {'TYP':<10} | {'DYSTANS':<8} | {'CENA LIQ':<12} | {'DLUG USD':<11} | {'AKTYWA (C/D)':<22} | {'STATUS'}")
            print("-" * W)

            for b in hot_candidates:
                dist_str  = f"{b.dist_to_liq_pct:+.2f}%" if b.dist_to_liq_pct != 0 else "N/A"
                debt_str  = f"${b.total_debt_usd:,.0f}"
                assets_str = f"{b.main_collateral_symbol}/{b.main_debt_symbol}" if b.main_collateral_symbol else "CHECKING..."
                short_addr = f"{b.address[:10]}...{b.address[-8:]}"

                # ETH-LOOP: pokazuj ratio zamiast ceny USD
                if getattr(b, 'is_eth_loop', False):
                    liq_price_str = f"r={b.liq_price_estimate:.4f}"
                elif b.liq_price_estimate > 0:
                    liq_price_str = f"${b.liq_price_estimate:,.2f}"
                else:
                    liq_price_str = "STABLE"

                print(f"{short_addr:<20} | {b.health_factor:<7.4f} | {b.pos_type:<10} | {dist_str:<8} | {liq_price_str:<12} | {debt_str:<11} | {assets_str:<22} | {b.status_label}")

            # 5. Execute Liquidations
            liquidatable = [b for b in all_borrowers if b.health_factor < 1.0]
            for b in liquidatable:
                if b.total_debt_usd < 5:
                    continue

                coll, debt = positions.get_user_assets(w3, b.address)
                if not coll or not debt:
                    log.error("Nie znaleziono aktywów dla %s", b.address)
                    continue

                coll_sym = reverse_token_map.get(coll.lower(), "???")
                debt_sym = reverse_token_map.get(debt.lower(), "???")

                # PT tokens — brak płynności na DEX
                if coll_sym in PT_TOKENS or debt_sym in PT_TOKENS:
                    log.info("Pomijam %s — PT token (%s/%s)", b.address[:14], coll_sym, debt_sym)
                    continue

                # Sprawdz czy para ma potwierdzoną pulę DEX na Plasma
                if executor and not executor.is_route_valid(coll_sym, debt_sym):
                    log.info("Pomijam %s — brak puli DEX dla %s/%s",
                             b.address[:14], coll_sym, debt_sym)
                    continue

                stats["attempts"] += 1
                log.warning("!!! LIKWIDACJA WYKRYTA: %s | HF: %.4f | %s/%s | $%.0f",
                            b.address[:14], b.health_factor, coll_sym, debt_sym, b.total_debt_usd)

                if not dry_run and executor:
                    try:
                        fee = executor.get_fee(coll_sym, debt_sym)
                        log.info("Fee tier: %d (%.2f%%) dla %s/%s", fee, fee/10000, coll_sym, debt_sym)
                        tx_hash = executor.execute(coll, debt, b.address, 2**256 - 1, fee)
                        if tx_hash:
                            stats["successes"] += 1
                            log.warning("💰 SUKCES LIKWIDACJI: %s", tx_hash)
                    except Exception as e:
                        log.error("Błąd egzekucji: %s", e)
                else:
                    log.info("[DRY-RUN] Próba: %s | %s/%s", b.address[:14], coll_sym, debt_sym)

            # 6. Dynamic Turbo Mode
            is_turbo = any(b.health_factor < 1.05 or (b.dist_to_liq_pct != 0 and abs(b.dist_to_liq_pct) < 1.5) for b in hot_candidates)
            current_interval = 5.0 if is_turbo else 30.0
            
            if is_turbo:
                print(f"  >>> 🚀 [TURBO MODE ACTIVE] Next scan in {current_interval}s")
            else:
                print(f"  >>> [Normal Mode] Next scan in {current_interval}s")
            print("="*115 + "\n")

            await asyncio.sleep(current_interval)
            
        except Exception as e:
            log.error(f"Błąd pętli głównej: {e}", exc_info=True)
            await asyncio.sleep(10)

if __name__ == "__main__":
    asyncio.run(main())
