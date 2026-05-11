import json
import logging
import time
import os
from typing import Optional
from eth_account import Account
from web3 import Web3
from web3.exceptions import ContractLogicError
import plasma_config as config

logger = logging.getLogger("AaveBot.Plasma.Executor")

FLASH_LIQUIDATOR_ABI = [
    {
        "name": "executeLiquidation",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "collateralAsset", "type": "address"},
            {"name": "debtAsset", "type": "address"},
            {"name": "user", "type": "address"},
            {"name": "debtToCover", "type": "uint256"},
            {"name": "swapPoolFee", "type": "uint24"},
        ],
        "outputs": [],
    }
]

class PlasmaAaveExecutor:
    def __init__(self):
        self.rpc_url = config.RPC_URL
        self.w3 = Web3(Web3.HTTPProvider(self.rpc_url))
        
        self.private_key = os.getenv("PRIVATE_KEY")
        self.executor_address = os.getenv("PLASMA_EXECUTOR_ADDRESS")
        
        if not self.private_key:
            raise ValueError("Brak PRIVATE_KEY w .env")
        
        self.account = Account.from_key(self.private_key)
        self.address = self.account.address
        
        if self.executor_address:
            self.contract = self.w3.eth.contract(
                address=Web3.to_checksum_address(self.executor_address),
                abi=FLASH_LIQUIDATOR_ABI
            )
            logger.info(f"Liquidator załadowany: {self.executor_address}")
        else:
            self.contract = None
            logger.warning("Brak PLASMA_EXECUTOR_ADDRESS - tryb TYLKO SYMULACJA")

    # Pary z potwierdzoną głęboką pulą na natywnym DEX Plasma
    # Tylko te będą próbowane — reszta oszczędza gas
    VALID_ROUTES = {
        ("USDe",      "USDT0"),
        ("sUSDe",     "USDT0"),
        ("syrupUSDT", "USDT0"),
        ("WETH",      "USDT0"),
        ("WXPL",      "USDT0"),
    }

    def get_fee(self, coll_sym: str, debt_sym: str) -> int:
        """Zwraca poprawny fee tier dla pary — zweryfikowane on-chain."""
        return config.SWAP_FEE_TIERS.get((coll_sym, debt_sym),
               config.SWAP_FEE_TIERS.get((debt_sym, coll_sym), 3000))

    def is_route_valid(self, coll_sym: str, debt_sym: str) -> bool:
        """Sprawdza czy para ma potwierdzoną pulę DEX na Plasma."""
        return (coll_sym, debt_sym) in self.VALID_ROUTES

    def simulate(self, collateral: str, debt: str, user: str, amount: int, fee: int) -> bool:
        """eth_call przed wysłaniem — łapie reverty bez spalania gasu."""
        try:
            self.contract.functions.executeLiquidation(
                Web3.to_checksum_address(collateral),
                Web3.to_checksum_address(debt),
                Web3.to_checksum_address(user),
                amount,
                fee,
            ).call({"from": self.address})
            return True
        except ContractLogicError as e:
            logger.warning("Symulacja odrzucona: %s", e)
            return False
        except Exception as e:
            logger.warning("Błąd symulacji: %s", e)
            return False

    def execute(self, collateral, debt, user, amount, fee=None):
        if not self.contract:
            logger.error("Nie można wykonać - brak adresu kontraktu!")
            return None

        if fee is None:
            fee = 3000

        # Symulacja — zero gasu, wyłapuje revert zanim wyślemy prawdziwy tx
        logger.info("Symulacja likwidacji...")
        if not self.simulate(collateral, debt, user, int(amount), int(fee)):
            logger.warning("Symulacja nie przeszła — pomijam, oszczędzam gas")
            return None
        logger.info("Symulacja OK — wysyłam transakcję")

        nonce = self.w3.eth.get_transaction_count(self.address)

        try:
            base_gas = self.w3.eth.gas_price
            gas_price = min(
                int(max(base_gas, Web3.to_wei(1, 'gwei')) * 1.5),
                Web3.to_wei(100, 'gwei')
            )
            logger.info("Gas price: %.2f gwei", Web3.from_wei(gas_price, 'gwei'))

            tx = self.contract.functions.executeLiquidation(
                Web3.to_checksum_address(collateral),
                Web3.to_checksum_address(debt),
                Web3.to_checksum_address(user),
                int(amount),
                int(fee)
            ).build_transaction({
                'from': self.address,
                'nonce': nonce,
                'gas': 3_000_000,
                'gasPrice': gas_price,
                'chainId': 9745
            })

            signed_tx = self.w3.eth.account.sign_transaction(tx, self.private_key)
            tx_hash = self.w3.eth.send_raw_transaction(signed_tx.raw_transaction)
            logger.warning("!!! TRANSAKCJA WYSŁANA: %s", tx_hash.hex())
            return tx_hash.hex()

        except Exception as e:
            logger.error("Błąd wysyłania transakcji: %s", e)
            return None
