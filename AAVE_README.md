# AAVE V3 Flash Liquidation Bot - Deployment Guide

I have successfully created all the new files for the AAVE V3 permissionless flash-loan liquidation bot. All files are standalone and will **not interfere with your existing GMX project files**.

## 1. Deploy the Smart Contract

You need to deploy the `AaveFlashLiquidator` contract to Arbitrum One.

1. Open [Remix IDE](https://remix.ethereum.org/).
2. Create a new file in the `contracts` folder on Remix called `AaveFlashLiquidator.sol`.
3. Copy the entire contents of `c:\Users\markowyy\Documents\ArbitrageBot\ClaudeMOnster\PythOracle\contracts\AaveFlashLiquidator.sol` and paste it into the new file on Remix.
4. Go to the **Solidity Compiler** tab on the left.
    - Set the **Compiler** version to `0.8.20`.
    - Under **Advanced Configurations**, enable **Optimization** (set it to `200`).
    - Click **Compile AaveFlashLiquidator.sol**.
5. Go to the **Deploy & Run Transactions** tab.
    - Change the **Environment** to `Injected Provider - MetaMask`.
    - Make sure your MetaMask is connected to **Arbitrum One** network.
    - In the **Deploy** section, expand the deployment parameters. You need to provide the following addresses:
        - `_addressProvider`: `0xa97684ead0e402dC232d5A977953DF7ECBaB3CDb` (AAVE V3 PoolAddressesProvider on Arbitrum)
        - `_swapRouter`: `0xE592427A0AEce92De3Edee1F18E0157C05861564` (Uniswap V3 SwapRouter)
    - Click **Deploy** and confirm the transaction in MetaMask.

## 2. Update Environment Variables

Once the contract is deployed, you'll get a contract address. Add this to your `.env` file.

1. Open `c:\Users\markowyy\Documents\ArbitrageBot\ClaudeMOnster\PythOracle\.env`
2. Add the following line at the end:

```env
FLASH_LIQUIDATOR_ADDRESS=0xYourDeployedContractAddressHere
```

## 3. Test the Bot

You can now test the bot in Dry-Run mode to see it find candidates without executing transactions.

1. Open a terminal in your project directory.
2. Run the main orchestrator in Dry-Run mode:
   ```bash
   .venv\Scripts\python.exe aave_main.py --dry-run
   ```

When you are ready to execute real liquidations, you can run it with the `--live` flag:
```bash
.venv\Scripts\python.exe aave_main.py --live
```

**Note:** The bot currently uses flash loans, so you only need enough ETH in your wallet to pay for Arbitrum gas fees (usually a few cents). The capital for the liquidation is borrowed entirely from AAVE.

Let me know once you have deployed the contract and we can do a test run!
