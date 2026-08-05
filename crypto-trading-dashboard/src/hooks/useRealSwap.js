import { useState, useCallback } from 'react';
import { BrowserProvider, Contract } from 'ethers';
import {
  TOKENS,
  QUICKSWAP_ROUTER,
  TRADING_ASSETS,
} from '../constants/contracts';

const POLYGON_CHAIN_ID = 137;
const SLIPPAGE_BPS = 50; // 0.5%
const MIN_PORTFOLIO_USD = 50;
const TRADE_PCT = 0.5;

const ROUTER_ABI = [
  'function getAmountsOut(uint amountIn, address[] path) view returns (uint[] amounts)',
  'function swapExactTokensForTokens(uint amountIn, uint amountOutMin, address[] path, address to, uint deadline) returns (uint[] amounts)',
  'function swapExactETHForTokens(uint amountOutMin, address[] path, address to, uint deadline) payable returns (uint[] amounts)',
];

const ERC20_ABI = [
  'function approve(address spender, uint256 amount) returns (bool)',
  'function allowance(address owner, address spender) view returns (uint256)',
];

const USDC = TOKENS.USDC_POLYGON;
const WMATIC = TOKENS.WMATIC_POLYGON;

function getUsdcBalance(balances) {
  return balances?.[USDC.id] ?? 0;
}

function getMaticBalance(balances) {
  const native = balances?.['matic-polygon'] ?? 0;
  const wrapped = balances?.['wmatic-polygon'] ?? 0;
  return native + wrapped;
}

