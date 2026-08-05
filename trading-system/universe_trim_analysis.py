#!/usr/bin/env python3.10
import yfinance as yf, numpy as np, pandas as pd, json

ALWAYS_KEEP = {"SPY", "QQQ", "TQQQ", "SOXL", "NVDA", "AAPL", "MSFT", "AMZN", "META", "TSLA"}

symbols = [
    "AAPL","MSFT","NVDA","GOOGL","AMZN","META","TSLA","AVGO","JPM","V",
    "LLY","XOM","MA","BAC","WMT","CVX","AMD","MCD","GE","NFLX","QCOM",
    "GS","MS","SBUX","AMAT","GILD","SYK","ISRG","VRTX","F","UBER",
    "COIN","PLTR","SOFI","MSTR","SMCI","CRWD","SNOW","SHOP","SE","HOOD",
    "APP","MU","ANET","PANW","DKNG","RBLX","TTD","PINS","DUOL","SQ",
    "QQQ","IWM","XLK","XLF","XLE","XLV","SOXL","TQQQ",
    "GLD","SLV","GDX","USO","UNG","WEAT","CORN","SOYB","DBA","PDBC",
    "EWJ","EWG","EWU","EWC","EWA","FXI","KWEB","EEM","EWZ","INDA",
    "EWH","EWT","EWY","VWO","IEMG","EWS",
    "EURUSD=X","GBPUSD=X","USDJPY=X","AUDUSD=X","USDCAD=X",
    "USDCHF=X","NZDUSD=X","USDMXN=X","USDBRL=X","USDCNY=X",
    "BTC-USD","ETH-USD","SOL-USD","BNB-USD","XRP-USD",
]

print(f"Analyzing {len(symbols)} symbols...")
results = []
failed = []

for sym in symbols:
    try:
        h = yf.download(sym, period="6mo", interval="1d", progress=False, auto_adjust=True)
        if len(h) < 60:
            print(f"  {sym}: only {len(h)} bars — skip")
            failed.append(sym)
            continue
        # Flatten multi-index columns if present
        if isinstance(h.columns, pd.MultiIndex):
            h.columns = h.columns.get_level_values(0)
        rets = h["Close"].pct_change().dropna()
        vol = h["Volume"] * h["Close"]
        avg_dvol = float(vol.mean())
        sharpe = float(rets.mean() / rets.std() * (252**0.5))
        max_dd = float((h["Close"] / h["Close"].cummax() - 1).min())
        results.append({
            "sym": sym,
            "sharpe": round(sharpe, 3),
            "avg_dvol": round(avg_dvol/1e6, 1),
            "max_dd": round(max_dd, 3),
        })
        print(f"  {sym:<12}  sharpe={sharpe:>+.2f}  dvol=${avg_dvol/1e6:>8.0f}M  max_dd={max_dd:.2%}")
    except Exception as e:
        print(f"  {sym}: ERROR {e}")
        failed.append(sym)

print(f"\n{'='*60}")
print(f"Analyzed: {len(results)}  Failed to fetch: {len(failed)}")

# Apply filter: remove if failing 2+ criteria
# Criterion A: avg_dvol > 200 (M)
# Criterion B: abs(max_dd) < 0.60
# Criterion C: sharpe > -0.5

removed = []
kept = []

for r in results:
    sym = r["sym"]
    fail_A = r["avg_dvol"] <= 200
    fail_B = abs(r["max_dd"]) >= 0.60
    fail_C = r["sharpe"] <= -0.5
    fails = int(fail_A) + int(fail_B) + int(fail_C)
    r["fail_dvol"] = fail_A
    r["fail_dd"]   = fail_B
    r["fail_sharpe"] = fail_C
    r["n_fails"]   = fails
    if sym in ALWAYS_KEEP:
        kept.append(sym)
        status = "KEEP (always)"
    elif fails >= 2:
        removed.append(sym)
        status = f"REMOVE ({fails} fails: {'dvol ' if fail_A else ''}{'dd ' if fail_B else ''}{'sharpe' if fail_C else ''})"
    else:
        kept.append(sym)
        status = f"KEEP  ({fails} fail)"
    print(f"  {sym:<14}  {status}")

# failed symbols are also removed from universe
for sym in failed:
    if sym not in ALWAYS_KEEP:
        removed.append(sym)
        print(f"  {sym:<14}  REMOVE (no data)")
    else:
        kept.append(sym)

print(f"\n{'='*60}")
print(f"Original: {len(symbols)}  Kept: {len(kept)}  Removed: {len(removed)}")
print(f"\nRemoved: {sorted(removed)}")
print(f"\nKept:    {sorted(kept)}")

# Save result
out = {
    "task": "universe_trim",
    "original_count": len(symbols),
    "new_count": len(kept),
    "removed": sorted(removed),
    "kept": sorted(kept),
    "analysis": results,
    "summary": (
        f"Trimmed universe from {len(symbols)} to {len(kept)} symbols. "
        f"Removed {len(removed)} symbols failing 2+ quality criteria "
        f"(avg daily dollar volume >$200M, max drawdown <60%, Sharpe >-0.5). "
        f"Always-keep symbols preserved: {sorted(ALWAYS_KEEP & set(kept))}."
    ),
    "success": True,
}
with open("/Users/quarki3/swarm_results/completed/universe_trim.json", "w") as f:
    json.dump(out, f, indent=2)
print("\nSaved to /Users/quarki3/swarm_results/completed/universe_trim.json")

# Print the new list formatted for mock_portfolio.py
print("\n--- New SCANNER_UNIVERSE (copy into mock_portfolio.py) ---")
print("SCANNER_UNIVERSE = [")
# Group by category
us_large = [s for s in kept if s not in {"QQQ","IWM","XLK","XLF","XLE","XLV","SOXL","TQQQ","GLD","SLV","GDX","USO","UNG","WEAT","CORN","SOYB","DBA","PDBC","EWJ","EWG","EWU","EWC","EWA","FXI","KWEB","EEM","EWZ","INDA","EWH","EWT","EWY","VWO","IEMG","EWS"} and "=X" not in s and "-USD" not in s]
etfs      = [s for s in kept if s in {"QQQ","IWM","XLK","XLF","XLE","XLV","SOXL","TQQQ"}]
comm      = [s for s in kept if s in {"GLD","SLV","GDX","USO","UNG","WEAT","CORN","SOYB","DBA","PDBC"}]
intl      = [s for s in kept if s in {"EWJ","EWG","EWU","EWC","EWA","FXI","KWEB","EEM","EWZ","INDA","EWH","EWT","EWY","VWO","IEMG","EWS"}]
forex     = [s for s in kept if "=X" in s]
crypto    = [s for s in kept if "-USD" in s]
if us_large: print(f"    # US large-cap / momentum\n    {','.join(repr(s) for s in us_large)},")
if etfs:     print(f"    # US ETFs\n    {','.join(repr(s) for s in etfs)},")
if comm:     print(f"    # Commodities ETFs\n    {','.join(repr(s) for s in comm)},")
if intl:     print(f"    # International\n    {','.join(repr(s) for s in intl)},")
if forex:    print(f"    # FOREX\n    {','.join(repr(s) for s in forex)},")
if crypto:   print(f"    # Crypto\n    {','.join(repr(s) for s in crypto)},")
print("]")
