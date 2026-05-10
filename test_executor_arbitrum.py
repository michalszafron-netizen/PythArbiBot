import os
import logging
from web3 import Web3
from dotenv import load_dotenv

# Konfiguracja logowania
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("Test.Arbitrum")

load_dotenv()

# Parametry Arbitrum
RPC_URL = os.getenv("ARBITRUM_RPC_URL")
EXECUTOR_ADDRESS = os.getenv("FLASH_LIQUIDATOR_ADDRESS")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")

# Adresy tokenów (Arbitrum)
USDC = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"
WETH = "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"
DUMMY_USER = "0x0000000000000000000000000000000000000001" # Losowy adres

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
        logger.error("Brak FLASH_LIQUIDATOR_ADDRESS lub PRIVATE_KEY w .env!")
        return

    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    account = w3.eth.account.from_key(PRIVATE_KEY)
    contract = w3.eth.contract(address=Web3.to_checksum_address(EXECUTOR_ADDRESS), abi=ABI)

    print(f"--- TEST KONTRAKTU ARBITRUM ---")
    print(f"Kontrakt: {EXECUTOR_ADDRESS}")
    print(f"Twój adres: {account.address}")

    # Próba symulacji (nie kosztuje gazu)
    print("Rozpoczynam symulację (static call)...")
    try:
        contract.functions.executeLiquidation(
            Web3.to_checksum_address(WETH),
            Web3.to_checksum_address(USDC),
            Web3.to_checksum_address(DUMMY_USER),
            10**6, # 1 USDC
            3000   # 0.3% fee
        ).call({'from': account.address})
        print("WAŻNE: Symulacja przeszła? To dziwne, użytkownik powinien być zdrowy.")
    except Exception as e:
        error_msg = str(e)
        if "Position is healthy" in error_msg:
            print("\n✅ SUKCES TESTU: Kontrakt na ARBITRUM działa poprawnie!")
            print("Otrzymano spodziewany błąd: 'Position is healthy, HF >= 1'")
            print("To oznacza, że skrypt Python, klucz prywatny i kontrakt są ZSYNCHRONIZOWANE.\n")
        else:
            print(f"\n❌ BŁĄD KONTRAKTU: {error_msg}\n")

if __name__ == "__main__":
    run_test()
