"""
PythOracle MEV Bot — Final Executor (Multicall3 Edition).

Combines Pyth price updates and GMX V2 liquidations into a single atomic transaction.
"""
import asyncio
import logging
import os
import time
from typing import List, Optional

import aiohttp
from eth_account import Account
from eth_account.signers.local import LocalAccount
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

from config import (
    ARBITRUM_RPC_URL,
    LOG_LEVEL,
    PYTH_CONTRACT,
    PYTH_FEEDS,
    PYTH_HERMES_HTTP,
    GMX_LIQUIDATION_HANDLER,
    MULTICALL_ADDRESS,
)

from dotenv import load_dotenv
load_dotenv()

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger("executor")
# Add file handler for executor specifically to ensure visibility
fh = logging.FileHandler("execution.log")
fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
log.addHandler(fh)
log.setLevel(logging.INFO)

# ---------------------------------------------------------------------------
# ABIs
# ---------------------------------------------------------------------------
PYTH_ABI = [
    {"name": "updatePriceFeeds", "type": "function", "stateMutability": "payable", "inputs": [{"name": "updateData", "type": "bytes[]"}], "outputs": []},
    {"name": "getUpdateFee", "type": "function", "stateMutability": "view", "inputs": [{"name": "updateData", "type": "bytes[]"}], "outputs": [{"name": "fee", "type": "uint256"}]}
]

GMX_LIQ_ABI = [
    {"name": "executeLiquidation", "type": "function", "stateMutability": "nonpayable", "inputs": [
        {"name": "account", "type": "address"},
        {"name": "market", "type": "address"},
        {"name": "collateralToken", "type": "address"},
        {"name": "isLong", "type": "bool"}
    ], "outputs": []}
]

MULTICALL_ABI = [
    {"name": "aggregate3", "type": "function", "stateMutability": "payable", "inputs": [
        {"name": "calls", "type": "tuple[]", "components": [
            {"name": "target", "type": "address"},
            {"name": "allowFailure", "type": "bool"},
            {"name": "callData", "type": "bytes"}
        ]}
    ], "outputs": [
        {"name": "returnData", "type": "tuple[]", "components": [
            {"name": "success", "type": "bool"},
            {"name": "returnData", "type": "bytes"}
        ]}
    ]}
]

