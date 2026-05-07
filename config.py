"""Static configuration: contract addresses, Pyth feed IDs, target list."""
import os
from dotenv import load_dotenv

load_dotenv()

# === Network ===
CHAIN_ID = 42161  # Arbitrum One
ARBITRUM_RPC_URL = os.getenv("ARBITRUM_RPC_URL", "https://arb1.arbitrum.io/rpc")
ARBITRUM_WS_URL = os.getenv("ARBITRUM_WS_URL", "")

# === Pyth ===
PYTH_HERMES_HTTP = os.getenv("PYTH_HERMES_HTTP", "https://hermes.pyth.network")
PYTH_HERMES_WS = os.getenv("PYTH_HERMES_WS", "wss://hermes.pyth.network/ws")
PYTH_CONTRACT = "0xff1a0f4744e8582DF1aE09D5611b887B6a12925C"

# Pyth price feed IDs
PYTH_FEEDS = {
    "ETH/USD": "0xff61491a931112ddf1bd8147cd1b641375f79f5825126d665480874634fd0ace",
    "BTC/USD": "0xe62df6c8b4a85fe1a67db44dc12de5db330f7ac66b72dc658afedf0f4a415b43",
    "ARB/USD": "0x3fa4252848f9f0a1480be62745a4629d9eb1322aebab8a791e344b3b9c1adcf5",
    "SOL/USD": "0xef0d8b6fda2ceba41da15d4095d1da392a0d2f8ed0c6c7bc0f4cfac8c280b56d",
    "LINK/USD": "0x8ac0c70fff57e9aefdf5edf44b51d62c2d433653cbb2cf5cc06bb115af04d221",
}

# === GMX V2 (Arbitrum) ===
GMX_V2 = {
    "DataStore": "0xFD70de6b91282D8017aA4E741e9Ae325CAb992d8",
    "Reader": "0xf60becbba223EEA9495Da3f606753867eC10d139",
    "OrderHandler": "0xe68CAAACdf6439628DFD2fe624847602991A31eB",
    "LiquidationHandler": "0x7940177770E83d690a78F04B3507c300fA8A73f4",
    "ExchangeRouter": "0x900173A66dbD345006C51fA35fA3aB760FcD843b",
    "EventEmitter": "0xC8ee91A54287DB53897056e12D9819156D3822Fb",
}

GMX_LIQUIDATION_HANDLER = GMX_V2["LiquidationHandler"]
MULTICALL_ADDRESS = "0xcA11bde05977b3631167028862bE2a173976CA11"

# === Logging ===
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# === Wallet ===
PRIVATE_KEY = os.getenv("PRIVATE_KEY", "")
