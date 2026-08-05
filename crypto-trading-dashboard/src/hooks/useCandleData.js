import { useState, useCallback } from 'react';
import { ASSETS } from '../constants/contracts';

// Aggregate price ticks into OHLC candles (5-second buckets)
const CANDLE_INTERVAL_SEC = 5;

function buildCandlesFromTicks(ticks) {
  if (!ticks || ticks.length === 0) return [];
  const buckets = {};
  for (const tick of ticks) {
    const bucket = Math.floor(tick.time / CANDLE_INTERVAL_SEC) * CANDLE_INTERVAL_SEC;
    if (!buckets[bucket]) {
      buckets[bucket] = { open: tick.price, high: tick.price, low: tick.price, close: tick.price };
    } else {
      buckets[bucket].high = Math.max(buckets[bucket].high, tick.price);
      buckets[bucket].low = Math.min(buckets[bucket].low, tick.price);
      buckets[bucket].close = tick.price;
    }
  }
  return Object.entries(buckets)
    .map(([t, ohlc]) => ({
      time: parseInt(t, 10),
      open: ohlc.open,
      high: ohlc.high,
      low: ohlc.low,
      close: ohlc.close,
    }))
    .sort((a, b) => a.time - b.time);
}

export function useCandleData() {
  const [candleData, setCandleData] = useState({});

  const onPriceUpdate = useCallback((prices, buffers) => {
    if (!buffers) return;
    const newCandleData = {};
    for (const asset of ASSETS) {
      const ticks = buffers[asset.id] || [];
      newCandleData[asset.id] = buildCandlesFromTicks(ticks);
    }
    setCandleData(newCandleData);
  }, []);

  return { candleData, onPriceUpdate };
}
