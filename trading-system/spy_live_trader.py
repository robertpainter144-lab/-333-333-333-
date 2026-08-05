#!/usr/bin/env python3
"""
SPY Live Trader — Adaptive Regime Strategy
Best config: EMA25, trail=3×ATR, direction-aware ADX(20) threshold=35,
             short regime in bear trends, 09:30–11:00 ET entry window.

Setup:
  pip install ib_insync pandas numpy
  Start IB Gateway (paper: port 4002 | live: port 4001)

Usage:
  python spy_live_trader.py --paper              # paper trade (safe default)
  python spy_live_trader.py --paper --dry-run    # print signals, no orders
  python spy_live_trader.py --live               # real money — prompts for confirm
"""

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

# eventkit (ib_insync dependency) calls get_event_loop() at module level,
# which raises RuntimeError in Python 3.14+ if no loop exists yet.
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

try:
    from ib_insync import IB, Stock, MarketOrder
except ImportError:
    sys.exit("Run: pip install ib_insync")

# ── Strategy parameters ───────────────────────────────────────────────────────
ET          = ZoneInfo("America/New_York")
EMA_PERIOD  = 25
ATR_PERIOD  = 14
ADX_PERIOD  = 20
LONG_TRAIL  = 3.0
SHORT_TRAIL = 3.0
ADX_THRESH  = 35
ENTRY_START = 9 * 60 + 30    # 09:30 ET
ENTRY_END   = 11 * 60         # 11:00 ET
EOD_MINS    = 15 * 60 + 50   # 15:50 ET — force-close shorts
WARMUP      = 150             # bars required before trading
LEVERAGE    = 2.0

STATE_FILE  = Path("spy_trader_state.json")
LOG_FILE    = Path("spy_trader.log")

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(), logging.FileHandler(LOG_FILE)],
)
log = logging.getLogger(__name__)


# ── Strategy ──────────────────────────────────────────────────────────────────

