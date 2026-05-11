import asyncio
from web3 import Web3
import plasma_config as config

async def check_reserves():
    w3 = Web3(Web3.HTTPProvider(config.RPC_URL))
    pool_abi = [{"inputs": [], "name": "getReservesList", "outputs": [{"internalType": "address[]", "name": "", "type": "address[]"}], "stateMutability": "view", "type": "function"}]
    pool = w3.eth.contract(address=Web3.to_checksum_address(config.POOL), abi=pool_abi)
    reserves = pool.functions.getReservesList().call()
    
    erc20_abi = [{"inputs": [], "name": "symbol", "outputs": [{"internalType": "string", "name": "", "type": "string"}], "stateMutability": "view", "type": "function"}]
    
    print("Reserves on Plasma Aave:")
    for r in reserves:
        try:
            token = w3.eth.contract(address=r, abi=erc20_abi)
            sym = token.functions.symbol().call()
            print(f"  {sym}: {r}")
        except:
            print(f"  Unknown: {r}")

if __name__ == "__main__":
    asyncio.run(check_reserves())
