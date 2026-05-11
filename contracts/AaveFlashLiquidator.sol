// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * @title AaveFlashLiquidator
 * @notice Flash loan-powered liquidation bot for AAVE V3 on Arbitrum.
 * 
 * Flow:
 *   1. Bot calls executeLiquidation() with target parameters
 *   2. Contract takes flash loan from AAVE Pool (borrows the debt token)
 *   3. In executeOperation() callback:
 *      a. Approve Pool to spend debt tokens
 *      b. Call Pool.liquidationCall() → repay debt, receive collateral + bonus
 *      c. Swap received collateral → debt token via Uniswap V3
 *      d. Repay flash loan (principal + 0.05% fee)
 *      e. Transfer remaining profit to owner
 *
 * Deploy via Remix on Arbitrum One (chainId 42161).
 * 
 * Remix settings:
 *   - Compiler: 0.8.20+
 *   - EVM version: paris
 *   - Optimization: 200 runs
 *   - Deploy with constructor args: 
 *       _poolProvider = 0xa97684ead0e402dC232d5A977953DF7ECBaB3CDb
 *       _swapRouter   = 0xE592427A0AEce92De3Edee1F18E0157C05861564
 */

// ============================================================
// Interfaces (inline to avoid import issues in Remix)
// ============================================================

interface IPoolAddressesProvider {
    function getPool() external view returns (address);
}

interface IPool {
    function flashLoanSimple(
        address receiverAddress,
        address asset,
        uint256 amount,
        bytes calldata params,
        uint16 referralCode
    ) external;

    function liquidationCall(
        address collateralAsset,
        address debtAsset,
        address user,
        uint256 debtToCover,
        bool receiveAToken
    ) external;

    function getUserAccountData(address user)
        external
        view
        returns (
            uint256 totalCollateralBase,
            uint256 totalDebtBase,
            uint256 availableBorrowsBase,
            uint256 currentLiquidationThreshold,
            uint256 ltv,
            uint256 healthFactor
        );
}

interface IERC20 {
    function balanceOf(address account) external view returns (uint256);
    function transfer(address to, uint256 amount) external returns (bool);
    function approve(address spender, uint256 amount) external returns (bool);
    function allowance(address owner, address spender) external view returns (uint256);
}

// Router02 interface (no deadline field — used by native Plasma DEX)
interface ISwapRouter {
    struct ExactInputSingleParams {
        address tokenIn;
        address tokenOut;
        uint24 fee;
        address recipient;
        uint256 amountIn;
        uint256 amountOutMinimum;
        uint160 sqrtPriceLimitX96;
    }

    function exactInputSingle(ExactInputSingleParams calldata params)
        external
        payable
        returns (uint256 amountOut);
}

// ============================================================
// Contract
// ============================================================

