import os
import logging
from web3 import Web3
from dotenv import load_dotenv

# Konfiguracja logowania
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("Test.Plasma")

load_dotenv()

# Parametry Plasma
RPC_URL = os.getenv("PLASMA_RPC_URL")
EXECUTOR_ADDRESS = os.getenv("PLASMA_EXECUTOR_ADDRESS")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")

# Adresy tokenów (Plasma - z Twojej konfiguracji)
WETH = "0x9895D81bB462A195b4922ED7De0e3ACD007c32CB"
USDT = "0xB8CE59FC3717ada4C02eaDF9682A9e934F625ebb"
DUMMY_USER = "0x0000000000000000000000000000000000000001"

ABI = [
    {
        "name": "executeLiquidation",
        "type": "function",
        "stateMutability": "external",
        "inputs": [
            {"name": "collateralAsset", "type": "address"},
            {"name": "debtAsset", "type": "address"},
            {"name": "user", "type": "address"},
            {"name": "debtToCover", "type": "uint256"},
            {"name": "swapPoolFee", "type": "uint24"}
        ]
    }
]

def run_test():
    if not EXECUTOR_ADDRESS or not PRIVATE_KEY:
        logger.error("Brak PLASMA_EXECUTOR_ADDRESS lub PRIVATE_KEY w .env!")
        return

    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    account = w3.eth.account.from_key(PRIVATE_KEY)
    contract = w3.eth.contract(address=Web3.to_checksum_address(EXECUTOR_ADDRESS), abi=ABI)

    print(f"--- TEST KONTRAKTU PLASMA ---")
    print(f"Kontrakt: {EXECUTOR_ADDRESS}")
    print(f"Twój adres: {account.address}")

    # Próba symulacji
    print("Rozpoczynam symulację (static call)...")
    try:
        contract.functions.executeLiquidation(
            Web3.to_checksum_address(WETH),
            Web3.to_checksum_address(USDT),
            Web3.to_checksum_address(DUMMY_USER),
            10**6, # 1 USDT (zakładając 6 decimals)
            3000   
        ).call({'from': account.address})
        print("WAŻNE: Symulacja przeszła? To dziwne, użytkownik powinien być zdrowy.")
    except Exception as e:
        error_msg = str(e)
        if "Position is healthy" in error_msg:
            print("\n✅ SUKCES TESTU: Kontrakt na PLASMIE działa poprawnie!")
            print("Otrzymano spodziewany błąd: 'Position is healthy, HF >= 1'")
            print("To oznacza, że skrypt Python, klucz prywatny i kontrakt są ZSYNCHRONIZOWANE.\n")
        else:
            print(f"\n❌ BŁĄD KONTRAKTU: {error_msg}\n")

if __name__ == "__main__":
    run_test()
