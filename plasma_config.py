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

# Uniswap V3 SwapRouter02 (Deterministic address on many chains)
# If this doesn't work, we'll need to verify the specific DEX router on Plasma.
UNISWAP_ROUTER = "0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45"

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
    "USDe": "0x5d3a1Ff2b6BAb83b63cd9AD0787074081a52ef34",
    "WXPL": "0x6100E367285b01F48D07953803A2d8dCA5D19873",
}
