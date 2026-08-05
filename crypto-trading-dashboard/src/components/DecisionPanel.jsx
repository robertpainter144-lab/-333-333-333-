import { useState } from 'react';
import { TRADING_ASSETS } from '../constants/contracts';

const USDC_IDS = ['usdc-polygon', 'usdc-eth'];

const SIGNAL_COLORS = {
  BUY: '#3fb950',
  SELL: '#f85149',
  HOLD: '#8b949e',
  SWAP: '#d29922',
};

function SpreadBadge({ spreadPct }) {
  if (spreadPct == null) return null;
  const pct = spreadPct;
  let variant = 'tight';
  if (pct > 0.15) variant = 'wide';
  else if (pct > 0.05) variant = 'medium';
  return (
    <span className={`spread-badge spread-badge-${variant}`} title="Live spread: high-low range of last candle. Tight = good to trade.">
      <span className="spread-badge-dot" />
      {pct.toFixed(3)}% spread
    </span>
  );
}

// Rough gas estimates (USD): Ethereum ~$3-15, Polygon <$0.10
const GAS_ESTIMATE_ETH = 10;
const GAS_ESTIMATE_POLYGON = 0.05;

function GasFeasibility({ prices, balances, assets }) {
  const portfolioUsd = (assets || TRADING_ASSETS).reduce((sum, a) => {
    let bal = balances?.[a.id] ?? 0;
    if (a.id === 'matic-polygon') bal += balances?.['wmatic-polygon'] ?? 0;
    const price = prices?.[a.id] ?? prices?.['wmatic-polygon'] ?? 0;
    return sum + bal * price;
  }, 0);
  return (
    <div className="gas-feasibility">
      <div className="gas-feasibility-row">
        <strong>Portfolio:</strong> ${portfolioUsd.toFixed(2)} &middot;{' '}
        <strong>Gas:</strong> Eth ~${GAS_ESTIMATE_ETH}, Polygon ~${GAS_ESTIMATE_POLYGON.toFixed(2)} &middot;{' '}
        {portfolioUsd < 50 ? (
          <span className="gas-warning">
            Small portfolio – gas may eat profits on Ethereum swaps.
          </span>
        ) : (
          <span className="gas-ok">Swaps feasible if expected gain &gt; ~${GAS_ESTIMATE_ETH}.</span>
        )}
      </div>
      <div className="gas-strategy">
        <strong>Strategy:</strong> Trade USDC ↔ MATIC on Polygon with negligible gas (~$0.05/swap).
      </div>
    </div>
  );
}

function DecisionCard({
  asset,
  decision,
  bal,
  prices,
  paperTrading,
  assets,
  realMode,
  realSwap,
  account,
  canTradeReal,
  rawBalances,
}) {
  const d = decision || { displaySignal: 'HOLD', reason: '-' };
  const displaySignal = d.displaySignal ?? d.signal;
  const canExecutePaper =
    paperTrading?.isActive &&
    displaySignal !== 'HOLD' &&
    prices?.[asset.id] > 0;
  const canExecuteReal =
    realMode &&
    canTradeReal &&
    account &&
    displaySignal !== 'HOLD' &&
    prices?.[asset.id] > 0 &&
    (asset.id === 'matic-polygon' || asset.id === 'usdc-polygon');
  const handleExecute = () => {
    if (canExecuteReal && realSwap) {
      realSwap.executeRealTrade(asset.id, d.signal, d.swapTarget, prices, rawBalances ?? bal, account);
    } else if (canExecutePaper && paperTrading) {
      paperTrading.executeTrade(asset.id, d.signal, d.swapTarget, prices);
    }
  };
  const canExecute = canExecutePaper || canExecuteReal;
  return (
    <div className="decision-card">
      <div className="decision-header">
        <span className="asset-name">{asset.name}</span>
        <span
          className="signal-badge"
          style={{ color: SIGNAL_COLORS[displaySignal] || SIGNAL_COLORS.HOLD }}
        >
          {displaySignal}
        </span>
      </div>
      <div className="decision-detail">
        Balance: {((bal?.[asset.id] ?? 0) + (asset.id === 'matic-polygon' ? (bal?.['wmatic-polygon'] ?? 0) : 0)).toFixed(4)} {asset.symbol}
      </div>
      <div className="decision-reason">{d.reason}</div>
      {d.swapTarget && displaySignal === 'SWAP' && (
        <div className="swap-target">
          → {assets.find((a) => a.id === d.swapTarget)?.name ?? d.swapTarget}
        </div>
      )}
      {!USDC_IDS.includes(asset.id) && d.spreadPct != null && (
        <div className="spread-badge-wrap">
          <SpreadBadge spreadPct={d.spreadPct} />
        </div>
      )}
      {canExecute && (
        <button
          type="button"
          className={`execute-btn ${canExecuteReal ? 'execute-btn-real' : ''}`}
          onClick={handleExecute}
          disabled={realSwap?.isSwapping}
        >
          {realSwap?.isSwapping ? 'Swapping…' : canExecuteReal ? 'Execute (real)' : 'Execute (paper)'}
        </button>
      )}
    </div>
  );
}

