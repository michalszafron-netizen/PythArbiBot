import logging
import json
import time
from decimal import Decimal
from datetime import datetime
from web3 import Web3
from eth_abi import decode
import plasma_config as config

log = logging.getLogger("AaveBot.Plasma.Positions")

# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

STABLECOINS = ["USDT", "USDC", "DAI", "USDe", "sUSDe", "crvUSD", "USTB", "RWA", "USD+", "GHO", "syrupUSDT", "USDT0"]

# Tokeny które śledzą cenę ETH — ich loop pozycje likwidują się przez zmianę RATIO, nie ceny USD
ETH_FAMILY = {"WETH", "weETH", "wstETH", "wrsETH"}

class AaveBorrower:
    def __init__(self, address, health_factor, total_collateral_usd, total_debt_usd, liquidation_threshold):
        self.address = Web3.to_checksum_address(address)
        self.health_factor = float(health_factor)
        self.total_collateral_usd = float(total_collateral_usd)
        self.total_debt_usd = float(total_debt_usd)
        self.liquidation_threshold = float(liquidation_threshold)
        
        # Intelligence Metrics
        self.pos_type = "UNKNOWN"
        self.main_collateral_symbol = ""
        self.main_collateral_usd = 0.0
        self.main_debt_symbol = ""
        self.main_debt_usd = 0.0
        self.liq_price_estimate = 0.0
        self.dist_to_liq_pct = 0.0
        self.is_eth_loop = False   # True gdy obie strony to tokeny ETH-family (weETH/WETH itp.)
        
        self.timestamp = datetime.utcnow().isoformat()

    @property
    def status_label(self) -> str:
        if self.health_factor < 1.0: return "LIKWIDACJA"
        if self.health_factor < 1.05: return "KRYTYCZNY"
        if self.health_factor < 1.15: return "ZAGROŻONY"
        return "OK"

    def calculate_metrics(self, current_prices: dict):
        """
        Calculates position type and estimated liquidation trigger.
        current_prices: { symbol: price_usd }
        """
        if not self.main_collateral_symbol or not self.main_debt_symbol:
            return

        is_coll_stable = self.main_collateral_symbol in STABLECOINS
        is_debt_stable = self.main_debt_symbol in STABLECOINS

        # 1. Classify Position
        if not is_coll_stable and is_debt_stable:
            self.pos_type = "LONG"
        elif is_coll_stable and not is_debt_stable:
            self.pos_type = "SHORT"
        else:
            self.pos_type = "HEDGE/LOOP"

        coll_price = current_prices.get(self.main_collateral_symbol, 0)
        debt_price = current_prices.get(self.main_debt_symbol, 0)
        if coll_price <= 0:
            return

        # 2. ETH-family loop (weETH/WETH, wstETH/WETH itp.)
        # Dla tych pozycji HF zalezy od RATIO coll/debt, nie od ceny USD.
        # Dowod: HF = (coll_amt * coll_usd * LT) / (debt_amt * debt_usd)
        #           = (coll_amt * ratio * P * LT) / (debt_amt * P)  — P (cena ETH) sie redukuje
        # Wiec: liq_ratio = current_ratio / HF
        if (self.main_collateral_symbol in ETH_FAMILY and
                self.main_debt_symbol in ETH_FAMILY and
                self.main_collateral_symbol != self.main_debt_symbol and
                debt_price > 0):
            self.pos_type = "ETH-LOOP"
            self.is_eth_loop = True
            current_ratio = coll_price / debt_price          # np. 1.0944
            liq_ratio = current_ratio / self.health_factor   # np. 1.069
            self.liq_price_estimate = liq_ratio              # przechowujemy ratio, nie cene USD
            self.dist_to_liq_pct = (liq_ratio / current_ratio - 1) * 100
            return

        # 3. Standardowe pozycje
        if self.pos_type in ("LONG", "HEDGE/LOOP"):
            self.liq_price_estimate = (self.total_debt_usd * coll_price) / (self.total_collateral_usd * (self.liquidation_threshold / 100))
            self.dist_to_liq_pct = ((self.liq_price_estimate / coll_price) - 1) * 100

        elif self.pos_type == "SHORT":
            if debt_price > 0:
                self.liq_price_estimate = (self.total_collateral_usd * (self.liquidation_threshold / 100) * debt_price) / self.total_debt_usd
                self.dist_to_liq_pct = ((self.liq_price_estimate / debt_price) - 1) * 100

# ---------------------------------------------------------------------------
# ABIs
# ---------------------------------------------------------------------------

MULTICALL3_ABI = [
    {"inputs":[{"components":[{"internalType":"address","name":"target","type":"address"},{"internalType":"bytes","name":"callData","type":"bytes"}],"internalType":"struct Multicall3.Call[]","name":"calls","type":"tuple[]"}],"name":"aggregate","outputs":[{"internalType":"uint256","name":"blockNumber","type":"uint256"},{"internalType":"bytes[]","name":"returnData","type":"bytes[]"}],"stateMutability":"payable","type":"function"}
]