export function useRealSwap(onSuccess) {
  const [txHash, setTxHash] = useState(null);
  const [isSwapping, setIsSwapping] = useState(false);
  const [error, setError] = useState(null);

  const portfolioUsd = useCallback(
    (balances, prices) =>
      (TRADING_ASSETS || []).reduce((sum, a) => {
        const bal = balances?.[a.id] ?? 0;
        const wmatic = a.id === 'matic-polygon' ? (balances?.['wmatic-polygon'] ?? 0) : 0;
        const total = a.id === 'matic-polygon' ? bal + wmatic : bal;
        return sum + total * (prices?.[a.id] ?? prices?.['wmatic-polygon'] ?? 0);
      }, 0),
    []
  );

  const canTradeReal = useCallback(
    (balances, prices, chainId) => {
      if (chainId !== POLYGON_CHAIN_ID) return false;
      const usd = portfolioUsd(balances, prices);
      return usd >= MIN_PORTFOLIO_USD;
    },
    [portfolioUsd]
  );

  const executeRealTrade = useCallback(
    async (assetId, signal, swapTarget, prices, balances, account) => {
      if (!window.ethereum || !account || !prices || !balances) {
        setError('Missing wallet or prices');
        return null;
      }
      const provider = new BrowserProvider(window.ethereum);
      const network = await provider.getNetwork();
      if (Number(network.chainId) !== POLYGON_CHAIN_ID) {
        setError('Switch to Polygon network');
        setIsSwapping(false);
        return null;
      }
      const usd = portfolioUsd(balances, prices);
      if (usd < MIN_PORTFOLIO_USD) {
        setError(`Portfolio below $${MIN_PORTFOLIO_USD} minimum`);
        setIsSwapping(false);
        return null;
      }

      setIsSwapping(true);
      setError(null);
      setTxHash(null);

      try {
        const signer = await provider.getSigner();
        const router = new Contract(QUICKSWAP_ROUTER, ROUTER_ABI, signer);
        const deadline = Math.floor(Date.now() / 1000) + 120;

        const usdcBal = getUsdcBalance(balances);
        const maticBal = getMaticBalance(balances);
        const maticPrice = prices?.['matic-polygon'] ?? prices?.['wmatic-polygon'] ?? 0;
        const usdcPrice = prices?.[USDC.id] ?? 1;

        if (signal === 'BUY' && assetId === 'matic-polygon') {
          const spendUsdc = Math.min(usdcBal * TRADE_PCT, usdcBal);
          if (spendUsdc < 0.01) {
            setError('Insufficient USDC');
            return null;
          }
          const amountIn = BigInt(Math.floor(spendUsdc * 10 ** USDC.decimals));
          const path = [USDC.address, WMATIC.address];
          const amounts = await router.getAmountsOut(amountIn, path);
          const amountOutMin = (amounts[1] * BigInt(10000 - SLIPPAGE_BPS)) / BigInt(10000);

          const usdcContract = new Contract(USDC.address, ERC20_ABI, signer);
          const allowance = await usdcContract.allowance(account, QUICKSWAP_ROUTER);
          if (allowance < amountIn) {
            const approveTx = await usdcContract.approve(QUICKSWAP_ROUTER, amountIn);
            await approveTx.wait();
          }

          const tx = await router.swapExactTokensForTokens(
            amountIn,
            amountOutMin,
            path,
            account,
            deadline
          );
          const receipt = await tx.wait();
          setTxHash(receipt.hash);
          onSuccess?.();
          return receipt.hash;
        }

        if ((signal === 'SELL' || signal === 'SWAP') && assetId === 'matic-polygon') {
          const sellAmount = Math.min(maticBal * TRADE_PCT, maticBal);
          if (sellAmount < 0.0001) {
            setError('Insufficient MATIC');
            return null;
          }

          const nativeMatic = balances?.['matic-polygon'] ?? 0;
          const wrappedMatic = balances?.['wmatic-polygon'] ?? 0;
          const nativeToSell = Math.min(sellAmount, nativeMatic);

          if (nativeMatic >= 0.0001 && nativeToSell >= 0.0001) {
            const amountInWei = BigInt(Math.floor(nativeToSell * 10 ** 18));
            const path = [WMATIC.address, USDC.address];
            const amounts = await router.getAmountsOut(amountInWei, path);
            const amountOutMin = (amounts[1] * BigInt(10000 - SLIPPAGE_BPS)) / BigInt(10000);

            const tx = await router.swapExactETHForTokens(
              amountOutMin,
              path,
              account,
              deadline,
              { value: amountInWei }
            );
            const receipt = await tx.wait();
            setTxHash(receipt.hash);
            onSuccess?.();
            return receipt.hash;
          }

          if (wrappedMatic >= 0.0001) {
            const wmaticToSell = Math.min(sellAmount, wrappedMatic);
            const amountIn = BigInt(Math.floor(wmaticToSell * 10 ** WMATIC.decimals));
            const path = [WMATIC.address, USDC.address];
            const amounts = await router.getAmountsOut(amountIn, path);
            const amountOutMin = (amounts[1] * BigInt(10000 - SLIPPAGE_BPS)) / BigInt(10000);

            const wmaticContract = new Contract(WMATIC.address, ERC20_ABI, signer);
            const allowance = await wmaticContract.allowance(account, QUICKSWAP_ROUTER);
            if (allowance < amountIn) {
              const approveTx = await wmaticContract.approve(QUICKSWAP_ROUTER, amountIn);
              await approveTx.wait();
            }

            const tx = await router.swapExactTokensForTokens(
              amountIn,
              amountOutMin,
              path,
              account,
              deadline
            );
            const receipt = await tx.wait();
            setTxHash(receipt.hash);
            onSuccess?.();
            return receipt.hash;
          }

          setError('Insufficient MATIC or WMATIC');
          return null;
        }

        setError('Unsupported trade');
        return null;
      } catch (err) {
        const msg = err?.reason ?? err?.message ?? 'Swap failed';
        setError(msg);
        return null;
      } finally {
        setIsSwapping(false);
      }
    },
    [portfolioUsd, onSuccess]
  );

  return {
    executeRealTrade,
    canTradeReal,
    isSwapping,
    txHash,
    error,
    minPortfolioUsd: MIN_PORTFOLIO_USD,
    clearError: () => setError(null),
    clearTxHash: () => setTxHash(null),
  };
}
