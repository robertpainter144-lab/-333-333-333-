import { useState, useCallback, useEffect, useRef } from 'react';
import { ASSETS } from '../constants/contracts';

const STORAGE_KEY = 'crypto-dashboard-paper';
const USDC_IDS = ['usdc-polygon', 'usdc-eth']; // Prefer Polygon
const TRADE_PCT = 0.5; // Use 50% of position per trade

function getUsdcBalance(portfolio) {
  for (const id of USDC_IDS) {
    const b = portfolio[id] ?? 0;
    if (b > 0.01) return { id, balance: b };
  }
  return { id: USDC_IDS[0], balance: 0 };
}

function loadState() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) return JSON.parse(raw);
  } catch {}
  return null;
}

function saveState(state) {
  try {
    if (state == null) {
      localStorage.removeItem(STORAGE_KEY);
    } else {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
    }
  } catch {}
}

export function usePaperTrading(prices) {
  const [paperPortfolio, setPaperPortfolio] = useState(() => {
    const saved = loadState();
    const p = saved?.portfolio ?? null;
    if (!p) return null;
    const migrated = {};
    for (const a of ASSETS) {
      migrated[a.id] = p[a.id] ?? 0;
    }
    return migrated;
  });
  const [startValue, setStartValue] = useState(() => {
    const saved = loadState();
    return saved?.startValue ?? null;
  });
  const [seedSource, setSeedSource] = useState(() => {
    const saved = loadState();
    return saved?.seedSource ?? null;
  });
  const [tradeHistory, setTradeHistory] = useState(() => {
    const saved = loadState();
    return saved?.tradeHistory ?? [];
  });
  const [isActive, setIsActive] = useState(() => {
    const saved = loadState();
    return saved?.isActive ?? false;
  });
  const [valueHistory, setValueHistory] = useState([]);

  const portfolioValue = (balances) =>
    ASSETS.reduce((sum, a) => sum + ((balances?.[a.id] ?? 0) * (prices?.[a.id] ?? 0)), 0);

  const persist = useCallback((portfolio, start, history, active, source) => {
    saveState({
      portfolio,
      startValue: start,
      tradeHistory: history,
      isActive: active,
      seedSource: source,
    });
  }, []);

  const startFromBalances = useCallback(
    (realBalances) => {
      const portfolio = {};
      for (const a of ASSETS) {
        portfolio[a.id] = realBalances?.[a.id] ?? 0;
      }
      const value = portfolioValue(portfolio);
      setPaperPortfolio(portfolio);
      setStartValue(value);
      setSeedSource('wallet');
      setTradeHistory([]);
      setValueHistory([{ time: Math.floor(Date.now() / 1000), value }]);
      setIsActive(true);
      persist(portfolio, value, [], true, 'wallet');
    },
    [persist, portfolioValue]
  );

  const syncFromWallet = useCallback(
    (realBalances) => {
      if (!paperPortfolio || seedSource !== 'wallet') return;
      const portfolio = {};
      for (const a of ASSETS) {
        portfolio[a.id] = realBalances?.[a.id] ?? 0;
      }
      setPaperPortfolio(portfolio);
      persist(portfolio, startValue, tradeHistory, isActive, seedSource);
    },
    [paperPortfolio, seedSource, startValue, tradeHistory, isActive, persist]
  );

  const startWithSeed = useCallback(
    (usdcAmount = 100) => {
      const portfolio = {};
      for (const a of ASSETS) {
        portfolio[a.id] = a.id === USDC_IDS[0] ? usdcAmount : 0;
      }
      const value = usdcAmount;
      setPaperPortfolio(portfolio);
      setStartValue(value);
      setSeedSource('seed');
      setTradeHistory([]);
      setValueHistory([{ time: Math.floor(Date.now() / 1000), value }]);
      setIsActive(true);
      persist(portfolio, value, [], true, 'seed');
    },
    [persist]
  );

  const stop = useCallback(() => {
    setIsActive(false);
    persist(paperPortfolio, startValue, tradeHistory, false, seedSource);
  }, [paperPortfolio, startValue, tradeHistory, seedSource, persist]);

  const reset = useCallback(() => {
    setPaperPortfolio(null);
    setStartValue(null);
    setSeedSource(null);
    setTradeHistory([]);
    setValueHistory([]);
    setIsActive(false);
    saveState(null);
  }, []);

  const executeTrade = useCallback(
    (assetId, signal, swapTarget, currentPrices) => {
      if (!paperPortfolio || !currentPrices) return null;
      const prices = currentPrices;
      const asset = ASSETS.find((a) => a.id === assetId);
      const { id: usdcId, balance: usdcBal } = getUsdcBalance(paperPortfolio);
      const usdcPrice = prices[usdcId] ?? 1;

      let fromId, toId, fromAmount, toAmount, desc;

      if (signal === 'BUY') {
        const spend = usdcBal * TRADE_PCT;
        if (spend < 0.01) return null;
        const buyPrice = prices[assetId] ?? 0;
        if (buyPrice <= 0) return null;
        toAmount = spend / buyPrice;
        fromId = usdcId;
        toId = assetId;
        fromAmount = spend / usdcPrice;
        desc = `BUY ${asset?.symbol ?? assetId} with ${spend.toFixed(2)} USDC`;
      } else if (signal === 'SELL') {
        const sellBal = paperPortfolio[assetId] ?? 0;
        if (sellBal < 0.0001) return null;
        const sellPrice = prices[assetId] ?? 0;
        if (sellPrice <= 0) return null;
        const proceeds = sellBal * sellPrice;
        fromId = assetId;
        toId = USDC_IDS[0]; // Credit to Polygon USDC (polygon-first)
        fromAmount = sellBal;
        toAmount = proceeds / (prices[toId] ?? 1);
        desc = `SELL ${sellBal.toFixed(4)} ${asset?.symbol ?? assetId} for ${proceeds.toFixed(2)} USDC`;
      } else if (signal === 'SWAP' && swapTarget) {
        const fromBal = paperPortfolio[assetId] ?? 0;
        if (fromBal < 0.0001) return null;
        const fromPrice = prices[assetId] ?? 0;
        const toPrice = prices[swapTarget] ?? 0;
        if (fromPrice <= 0 || toPrice <= 0) return null;
        const usdValue = fromBal * fromPrice;
        toAmount = usdValue / toPrice;
        const toAsset = ASSETS.find((a) => a.id === swapTarget);
        fromId = assetId;
        toId = swapTarget;
        fromAmount = fromBal;
        desc = `SWAP ${fromBal.toFixed(4)} ${asset?.symbol ?? assetId} → ${toAsset?.symbol ?? swapTarget}`;
      } else {
        return null;
      }

      const next = { ...paperPortfolio };
      next[fromId] = Math.max(0, (next[fromId] ?? 0) - fromAmount);
      next[toId] = (next[toId] ?? 0) + toAmount;

      const valueBefore = portfolioValue(paperPortfolio);
      const valueAfter = portfolioValue(next);

      const trade = {
        time: Date.now(),
        assetId,
        signal,
        swapTarget: swapTarget || null,
        desc,
        valueBefore,
        valueAfter,
        pnl: valueAfter - valueBefore,
      };

      setPaperPortfolio(next);
      setTradeHistory((h) => [...h.slice(-99), trade]);
      setValueHistory((v) => [...v.slice(-500), { time: Math.floor(trade.time / 1000), value: valueAfter }]);
      persist(next, startValue, [...tradeHistory.slice(-99), trade], isActive, seedSource);

      return trade;
    },
    [paperPortfolio, startValue, tradeHistory, isActive, seedSource, persist]
  );

  useEffect(() => {
    if (paperPortfolio && isActive) {
      persist(paperPortfolio, startValue, tradeHistory, true, seedSource);
    }
  }, [paperPortfolio, startValue, tradeHistory, isActive, seedSource, persist]);

  const valueHistoryIntervalRef = useRef(null);
  useEffect(() => {
    if (!paperPortfolio || !isActive || !prices) return;
    const recordValue = () => {
      const value = portfolioValue(paperPortfolio);
      setValueHistory((v) => {
        const last = v[v.length - 1];
        const now = Math.floor(Date.now() / 1000);
        if (last && last.time === now) return v;
        return [...v.slice(-500), { time: now, value }];
      });
    };
    recordValue();
    valueHistoryIntervalRef.current = setInterval(recordValue, 5000);
    return () => {
      if (valueHistoryIntervalRef.current) clearInterval(valueHistoryIntervalRef.current);
    };
  }, [paperPortfolio, isActive, prices, portfolioValue]);

  const currentValue = paperPortfolio ? portfolioValue(paperPortfolio) : 0;
  const pnl = startValue != null ? currentValue - startValue : 0;
  const pnlPct = startValue != null && startValue > 0 ? (pnl / startValue) * 100 : 0;

  return {
    isActive,
    paperPortfolio,
    startValue,
    seedSource,
    currentValue,
    pnl,
    pnlPct,
    tradeHistory,
    valueHistory,
    startFromBalances,
    syncFromWallet,
    startWithSeed,
    stop,
    reset,
    executeTrade,
  };
}
