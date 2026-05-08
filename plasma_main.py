import os
import time
import logging
import json
from web3 import Web3
from dotenv import load_dotenv

# Importy lokalne
import plasma_config as config
import plasma_positions as positions
from plasma_executor import PlasmaAaveExecutor

# Ładowanie .env
load_dotenv()

# Konfiguracja Logowania (Konsola + Plik)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("plasma_bot.log", encoding='utf-8')
    ]
)
logger = logging.getLogger("AaveBot.Plasma")

DB_FILE = "plasma_borrowers.json"

def load_borrowers():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r") as f:
            return set(json.load(f))
    return set()

def save_borrowers(borrowers):
    with open(DB_FILE, "w") as f:
        json.dump(list(borrowers), f)

def main():
    logger.info("=== STARTUJĘ AAVE V3 BOT (PLASMA NETWORK) - DEEP SCAN MODE ===")
    
    # 1. Połączenie i pobranie dynamicznego adresu POOL
    w3 = Web3(Web3.HTTPProvider(config.RPC_URL))
    if not w3.is_connected():
        logger.error("Błąd połączenia z RPC Plasma!")
        return
    
    # Pobieramy adres Poola z Providera (to gwarantuje poprawność)
    provider_abi = [{"inputs":[],"name":"getPool","outputs":[{"internalType":"address","name":"","type":"address"}],"stateMutability":"view","type":"function"}]
    provider = w3.eth.contract(address=Web3.to_checksum_address(config.POOL_ADDRESSES_PROVIDER), abi=provider_abi)
    config.POOL = provider.functions.getPool().call()
    logger.info(f"Dynamicznie pobrany adres POOL: {config.POOL}")

    current_block = w3.eth.block_number
    logger.info(f"Połączono. Obecny blok: {current_block}")

    # Załaduj już znanych dłużników
    known_borrowers = load_borrowers()
    logger.info(f"Załadowano {len(known_borrowers)} dłużników z pliku.")

    # Zaczynamy od bloku 20 mln (świeższe dane), żeby sprawdzić czy bot łapie ruch
    start_search_block = 20000000 
    
    # Jeśli mamy plik konfiguracyjny z ostatnim blokiem, czytamy go
    last_scanned_file = "plasma_last_block.txt"
    if os.path.exists(last_scanned_file):
        with open(last_scanned_file, "r") as f:
            start_search_block = int(f.read().strip())
    
    # 1. GŁĘBOKIE SKANOWANIE HISTORYCZNE (tylko jeśli jesteśmy bardzo do tyłu)
    if start_search_block < current_block - 1000:
        logger.info(f"Rozpoczynam głębokie skanowanie od bloku {start_search_block}...")
        
        batch_size = 50000 # dRPC zazwyczaj pozwala na paczki 50-100k
        for block in range(start_search_block, current_block, batch_size):
            to_block = min(block + batch_size - 1, current_block)
            
            new_list = positions.get_borrowers_from_logs(w3, block, to_block)
            if new_list:
                known_borrowers.update(new_list)
                save_borrowers(known_borrowers)
            
            logger.info(f"Postęp skanowania: {to_block}/{current_block} (Znaleziono: {len(known_borrowers)})")
            
            with open(last_scanned_file, "w") as f:
                f.write(str(to_block))
            
            time.sleep(0.5) # Oddech dla RPC

    # 2. Inicjalizacja egzekutora
    executor_address = os.getenv("PLASMA_EXECUTOR_ADDRESS")
    executor = PlasmaAaveExecutor() if executor_address else None

    # 3. Statystyki
    stats_data = {
        "scans": 0,
        "attempts": 0,
        "successes": 0
    }

    logger.info("Rozpoczynam pętlę monitorowania HF...")

    while True:
        try:
            stats_data["scans"] += 1
            current_block = w3.eth.block_number
            
            # A. Szybki skan nowych bloków
            new_borrows = positions.get_borrowers_from_logs(w3, current_block - 50, current_block)
            if new_borrows:
                known_borrowers.update(new_borrows)
                save_borrowers(known_borrowers)

            # B. Sprawdzanie zdrowia
            if known_borrowers:
                borrowers_list = list(known_borrowers)
                user_stats = positions.check_health_factors_multicall(w3, borrowers_list)
                
                # Sortowanie po HF
                user_stats.sort(key=lambda x: x['healthFactor'])
                
                print("\n" + "="*95)
                header = f"SCANS: {stats_data['scans']} | KNOWN: {len(known_borrowers)} | ATTEMPTS: {stats_data['attempts']} | SUCCESS: {stats_data['successes']}"
                print(f"{header:^95}")
                print("-" * 95)
                print(f"{'UŻYTKOWNIK':<44} | {'HF':<8} | {'DŁUG (USD)':<12} | {'STATUS'}")
                print("-" * 95)
                
                for target in user_stats[:15]:
                    user = target['user']
                    hf = target['healthFactor']
                    debt = target['totalDebt']
                    
                    status = "OK"
                    if hf < 1.0: status = "!!! LIKWIDACJA !!!"
                    elif hf < 1.05: status = "KRYTYCZNY"
                    elif hf < 1.3: status = "ZAGROŻONY"
                    
                    print(f"{user:<44} | {hf:<8.4f} | {debt:<12.2f} | {status}")
                    
                    if hf < 1.0 and debt > 10:
                        stats_data["attempts"] += 1
                        logger.error(f"PRÓBA LIKWIDACJI: {user} | Dług: {debt}$")
                        
                        if executor:
                            try:
                                collateral_asset, debt_asset = positions.get_user_assets(w3, user)
                                if collateral_asset and debt_asset:
                                    max_amount = 2**256 - 1 
                                    tx_hash = executor.execute(collateral_asset, debt_asset, user, max_amount)
                                    if tx_hash:
                                        stats_data["successes"] += 1
                                        logger.warning(f"SUKCES! Hash: {tx_hash}")
                                else:
                                    logger.error("Nie udało się dopasować tokenów do likwidacji.")
                            except Exception as e:
                                logger.error(f"Błąd podczas próby likwidacji: {e}")
                        else:
                            logger.warning("Tryb symulacji - brak adresu egzekutora.")
                
                print("="*95 + "\n")

            time.sleep(15) # Szybsze odświeżanie dla lepszego UX
        except Exception as e:
            logger.error(f"Błąd: {e}")
            time.sleep(10)

if __name__ == "__main__":
    main()