POOL_ABI = [
    {"inputs":[{"internalType":"address","name":"user","type":"address"}],"name":"getUserAccountData","outputs":[{"internalType":"uint256","name":"totalCollateralBase","type":"uint256"},{"internalType":"uint256","name":"totalDebtBase","type":"uint256"},{"internalType":"uint256","name":"availableBorrowsBase","type":"uint256"},{"internalType":"uint256","name":"currentLiquidationThreshold","type":"uint256"},{"internalType":"uint256","name":"ltv","type":"uint256"},{"internalType":"uint256","name":"healthFactor","type":"uint256"}],"stateMutability":"view","type":"function"},
    {"inputs":[],"name":"getReservesList","outputs":[{"internalType":"address[]","name":"","type":"address[]"}],"stateMutability":"view","type":"function"}
]

AAVE_DATA_PROVIDER_ABI = [
    {"inputs":[{"internalType":"address","name":"asset","type":"address"},{"internalType":"address","name":"user","type":"address"}],"name":"getUserReserveData","outputs":[{"internalType":"uint256","name":"currentATokenBalance","type":"uint256"},{"internalType":"uint256","name":"currentStableDebt","type":"uint256"},{"internalType":"uint256","name":"currentVariableDebt","type":"uint256"},{"internalType":"uint256","name":"principalStableDebt","type":"uint256"},{"internalType":"uint256","name":"scaledVariableDebt","type":"uint256"},{"internalType":"uint256","name":"stableBorrowRate","type":"uint256"},{"internalType":"uint256","name":"liquidityRate","type":"uint256"},{"internalType":"uint40","name":"stableRateLastUpdated","type":"uint40"},{"internalType":"bool","name":"usageAsCollateralEnabled","type":"bool"}],"stateMutability":"view","type":"function"},
    {"inputs":[{"internalType":"address","name":"asset","type":"address"}],"name":"getReserveConfigurationData","outputs":[{"internalType":"uint256","name":"decimals","type":"uint256"},{"internalType":"uint256","name":"ltv","type":"uint256"},{"internalType":"uint256","name":"liquidationThreshold","type":"uint256"},{"internalType":"uint256","name":"liquidationBonus","type":"uint256"},{"internalType":"uint256","name":"reserveFactor","type":"uint256"},{"internalType":"bool","name":"usageAsCollateralEnabled","type":"bool"},{"internalType":"bool","name":"borrowingEnabled","type":"bool"},{"internalType":"bool","name":"stableBorrowRateEnabled","type":"bool"},{"internalType":"bool","name":"isActive","type":"bool"},{"internalType":"bool","name":"isFrozen","type":"bool"}],"stateMutability":"view","type":"function"}
]

ORACLE_ABI = [{"inputs":[{"internalType":"address","name":"asset","type":"address"}],"name":"getAssetPrice","outputs":[{"internalType":"uint256","name":"","type":"uint256"}],"stateMutability":"view","type":"function"}]

# ---------------------------------------------------------------------------
# Functions
# ---------------------------------------------------------------------------

def get_borrowers_from_logs(w3, from_block, to_block):
    """Skanuje logi sieci w poszukiwaniu dłużników."""
    BORROW_TOPIC = "0xb1ed314f06655c328104561dc88478b21f17fa5ad44bc9fe2497e83e05bc77b3"
    SUPPLY_TOPIC = "0x2b627736bca15cd5381dcf80b0bf11fd197d01a037c52b927a881a10fb73ba61"
    
    borrowers = set()
    try:
        logs = w3.eth.get_logs({
            "fromBlock": from_block,
            "toBlock": to_block,
            "address": config.POOL,
            "topics": [[BORROW_TOPIC, SUPPLY_TOPIC]]
        })
        for log in logs:
            if len(log['topics']) >= 3:
                user = "0x" + log['topics'][2].hex()[-40:]
                borrowers.add(Web3.to_checksum_address(user))
    except Exception as e:
        log.error("Log scan error: %s", e)
    return list(borrowers)

async def check_health_factors_multicall(w3, borrowers, chunk_size=50):
    """Sprawdza Health Factor dla listy adresów."""
    if not borrowers: return []
    
    multicall_addr = "0xca11bde05977b3631167028862be2a173976ca11"
    multicall = w3.eth.contract(address=Web3.to_checksum_address(multicall_addr), abi=MULTICALL3_ABI)
    pool = w3.eth.contract(address=Web3.to_checksum_address(config.POOL), abi=POOL_ABI)
    
    results = []
    for i in range(0, len(borrowers), chunk_size):
        chunk = borrowers[i:i + chunk_size]
        calls = []
        for addr in chunk:
            calls.append({"target": config.POOL, "callData": pool.encode_abi("getUserAccountData", [addr])})
            
        try:
            _, return_data = multicall.functions.aggregate(calls).call()
            for idx, data in enumerate(return_data):
                decoded = decode(['uint256', 'uint256', 'uint256', 'uint256', 'uint256', 'uint256'], data)
                hf = decoded[5] / 1e18
                if hf > 0 and hf < 10: # Only care about active borrowers with reasonable risk
                    results.append(AaveBorrower(
                        address=chunk[idx],
                        health_factor=hf,
                        total_collateral_usd=decoded[0] / 1e8,
                        total_debt_usd=decoded[1] / 1e8,
                        liquidation_threshold=decoded[3] / 100
                    ))
        except Exception as e:
            log.error("Multicall chunk error: %s", e)
            
    return results

