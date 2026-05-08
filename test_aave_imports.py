import sys
try:
    import aave_config
    print("aave_config OK")
except Exception as e:
    print(f"aave_config FAILED: {e}")
    sys.exit(1)

try:
    from aave_positions import AaveBorrower, fetch_borrowers_subgraph
    print("aave_positions OK")
except Exception as e:
    print(f"aave_positions FAILED: {e}")
    sys.exit(1)

try:
    from aave_executor import AaveExecutor
    print("aave_executor OK (import only - init needs contract address)")
except Exception as e:
    print(f"aave_executor FAILED: {e}")
    sys.exit(1)

try:
    from aave_main import AaveOrchestrator
    print("aave_main OK")
except Exception as e:
    print(f"aave_main FAILED: {e}")
    sys.exit(1)

print("\nAll AAVE modules import successfully!")
