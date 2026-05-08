import logging
from web3 import Web3
import plasma_config as config
from eth_utils import event_abi_to_log_topic

# Konfiguracja
w3 = Web3(Web3.HTTPProvider(config.RPC_URL))
logger = logging.getLogger("Diag")
logging.basicConfig(level=logging.INFO)

def diagnose():
    # 1. Pobierz aktualny adres Poola
    provider_abi = [{"inputs":[],"name":"getPool","outputs":[{"internalType":"address","name":"","type":"address"}],"stateMutability":"view","type":"function"}]
    provider = w3.eth.contract(address=Web3.to_checksum_address(config.POOL_ADDRESSES_PROVIDER), abi=provider_abi)
    pool_addr = provider.functions.getPool().call()
    
    logger.info(f"Diagnozuję Pool: {pool_addr}")
    
    # 2. Pobierz ostatnie 5000 bloków logów (bez filtrów tematów)
    current = w3.eth.block_number
    logger.info(f"Blok: {current}")
    
    logs = w3.eth.get_logs({
        "fromBlock": current - 5000,
        "toBlock": current,
        "address": pool_addr
    })
    
    logger.info(f"Znaleziono {len(logs)} logów w ostatnich 5000 blokach.")
    
    topics_seen = set()
    for log in logs:
        topics_seen.add(log['topics'][0].hex())
    
    for t in topics_seen:
        logger.info(f"Widziany Topic 0: {t}")

if __name__ == "__main__":
    diagnose()
