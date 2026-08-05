// Token contract addresses for MetaMask balance fetching
export const NETWORKS = {
  ETHEREUM: {
    chainId: '0x1',
    name: 'Ethereum Mainnet',
    rpcUrl: 'https://eth.llamarpc.com',
  },
  POLYGON: {
    chainId: '0x89',
    name: 'Polygon Mainnet',
    rpcUrl: 'https://polygon-rpc.com',
  },
};

export const TOKENS = {
  USDC_ETH: {
    id: 'usdc-eth',
    name: 'USDC (Ethereum)',
    symbol: 'USDC',
    chain: 'ethereum',
    address: '0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48',
    decimals: 6,
    coinpaprikaId: 'usdc-usd-coin',
  },
  USDC_POLYGON: {
    id: 'usdc-polygon',
    name: 'USDC (Polygon)',
    symbol: 'USDC',
    chain: 'polygon',
    address: '0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359',
    decimals: 6,
    coinpaprikaId: 'usdc-usd-coin',
  },
  MATIC_POLYGON: {
    id: 'matic-polygon',
    name: 'MATIC (Polygon)',
    symbol: 'MATIC',
    chain: 'polygon',
    address: null,
    decimals: 18,
    coinpaprikaId: 'matic-polygon',
  },
  WMATIC_POLYGON: {
    id: 'wmatic-polygon',
    name: 'WMATIC (Polygon)',
    symbol: 'WMATIC',
    chain: 'polygon',
    address: '0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270',
    decimals: 18,
    coinpaprikaId: 'matic-polygon',
  },
  MATIC_ETH: {
    id: 'matic-eth',
    name: 'MATIC (Ethereum)',
    symbol: 'WMATIC',
    chain: 'ethereum',
    address: '0x7D1AfA7B718fb893dB30A3aBc0Cfc608AaCfeBB0',
    decimals: 18,
    coinpaprikaId: 'matic-polygon',
  },
  ETH: {
    id: 'eth',
    name: 'Ethereum',
    symbol: 'ETH',
    chain: 'ethereum',
    address: null,
    decimals: 18,
    coinpaprikaId: 'eth-ethereum',
  },
  WETH_POLYGON: {
    id: 'weth-polygon',
    name: 'WETH (Polygon)',
    symbol: 'WETH',
    chain: 'polygon',
    address: '0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619',
    decimals: 18,
    coinpaprikaId: 'eth-ethereum',
  },
  WBTC_POLYGON: {
    id: 'wbtc-polygon',
    name: 'WBTC (Polygon)',
    symbol: 'WBTC',
    chain: 'polygon',
    address: '0x1BFD67037B42Cf73acF2047067bd4F2C47D9BfD6',
    decimals: 8,
    coinpaprikaId: 'btc-bitcoin',
  },
};

export const ASSETS = [
  TOKENS.USDC_POLYGON,
  TOKENS.MATIC_POLYGON,
  TOKENS.WMATIC_POLYGON,
  TOKENS.WETH_POLYGON,
  TOKENS.WBTC_POLYGON,
  TOKENS.USDC_ETH,
  TOKENS.MATIC_ETH,
  TOKENS.ETH,
];

// Polygon-only assets (Ethereum minimized for now)
export const POLYGON_ASSETS = [
  TOKENS.USDC_POLYGON,
  TOKENS.MATIC_POLYGON,
  TOKENS.WETH_POLYGON,
  TOKENS.WBTC_POLYGON,
];

// Focus: USDC and MATIC on Polygon for trading
export const TRADING_ASSETS = [TOKENS.USDC_POLYGON, TOKENS.MATIC_POLYGON];

// QuickSwap on Polygon (Uniswap V2 fork)
export const QUICKSWAP_ROUTER = '0xa5E0829CaCEd8fFDD4De3c43696c57F7D7A678ff';
