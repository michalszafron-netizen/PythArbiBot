import logging
import json
import time
from web3 import Web3
from eth_abi import decode
import plasma_config as config

logger = logging.getLogger("AaveBot.Plasma.Positions")

# Standardowe ABI dla Multicall3 i Aave V3 Pool
MULTICALL3_ABI = [
    {
        "inputs": [
            {
                "components": [
                    {"internalType": "address", "name": "target", "type": "address"},
                    {"internalType": "bytes", "name": "callData", "type": "bytes"}
                ],
                "internalType": "struct Multicall3.Call[]",
                "name": "calls",
                "type": "tuple[]"
            }
        ],
        "name": "aggregate",
        "outputs": [
            {"internalType": "uint256", "name": "blockNumber", "type": "uint256"},
            {"internalType": "bytes[]", "name": "returnData", "type": "bytes[]"}
        ],
        "stateMutability": "payable",
        "type": "function"
    }
]

POOL_ABI = [
    {
        "inputs": [{"internalType": "address", "name": "user", "type": "address"}],
        "name": "getUserAccountData",
        "outputs": [
            {"internalType": "uint256", "name": "totalCollateralBase", "type": "uint256"},
            {"internalType": "uint256", "name": "totalDebtBase", "type": "uint256"},
            {"internalType": "uint256", "name": "availableBorrowsBase", "type": "uint256"},
            {"internalType": "uint256", "name": "currentLiquidationThreshold", "type": "uint256"},
            {"internalType": "uint256", "name": "ltv", "type": "uint256"},
            {"internalType": "uint256", "name": "healthFactor", "type": "uint256"}
        ],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "getReservesList",
        "outputs": [{"internalType": "address[]", "name": "", "type": "address[]"}],
        "stateMutability": "view",
        "type": "function"
    }
]

# Prawidłowe sygnatury eventów Aave V3
BORROW_EVENT_TOPIC = "0xb1ed314f06655c328104561dc88478b21f17fa5ad44bc9fe2497e83e05bc77b3"
SUPPLY_EVENT_TOPIC = "0x2b627736bca15cd5381dcf80b0bf11fd197d01a037c52b927a881a10fb73ba61"

def get_borrowers_from_logs(w3, from_block, to_block):
    """
    Skanuje logi sieci w poszukiwaniu eventów Borrow i Supply.
    """
    logger.info(f"Skanowanie logów w blokach {from_block} - {to_block}...")
    
    borrowers = set()
    
    filter_params = {
        "fromBlock": from_block,
        "toBlock": to_block,
        "address": config.POOL,
        "topics": [[BORROW_EVENT_TOPIC, SUPPLY_EVENT_TOPIC]]
    }
    
    try:
        logs = w3.eth.get_logs(filter_params)
        for log in logs:
            # W Aave V3 'onBehalfOf' (użytkownik) jest zawsze w Topic 2 (indexed)
            if len(log['topics']) >= 3:
                user_addr = "0x" + log['topics'][2].hex()[-40:]
                borrowers.add(Web3.to_checksum_address(user_addr))
        
        return list(borrowers)
    except Exception as e:
        logger.error(f"Błąd skanowania logów: {e}")
        return []

def check_health_factors_multicall(w3, borrowers, chunk_size=50):
    """
    Sprawdza Health Factor dla listy adresów używając Multicall3.
    """
    if not borrowers:
        return []

    # Multicall3 na Plasma (Re.al) zazwyczaj jest pod standardowym adresem
    multicall_addr = "0xca11bde05977b3631167028862be2a173976ca11"
    multicall = w3.eth.contract(address=Web3.to_checksum_address(multicall_addr), abi=MULTICALL3_ABI)
    pool = w3.eth.contract(address=Web3.to_checksum_address(config.POOL), abi=POOL_ABI)
    
    results = []
    for i in range(0, len(borrowers), chunk_size):
        chunk = borrowers[i:i + chunk_size]
        calls = []
        for addr in chunk:
            call_data = pool.encode_abi("getUserAccountData", [addr])
            calls.append((config.POOL, call_data))
            
        try:
            _, return_data = multicall.functions.aggregate(calls).call()
            
            for idx, data in enumerate(return_data):
                decoded = decode(['uint256', 'uint256', 'uint256', 'uint256', 'uint256', 'uint256'], data)
                hf = decoded[5] / 1e18
                debt = decoded[1] / 1e8 # Aave V3 Base currency zazwyczaj 8 dec
                
                # Zapisujemy wszystkich, którzy mają jakikolwiek depozyt lub dług
                results.append({
                    "user": chunk[idx],
                    "healthFactor": hf,
                    "totalDebt": debt,
                    "totalCollateral": decoded[0] / 1e8
                })
        except Exception as e:
            logger.error(f"Błąd Multicall dla paczki {i}: {e}")
            
    return results

