import web3
from web3 import Web3

w3 = Web3()
print("web3 version:", web3.__version__)

try:
    # Web3 v6
    w3.codec.decode(["uint256"], b'\x00'*32)
    print("w3.codec.decode works!")
except AttributeError:
    try:
        # Web3 v5
        w3.codec.decode_abi(["uint256"], b'\x00'*32)
        print("w3.codec.decode_abi works!")
    except Exception as e:
        print("both failed", e)
