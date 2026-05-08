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

    def execute(self, collateral, debt, user, amount, fee=3000):
        if not self.contract:
            logger.error("Nie można wykonać - brak adresu kontraktu!")
            return None

        nonce = self.w3.eth.get_transaction_count(self.address)
        
        try:
            # Budowa transakcji
            tx = self.contract.functions.executeLiquidation(
                Web3.to_checksum_address(collateral),
                Web3.to_checksum_address(debt),
                Web3.to_checksum_address(user),
                int(amount),
                int(fee)
            ).build_transaction({
                'from': self.address,
                'nonce': nonce,
                'gas': 3000000,
                'gasPrice': self.w3.eth.gas_price,
                'chainId': 9745
            })

            signed_tx = self.w3.eth.account.sign_transaction(tx, self.private_key)
            tx_hash = self.w3.eth.send_raw_transaction(signed_tx.raw_transaction)
            logger.warning(f"!!! TRANSAKCJA WYSŁANA: {tx_hash.hex()}")
            return tx_hash.hex()
            
        except Exception as e:
            logger.error(f"Błąd wysyłania transakcji: {e}")
            return None
