I have analyzed the GMX Synthetics LiquidationHandler deployment on Arbitrum and its core logic. Below is a comprehensive breakdown of the contract's interaction requirements and the steps needed for programmatic liquidation.

1. Contract Overview
The LiquidationHandler (Address: 0xaf157Eb8e2398A8E1Fc1dA929974652b9ba9BC25) is the primary entry point for executing liquidations in GMX V2. It relies on a role-based access control system and requires real-time oracle price updates to be bundled with the liquidation call.

2. Primary Function: executeLiquidation
To trigger a liquidation, the executeLiquidation function must be called with the following signature:

```solidity
function executeLiquidation(
    address account,
    address market,
    address collateralToken,
    bool isLong,
    OracleUtils.SetPricesParams calldata oracleParams
) external;
```

Parameter Details:
- **account**: The wallet address of the user whose position is being liquidated.
- **market**: The GMX V2 market address where the position is held.
- **collateralToken**: The token used as collateral for the position (e.g., WETH, USDC).
- **isLong**: true if the position is a long, false if it is a short.
- **oracleParams**: A struct containing signed price updates required to validate the liquidation at the current market price.

3. Oracle Interaction: SetPricesParams
The oracleParams (of type OracleUtils.SetPricesParams) is critical. In GMX V2, price data is "pushed" by the keeper at the time of execution. The struct typically looks like this:

```solidity
struct SetPricesParams {
    address[] tokens;     // Array of tokens being updated
    address[] providers;  // Array of price provider addresses
    bytes[] data;         // Signed price data (e.g., Pyth price feeds)
}
```

Liquidations will fail if the provided oracle prices do not show that the position's maintenance margin has been breached.

4. Authorization and Roles
The contract is protected by the onlyLiquidationKeeper modifier. This means only addresses explicitly granted the LIQUIDATION_KEEPER role can call this function.

- **Role Hash**: `0x556c788ffc0574ec93966d808c170833d96489c9c58f5bcb3dadf711ba28720e`
- **Verification**: You can check if an address has this role by calling `hasRole(address, roleHash)` on the RoleStore contract.

5. Implementation Roadmap for Automation
To build a liquidation bot or automation script, follow these steps:

1. **Monitor Positions**: Scan the GMX DataStore or use a subgraph to track position health.
2. **Fetch Oracle Data**: When a position is identified as liquidatable, fetch the latest signed price feeds (likely from Pyth or GMX's internal oracles).
3. **Construct SetPricesParams**: Format the token addresses and signed data into the required struct.
4. **Execute**: Submit the executeLiquidation transaction from a wallet that holds the LIQUIDATION_KEEPER role.

## Summary of Deployment Context
The deployment was compiled with Solidity 0.8.29 and the transaction hash for the deployment on Arbitrum is `0x413b66356c804e3240539f8ab4489ab879ee3b30ad52e33e8ec8a0fa586efd1e`.
