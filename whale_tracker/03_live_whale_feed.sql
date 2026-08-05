-- Live whale-trade feed.
--
-- Every DEX trade in the last 7 days by wallets on our smart-money list,
-- sorted by USD size, with entity labels where Dune has them.
--
-- This is the "raw pattern-recognition surface" — not a trigger. You watch
-- this, notice recurring wallets buying the same token, notice a market
-- maker rotating out of one sector into another, notice a VC accumulating
-- before a launch, etc.
--
-- Sort order: most recent big trades first. Change ORDER BY to amount_usd
-- DESC to see the biggest trades regardless of when they happened.

WITH params AS (
    SELECT
        INTERVAL '7' DAY         AS lookback,
        CAST(25000 AS DOUBLE)    AS min_trade_usd
),

-- Smart money list (spliced in by bootstrap.py from query 02 results)
smart_money (address) AS (
    VALUES
        (0x0000000000000000000000000000000000000001),
        (0x0000000000000000000000000000000000000002)
        -- paste more addresses here, one per line, comma-separated
),

feed AS (
    SELECT
        t.block_time,
        t.blockchain,
        t.taker                                 AS wallet,
        t.token_bought_symbol                   AS bought_symbol,
        t.token_bought_address                  AS bought_token,
        t.token_sold_symbol                     AS sold_symbol,
        t.token_sold_address                    AS sold_token,
        t.amount_usd,
        t.project                               AS dex,
        t.tx_hash
    FROM dex.trades t
    JOIN smart_money sm ON sm.address = t.taker
    CROSS JOIN params p
    WHERE t.block_time >= NOW() - p.lookback
      AND t.amount_usd >= p.min_trade_usd
)

SELECT
    block_time,
    blockchain,
    wallet,
    '(unlabeled)'                                AS entity,      -- placeholder col
    CAST(NULL AS VARCHAR)                        AS entity_type,
    CASE
        WHEN sold_symbol   IN ('USDC','USDT','DAI','USDe','ETH','WETH')
             THEN 'BUY '  || bought_symbol
        WHEN bought_symbol IN ('USDC','USDT','DAI','USDe','ETH','WETH')
             THEN 'SELL ' || sold_symbol
        ELSE                                       'SWAP ' || sold_symbol || '->' || bought_symbol
    END                                          AS action,
    bought_symbol,
    sold_symbol,
    amount_usd,
    dex,
    tx_hash
FROM feed
ORDER BY block_time DESC
LIMIT 500;
