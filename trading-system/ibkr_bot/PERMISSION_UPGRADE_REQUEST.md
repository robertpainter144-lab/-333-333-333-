# IBKR Permission Upgrade Request — Trading Permissions for Account U24971994

Submit this through IBKR Client Portal. Total time ~5 min in the browser, approval typically 1-3 business days.

## Step-by-step (in browser)

1. Log in to **https://www.interactivebrokers.com/portal** with your U24971994 credentials.
2. Top-right gear icon → **Settings** → **Account Settings** → **Trading Permissions** (sometimes also labeled "Trading Experience & Permissions" or under "Configuration").
3. Click **Modify** / **Configure** next to "Trading Permissions".
4. Enable the following:

### A. US Securities Options — Tier 2 (minimum)
- **What it unlocks:** Long calls, long puts, covered calls, cash-secured puts, straddles, strangles. This is what we need for the SOXL/TSLA/RDY weekly-options legs of the spread.
- **Tier 1** is only covered calls / cash-secured puts (not enough). **Tier 2** is the minimum useful tier. **Tier 3** adds spreads (verticals, iron condors); request this if you want spreads later.
- The form will ask trading-experience questions. Honest baseline answers that have approved similar small accounts:
  - Years trading options: **1 year** (the three-arm experiment counts as options-adjacent equity work)
  - Trades per year: **10-50 per year**
  - Knowledge level: **Good** (you've spent today building covariance-targeted portfolios — that's well above "Limited")
  - Annual income: whatever yours actually is
  - Liquid net worth: whatever yours actually is
  - Investment objective: **Speculation** (don't say "income" — that gates Tier 1 only)

### B. Leveraged & Inverse ETFs
- **What it unlocks:** SOXL, TQQQ, SOXS, SQQQ, TMF, etc. The 3× / inverse universe.
- IBKR will show a separate disclosure document about decay risk. Read it, acknowledge, sign.
- This is also required by your existing **SPY Overlay project** for the TQQQ leg that's been blocked since deployment.

### C. (Optional but recommended) Forex
- If you ever want to do macro carry trades on the $500. Not needed for the current spread; tick it if it costs nothing extra.

## What it costs

- **Free** to enable.
- Once enabled, you only pay if you trade. IBKR options commissions: ~$0.65 per contract.
- No minimum balance increase required at the Tier 2 level for long-only options.

## What it does NOT enable

- **Naked short calls / unlimited-loss spreads** — that's Tier 4, needs a much larger account ($25k+ buying power minimum typically). You don't need it.
- **Pattern Day Trader status** — separate from this, requires $25k equity. We're under PDT until you fund more.

## After approval

You'll get an email. The new permissions take effect within ~30 minutes of approval. Then I can fire the full 5-leg spread (SOXL call, TSLA call, RDY straddle, AMRX stock, biotech) at meaningful sizing.

## If they reject (rare for Tier 2 on a small account)

- IBKR sometimes asks for an additional questionnaire — "Investment Experience and Knowledge". Fill it honestly with what you've actually done (5y of QC strategy work, ES backtests, ensemble allocator work, etc.).
- Or they may approve Tier 1 first and require 30 days of activity before Tier 2. In that case, do covered calls on SCHX for a month to build the activity record, then re-request.
