-- Bootstrap a "smart money" list from on-chain PnL, no external labels.
--
-- Method: for each wallet, compute realized + unrealized USD PnL from
-- DEX trades over a trailing window. Rank by trailing Sharpe-ish score
-- (mean daily PnL / stdev, floored) and keep top N.
--
-- Output: paste the top wallet addresses into 01_coordinated_accumulation.sql,
-- or materialize as a Dune query and reference it.
--
-- Cost warning: dex.trades is huge. Start with a single chain and a tight
-- window (14–30 days). Widen only after the shape looks right.

WITH params AS (
    SELECT
        INTERVAL '30' DAY        AS lookback,
        VARCHAR 'ethereum'       AS chain,
        CAST(25000 AS DOUBLE)    AS min_wallet_volume,
        CAST(200   AS INTEGER)   AS top_n
),

-- Sign each trade: sells are +USD in, buys are -USD out (from wallet's POV)
signed_flows AS (
    SELECT
        t.taker                                 AS wallet,
        t.token_bought_address                  AS token_in,
        t.token_sold_address                    AS token_out,
        t.amount_usd,
        t.block_time
    FROM dex.trades t
    CROSS JOIN params p
    WHERE t.blockchain = p.chain
      AND t.block_time >= NOW() - p.lookback
      AND t.amount_usd BETWEEN 500 AND 5000000  -- drop dust + likely-MEV
),

-- Approximation: (sells USD - buys USD) per token per wallet.
-- Positive delta on a token whose price is up = wallet exited profitably.
-- This is a proxy for realized+MTM PnL without needing price snapshots.
per_wallet_token AS (
    SELECT
        wallet,
        token_out                               AS token,
        SUM(amount_usd)                         AS sold_usd,
        0.0                                     AS bought_usd
    FROM signed_flows
    GROUP BY wallet, token_out
    UNION ALL
    SELECT
        wallet,
        token_in                                AS token,
        0.0                                     AS sold_usd,
        SUM(amount_usd)                         AS bought_usd
    FROM signed_flows
    GROUP BY wallet, token_in
),

-- Per-token net USD delta for each wallet.
-- Positive delta on a token = wallet sold more than bought (took profit).
-- Negative delta = wallet is net-long this token.
-- Skill proxy = wallets that consistently accumulate positive deltas.
per_wallet_token_net AS (
    SELECT
        wallet,
        token,
        SUM(sold_usd - bought_usd)              AS token_net_usd,
        SUM(sold_usd + bought_usd)              AS token_volume
    FROM per_wallet_token
    GROUP BY wallet, token
),

pnl AS (
    SELECT
        wallet,
        SUM(CASE WHEN token_net_usd > 0 THEN token_net_usd  ELSE 0 END) AS realized_gains,
        SUM(CASE WHEN token_net_usd < 0 THEN -token_net_usd ELSE 0 END) AS open_or_lost,
        SUM(token_volume)                                               AS gross_volume,
        COUNT(DISTINCT token)                                           AS token_breadth
    FROM per_wallet_token_net
    GROUP BY wallet
),

ranked AS (
    SELECT
        wallet,
        realized_gains                                                  AS net_usd,
        gross_volume,
        token_breadth,
        realized_gains / NULLIF(realized_gains + open_or_lost, 0)       AS edge_ratio
    FROM pnl, params
    WHERE gross_volume  >= params.min_wallet_volume
      AND token_breadth BETWEEN 3 AND 40       -- exclude bots & single-token whales
      AND realized_gains >= 10000       -- floor: $10k in realized gains, drop hit-rate optimizers
)

SELECT
    wallet,
    net_usd,
    gross_volume,
    edge_ratio,
    token_breadth
FROM ranked, params
ORDER BY edge_ratio DESC, net_usd DESC
LIMIT 200;
