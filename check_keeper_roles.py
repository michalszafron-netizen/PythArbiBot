"""Query RoleStore on-chain to see who holds LIQUIDATION_KEEPER role."""
import os, sys
from web3 import Web3
from dotenv import load_dotenv

# Force output
output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "role_results.txt")
f = open(output_path, "w")

def log(msg):
    print(msg)
    f.write(msg + "\n")
    f.flush()

load_dotenv()

RPC_URL = os.getenv("ARBITRUM_RPC_URL", "https://arb1.arbitrum.io/rpc")
ROLE_STORE = "0x3c3d99FD298f679DBC2CEcd132b4eC4d0F5e6e72"

LIQUIDATION_KEEPER_HASH = "0x556c788ffc0574ec93966d808c170833d96489c9c58f5bcb3dadf711ba28720e"
ORDER_KEEPER_HASH = "0x40a07f8f0fc57fcf18b093d96362a8e661eaac7b7e6edbf66f242111f83a6794"

ABI = [
    {"name": "getRoleMemberCount", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "roleKey", "type": "bytes32"}],
     "outputs": [{"name": "", "type": "uint256"}]},
    {"name": "getRoleMembers", "type": "function", "stateMutability": "view",
     "inputs": [
         {"name": "roleKey", "type": "bytes32"},
         {"name": "start", "type": "uint256"},
         {"name": "end", "type": "uint256"}
     ],
     "outputs": [{"name": "", "type": "address[]"}]},
    {"name": "hasRole", "type": "function", "stateMutability": "view",
     "inputs": [
         {"name": "account", "type": "address"},
         {"name": "roleKey", "type": "bytes32"}
     ],
     "outputs": [{"name": "", "type": "bool"}]},
]

try:
    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    log(f"Connected: {w3.is_connected()}")
    log(f"Block: {w3.eth.block_number}")

    store = w3.eth.contract(address=Web3.to_checksum_address(ROLE_STORE), abi=ABI)

    log("\n" + "=" * 60)
    log("LIQUIDATION_KEEPER role")
    log("=" * 60)
    liq_count = store.functions.getRoleMemberCount(LIQUIDATION_KEEPER_HASH).call()
    log(f"  Members count: {liq_count}")
    if liq_count > 0:
        members = store.functions.getRoleMembers(LIQUIDATION_KEEPER_HASH, 0, liq_count).call()
        for i, m in enumerate(members):
            log(f"  [{i}] {m}")

    log("\n" + "=" * 60)
    log("ORDER_KEEPER role")
    log("=" * 60)
    ord_count = store.functions.getRoleMemberCount(ORDER_KEEPER_HASH).call()
    log(f"  Members count: {ord_count}")
    if ord_count > 0:
        members = store.functions.getRoleMembers(ORDER_KEEPER_HASH, 0, ord_count).call()
        for i, m in enumerate(members):
            log(f"  [{i}] {m}")

    YOUR_WALLET = "0x2800D765f64DeE522e919C75DF99Dfb5ECCA6d6e"
    log("\n" + "=" * 60)
    log(f"YOUR WALLET: {YOUR_WALLET}")
    log("=" * 60)
    has_liq = store.functions.hasRole(Web3.to_checksum_address(YOUR_WALLET), LIQUIDATION_KEEPER_HASH).call()
    has_ord = store.functions.hasRole(Web3.to_checksum_address(YOUR_WALLET), ORDER_KEEPER_HASH).call()
    log(f"  Has LIQUIDATION_KEEPER: {has_liq}")
    log(f"  Has ORDER_KEEPER: {has_ord}")

    LIQ_HANDLER = "0xaf157Eb8e2398A8E1Fc1dA929974652b9ba9BC25"
    log(f"\nLiquidationHandler ({LIQ_HANDLER}):")
    has_liq2 = store.functions.hasRole(Web3.to_checksum_address(LIQ_HANDLER), LIQUIDATION_KEEPER_HASH).call()
    log(f"  Has LIQUIDATION_KEEPER: {has_liq2}")
    
except Exception as e:
    log(f"ERROR: {e}")
    import traceback
    log(traceback.format_exc())

f.close()
log_msg = f"Results written to {output_path}"
print(log_msg)