class Strategy:
    """
    Pure logic — no IB coupling. Feed completed 5-min bars via add_bar().
    Returns an action dict when an order is needed, else None.
    """

    def __init__(self):
        self.history   = []       # list of bar dicts (open/high/low/close/date)
        self.position  = None     # dict or None
        self.equity    = 100_000.0
        self.n_trades  = 0
        self.total_pnl = 0.0

    def add_bar(self, bar: dict, equity: float = None) -> dict | None:
        if equity is not None:
            self.equity = equity
        self.history.append(bar)
        if len(self.history) > 600:
            self.history = self.history[-600:]

        if len(self.history) < WARMUP:
            return None

        df    = self._compute_indicators()
        row   = df.iloc[-1]
        close = float(row["close"])
        ema   = float(row["ema"])
        atr   = float(row["atr"])
        adx   = float(row["adx"])
        pdi   = float(row["pdi"])
        ndi   = float(row["ndi"])
        ts    = df.index[-1]

        ts_et  = ts.tz_localize(ET) if ts.tzinfo is None else ts.tz_convert(ET)
        mins   = ts_et.hour * 60 + ts_et.minute
        regime = self._regime(adx, pdi, ndi)

        action = None

        # ── Manage open position ──────────────────────────────────────────
        if self.position is not None:
            side = self.position["side"]

            if side == "long":
                ns = close - LONG_TRAIL * atr
                if ns > self.position["trail_stop"]:
                    self.position["trail_stop"] = ns
                stop_hit = close <= self.position["trail_stop"]
                eod_exit = False
            else:
                ns = close + SHORT_TRAIL * atr
                if ns < self.position["trail_stop"]:
                    self.position["trail_stop"] = ns
                stop_hit = close >= self.position["trail_stop"]
                eod_exit = mins >= EOD_MINS

            if stop_hit or eod_exit:
                shares = self.position["shares"]
                gross  = (close - self.position["entry_price"]) * shares if side == "long" \
                     else (self.position["entry_price"] - close) * shares
                pnl    = gross - shares * 0.01  # $0.005/share RT
                self.total_pnl += pnl
                self.n_trades  += 1
                reason = "eod" if eod_exit else f"trail@{self.position['trail_stop']:.2f}"
                log.info(
                    f"EXIT  {side.upper():<5}  {shares} SPY @ {close:.2f}"
                    f"  pnl={pnl:>+,.0f}  [{reason}]"
                    f"  cumulative={self.total_pnl:>+,.0f}  trades={self.n_trades}"
                )
                action = {
                    "action":  "sell" if side == "long" else "cover",
                    "shares":  shares,
                    "reason":  reason,
                }
                self.position = None

        # ── Entry ─────────────────────────────────────────────────────────
        if self.position is None and action is None and ENTRY_START <= mins < ENTRY_END:
            shares = int((self.equity * LEVERAGE) / close)
            if shares >= 1:
                if regime in ("ranging", "bull_trend") and close > ema:
                    trail_stop = close - LONG_TRAIL * atr
                    self.position = {
                        "side":        "long",
                        "entry_price": close,
                        "shares":      shares,
                        "trail_stop":  trail_stop,
                        "entry_time":  str(ts_et),
                        "regime":      regime,
                    }
                    action = {"action": "buy", "shares": shares, "reason": regime}
                    log.info(
                        f"ENTRY LONG   {shares} SPY @ {close:.2f}"
                        f"  stop={trail_stop:.2f}  regime={regime}"
                        f"  adx={adx:.1f}  ema={ema:.2f}"
                    )

                elif regime == "bear_trend" and close >= ema:
                    trail_stop = close + SHORT_TRAIL * atr
                    self.position = {
                        "side":        "short",
                        "entry_price": close,
                        "shares":      shares,
                        "trail_stop":  trail_stop,
                        "entry_time":  str(ts_et),
                        "regime":      regime,
                    }
                    action = {"action": "short", "shares": shares, "reason": regime}
                    log.info(
                        f"ENTRY SHORT  {shares} SPY @ {close:.2f}"
                        f"  stop={trail_stop:.2f}  regime={regime}"
                        f"  adx={adx:.1f}  ema={ema:.2f}"
                    )

        # Heartbeat during entry window when no position and no signal
        if ENTRY_START <= mins < ENTRY_END and action is None and self.position is None:
            log.info(
                f"  {ts_et.strftime('%H:%M')}  close={close:.2f}  ema={ema:.2f}"
                f"  adx={adx:.1f}  regime={regime}  → watching"
            )

        return action

    def _compute_indicators(self) -> pd.DataFrame:
        df = pd.DataFrame(self.history)
        dates = pd.to_datetime([b["date"] for b in self.history])
        if dates.tz is not None:
            dates = dates.tz_convert(ET).tz_localize(None)
        df.index = dates
        df = df[["open", "high", "low", "close"]].astype(float)

        df["ema"] = df["close"].ewm(span=EMA_PERIOD, adjust=False).mean()

        hl  = df["high"] - df["low"]
        hcp = (df["high"] - df["close"].shift(1)).abs()
        lcp = (df["low"]  - df["close"].shift(1)).abs()
        tr  = pd.concat([hl, hcp, lcp], axis=1).max(axis=1)
        df["atr"] = tr.ewm(span=ATR_PERIOD, adjust=False).mean()

        up   = df["high"] - df["high"].shift(1)
        down = df["low"].shift(1) - df["low"]
        pdm  = np.where((up > down) & (up > 0), up, 0.0)
        ndm  = np.where((down > up) & (down > 0), down, 0.0)
        aw   = tr.ewm(span=ADX_PERIOD, adjust=False).mean()
        pdi  = 100 * pd.Series(pdm, index=df.index).ewm(span=ADX_PERIOD, adjust=False).mean() / aw
        ndi  = 100 * pd.Series(ndm, index=df.index).ewm(span=ADX_PERIOD, adjust=False).mean() / aw
        dx   = 100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, np.nan)
        df["adx"] = dx.ewm(span=ADX_PERIOD, adjust=False).mean()
        df["pdi"] = pdi
        df["ndi"] = ndi
        return df

    def _regime(self, adx, pdi, ndi) -> str:
        if adx <= ADX_THRESH:
            return "ranging"
        return "bull_trend" if pdi >= ndi else "bear_trend"

    def to_dict(self) -> dict:
        return {
            "position":  self.position,
            "equity":    self.equity,
            "n_trades":  self.n_trades,
            "total_pnl": self.total_pnl,
            "updated":   str(datetime.now(ET)),
        }

    def from_dict(self, d: dict):
        self.position  = d.get("position")
        self.equity    = d.get("equity",    100_000.0)
        self.n_trades  = d.get("n_trades",  0)
        self.total_pnl = d.get("total_pnl", 0.0)


