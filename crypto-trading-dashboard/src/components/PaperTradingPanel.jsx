import { ASSETS, TRADING_ASSETS } from '../constants/contracts';

export function PaperTradingPanel({ paper, realBalances, prices, onRefreshWallet }) {
  const {
    isActive,
    paperPortfolio,
    startValue,
    seedSource,
    currentValue,
    pnl,
    pnlPct,
    tradeHistory,
    startFromBalances,
    syncFromWallet,
    startWithSeed,
    stop,
    reset,
  } = paper;

  if (!isActive) {
    const hasWalletBalances = realBalances && Object.keys(realBalances).length > 0;
    return (
      <div className="paper-panel paper-panel-inactive">
        <h3>Paper Trading</h3>
        <p className="paper-desc">
          Simulated trading — virtual USDC & MATIC only. No real funds move.
        </p>
        {hasWalletBalances && (
          <div className="paper-wallet-preview">
            <strong>Your USDC & MATIC (Polygon):</strong>
            <div className="paper-balances">
              {TRADING_ASSETS.map((asset) => {
                const bal = realBalances[asset.id] ?? 0;
                return (
                  <span key={asset.id} className={bal < 0.0001 ? 'paper-balance muted' : 'paper-balance'}>
                    {asset.symbol}: {bal.toFixed(4)}
                  </span>
                );
              })}
            </div>
          </div>
        )}
        <div className="paper-actions">
          <button
            type="button"
            className="paper-btn paper-btn-primary"
            onClick={() => startWithSeed(100)}
          >
            Start with $100 USDC
          </button>
          <button
            type="button"
            className="paper-btn paper-btn-secondary"
            onClick={() => startFromBalances(realBalances ?? {})}
            disabled={!hasWalletBalances}
          >
            Start from wallet
          </button>
        </div>
        <p className="paper-actions-hint">
          $100 = fixed virtual balance. Wallet = your actual balances.
        </p>
      </div>
    );
  }

  return (
    <div className="paper-panel paper-panel-active">
      <div className="paper-header">
        <h3>Paper Trading</h3>
        <div className="paper-header-actions">
          {seedSource === 'wallet' && (
            <button
              type="button"
              className="paper-btn paper-btn-sm"
              onClick={() => {
                onRefreshWallet?.();
                setTimeout(() => syncFromWallet(realBalances ?? {}), 500);
              }}
              title="Refresh balances from your connected wallet"
            >
              Sync wallet
            </button>
          )}
          <button type="button" className="paper-btn paper-btn-sm" onClick={stop}>
            Pause
          </button>
          <button type="button" className="paper-btn paper-btn-sm paper-btn-danger" onClick={reset}>
            Reset
          </button>
        </div>
      </div>
      <div className="paper-stats">
        <div className="paper-stat">
          <span className="paper-stat-label">Start</span>
          <span className="paper-stat-value">
            ${(startValue ?? 0).toFixed(2)}
            {seedSource === 'wallet' && (
              <span className="paper-stat-hint"> (from wallet)</span>
            )}
          </span>
          {seedSource === 'wallet' && paperPortfolio && (
            <span className="paper-stat-breakdown" title="Sum of (each holding × price) when you started">
              = {ASSETS.filter((a) => (paperPortfolio[a.id] ?? 0) > 0.0001)
                .map((a) => `${a.symbol} (${(paperPortfolio[a.id] ?? 0).toFixed(2)})`)
                .join(' + ')} at start prices
            </span>
          )}
        </div>
        <div className="paper-stat">
          <span className="paper-stat-label">Current</span>
          <span className="paper-stat-value">${(currentValue ?? 0).toFixed(2)}</span>
        </div>
        <div className="paper-stat">
          <span className="paper-stat-label">P&L</span>
          <span className={`paper-stat-value ${pnl >= 0 ? 'positive' : 'negative'}`}>
            {pnl >= 0 ? '+' : ''}${pnl.toFixed(2)} ({pnlPct >= 0 ? '+' : ''}{pnlPct.toFixed(2)}%)
          </span>
        </div>
        <div className="paper-stat">
          <span className="paper-stat-label">Trades</span>
          <span className="paper-stat-value">{tradeHistory.length}</span>
        </div>
      </div>
      <div className="paper-portfolio">
        <strong>Virtual balances (USDC & MATIC):</strong>
        <div className="paper-balances paper-balances-polygon">
          {TRADING_ASSETS.map((asset) => {
            const bal = paperPortfolio?.[asset.id] ?? 0;
            const price = prices?.[asset.id] ?? 0;
            const valueUsd = bal * price;
            return (
              <span
                key={asset.id}
                className={`paper-balance ${bal < 0.0001 ? 'muted' : ''}`}
              >
                {asset.symbol}: {bal.toFixed(4)}
                {price > 0 && (
                  <span className="paper-balance-usd"> ≈ ${valueUsd.toFixed(2)}</span>
                )}
              </span>
            );
          })}
        </div>
        {ASSETS.filter((a) => a.chain === 'ethereum').some((a) => (paperPortfolio?.[a.id] ?? 0) > 0.0001) && (
          <>
            <strong className="paper-portfolio-eth-label">Ethereum:</strong>
            <div className="paper-balances paper-balances-eth">
              {ASSETS.filter((a) => a.chain === 'ethereum').map((asset) => {
                const bal = paperPortfolio?.[asset.id] ?? 0;
                if (bal < 0.0001) return null;
                return (
                  <span key={asset.id} className="paper-balance paper-balance-eth">
                    {asset.symbol}: {bal.toFixed(4)}
                  </span>
                );
              })}
            </div>
          </>
        )}
      </div>
      {tradeHistory.length > 0 && (
        <div className="paper-history">
          <strong>Recent trades:</strong>
          <ul>
            {tradeHistory.slice(-5).reverse().map((t, i) => (
              <li key={t.time + i}>
                {t.desc} · P&L: {t.pnl >= 0 ? '+' : ''}${t.pnl.toFixed(2)}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
