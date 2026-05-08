"""
AAVE V3 Liquidation Executor — Flash Loan Integration.

This module sends liquidation transactions to the deployed
AaveFlashLiquidator smart contract on Arbitrum.

Flow:
  1. Python bot identifies a liquidatable borrower (HF < 1.0)
  2. Determines optimal (collateralAsset, debtAsset) pair
  3. Calculates debtToCover amount (50% or 100% of debt)
  4. Simulates via eth_call before sending
  5. Sends transaction to AaveFlashLiquidator.executeLiquidation()
"""
import json
import logging
import time
from typing import Optional

from eth_account import Account
from web3 import Web3
from web3.exceptions import ContractLogicError

from aave_config import (
    AAVE_TOKENS,
    AAVE_V3_POOL,
    ARBITRUM_RPC_URL,
    DEFAULT_SWAP_FEE,
    FLASH_LIQUIDATOR_ADDRESS,
    GAS_LIMIT_LIQUIDATION,
    LOG_LEVEL,
    MAX_GAS_PRICE_GWEI,
    MIN_PROFIT_USD,
    PRIVATE_KEY,
    STABLECOINS,
    SWAP_FEE_TIERS,
)
from aave_positions import AaveBorrower

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("aave_executor")


# ---------------------------------------------------------------------------
# ABI for the deployed AaveFlashLiquidator contract
# ---------------------------------------------------------------------------

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
    },
    {
        "name": "checkHealthFactor",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "user", "type": "address"}],
        "outputs": [{"name": "healthFactor", "type": "uint256"}],
    },
    {
        "name": "owner",
        "type": "function",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{"name": "", "type": "address"}],
    },
    {
        "name": "withdrawToken",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [{"name": "token", "type": "address"}],
        "outputs": [],
    },
    {
        "name": "withdrawETH",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [],
        "outputs": [],
    },
]

POOL_ABI_MINIMAL = [
    {
        "name": "getUserAccountData",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "user", "type": "address"}],
        "outputs": [
            {"name": "totalCollateralBase", "type": "uint256"},
            {"name": "totalDebtBase", "type": "uint256"},
            {"name": "availableBorrowsBase", "type": "uint256"},
            {"name": "currentLiquidationThreshold", "type": "uint256"},
            {"name": "ltv", "type": "uint256"},
            {"name": "healthFactor", "type": "uint256"},
        ],
    },
]


# ---------------------------------------------------------------------------
# Executor class
# ---------------------------------------------------------------------------