contract AaveFlashLiquidator {
    // --- State ---
    address public owner;
    IPool public immutable POOL;
    IPoolAddressesProvider public immutable ADDRESSES_PROVIDER;
    ISwapRouter public immutable SWAP_ROUTER;

    // --- Events ---
    event LiquidationExecuted(
        address indexed user,
        address indexed collateralAsset,
        address indexed debtAsset,
        uint256 debtCovered,
        uint256 collateralReceived,
        uint256 profit
    );
    event Withdrawn(address token, uint256 amount);

    // --- Modifiers ---
    modifier onlyOwner() {
        require(msg.sender == owner, "Not owner");
        _;
    }

    modifier onlyPool() {
        require(msg.sender == address(POOL), "Caller is not the Pool");
        _;
    }

    // --- Constructor ---
    constructor(address _poolProvider, address _swapRouter) {
        owner = msg.sender;
        ADDRESSES_PROVIDER = IPoolAddressesProvider(_poolProvider);
        POOL = IPool(ADDRESSES_PROVIDER.getPool());
        SWAP_ROUTER = ISwapRouter(_swapRouter);
    }

    // ============================================================
    // Main entry point — called by your Python bot
    // ============================================================

    /**
     * @notice Execute a flash-loan-powered liquidation.
     * @param collateralAsset The collateral to seize (e.g., WETH)
     * @param debtAsset       The debt token to repay (e.g., USDC)
     * @param user            The borrower to liquidate
     * @param debtToCover     Amount of debt to repay (use type(uint256).max for maximum)
     * @param swapPoolFee     Uniswap V3 pool fee tier (500=0.05%, 3000=0.3%, 10000=1%)
     */
    function executeLiquidation(
        address collateralAsset,
        address debtAsset,
        address user,
        uint256 debtToCover,
        uint24 swapPoolFee
    ) external onlyOwner {
        // Verify position is actually liquidatable
        (, , , , , uint256 healthFactor) = POOL.getUserAccountData(user);
        require(healthFactor < 1e18, "Position is healthy, HF >= 1");

        // Encode liquidation params for the flash loan callback
        bytes memory params = abi.encode(
            collateralAsset,
            debtAsset,
            user,
            debtToCover,
            swapPoolFee
        );

        // Request flash loan of the debt token
        // The callback (executeOperation) will handle the liquidation
        POOL.flashLoanSimple(
            address(this),  // receiver
            debtAsset,       // asset to borrow
            debtToCover,     // amount
            params,          // data passed to callback
            0                // referralCode
        );
    }

    // ============================================================
    // AAVE Flash Loan callback — called by Pool
    // ============================================================

    /**
     * @notice Called by AAVE Pool after flash loan funds are received.
     *         Must repay principal + premium by end of this function.
     */
    function executeOperation(
        address /* asset */,          // debt token (what we borrowed)
        uint256 amount,               // principal amount
        uint256 premium,     // flash loan fee (0.05%)
        address initiator,   // should be this contract
        bytes calldata params
    ) external onlyPool returns (bool) {
        require(initiator == address(this), "Invalid initiator");

        // Decode liquidation parameters
        (
            address collateralAsset,
            address debtAsset,
            address user,
            uint256 debtToCover,
            uint24 swapPoolFee
        ) = abi.decode(params, (address, address, address, uint256, uint24));

        // --- Step 1: Approve Pool to spend debt tokens for liquidation ---
        IERC20(debtAsset).approve(address(POOL), debtToCover);

        // --- Step 2: Execute liquidation ---
        // We repay `debtToCover` of the user's debt
        // and receive collateralAsset + liquidation bonus
        uint256 collateralBefore = IERC20(collateralAsset).balanceOf(address(this));

        POOL.liquidationCall(
            collateralAsset,
            debtAsset,
            user,
            debtToCover,
            false  // receive underlying token, not aToken
        );

        uint256 collateralReceived = IERC20(collateralAsset).balanceOf(address(this)) - collateralBefore;
        require(collateralReceived > 0, "Liquidation failed: no collateral received");

        // --- Step 3: Swap collateral → debt token to repay flash loan ---
        uint256 amountOwed = amount + premium;  // principal + 0.05% fee
        
        uint256 debtBalanceAfterLiq = IERC20(debtAsset).balanceOf(address(this));
        
        if (collateralAsset != debtAsset) {
            // Need to swap collateral back to debt token
            IERC20(collateralAsset).approve(address(SWAP_ROUTER), collateralReceived);

            uint256 amountNeeded = amountOwed > debtBalanceAfterLiq 
                ? amountOwed - debtBalanceAfterLiq 
                : 0;

            if (amountNeeded > 0) {
                ISwapRouter.ExactInputSingleParams memory swapParams = ISwapRouter.ExactInputSingleParams({
                    tokenIn: collateralAsset,
                    tokenOut: debtAsset,
                    fee: swapPoolFee,
                    recipient: address(this),
                    amountIn: collateralReceived,
                    amountOutMinimum: amountNeeded,
                    sqrtPriceLimitX96: 0
                });

                SWAP_ROUTER.exactInputSingle(swapParams);
            }
        }

        // --- Step 4: Repay flash loan ---
        uint256 finalDebtBalance = IERC20(debtAsset).balanceOf(address(this));
        require(finalDebtBalance >= amountOwed, "Insufficient funds to repay flash loan");
        IERC20(debtAsset).approve(address(POOL), amountOwed);

        // --- Step 5: Calculate and emit profit ---
        uint256 profit = finalDebtBalance - amountOwed;
        
        // Transfer the profit in debt token to the owner
        if (profit > 0) {
            IERC20(debtAsset).transfer(owner, profit);
        }

        // Transfer any remaining collateral to owner (if swap was partial)
        uint256 remainingCollateral = IERC20(collateralAsset).balanceOf(address(this));
        if (remainingCollateral > 0 && collateralAsset != debtAsset) {
            IERC20(collateralAsset).transfer(owner, remainingCollateral);
        }

        emit LiquidationExecuted(
            user,
            collateralAsset,
            debtAsset,
            debtToCover,
            collateralReceived,
            profit
        );

        return true;
    }

    // ============================================================
    // Utility functions
    // ============================================================

    /**
     * @notice Check if a user's position is liquidatable.
     * @return healthFactor The user's current health factor (18 decimals, <1e18 = liquidatable)
     */
    function checkHealthFactor(address user) external view returns (uint256 healthFactor) {
        (, , , , , healthFactor) = POOL.getUserAccountData(user);
    }

    /**
     * @notice Withdraw any ERC20 tokens stuck in the contract.
     */
    function withdrawToken(address token) external onlyOwner {
        uint256 balance = IERC20(token).balanceOf(address(this));
        require(balance > 0, "No balance");
        IERC20(token).transfer(owner, balance);
        emit Withdrawn(token, balance);
    }

    /**
     * @notice Withdraw ETH stuck in the contract.
     */
    function withdrawETH() external onlyOwner {
        uint256 balance = address(this).balance;
        require(balance > 0, "No ETH");
        (bool success, ) = payable(owner).call{value: balance}("");
        require(success, "ETH transfer failed");
    }

    /**
     * @notice Transfer ownership.
     */
    function transferOwnership(address newOwner) external onlyOwner {
        require(newOwner != address(0), "Zero address");
        owner = newOwner;
    }

    // Allow receiving ETH
    receive() external payable {}
}