def get_user_assets(w3, user_address):
    """
    Znajduje tokeny o największej wartości kolaterału i długu dla danego użytkownika.
    """
    pool = w3.eth.contract(address=Web3.to_checksum_address(config.POOL), abi=POOL_ABI)
    reserves = pool.functions.getReservesList().call()
    
    # ABI dla pobierania danych o rezerwie (potrzebujemy adresów aToken i debtToken)
    reserve_abi = [{"inputs":[{"internalType":"address","name":"asset","type":"address"}],"name":"getReserveData","outputs":[{"components":[{"components":[{"internalType":"uint256","name":"data","type":"uint256"}],"internalType":"struct ReserveConfigurationMap","name":"configuration","type":"tuple"},{"internalType":"uint128","name":"liquidityIndex","type":"uint128"},{"internalType":"uint128","name":"currentLiquidityRate","type":"uint128"},{"internalType":"uint128","name":"variableBorrowIndex","type":"uint128"},{"internalType":"uint128","name":"currentVariableBorrowRate","type":"uint128"},{"internalType":"uint128","name":"currentStableBorrowRate","type":"uint128"},{"internalType":"uint40","name":"lastUpdateTimestamp","type":"uint40"},{"internalType":"uint16","name":"id","type":"uint16"},{"internalType":"address","name":"aTokenAddress","type":"address"},{"internalType":"address","name":"stableDebtTokenAddress","type":"address"},{"internalType":"address","name":"variableDebtTokenAddress","type":"address"},{"internalType":"address","name":"interestRateStrategyAddress","type":"address"},{"internalType":"uint128","name":"accruedToTreasury","type":"uint128"},{"internalType":"uint128","name":"unbacked","type":"uint128"},{"internalType":"uint128","name":"isolationModeTotalDebt","type":"uint128"}],"internalType":"struct DataTypes.ReserveData","name":"","type":"tuple"}],"stateMutability":"view","type":"function"}]
    
    # ABI dla ERC20 balance
    erc20_abi = [{"inputs":[{"internalType":"address","name":"account","type":"address"}],"name":"balanceOf","outputs":[{"internalType":"uint256","name":"","type":"uint256"}],"stateMutability":"view","type":"function"}]
    
    pool_contract = w3.eth.contract(address=Web3.to_checksum_address(config.POOL), abi=reserve_abi)
    
    max_collateral_val = 0
    best_collateral = None
    max_debt_val = 0
    best_debt = None
    
    for asset in reserves:
        data = pool_contract.functions.getReserveData(asset).call()
        a_token = data[8]
        v_debt_token = data[10]
        
        # Sprawdzamy balanse (uproszczone bez Multicalla dla pojedynczego usera, żeby było pewne)
        a_bal = w3.eth.contract(address=a_token, abi=erc20_abi).functions.balanceOf(user_address).call()
        d_bal = w3.eth.contract(address=v_debt_token, abi=erc20_abi).functions.balanceOf(user_address).call()
        
        if a_bal > max_collateral_val:
            max_collateral_val = a_bal
            best_collateral = asset
            
        if d_bal > max_debt_val:
            max_debt_val = d_bal
            best_debt = asset
            
    return best_collateral, best_debt
