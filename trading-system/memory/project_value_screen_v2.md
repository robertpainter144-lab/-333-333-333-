---
name: project-value-screen-v2
description: Value Screen v2 SHELVED 2026-08-02 after PIT backtest — never beat SPY / Sharpe <1.0; keeper config + reusable PIT harness on unpushed scanner branch value-screen-v2
metadata: 
  node_type: memory
  type: project
  originSessionId: d19204af-c34a-499c-95c5-54ba4d7d285e
  modified: 2026-08-03T02:11:11.504Z
---

Value Screen v2 (long-only free-data value screen, evolved from dad's finviz
recipe) was **shelved 2026-08-02** — and **round 2 (same evening) double-shelved
both remaining reinterpretations. The program is CLOSED — do not schedule a
round 3 on this free-data screen.** Round-2 verdicts (judge independently
re-ran both backtests, reproduced identically; VERDICT_ROUND2.md, commit
8525b61 on value-screen-v2):
- Approach A quality-momentum-pullback (momentum>0 + sector-relative drawdown
  entry, value leg dropped): CAGR 8.64% / Sharpe 0.544 — strictly dominated
  by the already-shelved v2 on every metric. SHELVE.
- Approach B lane-feed hybrid (screen as ranking feed, top-5 weekly lane):
  forward alpha vs SPY t≈0.5–1.1 (nil), vs own gate-passed pool sign-flips
  4 of 11 years; lane Sharpe 0.601, validation expectancy 0.41%/trade fails
  the 0.59% bar. Decisive: universe_hold BEATS the ranked lane and SPY beats
  both — the ranking layer adds noise. SHELVE.

- Winner design "v2" (QARP composite, weights 0.40/0.35/0.25 G/V/C, gates +
  hysteresis + breadth): full 2016–2026 PIT run CAGR 13.49% / Sharpe 0.762 /
  MaxDD −31.8% vs SPY 15.09% / 0.880 / −33.7%. Ship bar SHIP=false.
- Stop-refining clause invoked: structurally can't clear Sharpe ≥1.0 while
  beating SPY's 2016–26 run; sensitivity showed a fragile parameter island.
- Still useful: +1.7–2.1%/trade expectancy as an idea-generator screen, and
  the reusable PIT backtest harness (`backtests/value2_backtest.py`,
  `gen_universe_snapshot.py` — PIT S&P 500 membership, as-filed EPS,
  ablation arms, ship-bar validation).
- Artifacts: scanner branch `value-screen-v2` (worktree
  `~/worktrees/scanner-value-v2`, commit cae2e07, NOT pushed/merged);
  verdict at `backtests/VERDICT.md`; CONTEXT entry on branch
  `value-screen-v2-docs` (~/worktrees/trading-stack-value-v2, 8975e45).
  v1 (`scanner/sources/value_screen.py`) untouched; no capital lane ever
  created. Related: [[project-scanner]], [[feedback-paper-vs-real]].
