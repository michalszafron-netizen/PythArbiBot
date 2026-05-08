"""
AAVE V3 Liquidation Bot — Configuration.

All AAVE V3 contract addresses, token mappings, and Chainlink oracle configs
for Arbitrum One (chainId 42161).

This file is independent from the GMX config.py.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# === Network ===
CHAIN_ID = 42161  # Arbitrum One
ARBITRUM_RPC_URL = os.getenv("ARBITRUM_RPC_URL", "https://arb1.arbitrum.io/rpc")

# === AAVE V3 Core Contracts (Arbitrum) ===
AAVE_V3_POOL = "0x794a61358D6845594F94dc1DB02A252b5b4814aD"
AAVE_V3_POOL_PROVIDER = "0xa97684ead0e402dC232d5A977953DF7ECBaB3CDb"
AAVE_V3_ORACLE = "0xb56c2F0B653B2e0b10C9b928C8580Ac5Df02C7C7"
AAVE_V3_DATA_PROVIDER = "0x69FA688f1Dc47d4B5d8029D5a35FB7a548310654"

# === Your Flash Liquidator Contract (fill after deploying via Remix) ===
FLASH_LIQUIDATOR_ADDRESS = os.getenv("FLASH_LIQUIDATOR_ADDRESS", "")

# === Uniswap V3 (Arbitrum) ===
UNISWAP_V3_ROUTER = "0xE592427A0AEce92De3Edee1F18E0157C05861564"

# === Pyth (reused from original bot — early warning system) ===
PYTH_HERMES_HTTP = os.getenv("PYTH_HERMES_HTTP", "https://hermes.pyth.network")
PYTH_HERMES_WS = os.getenv("PYTH_HERMES_WS", "wss://hermes.pyth.network/ws")
PYTH_CONTRACT = "0xff1a0f4744e8582DF1aE09D5611b887B6a12925C"

# Pyth price feed IDs (same as GMX bot — used for early warning)
PYTH_FEEDS = {
    "ETH/USD": "0xff61491a931112ddf1bd8147cd1b641375f79f5825126d665480874634fd0ace",
    "BTC/USD": "0xe62df6c8b4a85fe1a67db44dc12de5db330f7ac66b72dc658afedf0f4a415b43",
    "ARB/USD": "0x3fa4252848f9f0a1480be62745a4629d9eb1322aebab8a791e344b3b9c1adcf5",
    "LINK/USD": "0x8ac0c70fff57e9aefdf5edf44b51d62c2d433653cbb2cf5cc06bb115af04d221",
    "USDC/USD": "0xeaa020c61cc479712813461ce153894a96a6c00b21ed0cfc2798d1f9a9e9c94a",
}

# === AAVE Supported Tokens (Arbitrum) ===
# token address → (symbol, decimals, pyth_feed_name)
AAVE_TOKENS = {
    "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1": ("WETH", 18, "ETH/USD"),
    "0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f": ("WBTC", 8, "BTC/USD"),
    "0xaf88d065e77c8cC2239327C5EDb3A432268e5831": ("USDC", 6, "USDC/USD"),
    "0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9": ("USDT", 6, None),
    "0x912CE59144191C1204E64559FE8253a0e49E6548": ("ARB", 18, "ARB/USD"),
    "0xf97f4df75117a78c1A5a0DBb814Af92458539FB4": ("LINK", 18, "LINK/USD"),
    "0x5979D7b546E38E9Ab8ED1aF5903190b3D3D11e45": ("wstETH", 18, "ETH/USD"),
    "0x35751007a407ca6FEFfE80b3cB397736D2cf4dbe": ("weETH", 18, "ETH/USD"),
}

# Stablecoins (price assumed ~1.0 for quick HF estimation)
STABLECOINS = {
    "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",  # USDC
    "0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9",  # USDT
}

# Uniswap V3 fee tiers (used when swapping collateral → debt token)
# 500 = 0.05%, 3000 = 0.3%, 10000 = 1%
SWAP_FEE_TIERS = {
    ("WETH", "USDC"): 500,
    ("WETH", "USDT"): 500,
    ("WBTC", "USDC"): 3000,
    ("WBTC", "WETH"): 500,
    ("ARB", "USDC"): 3000,
    ("ARB", "WETH"): 3000,
    ("LINK", "WETH"): 3000,
    ("wstETH", "WETH"): 100,   # 0.01% — correlated
    ("weETH", "WETH"): 100,
}

# Default fee if pair not found above
DEFAULT_SWAP_FEE = 3000

# === Subgraph ===
# Official hosted endpoint is deprecated and unstable. Use Messari or The Graph decentralized network.
AAVE_SUBGRAPH_URL = os.getenv("AAVE_SUBGRAPH_URL", "https://api.thegraph.com/subgraphs/name/messari/aave-v3-arbitrum")
# Backup: AAVE's own API
AAVE_API_URL = "https://aave-api-v2.aave.com"

# === Thresholds ===
# Health Factor thresholds
HF_LIQUIDATABLE = 1.0        # HF < 1.0 → can be liquidated NOW
HF_MONITOR = 1.05            # HF < 1.05 → watch closely (HOT)
HF_EARLY_WARNING = 1.10      # HF < 1.10 → add to candidate list

# Minimum position size to consider (in USD) — skip dust
MIN_POSITION_USD = 500

# Minimum expected profit in USD (after gas + flash fee)
MIN_PROFIT_USD = 1.0

# === Gas ===
GAS_LIMIT_LIQUIDATION = 800_000   # typical for flash + liquidate + swap
MAX_GAS_PRICE_GWEI = 1.0         # Arbitrum is cheap

# === Logging ===
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# === Wallet ===
PRIVATE_KEY = os.getenv("PRIVATE_KEY", "")
