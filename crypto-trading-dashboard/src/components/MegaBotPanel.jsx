import { useState, useEffect, useCallback } from 'react';

const API_BASE = ''; // Use Vite proxy to localhost:8000

export function MegaBotPanel() {
  const [paperStatus, setPaperStatus] = useState(null);
  const [strategyData, setStrategyData] = useState(null);
  const [connected, setConnected] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [activeChart, setActiveChart] = useState('trade_signals_interactive.html');

  const fetchPaper = useCallback(async () => {
    try {
      const r = await fetch(`${API_BASE}/api/paper/status`);
      if (r.ok) {
        const data = await r.json();
        setPaperStatus(data);
        setConnected(true);
        setError(null);
      }
    } catch (e) {
      setConnected(false);
      setError(e.message);
    }
  }, []);

  const fetchStrategy = useCallback(async () => {
    try {
      const r = await fetch(`${API_BASE}/api/strategy/consistency`);
      if (r.ok) {
        const data = await r.json();
        setStrategyData(data);
      }
    } catch {
      // Strategy endpoint may not exist
    }
  }, []);

  useEffect(() => {
    let mounted = true;
    const load = async () => {
      setLoading(true);
      await fetchPaper();
      await fetchStrategy();
      setLoading(false);
    };
    load();
    const id = setInterval(() => {
      if (mounted) {
        fetchPaper();
        fetchStrategy();
      }
    }, 5000);
    return () => {
      mounted = false;
      clearInterval(id);
    };
  }, [fetchPaper, fetchStrategy]);

  const startPaper = async () => {
    try {
      await fetch(`${API_BASE}/api/paper/start`, { method: 'POST' });
      await fetchPaper();
    } catch (e) {
      setError(e.message);
    }
  };

  const stopPaper = async () => {
    try {
      await fetch(`${API_BASE}/api/paper/stop`, { method: 'POST' });
      await fetchPaper();
    } catch (e) {
      setError(e.message);
    }
  };

  if (loading && !paperStatus && !error) {
    return (
      <div className="mega-bot-panel">
        <p className="mega-bot-loading">Checking Ultimate-Mega-Trading-Bot (localhost:8000)…</p>
      </div>
    );
  }

  if (!connected && error) {
    return (
      <div className="mega-bot-panel">
        <p className="mega-bot-offline">
          Ultimate-Mega-Trading-Bot backend not running. Start it with: <code>cd Ultimate-Mega-Trading-Bot && ./run.sh</code>
        </p>
      </div>
    );
  }

  const startVal = paperStatus?.config?.initial_usdc ?? 100;
  const pnl = paperStatus?.equity_usdc != null ? paperStatus.equity_usdc - startVal : null;
  const pnlPct = paperStatus?.pnl_pct != null ? paperStatus.pnl_pct * 100 : (pnl != null && startVal > 0 ? (pnl / startVal) * 100 : null);

  return (
    <div className="mega-bot-panel">
      <div className="mega-bot-paper">
        <h4>Paper Trading (Backend)</h4>
        {paperStatus?.running === true ? (
          <>
            <div className="mega-bot-stats">
              <span>Equity: ${(paperStatus.equity_usdc ?? 0).toFixed(2)}</span>
              <span className={pnl >= 0 ? 'positive' : 'negative'}>
                P&L: {pnl != null ? `${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)}` : '-'}
                {pnlPct != null ? ` (${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(1)}%)` : ''}
              </span>
              <span>Ticks: {paperStatus.tick_count ?? 0}</span>
            </div>
            <div className="mega-bot-actions">
              <button type="button" className="paper-btn paper-btn-sm paper-btn-danger" onClick={stopPaper}>
                Stop
              </button>
            </div>
          </>
        ) : (
          <div className="mega-bot-actions">
            <button type="button" className="paper-btn paper-btn-primary" onClick={startPaper}>
              Start Paper Trading
            </button>
          </div>
        )}
      </div>

      {strategyData?.available && (
        <div className="mega-bot-strategy">
          <h4>BTC Perps Strategy</h4>
          <div className="mega-bot-strategy-grid">
            {strategyData.headline_metrics?.sharpe != null && <span>Sharpe: {Number(strategyData.headline_metrics.sharpe).toFixed(2)}</span>}
            {strategyData.headline_metrics?.max_drawdown != null && <span>Max DD: {(Number(strategyData.headline_metrics.max_drawdown) * 100).toFixed(1)}%</span>}
            {strategyData.latest_bar?.regime_score != null && <span>Regime: {Number(strategyData.latest_bar.regime_score).toFixed(2)}</span>}
          </div>
        </div>
      )}

      <div className="mega-bot-charts">
        <h4>BTC Perps Charts</h4>
        <div className="mega-bot-chart-tabs">
          <button
            type="button"
            className={activeChart === 'trade_signals_interactive.html' ? 'active' : ''}
            onClick={() => setActiveChart('trade_signals_interactive.html')}
          >
            Trade Signals
          </button>
          <button
            type="button"
            className={activeChart === 'simple_extrema_interactive.html' ? 'active' : ''}
            onClick={() => setActiveChart('simple_extrema_interactive.html')}
          >
            Simple Extrema
          </button>
          <button
            type="button"
            className={activeChart === 'dashboard_sweep.html' ? 'active' : ''}
            onClick={() => setActiveChart('dashboard_sweep.html')}
          >
            Live Sweep
          </button>
        </div>
        <div className="mega-bot-chart-frame">
          <iframe
            title={activeChart}
            src={`${API_BASE}/btc-charts/${activeChart}`}
            className="mega-bot-iframe"
          />
        </div>
      </div>
    </div>
  );
}