export function DecisionPanel({
  assets = TRADING_ASSETS,
  polygonAssets = TRADING_ASSETS,
  decisions = {},
  prices,
  balances,
  paperTrading,
  displayBalances,
  autoExecute = false,
  onAutoExecuteChange,
  realMode = false,
  onRealModeChange,
  realSwap,
  account,
  chainId,
}) {
  const bal = displayBalances ?? balances;
  const ethAssets = assets.filter((a) => a.chain === 'ethereum');
  const [ethExpanded, setEthExpanded] = useState(false);
  const canTradeReal = realSwap?.canTradeReal(balances ?? bal, prices, chainId) ?? false;

  return (
    <div className="decision-panel">
      <div className="decision-panel-header">
        <h3>Decision Engine</h3>
        <div className="decision-panel-toggles">
          {paperTrading?.isActive && onAutoExecuteChange && (
            <label className="decision-auto-toggle">
              <input
                type="checkbox"
                checked={autoExecute}
                onChange={(e) => onAutoExecuteChange(e.target.checked)}
              />
              <span>Auto (momentum)</span>
            </label>
          )}
          {canTradeReal && onRealModeChange && (
            <label className="decision-real-toggle">
              <input
                type="checkbox"
                checked={realMode}
                onChange={(e) => onRealModeChange(e.target.checked)}
              />
              <span>Real trading</span>
            </label>
          )}
        </div>
      </div>
      {realSwap?.error && (
        <div className="real-swap-error">
          {realSwap.error}
          <button type="button" onClick={realSwap.clearError}>×</button>
        </div>
      )}
      {realSwap?.txHash && (
        <div className="real-swap-success">
          <a href={`https://polygonscan.com/tx/${realSwap.txHash}`} target="_blank" rel="noopener noreferrer">
            View tx
          </a>
          <button type="button" onClick={realSwap.clearTxHash}>×</button>
        </div>
      )}
      {Object.keys(balances || {}).length > 0 && !paperTrading?.isActive && (
        <GasFeasibility prices={prices} balances={balances} assets={polygonAssets} />
      )}
      {paperTrading?.isActive && !realMode && (
        <div className="paper-mode-banner">
          Paper trading – virtual balances
          {autoExecute && <span className="paper-mode-auto"> · Auto-executing</span>}
        </div>
      )}
      {realMode && canTradeReal && (
        <div className="real-mode-banner">
          Real trading – live swaps on QuickSwap (Polygon)
        </div>
      )}
      <div className="decision-grid">
        {polygonAssets.map((asset) => (
          <DecisionCard
            key={asset.id}
            asset={asset}
            decision={decisions[asset.id]}
            bal={bal}
            prices={prices}
            paperTrading={paperTrading}
            assets={assets}
            realMode={realMode}
            realSwap={realSwap}
            account={account}
            canTradeReal={canTradeReal}
            rawBalances={balances}
          />
        ))}
      </div>
      {ethAssets.length > 0 && (
        <div className="decision-eth-section">
          <button
            type="button"
            className="decision-eth-toggle"
            onClick={() => setEthExpanded((e) => !e)}
          >
            {ethExpanded ? '▼' : '▶'} Ethereum (minimized)
          </button>
          {ethExpanded && (
            <div className="decision-grid decision-grid-eth">
              {ethAssets.map((asset) => (
                <DecisionCard
                  key={asset.id}
                  asset={asset}
                  decision={decisions[asset.id]}
                  bal={bal}
                  prices={prices}
                  paperTrading={paperTrading}
                  assets={assets}
                />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
