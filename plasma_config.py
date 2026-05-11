import os
from dotenv import load_dotenv

load_dotenv()

# ============================================================
# Network Configuration: Plasma Network (ChainID: 9745)
# ============================================================
RPC_URL = os.getenv("PLASMA_RPC_URL", "https://lb.drpc.live/plasma/AiqgXN0QTUSngZwuEHTctguUPy1HSwYR8aDMtiKh6MJI")
CHAIN_ID = 9745

# ============================================================
# Aave V3 Plasma Contract Addresses
# ============================================================
# Official addresses from Aave Address Book (AaveV3Plasma.sol)
POOL_ADDRESSES_PROVIDER = "0x061D8e131F26512348ee5FA42e2DF1bA9d6505E9"
POOL = "0x925a2A7214Ed92428B5b1B090F80b25700095e12"
AAVE_ORACLE = "0x33E0b3fc976DC9C516926BA48CfC0A9E10a2aAA5"
AAVE_DATA_PROVIDER = "0xf2D6E38B407e31E7E7e4a16E6769728b76c7419F"

# ============================================================
# Execution Configuration
# ============================================================
# NOTE: Deploy the SAME AaveFlashLiquidator.sol contract to Plasma.
# Use POOL_ADDRESSES_PROVIDER and the DEX Router below in the constructor.
EXECUTOR_ADDRESS = os.getenv("PLASMA_EXECUTOR_ADDRESS", "") 

# Natywny DEX na Plasma (fork UniV3 z Router02 interface — bez pola deadline)
# Factory: 0xcb2436774C3e191c85056d248EF4260ce5f27A9D
UNISWAP_ROUTER = "0x807f4e281b7a3b324825c64ca53c69f0b418de40"

# Fee tiery dla znanych par z głęboką liquidity (zweryfikowane on-chain)
SWAP_FEE_TIERS = {
    ("USDe",      "USDT0"):  500,   # 0.05% — głęboka pula $83M/$105M pozycje
    ("sUSDe",     "USDT0"):  100,   # 0.01% — głęboka pula $5M/$19M pozycje
    ("syrupUSDT", "USDT0"):  500,   # 0.05% — głęboka pula $25M/$15M pozycje
    ("WETH",      "USDT0"): 3000,   # 0.30% — głęboka pula, historycznie działa
    ("WXPL",      "USDT0"): 3000,   # 0.30% — historycznie działa
    ("weETH",     "USDT0"): 3000,   # fallback
}

# ============================================================
# Discovery Configuration
# ============================================================
# Since there is no official Subgraph on Plasma yet, we will use 
# event scanning to find borrowers.
# This is our "EDGE" - other bots can't find borrowers easily!
DISCOVERY_MODE = "events" # "subgraph" or "events"
SUBGRAPH_URL = "" # Empty for now

# ============================================================
# Liquidation Thresholds
# ============================================================
HF_LIQUIDATABLE = 1.0
HF_MONITOR = 1.05
HF_EARLY_WARNING = 1.1

# ============================================================
# Oracle Configuration (Pyth for Plasma)
# ============================================================
# Pyth is supported on Plasma
PYTH_HERMES_URL = "https://hermes.pyth.network"
PYTH_CONTRACT = "0xff1a2d3a677E2094B0D33948Ec28109650A21111" # Standard address

# ============================================================
# Token Map (Plasma specific)
# ============================================================
TOKEN_MAP = {
    "WETH": "0x9895D81bB462A195b4922ED7De0e3ACD007c32CB",
    "USDT": "0xB8CE59FC3717ada4C02eaDF9682A9e934F625ebb",
    "USDT0": "0xB8CE59FC3717ada4C02eaDF9682A9e934F625ebb",
    "USDe": "0x5d3a1Ff2b6BAb83b63cd9AD0787074081a52ef34",
    "sUSDe": "0x211Cc4DD073734dA055fbF44a2b4667d5E5fE5d2",
    "WXPL": "0x6100E367285b01F48D07953803A2d8dCA5D19873",
    "XAUt0": "0x1B64B9025EEbb9A6239575dF9Ea4b9Ac46D4d193",
    "weETH": "0xA3D68b74bF0528fdD07263c60d6488749044914b",
    "wstETH": "0xe48D935e6C9e735463ccCf29a7F11e32bC09136E",
    "wrsETH": "0xe561FE05C39075312Aa9Bc6af79DdaE981461359",
    "syrupUSDT": "0xC4374775489CB9C56003BF2C9b12495fC64F0771",
    "GHO": "0xb77E872A68C62CfC0dFb02C067Ecc3DA23B4bbf3",
    "PT-USDe-15JAN2026": "0x93B544c330F60A2aa05ceD87aEEffB8D38FD8c9a",
    "PT-sUSDE-15JAN2026": "0x02FCC4989B4C9D435b7ceD3fE1Ba4CF77BBb5Dd8",
    "PT-sUSDE-9APR2026": "0xab509448ad489e2E1341e25CC500f2596464Cc82",
    "PT-USDe-9APR2026": "0x54Dc267be2839303ff1e323584A16e86CeC4Aa44",
    "PT-USDe-18JUN2026": "0x23B17d3944742ACe3d0C71586FcB320d1e4a1Ed2",
    "PT-sUSDE-18JUN2026": "0x30559E3d35e33AB69399a3fe9F383d32bd3c016E",
}
