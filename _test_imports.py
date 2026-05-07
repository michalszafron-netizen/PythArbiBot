import traceback, os
RESULT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_r.txt")
try:
    from gmx_positions import (
        calc_liquidation_price, Position, INDEX_TOKEN_DECIMALS,
        fetch_hermes_prices, fetch_positions_subgraph, fetch_positions_datastore,
        parse_onchain_position, parse_subgraph_position,
        MARKET_TO_FEED, MAX_POSITIONS, MIN_COLLATERAL_FACTOR,
        STABLES, TOKEN_DECIMALS, USD_PRECISION,
    )
    with open(RESULT, "w") as f:
        f.write("gmx_positions: OK\n")
    import main
    with open(RESULT, "a") as f:
        f.write("main: OK\n")
except Exception as e:
    with open(RESULT, "w") as f:
        f.write(f"FAIL\n")
        traceback.print_exc(file=f)
