export interface Regime {
  id: string;
  name: string;
  status: string;
  rationale?: string | null;
  action: {
    symbol: string;
    side: 'BUY' | 'SELL';
    size_pct_of_buying_power?: number | null;
    hold_days?: number | null;
  } | null;
}

export interface SignalsResponse {
  as_of: string;
  regimes: Regime[];
}

export interface Position {
  conid: number;
  ticker?: string;
  position: number;
  mktValue?: number;
  unrealizedPnl?: number;
}

export interface IBKRAuthStatus {
  authenticated: boolean;
  connected: boolean;
  competing: boolean;
}

export interface OrderRequest {
  symbol: string;
  side: 'BUY' | 'SELL';
  quantity: number;
  order_type: 'MKT' | 'LMT';
  limit_price?: number;
  tif?: 'DAY' | 'GTC' | 'IOC';
}

export type Brokerage = 'qc_paper' | 'ibkr' | 'alpaca_paper' | 'alpaca_live';

export const BROKERAGE_LABEL: Record<Brokerage, string> = {
  qc_paper: 'QC paper',
  ibkr: 'Interactive Brokers',
  alpaca_paper: 'Alpaca paper',
  alpaca_live: 'Alpaca live'
};

export interface Strategy {
  id: string;
  qc_project_id: number;
  label: string;
  brokerage: Brokerage;
}

export interface Holding {
  symbol: string;
  quantity: number;
  market_value?: number | null;
  unrealized_pnl?: number | null;
}

export interface SyncResponse {
  strategy: Strategy;
  qc_live_state: string;
  holdings: Holding[];
  equity: number | null;
}