class AaveExecutor:
    """
    Executes liquidations via the deployed AaveFlashLiquidator contract.
    """

    def __init__(self):
        if not PRIVATE_KEY:
            raise ValueError("PRIVATE_KEY not set in .env")
        if not FLASH_LIQUIDATOR_ADDRESS:
            raise ValueError(
                "FLASH_LIQUIDATOR_ADDRESS not set in .env — "
                "deploy AaveFlashLiquidator.sol via Remix first!"
            )

        self.w3 = Web3(Web3.HTTPProvider(ARBITRUM_RPC_URL))
        if not self.w3.is_connected():
            raise ConnectionError(f"Cannot connect to RPC: {ARBITRUM_RPC_URL}")

        self.account = Account.from_key(PRIVATE_KEY)
        self.address = self.account.address
        log.info("Executor wallet: %s", self.address)

        self.liquidator = self.w3.eth.contract(
            address=Web3.to_checksum_address(FLASH_LIQUIDATOR_ADDRESS),
            abi=FLASH_LIQUIDATOR_ABI,
        )
        self.pool = self.w3.eth.contract(
            address=Web3.to_checksum_address(AAVE_V3_POOL),
            abi=POOL_ABI_MINIMAL,
        )

        # Verify ownership
        try:
            owner = self.liquidator.functions.owner().call()
            if owner.lower() != self.address.lower():
                log.warning(
                    "⚠️ Contract owner (%s) != your wallet (%s). "
                    "Transactions will fail!",
                    owner, self.address
                )
            else:
                log.info("✅ Contract ownership verified")
        except Exception as e:
            log.warning("Could not verify contract ownership: %s", e)

        # Stats
        self.stats = {
            "attempted": 0,
            "simulated_ok": 0,
            "sent": 0,
            "confirmed": 0,
            "failed": 0,
            "total_profit_usd": 0.0,
        }

    def get_swap_fee(self, collateral_symbol: str, debt_symbol: str) -> int:
        """Get Uniswap V3 fee tier for a token pair."""
        key = (collateral_symbol, debt_symbol)
        if key in SWAP_FEE_TIERS:
            return SWAP_FEE_TIERS[key]
        # Try reverse
        reverse_key = (debt_symbol, collateral_symbol)
        if reverse_key in SWAP_FEE_TIERS:
            return SWAP_FEE_TIERS[reverse_key]
        return DEFAULT_SWAP_FEE

    def select_liquidation_params(
        self, borrower: AaveBorrower
    ) -> Optional[dict]:
        """
        Select the best (collateral, debt) pair for liquidation.
        Strategy: maximize (collateral_value * liquidation_bonus) - costs
        
        Returns dict with: collateral_addr, debt_addr, debt_to_cover, swap_fee
        """
        if not borrower.collateral_assets or not borrower.debt_assets:
            log.warning("No collateral/debt breakdown for %s", borrower.address[:14])
            return None

        best_params = None
        best_score = 0

        for coll_addr, coll_sym, coll_usd, coll_amount, coll_decimals in borrower.collateral_assets:
            for debt_addr, debt_sym, debt_usd, debt_amount, debt_decimals in borrower.debt_assets:
                # Close factor: 50% if HF > 0.95, else 100%
                close_factor = 1.0 if borrower.health_factor <= 0.95 else 0.5
                max_debt_to_cover = debt_amount * close_factor

                # Estimated bonus (5% for majors, up to 10% for minors)
                bonus_pct = 0.05  # conservative default
                estimated_profit = max_debt_to_cover * bonus_pct

                # Score: potential profit in USD terms
                score = debt_usd * close_factor * bonus_pct

                if score > best_score:
                    best_score = score
                    swap_fee = self.get_swap_fee(coll_sym, debt_sym)
                    
                    # Calculate debtToCover in raw units
                    debt_to_cover_raw = int(max_debt_to_cover * (10 ** debt_decimals))

                    best_params = {
                        "collateral_addr": coll_addr,
                        "collateral_symbol": coll_sym,
                        "debt_addr": debt_addr,
                        "debt_symbol": debt_sym,
                        "debt_to_cover": debt_to_cover_raw,
                        "debt_to_cover_usd": debt_usd * close_factor,
                        "swap_fee": swap_fee,
                        "estimated_profit_usd": best_score,
                    }

        if best_params and best_params["estimated_profit_usd"] < MIN_PROFIT_USD:
            log.info(
                "Skipping %s — estimated profit $%.2f < min $%.2f",
                borrower.address[:14],
                best_params["estimated_profit_usd"],
                MIN_PROFIT_USD,
            )
            return None

        return best_params

    def simulate_liquidation(
        self,
        collateral_addr: str,
        debt_addr: str,
        user_addr: str,
        debt_to_cover: int,
        swap_fee: int,
    ) -> bool:
        """
        Dry-run the liquidation via eth_call to check if it would succeed.
        Returns True if simulation passes.
        """
        try:
            self.liquidator.functions.executeLiquidation(
                Web3.to_checksum_address(collateral_addr),
                Web3.to_checksum_address(debt_addr),
                Web3.to_checksum_address(user_addr),
                debt_to_cover,
                swap_fee,
            ).call({"from": self.address})
            return True
        except ContractLogicError as e:
            log.warning("Simulation reverted: %s", e)
            return False
        except Exception as e:
            log.warning("Simulation error: %s", e)
            return False

    def execute_liquidation(self, borrower: AaveBorrower) -> Optional[str]:
        """
        Execute a flash loan liquidation against a borrower.
        
        Returns the transaction hash if successful, None otherwise.
        """
        self.stats["attempted"] += 1
        
        # Step 1: Verify HF is still < 1.0 (might have changed)
        try:
            result = self.pool.functions.getUserAccountData(
                Web3.to_checksum_address(borrower.address)
            ).call()
            current_hf = result[5] / 1e18
            if current_hf >= 1.0:
                log.info(
                    "⏭️ %s no longer liquidatable (HF=%.4f)",
                    borrower.address[:14], current_hf
                )
                return None
        except Exception as e:
            log.error("Failed to re-check HF: %s", e)
            return None

        # Step 2: Select params
        params = self.select_liquidation_params(borrower)
        if not params:
            return None

        log.info(
            "🎯 Target: %s | %s→%s | debt=$%.0f | est.profit=$%.2f",
            borrower.address[:14],
            params["collateral_symbol"],
            params["debt_symbol"],
            params["debt_to_cover_usd"],
            params["estimated_profit_usd"],
        )

        # Step 3: Simulate
        log.info("Simulating liquidation...")
        if not self.simulate_liquidation(
            params["collateral_addr"],
            params["debt_addr"],
            borrower.address,
            params["debt_to_cover"],
            params["swap_fee"],
        ):
            self.stats["failed"] += 1
            return None
        
        self.stats["simulated_ok"] += 1
        log.info("✅ Simulation passed — sending transaction...")

        # Step 4: Build and send transaction
        try:
            nonce = self.w3.eth.get_transaction_count(self.address)
            gas_price = self.w3.eth.gas_price
            max_gas_wei = Web3.to_wei(MAX_GAS_PRICE_GWEI, "gwei")

            if gas_price > max_gas_wei:
                log.warning(
                    "Gas price %.2f gwei > max %.2f gwei — skipping",
                    gas_price / 1e9, MAX_GAS_PRICE_GWEI,
                )
                return None

            tx = self.liquidator.functions.executeLiquidation(
                Web3.to_checksum_address(params["collateral_addr"]),
                Web3.to_checksum_address(params["debt_addr"]),
                Web3.to_checksum_address(borrower.address),
                params["debt_to_cover"],
                params["swap_fee"],
            ).build_transaction({
                "from": self.address,
                "nonce": nonce,
                "gas": GAS_LIMIT_LIQUIDATION,
                "maxFeePerGas": gas_price * 2,
                "maxPriorityFeePerGas": gas_price,
                "chainId": 42161,
            })

            signed = self.account.sign_transaction(tx)
            tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
            tx_hex = tx_hash.hex()
            
            self.stats["sent"] += 1
            log.info("📤 TX sent: %s", tx_hex)
            log.info("   https://arbiscan.io/tx/%s", tx_hex)

            # Step 5: Wait for confirmation
            log.info("Waiting for confirmation...")
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)

            if receipt["status"] == 1:
                self.stats["confirmed"] += 1
                gas_used = receipt["gasUsed"]
                gas_cost_eth = gas_used * gas_price / 1e18
                log.info(
                    "✅ LIQUIDATION SUCCESS! Gas: %d (%.6f ETH ≈ $%.4f)",
                    gas_used, gas_cost_eth, gas_cost_eth * 2500,  # rough ETH price
                )
                return tx_hex
            else:
                self.stats["failed"] += 1
                log.error("❌ Transaction REVERTED: %s", tx_hex)
                return None

        except Exception as e:
            self.stats["failed"] += 1
            log.error("Transaction failed: %s", e)
            return None

    def print_stats(self) -> None:
        """Print execution statistics."""
        s = self.stats
        print("\n📊 Executor Stats:")
        print(f"  Attempted:     {s['attempted']}")
        print(f"  Simulated OK:  {s['simulated_ok']}")
        print(f"  Sent:          {s['sent']}")
        print(f"  Confirmed:     {s['confirmed']}")
        print(f"  Failed:        {s['failed']}")
