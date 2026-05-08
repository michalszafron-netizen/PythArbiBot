# AAVE Flash Liquidation Bot - Project Handoff

## Summary of Work So Far
We are building a highly optimized **Aave V3 Flash Liquidation Bot** on the Arbitrum network. 

### What is working:
1. **Smart Contracts:** The `AaveFlashLiquidator.sol` contract is complete. It executes atomic flash loans, calls the AAVE pool liquidation function, swaps the collateral via Uniswap V3, repays the flash loan, and sends profit to the owner.
2. **Orchestrator (`aave_main.py`):** The main loop is fully functional. It successfully integrates with Pyth Hermes for off-chain early warning price updates (10-30s ahead of on-chain) and prints colored monitoring statuses (`🟢/🟠/🔴`).
3. **Subgraph Pagination:** We successfully integrated ID-based pagination to map the *entire* Aave V3 market. We are successfully retrieving the full list of ~184,104 borrowers.
4. **Multicall3 Implementation:** We successfully refactored `fetch_health_factors` in `aave_positions.py` to batch `getUserAccountData` on-chain reads via Multicall3 using `asyncio` and semaphores.

### Identified Bottlenecks / Current Status:
1. **RPC Timeout / Dropped Packets:** Even after upgrading to a premium dRPC Growth plan and optimizing our chunk size (50) and concurrency (40), fetching 184k borrowers on-chain is too heavy. It currently takes ~6 minut per loop and drops 93% of the requests (only successfully reading ~11,600 / 184k borrowers per cycle due to timeouts). 
2. **Going LIVE:** The bot is currently in `--dry-run`. It requires setting the `FLASH_LIQUIDATOR_ADDRESS` in `.env` and running with `--live` to actually execute liquidations for the users it does successfully scan.

---

## 📋 Copy and Paste the Prompt Below into a New Chat Window:

```text
Hi! We are building an AAVE V3 Flash Liquidation Bot on Arbitrum. I need you to read the `AAVE_BOT_NEXT_STEPS.md` file in the root of my project directory to get full context on where we left off. 

Our immediate goals for this session are:
1. **Optimize Subgraph Query (Filter by Debt):** We discovered that the Subgraph is returning 184k borrowers, but most of them likely have 0 debt. Checking all 184k is burning our dRPC API limits rapidly, and the RPC is timing out and dropping 93% of the requests. We need to modify our Subgraph GraphQL query in `aave_positions.py` to only fetch borrowers that actually have open debt (e.g., `hasDebt: true` or `totalDebtBase_gt: 0`). This should drastically reduce the list from 184k to a few thousand, saving RPC tokens and eliminating timeouts completely.
2. **Going LIVE:** Once the filtering is fixed, the bot should be able to scan the entire *active* market instantly. We will then focus on running the `--live` mode to catch a real liquidation.

Please read the `AAVE_BOT_NEXT_STEPS.md` file and let's start!
```