async def fetch_detailed_data(w3, borrowers: list[AaveBorrower], current_prices: dict):
    """
    Enriches borrower objects with asset breakdown and intelligence metrics.
    Uses Multicall to get balances for ALL reserves.
    """
    if not borrowers: return
    
    multicall_addr = "0xca11bde05977b3631167028862be2a173976ca11"
    multicall = w3.eth.contract(address=Web3.to_checksum_address(multicall_addr), abi=MULTICALL3_ABI)
    pool = w3.eth.contract(address=Web3.to_checksum_address(config.POOL), abi=POOL_ABI)
    data_provider = w3.eth.contract(address=Web3.to_checksum_address(config.AAVE_DATA_PROVIDER), abi=AAVE_DATA_PROVIDER_ABI)
    oracle = w3.eth.contract(address=Web3.to_checksum_address(config.AAVE_ORACLE), abi=ORACLE_ABI)
    
    # 1. Get all reserves
    reserves = pool.functions.getReservesList().call()
    
    # 2. Get metadata for all reserves (decimals + symbols)
    # We'll use a small cache here or just assume symbols from config if possible
    # For now, let's just use the config TOKEN_MAP as a reverse lookup
    reverse_map = {addr.lower(): sym for sym, addr in config.TOKEN_MAP.items()}
    
    for b in borrowers:
        calls = []
        for asset in reserves:
            calls.append({"target": config.AAVE_DATA_PROVIDER, "callData": data_provider.encode_abi("getUserReserveData", [asset, b.address])})
            calls.append({"target": config.AAVE_ORACLE, "callData": oracle.encode_abi("getAssetPrice", [asset])})
            
        try:
            _, return_data = multicall.functions.aggregate(calls).call()
            
            max_coll_usd = 0
            max_debt_usd = 0
            
            for i in range(0, len(return_data), 2):
                asset_addr = reserves[i // 2]
                res_data = decode(['uint256', 'uint256', 'uint256', 'uint256', 'uint256', 'uint256', 'uint256', 'uint40', 'bool'], return_data[i])
                price_base = decode(['uint256'], return_data[i+1])[0]
                
                # Aave V3 price is in Base currency (usually 8 decimals USD)
                price_usd = price_base / 1e8
                sym = reverse_map.get(asset_addr.lower())
                if not sym:
                    sym = asset_addr[:6]
                    log.debug("Found unknown token on Plasma: %s", asset_addr)
                
                # decimals - we assume 18 for most, but should ideally fetch. 
                # For simplicity in this intelligence layer, we use the value relative to total
                # Current AToken Balance / Current Variable Debt
                # Note: This is an approximation for UI, real liquidation uses contract decimals
                
                # USD Value = (Balance / 10^Decimals) * Price
                # But we can also infer which is "main" by comparing raw contribution to totalCollateralBase
                
                coll_bal = res_data[0] # aToken balance
                debt_bal = res_data[1] + res_data[2] # stable + variable debt
                
                # Rough USD calculation (assuming 18 decimals for now, adjusted if needed)
                # Actually, Aave V3 Base currency values are more reliable
                coll_usd = (coll_bal / 1e18) * price_usd if coll_bal > 0 else 0
                debt_usd = (debt_bal / 1e18) * price_usd if debt_bal > 0 else 0
                
                if coll_usd > max_coll_usd:
                    max_coll_usd = coll_usd
                    b.main_collateral_symbol = sym
                    b.main_collateral_usd = coll_usd
                
                if debt_usd > max_debt_usd:
                    max_debt_usd = debt_usd
                    b.main_debt_symbol = sym
                    b.main_debt_usd = debt_usd
            
            # Finalize metrics
            b.calculate_metrics(current_prices)
            
        except Exception as e:
            log.error("Detail fetch failed for %s: %s", b.address, e)

def get_user_assets(w3, user_address):
    """Uproszczona wersja dla egzekutora (zwraca adresy)."""
    pool = w3.eth.contract(address=Web3.to_checksum_address(config.POOL), abi=POOL_ABI)
    data_provider = w3.eth.contract(address=Web3.to_checksum_address(config.AAVE_DATA_PROVIDER), abi=AAVE_DATA_PROVIDER_ABI)
    reserves = pool.functions.getReservesList().call()
    
    max_coll = 0
    best_coll = None
    max_debt = 0
    best_debt = None
    
    for asset in reserves:
        data = data_provider.functions.getUserReserveData(asset, user_address).call()
        if data[0] > max_coll:
            max_coll = data[0]
            best_coll = asset
        if (data[1] + data[2]) > max_debt:
            max_debt = data[1] + data[2]
            best_debt = asset
            
    return best_coll, best_debt
