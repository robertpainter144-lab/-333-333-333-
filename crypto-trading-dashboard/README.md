# Crypto Trading Dashboard

A MetaMask-connected dashboard for monitoring **USDC**, **MATIC (Polygon)**, **MATIC (Ethereum)**, and **ETH**. Features real-time candlestick charts with price spread at each poll and a decision engine that suggests BUY, SELL, or HOLD for each asset.

## Features

- **MetaMask Integration** – Connect your wallet to view balances for all 4 assets
- **Four Candlestick Charts** – One per asset, each showing OHLC candles with spread derived from price polls
- **Price Polling** – Fetches prices every 5 seconds via CoinGecko API; candles aggregate into 30-second buckets
- **Decision Engine** – Analyzes momentum and spread to suggest BUY, SELL, or HOLD for cross-asset trading

## Quick Start

```bash
npm install
npm run dev
```

Open [http://localhost:5173](http://localhost:5173) and connect MetaMask.

## Project Structure

```
src/
├── constants/contracts.js   # Token addresses, networks
├── hooks/
│   ├── useWallet.js         # MetaMask connection & balances
│   ├── usePrices.js         # Price polling (CoinGecko)
│   └── useCandleData.js     # OHLC candle aggregation
├── components/
│   ├── CandlestickChart.jsx # Lightweight Charts candlestick
│   └── DecisionPanel.jsx    # BUY/SELL/HOLD display
├── engine/
│   └── decisionEngine.js    # Trading signal logic
└── App.jsx
```

## Assets

| Asset        | Chain    | Notes                        |
|-------------|----------|------------------------------|
| USDC        | Ethereum | Stablecoin                   |
| MATIC       | Polygon  | Native token                 |
| WMATIC      | Ethereum | Bridged MATIC                |
| ETH         | Ethereum | Native token                 |

## Decision Engine

Uses a simple momentum + mean-reversion strategy:

- **BUY** – Price up >0.2% over last ~3 minutes
- **SELL** – Price down >0.2% and you hold enough to sell
- **HOLD** – Otherwise; also for USDC when stable

You can customize the logic in `src/engine/decisionEngine.js`.

## Environment

No environment variables required. CoinGecko free API is used for prices. For production, consider adding an API key or switching to Chainlink/DEX feeds.