class Executor:
    def __init__(self):
        self.w3 = Web3(Web3.HTTPProvider(ARBITRUM_RPC_URL))
        self.w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
        
        self.pyth = self.w3.eth.contract(address=Web3.to_checksum_address(PYTH_CONTRACT), abi=PYTH_ABI)
        self.gmx_liq = self.w3.eth.contract(address=Web3.to_checksum_address(GMX_LIQUIDATION_HANDLER), abi=GMX_LIQ_ABI)
        self.multicall = self.w3.eth.contract(address=Web3.to_checksum_address(MULTICALL_ADDRESS), abi=MULTICALL_ABI)
        
        priv_key = os.getenv("PRIVATE_KEY")
        self.account: Optional[LocalAccount] = Account.from_key(priv_key) if priv_key else None
        
        if self.account:
            log.info("Executor loaded with account: %s", self.account.address)
        else:
            log.warning("No PRIVATE_KEY found in .env. Execution will be SIMULATION ONLY.")
            
        self.session: Optional[aiohttp.ClientSession] = None

    async def start(self):
        self.session = aiohttp.ClientSession()

    async def stop(self):
        if self.session:
            await self.session.close()

    async def get_vaa_for_feeds(self, feed_names: List[str]) -> Optional[List[str]]:
        """Fetch fresh price update data from Hermes."""
        if not self.session: await self.start()
        
        feed_ids = [PYTH_FEEDS[name] for name in feed_names if name in PYTH_FEEDS]
        url = f"{PYTH_HERMES_HTTP}/v2/updates/price/latest"
        params = {"ids[]": [f"0x{f.lower().removeprefix('0x')}" for f in feed_ids], "encoding": "hex"}
        
        try:
            async with self.session.get(url, params=params) as resp:
                if resp.status != 200: return None
                data = await resp.json()
                return data.get("binary", {}).get("data", [])
        except Exception as e:
            log.error("VAA fetch error: %s", e)
            return None

    async def execute_liquidation(self, pos, dry_run: bool = True):
        """
        The main MEV function. 
        Combines Pyth update + GMX liquidation into one Multicall.
        """
        log.info("Attempting liquidation for %s %s (Account: %s)", 
                 pos.feed, "LONG" if pos.is_long else "SHORT", pos.account[:10])
        
        # 1. Get fresh VAA
        vaas = await self.get_vaa_for_feeds([pos.feed])
        if not vaas:
            log.error("Failed to get VAA for %s", pos.feed)
            return False
        
        update_data = [bytes.fromhex(v.removeprefix("0x")) for v in vaas]
        
        from eth_abi import encode as abi_encode

        try:
            # 2. Prepare sub-calls (Manual Encoding to bypass web3.py issues)
            try:
                # Call 1: Pyth.updatePriceFeeds(bytes[])
                pyth_selector = self.w3.keccak(text="updatePriceFeeds(bytes[])")[:4]
                pyth_call_bytes = pyth_selector + abi_encode(['bytes[]'], [update_data])
                
                # Call 2: GMX.liquidatePosition(address,address,address,bool,address)
                gmx_selector = self.w3.keccak(text="liquidatePosition(address,address,address,bool,address)")[:4]
                gmx_call_bytes = gmx_selector + abi_encode(['address', 'address', 'address', 'bool', 'address'], [
                    Web3.to_checksum_address(pos.account),
                    Web3.to_checksum_address(pos.market),
                    Web3.to_checksum_address(pos.collateral_token),
                    pos.is_long,
                    Web3.to_checksum_address(self.account.address if self.account else "0x0000000000000000000000000000000000000000")
                ])
                
                log.debug("Manual encoding successful")
            except Exception as e:
                log.error("Manual encoding failed: %s", e)
                return False
            
            # 3. Calculate Fee
            try:
                update_fee = self.pyth.functions.getUpdateFee(update_data).call()
            except Exception as e:
                log.warning("Failed to get Pyth update fee: %s. Using 1 wei.", e)
                update_fee = 1

            # 4. Multicall3 encoding (Manual)
            # struct Call3Value { address target; bool allowFailure; uint256 value; bytes callData; }
            try:
                # We use aggregate3Value to send ETH only to Pyth
                calls_data = [
                    (Web3.to_checksum_address(self.pyth.address), True, update_fee, pyth_call_bytes),
                    (Web3.to_checksum_address(self.gmx_liq.address), False, 0, gmx_call_bytes)
                ]
                multicall_selector = self.w3.keccak(text="aggregate3Value((address,bool,uint256,bytes)[])")[:4]
                multicall_data = multicall_selector + abi_encode(['(address,bool,uint256,bytes)[]'], [calls_data])
            except Exception as e:
                log.error("Multicall encoding failed: %s", e)
                return False
            
            # 5. Simulation (eth_call)
            tx_params = {
                "to": Web3.to_checksum_address(MULTICALL_ADDRESS),
                "from": self.account.address if self.account else "0x0000000000000000000000000000000000000000",
                "value": update_fee,
                "data": multicall_data,
                "gas": 3000000,
            }
            
            try:
                self.w3.eth.call(tx_params)
                log.info("[SUCCESS] SIMULATION SUCCESS for %s", pos.feed)
            except Exception as sim_err:
                log.warning("[FAILED] SIMULATION FAILED: %s", sim_err)
                return False
            
            if dry_run or not self.account:
                log.info("Dry-run mode: skipping actual broadcast.")
                return True

            # 6. Real Broadcast
            nonce = self.w3.eth.get_transaction_count(self.account.address)
            gas_price = self.w3.eth.gas_price
            
            tx = {
                "to": Web3.to_checksum_address(MULTICALL_ADDRESS),
                "from": self.account.address,
                "value": update_fee,
                "data": multicall_data,
                "gas": 2500000,
                "gasPrice": int(gas_price * 1.1),
                "nonce": nonce,
                "chainId": 42161
            }
            
            signed_tx = self.w3.eth.account.sign_transaction(tx, self.account.key)
            tx_hash = self.w3.eth.send_raw_transaction(signed_tx.raw_transaction)
            
            log.warning("[SENDING] TRANSACTION SENT! Hash: %s", tx_hash.hex())
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
            
            if receipt.status == 1:
                log.warning("[CONFIRMED] SUCCESS! Liquidation confirmed in block %d", receipt.blockNumber)
                return True
            else:
                log.error("[ERROR] FAILED: Transaction reverted on-chain.")
                return False
                
        except Exception as e:
            log.error("Execution error: %s", e)
            return False

    async def run_test(self):
        """DUMMY position for testing simulation logic."""
        from gmx_positions import Position
        dummy = Position(
            key="test", account="0x0000000000000000000000000000000000000000",
            market="0x47c031236e19d024b42f8ae6780e44a573170703", # BTC Market
            collateral_token="0x2f2a2543b76a4166549f7aab2e75bef0aefc5b0f", # WBTC
            size_in_usd=1000, size_in_tokens=0.01, collateral_amount=0.005,
            is_long=True, feed="BTC/USD"
        )
        await self.execute_liquidation(dummy, dry_run=True)

if __name__ == "__main__":
    ex = Executor()
    try:
        asyncio.run(ex.run_test())
    except KeyboardInterrupt:
        pass
    finally:
        asyncio.run(ex.stop())
