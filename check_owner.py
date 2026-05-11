from web3 import Web3
import plasma_config as c
import os

w3 = Web3(Web3.HTTPProvider(c.RPC_URL))
addr = "0xB04Df4C1a64671fb21b033e634FB2f8d710dea25"
abi = [{"inputs":[],"name":"owner","outputs":[{"internalType":"address","name":"","type":"address"}],"stateMutability":"view","type":"function"}]

with open("owner_check.txt", "w") as f:
    try:
        contract = w3.eth.contract(address=addr, abi=abi)
        owner = contract.functions.owner().call()
        f.write(f"OWNER: {owner}\n")
        f.write(f"CONNECTED: {w3.is_connected()}\n")
    except Exception as e:
        f.write(f"ERROR: {e}\n")
