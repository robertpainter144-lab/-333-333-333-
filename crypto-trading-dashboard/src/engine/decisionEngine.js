/**
 * Decision Engine: Runs on each NEW candlestick for all assets.
 * Decides: BUY, SELL, SWAP (trade), or HOLD.
 *
 * Terminology:
 * - BUY/SELL = involve USDC (buy = use USDC to get asset; sell = get USDC)
 * - SWAP = trade between non-USDC assets (MATIC↔WETH↔WBTC etc.)
 *
 * Spread: Uses candle high-low as volatility signal. Wide spread = caution.
 */

import { ASSETS } from '../constants/contracts';

const LOOKBACK_CANDLES = 2;
const MOMENTUM_THRESHOLD = 0.0005; // 0.05% - more responsive for paper trading
const SPREAD_CAUTION_PCT = 0.15; // Avoid trading when spread > 0.15%
const MIN_BALANCE = 0.0001;
const USDC_IDS = ['usdc-eth', 'usdc-polygon'];

export function runDecisionEngine(candleData, prices, balances, assets) {
  const effectiveAssets = assets ?? ASSETS;
  const decisions = {};
  const pctChanges = {};

  // Pass 1: compute momentum for all assets
  for (const asset of effectiveAssets) {
    const candles = (candleData || {})[asset.id] || [];
    if (candles.length >= LOOKBACK_CANDLES) {
      const recent = candles.slice(-LOOKBACK_CANDLES);
      const firstClose = recent[0].close;
      const lastClose = recent[recent.length - 1].close;
      pctChanges[asset.id] = (lastClose - firstClose) / firstClose;
    }
  }

  // Pass 2: decisions
  for (const asset of effectiveAssets) {
    const candles = (candleData || {})[asset.id] || [];
    const price = prices?.[asset.id] || 0;
    const balance = balances?.[asset.id] ?? 0;

    let signal = 'HOLD';
    let reason = 'Waiting for data';
    let swapTarget = null;
    let spreadPct = 0;

    if (candles.length >= LOOKBACK_CANDLES && price > 0) {
      const recent = candles.slice(-LOOKBACK_CANDLES);
      const lastCandle = recent[recent.length - 1];
      const pctChange = pctChanges[asset.id] ?? 0;
      spreadPct = lastCandle.low > 0 ? ((lastCandle.high - lastCandle.low) / lastCandle.low) * 100 : 0;

      const spreadCaution = spreadPct > SPREAD_CAUTION_PCT;

      if (spreadCaution) {
        signal = 'HOLD';
        reason = `Wide spread ${spreadPct.toFixed(2)}% - wait`;
      } else if (pctChange < -MOMENTUM_THRESHOLD) {
        if (balance > MIN_BALANCE) {
          const bestTarget = effectiveAssets.filter((a) => a.id !== asset.id)
            .map((a) => ({ id: a.id, pct: pctChanges[a.id] ?? -1 }))
            .sort((a, b) => (b.pct - a.pct))[0];
          if (bestTarget && bestTarget.pct > MOMENTUM_THRESHOLD) {
            signal = 'SWAP';
            swapTarget = bestTarget.id;
            reason = `Down ${(pctChange * 100).toFixed(3)}% → swap to ${effectiveAssets.find((a) => a.id === swapTarget)?.symbol || swapTarget}`;
          } else {
            signal = 'SELL';
            reason = `Down ${(pctChange * 100).toFixed(3)}% - reduce exposure`;
          }
        } else {
          signal = 'HOLD';
          reason = `Down ${(pctChange * 100).toFixed(3)}% - no position`;
        }
      } else if (pctChange > MOMENTUM_THRESHOLD) {
        signal = 'BUY';
        reason = `Up ${(pctChange * 100).toFixed(3)}% - momentum`;
      } else {
        signal = 'HOLD';
        reason = `Flat (${(pctChange * 100).toFixed(2)}%)`;
      }

      if (USDC_IDS.includes(asset.id)) {
        if (Math.abs(pctChange) < 0.0005) {
          signal = 'HOLD';
          reason = 'Stablecoin - hold';
        } else if (pctChange > 0.0005) {
          signal = 'BUY';
          reason = 'USDC premium - accumulate';
        }
      }
    }

    // Normalize for display: SWAP → USDC = SELL; SWAP → other crypto = SWAP
    const displaySignal =
      signal === 'SWAP' && swapTarget && USDC_IDS.includes(swapTarget) ? 'SELL' : signal;

    decisions[asset.id] = {
      signal,
      displaySignal,
      reason,
      price,
      balance,
      spreadPct,
      swapTarget,
      lastCandleTime: candles.length > 0 ? candles[candles.length - 1].time : null,
    };
  }

  return decisions;
}
