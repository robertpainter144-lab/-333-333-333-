-- Raw ERC-20 transfer feed for curated whale list.
--
-- Unlike Q03 (DEX takers), this catches every large token movement — CEX
-- deposits, OTC settlements, VC treasury moves, bridge hops. Market makers
-- and VC funds show up here even when they never touch a public DEX.
--
-- Both "from" and "to" sides of the transfer are surfaced, so we see both
-- what leaves and what enters each tracked wallet.

WITH params AS (
    SELECT
        INTERVAL '7' DAY         AS lookback,
        CAST(50000 AS DOUBLE)    AS min_transfer_usd
),

curated (address) AS (
    VALUES
        (0x0000000000000000000000000000000000000001),
        (0x0000000000000000000000000000000000000002)
        -- spliced in from curated_wallets.py by run_curated.py
),

-- Transfers where our wallet is the sender OR receiver
outbound AS (
    SELECT
        tr.evt_block_time                       AS block_time,
        'ethereum'                              AS blockchain,
        'OUT'                                   AS direction,
        tr."from"                               AS our_wallet,
        tr.to                                   AS counterparty,
        tr.contract_address                     AS token,
        tr.value                                AS raw_value,
        tr.evt_tx_hash                          AS tx_hash
    FROM erc20_ethereum.evt_Transfer tr
    JOIN curated c ON c.address = tr."from"
    CROSS JOIN params p
    WHERE tr.evt_block_time >= NOW() - p.lookback
),
inbound AS (
    SELECT
        tr.evt_block_time                       AS block_time,
        'ethereum'                              AS blockchain,
        'IN'                                    AS direction,
        tr.to                                   AS our_wallet,
        tr."from"                               AS counterparty,
        tr.contract_address                     AS token,
        tr.value                                AS raw_value,
        tr.evt_tx_hash                          AS tx_hash
    FROM erc20_ethereum.evt_Transfer tr
    JOIN curated c ON c.address = tr.to
    CROSS JOIN params p
    WHERE tr.evt_block_time >= NOW() - p.lookback
),
all_flows AS (
    SELECT * FROM outbound
    UNION ALL
    SELECT * FROM inbound
),

-- Join token metadata for symbol + decimals, and price for USD
priced AS (
    SELECT
        f.block_time,
        f.blockchain,
        f.direction,
        f.our_wallet,
        f.counterparty,
        f.token,
        e.symbol                                AS token_symbol,
        f.raw_value / POWER(10, e.decimals)     AS token_amount,
        (f.raw_value / POWER(10, e.decimals)) * p.price AS amount_usd,
        f.tx_hash
    FROM all_flows f
    LEFT JOIN tokens.erc20 e
      ON e.blockchain = f.blockchain
     AND e.contract_address = f.token
    LEFT JOIN prices.usd p
      ON p.blockchain = f.blockchain
     AND p.contract_address = f.token
     AND p.minute = DATE_TRUNC('minute', f.block_time)
)

SELECT
    block_time,
    direction,
    our_wallet,
    counterparty,
    token_symbol,
    token_amount,
    amount_usd,
    tx_hash
FROM priced, params
WHERE amount_usd >= params.min_transfer_usd
ORDER BY block_time DESC
LIMIT 500;
