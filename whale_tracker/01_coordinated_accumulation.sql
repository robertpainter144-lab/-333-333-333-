-- Coordinated accumulation signal (DuneSQL / Trino dialect)
--
-- Fires when >= N distinct "smart money" wallets buy the same token
-- within a rolling window, with a minimum aggregate USD size.
--
-- Assumes: you have a curated smart-money list. Cheapest bootstrap is to
-- paste ~200 addresses into the VALUES block below (from Nansen exports,
-- Arkham entity dumps, or the self-derived query in 02_derive_smart_money.sql).
--
-- Tune knobs at top. Runs on Dune's free tier.

WITH params AS (
    SELECT
        INTERVAL '24' HOUR       AS lookback,
        CAST(3   AS INTEGER)     AS min_distinct_wallets,
        CAST(100000 AS DOUBLE)   AS min_agg_usd,
        CAST(2500   AS DOUBLE)   AS min_single_buy_usd  -- drops dust / test txs
),

-- Bring-your-own smart money list. Swap this VALUES block for a query
-- against a Dune table you maintain, or a Spellbook table if you promote it.
smart_money (address) AS (
    VALUES
        (0x0000000000000000000000000000000000000001),
        (0x0000000000000000000000000000000000000002)
        -- paste more addresses here, one per line, comma-separated
),

recent_buys AS (
    SELECT
        t.block_time,
        t.blockchain,
        t.token_bought_address                  AS token,
        t.token_bought_symbol                   AS symbol,
        t.taker                                 AS wallet,
        t.amount_usd
    FROM dex.trades t
    JOIN smart_money sm ON sm.address = t.taker
    CROSS JOIN params p
    WHERE t.block_time >= NOW() - p.lookback
      AND t.amount_usd >= p.min_single_buy_usd
      -- exclude stablecoin<->stablecoin and ETH<->WETH noise
      AND t.token_bought_symbol NOT IN (
          'USDC','USDT','DAI','FRAX','LUSD','GUSD','TUSD',       -- fiat stables
          'USDe','sUSDe','PYUSD','USDD','crvUSD','GHO','FDUSD',  -- newer stables
          'WETH','ETH','WBTC','BTC',                             -- majors + wrappers
          'stETH','wstETH','rETH','cbETH','weETH'                -- LSTs
      )
),

per_token AS (
    SELECT
        blockchain,
        token,
        MAX(symbol)                             AS symbol,
        COUNT(DISTINCT wallet)                  AS distinct_wallets,
        SUM(amount_usd)                         AS agg_usd,
        MIN(block_time)                         AS first_buy,
        MAX(block_time)                         AS last_buy,
        ARRAY_AGG(DISTINCT wallet)              AS wallets
    FROM recent_buys
    GROUP BY blockchain, token
)

SELECT
    blockchain,
    symbol,
    token,
    distinct_wallets,
    agg_usd,
    first_buy,
    last_buy,
    DATE_DIFF('minute', first_buy, last_buy)    AS accum_window_min,
    wallets
FROM per_token, params
WHERE distinct_wallets >= params.min_distinct_wallets
  AND agg_usd          >= params.min_agg_usd
ORDER BY distinct_wallets DESC, agg_usd DESC
LIMIT 100;
