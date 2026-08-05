import { TRADING_ASSETS } from '../constants/contracts';

const GAS_PER_SWAP_USD = 0.05;
const MIN_GAIN_PCT = 0.1; // Assume 0.1% min expected gain per trade for profit > gas

function computeMinInvestment(gasUsd, minGainPct) {
  return gasUsd / (minGainPct / 100);
}

export function PolygonBalancesPanel({ balances, prices }) {
  if (!balances || Object.keys(balances).length === 0) {
    return (
      <div className="polygon-panel">
        <h3>USDC & MATIC (Polygon) — real wallet</h3>
        <p className="polygon-desc">Connect your wallet to see USDC & MATIC balances and min. investment guidance.</p>
      </div>
    );
  }

  const polygonBalances = {};
  let portfolioUsd = 0;
  for (const asset of TRADING_ASSETS) {
    let bal = balances[asset.id] ?? 0;
    if (asset.id === 'matic-polygon') bal += balances['wmatic-polygon'] ?? 0;
    const price = prices?.[asset.id] ?? prices?.['wmatic-polygon'] ?? 0;
    polygonBalances[asset.id] = bal;
    portfolioUsd += bal * price;
  }

  const minInvestment = computeMinInvestment(GAS_PER_SWAP_USD, MIN_GAIN_PCT);
  const meetsMinimum = portfolioUsd >= minInvestment;

  return (
    <div className="polygon-panel">
      <h3>USDC & MATIC (Polygon) — real wallet</h3>
      <div className="polygon-balances">
        {TRADING_ASSETS.map((asset) => {
          const bal = polygonBalances[asset.id] ?? 0;
          const price = prices?.[asset.id] ?? prices?.['wmatic-polygon'] ?? 0;
          const valueUsd = bal * price;
          return (
            <div key={asset.id} className="polygon-balance-row">
              <span className="polygon-token">
                {asset.symbol}:
              </span>
              <span className="polygon-amount">
                {bal.toFixed(4)}
              </span>
              {price > 0 && (
                <span className="polygon-value">
                  ≈ ${valueUsd.toFixed(2)}
                </span>
              )}
            </div>
          );
        })}
      </div>
      <div className="polygon-summary">
        <div className="polygon-stat">
          <span className="polygon-stat-label">Portfolio (USD)</span>
          <span className="polygon-stat-value">${portfolioUsd.toFixed(2)}</span>
        </div>
        <div className="polygon-stat">
          <span className="polygon-stat-label">Gas per swap</span>
          <span className="polygon-stat-value">~${GAS_PER_SWAP_USD.toFixed(2)}</span>
        </div>
      </div>
      <div className="polygon-min-investment">
        <strong>Min. investment for profit &gt; gas:</strong>
        <p>
          At ~{MIN_GAIN_PCT}% expected gain per trade, invest at least{' '}
          <strong>${minInvestment.toFixed(2)}</strong> so profit exceeds gas (~${GAS_PER_SWAP_USD.toFixed(2)}).
        </p>
        <p className={meetsMinimum ? 'polygon-ok' : 'polygon-warning'}>
          {meetsMinimum
            ? `✓ Your portfolio ($${portfolioUsd.toFixed(2)}) meets the minimum. Trades can be profitable.`
            : `⚠ Portfolio below minimum. Consider adding ~$${Math.max(0, minInvestment - portfolioUsd).toFixed(2)} more to make small gains worthwhile.`}
        </p>
      </div>
    </div>
  );
}
