import { useState, useCallback, useMemo, useEffect, useRef } from 'react';
import { useWallet } from './hooks/useWallet';
import { usePrices } from './hooks/usePrices';
import { useCandleData } from './hooks/useCandleData';
import { usePaperTrading } from './hooks/usePaperTrading';
import { useRealSwap } from './hooks/useRealSwap';
import { CandlestickChart } from './components/CandlestickChart';
import { CollapsiblePanel } from './components/CollapsiblePanel';
import { DecisionPanel } from './components/DecisionPanel';
import { MegaBotPanel } from './components/MegaBotPanel';
import { PaperTradingPanel } from './components/PaperTradingPanel';
import { PolygonBalancesPanel } from './components/PolygonBalancesPanel';
import { PortfolioChart } from './components/PortfolioChart';
import { runDecisionEngine } from './engine/decisionEngine';
import { ASSETS, POLYGON_ASSETS, TRADING_ASSETS } from './constants/contracts';
import './App.css';

function App() {
  const { account, balances, chainId, connect, fetchBalances, isConnecting, error } = useWallet();
  const [prices, setPrices] = useState({});

  const { candleData, onPriceUpdate } = useCandleData();
  const paper = usePaperTrading(prices);
  const realSwap = useRealSwap(fetchBalances);
  const [realMode, setRealMode] = useState(false);

  const realBalancesWithMatic =
    account && balances
      ? {
          ...balances,
          'matic-polygon':
            (balances['matic-polygon'] ?? 0) + (balances['wmatic-polygon'] ?? 0),
        }
      : {};
  const effectiveBalances =
    realMode && account
      ? realBalancesWithMatic
      : paper.isActive && paper.paperPortfolio
        ? paper.paperPortfolio
        : account
          ? realBalancesWithMatic
          : {};
  const decisions = useMemo(
    () => runDecisionEngine(candleData, prices, effectiveBalances ?? {}, TRADING_ASSETS),
    [candleData, prices, effectiveBalances]
  );

  const handlePriceUpdate = useCallback(
    (newPrices, buffers) => {
      setPrices(newPrices);
      onPriceUpdate(newPrices, buffers);
    },
    [onPriceUpdate]
  );

  const { error: priceError, loading: priceLoading, retry } = usePrices(handlePriceUpdate);

  const [autoExecute, setAutoExecute] = useState(false);
  const lastExecutedRef = useRef({});
  const AUTO_COOLDOWN_MS = 30000;

  useEffect(() => {
    if (!autoExecute || !paper.isActive || !paper.paperPortfolio || !prices) return;
    const now = Date.now();
    for (const asset of TRADING_ASSETS) {
      const d = decisions[asset.id];
      const signal = d?.displaySignal ?? d?.signal;
      if (signal === 'HOLD' || !d || !prices[asset.id]) continue;
      const last = lastExecutedRef.current[asset.id] ?? 0;
      if (now - last < AUTO_COOLDOWN_MS) continue;
      const result = paper.executeTrade(asset.id, d.signal, d.swapTarget, prices);
      if (result) {
        lastExecutedRef.current[asset.id] = now;
      }
    }
  }, [autoExecute, decisions, paper.isActive, paper.paperPortfolio, paper.executeTrade, prices]);

  return (
    <div className="app">
      <header className="header">
        <div className="header-title">
          <h1>Crypto Trading Dashboard</h1>
          <p className="subtitle">
            USDC · MATIC · Paper trading · Ultimate Mega Trading Bot (localhost:8000)
          </p>
        </div>
        <div className="wallet-section">
          {account ? (
            <div className="wallet-connected">
              <span className="wallet-address">
                {account.slice(0, 6)}...{account.slice(-4)}
              </span>
            </div>
          ) : (
            <button
              className="connect-btn"
              onClick={connect}
              disabled={isConnecting}
            >
              {isConnecting ? 'Connecting...' : 'Connect MetaMask'}
            </button>
          )}
          {(error || priceError) && (
            <span className="error-msg">
              {error || priceError}
              {retry && (
                <button type="button" className="retry-btn" onClick={retry}>
                  Retry
                </button>
              )}
            </span>
          )}
          {priceLoading && !priceError && (
            <span className="loading-msg">Loading prices…</span>
          )}
        </div>
      </header>

      <main className="main">
        <CollapsiblePanel title="Candlestick Charts" defaultOpen={true}>
          <section className="charts-grid">
            {TRADING_ASSETS.map((asset) => {
            const candles = candleData[asset.id] || [];
            const firstCandle = candles[0];
            const lastCandle = candles[candles.length - 1];
            const firstOpen = firstCandle?.open;
            const lastClose = lastCandle?.close;
            const inceptionTime = firstCandle?.time;
            const pctChange = firstOpen > 0 && lastClose != null
              ? ((lastClose - firstOpen) / firstOpen) * 100
              : null;
            const elapsedSec = inceptionTime != null && lastCandle?.time != null
              ? lastCandle.time - inceptionTime
              : null;
            return (
              <CandlestickChart
                key={asset.id}
                data={candles}
                title={asset.name}
                currentPrice={prices[asset.id]}
                pctChangeSinceInception={pctChange}
                inceptionTime={inceptionTime}
                elapsedSeconds={elapsedSec}
                isLoading={priceLoading && candles.length === 0}
                decision={decisions[asset.id]}
              />
            );
          })}
          </section>
        </CollapsiblePanel>

        <CollapsiblePanel title="USDC & MATIC Balances" defaultOpen={true}>
          <PolygonBalancesPanel balances={account ? balances : {}} prices={prices} />
        </CollapsiblePanel>
        {paper.isActive && (
          <CollapsiblePanel title="Portfolio Chart" defaultOpen={true}>
            <div className="portfolio-chart-section">
              <PortfolioChart
                valueHistory={paper.valueHistory ?? []}
                tradeHistory={paper.tradeHistory ?? []}
                startValue={paper.startValue}
                currentValue={paper.currentValue}
              />
            </div>
          </CollapsiblePanel>
        )}
        <CollapsiblePanel title="Paper Trading (Client)" defaultOpen={true}>
          <PaperTradingPanel
          paper={paper}
          realBalances={account ? balances : {}}
          prices={prices}
          onRefreshWallet={fetchBalances}
        />
        </CollapsiblePanel>
        <CollapsiblePanel title="Ultimate Mega Trading Bot (Backend)" defaultOpen={true}>
          <MegaBotPanel />
        </CollapsiblePanel>
        <CollapsiblePanel title="Decision Engine & Execute" defaultOpen={true}>
          <DecisionPanel
          assets={TRADING_ASSETS}
          polygonAssets={TRADING_ASSETS}
          decisions={decisions}
          prices={prices}
          balances={account ? balances : {}}
          paperTrading={paper}
          displayBalances={effectiveBalances}
          autoExecute={autoExecute}
          onAutoExecuteChange={setAutoExecute}
          realMode={realMode}
          onRealModeChange={setRealMode}
          realSwap={realSwap}
          account={account}
          chainId={chainId}
        />
        </CollapsiblePanel>
      </main>
    </div>
  );
}

export default App;
