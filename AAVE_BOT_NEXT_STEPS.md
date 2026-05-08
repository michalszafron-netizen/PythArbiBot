# AAVE Flash Liquidation Bot - Project Handoff

## Summary of Work So Far
We are building a highly optimized, high-performance **Aave V3 Flash Liquidation Bot** on the Arbitrum network. 

### What is working:
1. **Smart Contracts:** The `AaveFlashLiquidator.sol` contract is complete. It executes atomic flash loans, calls the AAVE pool liquidation function, swaps the collateral via Uniswap V3, repays the flash loan, and sends profit to the owner.
2. **Orchestrator (`aave_main.py`):** The main loop is functional. It successfully integrates with Pyth Hermes for off-chain early warning price updates and falls back to on-chain Chainlink oracles for confirmation.
3. **Logging System:** We implemented a robust logging system that writes timestamped logs to the `data/` folder and tracks runs in CSV files.
4. **Overnight Dry-Run:** We successfully ran the bot overnight (419 scans). While no liquidations were caught, it revealed our exact scaling bottlenecks.

### Identified Bottlenecks to Fix Next:
1. **The RPC "Traffic Jam":** In `aave_positions.py`, the bot currently checks the Health Factor of every borrower individually. Checking just 328 borrowers takes ~60-80 seconds. We need to implement an on-chain **Multicall** to batch these requests and drop scan time under 2 seconds.
2. **Incomplete Market Coverage:** The current The Graph (Subgraph) query only returns 328 borrowers because it lacks pagination. We are missing 90%+ of the Aave market. We need to add pagination to the GraphQL query to map the *entire* market.

---

## 📋 Copy and Paste the Prompt Below into a New Chat Window:

```text
Hi! We are building an AAVE V3 Flash Liquidation Bot on Arbitrum. I need you to read the `AAVE_BOT_NEXT_STEPS.md` file in the root of my project directory to get full context on where we left off. 

Our immediate goals for this session are:
1. **Implement Multicall:** Refactor the `fetch_health_factors` logic in `aave_positions.py` to use a Multicall contract. We need to batch RPC requests so we don't hit rate limits and can scan the market in under 2 seconds.
2. **Subgraph Pagination:** Update the GraphQL query in `aave_positions.py` to use `skip` and `first` (or ID-based pagination) so we can pull thousands of borrowers from the Aave V3 Subgraph, not just the first 300.

Please read the file, review `aave_positions.py`, and let's start by implementing the Multicall!
```
