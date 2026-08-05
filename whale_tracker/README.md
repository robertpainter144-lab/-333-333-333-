# whale_tracker

DuneSQL queries for the coordinated-accumulation signal.

## Workflow

1. **Bootstrap the smart-money list.** Run `02_derive_smart_money.sql` on
   Dune. Copy the top ~200 wallets from the result set.
2. **Backtest the signal.** Paste those addresses into the `smart_money`
   VALUES block in `01_coordinated_accumulation.sql` and run it. This is
   the "would it have fired" view — change the lookback to a historical
   range to inspect specific dates.
3. **Only after step 2 looks tradeable**, wire it to a live feed:
   - `cp dune.env.example dune.env` and fill in `DUNE_API_KEY` +
     the `DUNE_QUERY_ID` from your saved Dune query.
   - `python3 runner.py` for a single poll, or `--loop` for a 15min
     cadence loop. Alerts go to Pushover via `~/ibkr_bot/notify.py`.
   - For 24/7, add a launchd plist that runs `runner.py` every 15min.
     Deduped via `state/seen.json` (chain+token+hour).

## Signal shape

The default fires when 3+ smart wallets buy the same token within 24h
for >= $100k aggregate, with each buy >= $2.5k. All three knobs live at
the top of the query.

## Gotchas

- `dex.trades` misses OTC and CEX flow. This is a DEX-only signal.
- Rebase / rebasing wrapper tokens will show phantom buys — filter by
  `symbol` or maintain a blocklist.
- Re-rank the smart-money list weekly. Wallet edge decays fast.
- The `net_usd` proxy in query 02 ignores unrealized inventory that
  never crossed a DEX. Wallets that hold forever will look flat. That's
  fine for this use case (we want wallets that both accumulate *and*
  distribute skillfully).