# ── IB Trader ─────────────────────────────────────────────────────────────────

class Trader:

    def __init__(self, host: str, port: int, client_id: int,
                 dry_run: bool = False, equity_override: float = None):
        self.ib              = IB()
        self.host            = host
        self.port            = port
        self.client_id       = client_id
        self.dry_run         = dry_run
        self.equity_override = equity_override
        self.strategy        = Strategy()
        self.contract        = None
        self.live_bars       = None
        self._live           = False   # True once historical seed is complete

        if STATE_FILE.exists():
            state = json.loads(STATE_FILE.read_text())
            self.strategy.from_dict(state)
            if self.strategy.position:
                log.info(f"Restored state: {self.strategy.position}")

    # ── Connection ────────────────────────────────────────────────────────────

    def connect(self):
        mode = "DRY RUN — " if self.dry_run else ""
        log.info(f"Connecting {mode}to IB @ {self.host}:{self.port} (clientId={self.client_id})")
        self.ib.connect(self.host, self.port, clientId=self.client_id)
        self.contract = self.ib.qualifyContracts(Stock("SPY", "SMART", "USD"))[0]
        # ib_insync auto-subscribes account updates on connect; wait for them to arrive
        self.ib.sleep(2)
        log.info(f"Connected. {self.contract}  Equity: ${self.get_equity():,.0f}")
        self._check_ib_position()

    def _check_ib_position(self):
        """Warn if IB position doesn't match saved state."""
        ib_spy = next(
            (p.position for p in self.ib.positions() if p.contract.symbol == "SPY"), 0
        )
        our = self.strategy.position
        our_shares = 0
        if our:
            our_shares = our["shares"] if our["side"] == "long" else -our["shares"]

        if abs(ib_spy - our_shares) > 2:
            log.warning(
                f"Position mismatch: IB has {ib_spy} SPY, state has {our_shares}. "
                "Clearing state — verify manually."
            )
            self.strategy.position = None

    # ── Account ───────────────────────────────────────────────────────────────

    def get_equity(self) -> float:
        if self.equity_override:
            return self.equity_override
        for av in self.ib.accountValues():
            if av.tag == "NetLiquidation" and av.currency == "USD":
                v = float(av.value)
                if v > 0:
                    return v
        return self.strategy.equity  # fall back to stored/default $100k

    # ── Main loop ─────────────────────────────────────────────────────────────

    def run(self):
        log.info("Requesting 10 days of 5-min historical bars...")
        self.live_bars = self.ib.reqHistoricalData(
            self.contract,
            endDateTime="",
            durationStr="10 D",
            barSizeSetting="5 mins",
            whatToShow="TRADES",
            useRTH=True,
            keepUpToDate=True,
        )

        # Seed historical bars for indicator warmup (no orders)
        log.info(f"Seeding {len(self.live_bars)} bars for warmup (need {WARMUP})...")
        for bar in self.live_bars[:-1]:   # skip the forming (last) bar
            self.strategy.add_bar({
                "date": bar.date, "open": bar.open,
                "high": bar.high, "low":  bar.low, "close": bar.close,
            })

        seeded = len(self.strategy.history)
        ready  = seeded >= WARMUP
        log.info(
            f"Warmup {'READY' if ready else f'INCOMPLETE ({seeded}/{WARMUP} bars)'}. "
            f"Strategy is {'LIVE' if ready else 'still warming — waiting for more bars'}."
        )

        # Re-reconcile after warmup: replay may have created a phantom position
        # from today's historical bars. Clear it if IB doesn't have a matching position.
        self._check_ib_position()

        log.info(
            f"Current position: {self.strategy.position or 'flat'}  "
            f"Equity: ${self.get_equity():,.0f}"
        )

        self._live = True
        self.live_bars.updateEvent += self._on_bar
        log.info("Listening for new bars...\n")
        self.ib.run()

    # ── Bar callback ──────────────────────────────────────────────────────────

    def _on_bar(self, bars, hasNewBar: bool):
        if not hasNewBar or not self._live or len(bars) < 2:
            return

        bar = bars[-2]   # just-completed bar; bars[-1] is the new forming bar
        bar_dict = {
            "date":  bar.date, "open": bar.open,
            "high":  bar.high, "low":  bar.low, "close": bar.close,
        }

        action = self.strategy.add_bar(bar_dict, equity=self.get_equity())

        if action:
            self._execute(action, bar.close)
            self._save_state()

    # ── Order execution ───────────────────────────────────────────────────────

    def _execute(self, action: dict, ref_price: float):
        verb   = action["action"]   # buy | sell | short | cover
        shares = action["shares"]
        ib_action = "BUY" if verb in ("buy", "cover") else "SELL"

        prefix = "[DRY RUN] " if self.dry_run else ""
        log.info(
            f"{prefix}ORDER: {verb.upper()} {shares} SPY @ MKT"
            f"  (bar close {ref_price:.2f})  reason={action.get('reason', '')}"
        )

        if self.dry_run:
            return

        order = MarketOrder(ib_action, shares)
        trade = self.ib.placeOrder(self.contract, order)
        self.ib.sleep(3)   # wait for fill confirmation
        s = trade.orderStatus
        log.info(
            f"  → status={s.status}  filled={s.filled}/{shares}"
            f"  avgFill={s.avgFillPrice:.2f}"
        )

    # ── State persistence ─────────────────────────────────────────────────────

    def _save_state(self):
        STATE_FILE.write_text(
            json.dumps(self.strategy.to_dict(), indent=2, default=str)
        )


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="SPY Adaptive Regime Live Trader")
    grp = ap.add_mutually_exclusive_group()
    grp.add_argument("--paper", action="store_true",
                     help="Paper trade via IB Gateway (port 4002) — DEFAULT")
    grp.add_argument("--live",  action="store_true",
                     help="Live trade via IB Gateway (port 4001) — requires confirmation")
    ap.add_argument("--dry-run", action="store_true",
                    help="Log signals only, place no orders")
    ap.add_argument("--host",   default="127.0.0.1")
    ap.add_argument("--port",   type=int,  help="Override IB Gateway port")
    ap.add_argument("--client", type=int,  default=10, help="IB client ID (default 10)")
    ap.add_argument("--equity", type=float,
                    help="Override account equity for position sizing")
    args = ap.parse_args()

    port = args.port or (4001 if args.live else 4002)

    if args.live and not args.dry_run:
        ans = input(
            "\n*** LIVE TRADING WITH REAL MONEY ***\n"
            "Type 'yes' to confirm: "
        ).strip().lower()
        if ans != "yes":
            sys.exit("Aborted.")

    log.info("=" * 65)
    log.info(f"  SPY Live Trader  |  {'LIVE' if args.live else 'PAPER'}{'  DRY RUN' if args.dry_run else ''}")
    log.info(f"  EMA{EMA_PERIOD}  trail={LONG_TRAIL}×ATR  ADX({ADX_PERIOD})<{ADX_THRESH}  "
             f"entry 09:30–11:00 ET")
    log.info("=" * 65)

    trader = Trader(
        host=args.host, port=port, client_id=args.client,
        dry_run=args.dry_run, equity_override=args.equity,
    )
    trader.connect()
    trader.run()


if __name__ == "__main__":
    main()
