#!/usr/bin/env python3
"""Directive auditor — runs ON THE VM, independent of the Mac.

Modes:
  --fast : deterministic invariant checks only (hourly timer)
  (none) : full audit — invariants + Claude-API review of the trailing week
           of Robert's typed requests vs DIRECTIVES.md (daily timer)

Output: ~/trading-data/compliance/latest.md (+ dated copy on full runs).
ntfy: only on violations (silent when green), plus the daily summary line.
"""
import json, os, re, subprocess, sys, urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

HOME = Path.home()
DIRECTIVES = HOME / "trading-stack" / "DIRECTIVES.md"
REQUESTS = HOME / "trading-data" / "user_requests.jsonl"
OUTDIR = HOME / "trading-data" / "compliance"
BASE = "http://localhost:8080"
XFF = {"X-Forwarded-For": "203.0.113.9"}  # simulate arriving via the proxy
PRIVATE_MARKERS = ("autopilot_cat", "/api/catalog", "owner_catalog")
SERVICES = ["quarki-scanner.service", "quarki-dashboard.service",
            "quarki-qc.service",
            "quarki-poc10.service", "quarki-soxl.service"]
TIMERS = ["quarki-autopush.timer", "quarki-watchdog.timer", "quarki-leadercheck.timer"]
ANTHROPIC_MODEL = "claude-sonnet-5"


def http(url, headers=None, timeout=15):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:
        return -1, str(e).encode()


def guest_key():
    try:
        return json.loads((HOME / "trading-data" / "dashboard_keys.json")
                          .read_text())["guest"]
    except Exception:
        return None


def check_invariants():
    """Returns (violations, notes) lists of strings."""
    v, notes = [], []
    gk = guest_key()

    # D1: guest/public isolation of owner-private surfaces
    if gk:
        code, _ = http(f"{BASE}/api/catalog?key={gk}", XFF)
        (notes if code == 403 else v).append(
            f"D1 guest /api/catalog -> {code} (want 403)")
        code, _ = http(f"{BASE}/owner/catalog.js?key={gk}", XFF)
        (notes if code == 403 else v).append(
            f"D1 guest /owner/catalog.js -> {code} (want 403)")
        code, body = http(f"{BASE}/api/edge?key={gk}", XFF)
        try:
            names = [s.get("name", "") for s in
                     json.loads(body).get("strategies", [])]
            bad = [n for n in names if "autopilot_cat" in n]
            (v if bad else notes).append(
                f"D1 guest /api/edge catalog rows: {bad or 'none'}")
        except Exception:
            notes.append("D1 guest /api/edge unparseable")
    else:
        v.append("D1 cannot read guest key from dashboard_keys.json")

    code, body = http(f"{BASE}/terminal/", XFF)
    if b"owner/catalog.js" in body:
        v.append("D1 PUBLIC shell contains owner injection!")
    else:
        notes.append("D1 public shell clean")

    # D1: shared trees contain no private markers
    for tree in (HOME / "MVYTraders", HOME / "trading-gui" / "src",
                 HOME / "trading-gui" / "dist"):
        if not tree.exists():
            continue
        r = subprocess.run(
            ["grep", "-rl", "-e", PRIVATE_MARKERS[0], "-e", PRIVATE_MARKERS[1],
             "-e", PRIVATE_MARKERS[2], str(tree)],
            capture_output=True, text=True)
        hits = [h for h in r.stdout.splitlines()
                if "node_modules" not in h and ".git/" not in h]
        if hits:
            v.append(f"D1 private markers in {tree.name}: {hits[:3]}")
        else:
            notes.append(f"D1 {tree} clean")

    # D6: $5 drawdown review trigger on the live book
    code, body = http(f"{BASE}/api/portfolio/alpaca_live")
    try:
        d = json.loads(body)
        eq = d.get("equity") or d.get("value")
        peak = None
        pf = HOME / "trading-data" / "portfolio_peaks" / "alpaca_live.json"
        if pf.exists():
            peak = json.loads(pf.read_text()).get("peak")
        if eq is not None and peak:
            dd = float(peak) - float(eq)
            if dd >= 5.0:
                v.append(f"D6 DRAWDOWN ${dd:.2f} >= $5 REVIEW TRIGGER "
                         f"(eq ${eq:.2f}, peak ${peak:.2f})")
            elif dd >= 4.0:
                v.append(f"D6 amber: drawdown ${dd:.2f} approaching $5 "
                         f"(eq ${eq:.2f})")
            else:
                notes.append(f"D6 dd ${dd:.2f} of $5 (eq ${eq:.2f})")
        else:
            notes.append(f"D6 equity={eq} peak={peak} (insufficient data)")
    except Exception as e:
        notes.append(f"D6 portfolio unreadable: {e}")

    # D25: consumption manager state freshness (Mac meter -> VM sync)
    try:
        import time as _t
        cs = HOME / "trading-data" / "consumption" / "state.json"
        if cs.exists():
            d = json.loads(cs.read_text())
            age_h = (_t.time() - d.get("generated_epoch", 0)) / 3600
            if age_h > 48:
                v.append(f"D25 consumption state stale {age_h:.0f}h (>48h) - Mac meter down?")
            else:
                notes.append("D25 tier %s 5h %s%% wk %s%% age %.1fh" % (d.get("tier"), d["five_hour"]["pct"], d["weekly"]["pct"], age_h))
        else:
            v.append("D25 consumption state.json missing on VM")
    except Exception as e:
        notes.append(f"D25 state unreadable: {e}")

    # D24: continuous mirror currency — leader-check must be running.
    # State file is written on every run (including early-skip branches for
    # "no promotion" / "curve too young") since 2026-08-03, so staleness or
    # absence here means the timer itself stopped firing, not just a quiet day.
    try:
        lcs = HOME / "scanner" / "state" / "leader_check_state.json"
        if lcs.exists():
            d = json.loads(lcs.read_text())
            last_run = d.get("last_run")
            if last_run:
                age_d = (datetime.now(timezone.utc)
                          - datetime.fromisoformat(last_run).replace(tzinfo=timezone.utc)).days
                if age_d > 4:
                    v.append(f"D24 leader-check stale: last_run {last_run} ({age_d}d ago, >4d)")
                else:
                    skip = d.get("last_skip_reason")
                    notes.append(f"D24 leader-check last_run {last_run} ({age_d}d ago)"
                                 + (f" skip={skip}" if skip else ""))
            else:
                v.append("D24 leader_check_state.json has no last_run field")
        else:
            v.append("D24 leader_check_state.json missing — leader-check may never have run")
    except Exception as e:
        notes.append(f"D24 state unreadable: {e}")

    # D19 churn guard: adaptive tempo must not turn into runaway rotation.
    # Ground truth = Alpaca live orders API, counted since midnight US/Eastern
    # (the trading day). The old check grepped BUY/SELL out of
    # live_activity.jsonl, which (a) matched the paper prop-desk's 24/7 crypto
    # signal summaries and (b) reads a log trimmed to ~300 rows — both caused
    # the 2026-08-02 false-positive weekend alert.
    try:
        import time as _time
        key = secret = ""
        for ln in (HOME / "quarki.env").read_text().splitlines():
            if ln.startswith("ALPACA_LIVE_KEY="):
                key = ln.split("=", 1)[1].strip().strip('"\'')
            elif ln.startswith("ALPACA_LIVE_SECRET="):
                secret = ln.split("=", 1)[1].strip().strip('"\'')
        if not (key and secret):
            raise RuntimeError("no live keys in quarki.env")
        # Midnight US/Eastern (start of the trading day) without zoneinfo
        # (VM python predates it): flip TZ just long enough to compute it.
        _oldtz = os.environ.get("TZ")
        os.environ["TZ"] = "America/New_York"
        _time.tzset()
        _lt = _time.localtime()
        _mid = _time.mktime((_lt.tm_year, _lt.tm_mon, _lt.tm_mday,
                             0, 0, 0, 0, 0, -1))
        if _oldtz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = _oldtz
        _time.tzset()
        after = datetime.fromtimestamp(_mid, timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
        code, body = http(
            "https://api.alpaca.markets/v2/orders?status=all&limit=500"
            f"&after={after}",
            headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret})
        if code != 200:
            raise RuntimeError(f"orders API {code}")
        n_orders = len(json.loads(body))
        if n_orders > 12:
            v.append(f"D19 churn: {n_orders} live orders today (>12) — "
                     "adaptive tempo may be over-rotating; review governor_config")
        else:
            notes.append(f"D19 live orders today: {n_orders} (churn cap 12)")
    except Exception as e:
        notes.append(f"D19 churn check unreadable: {e}")

    # D13: /opt/trading must not exist
    if Path("/opt/trading").exists():
        v.append("D13 /opt/trading EXISTS — data dir hijack risk")
    else:
        notes.append("D13 /opt/trading absent")

    # D14: services + timers
    for unit in SERVICES + TIMERS:
        r = subprocess.run(["systemctl", "is-active", unit],
                           capture_output=True, text=True, timeout=10)
        state = r.stdout.strip()
        (notes if state == "active" else v).append(f"D14 {unit}: {state}")

    return v, notes


def week_requests():
    if not REQUESTS.exists():
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    rows = []
    for line in REQUESTS.read_text(errors="replace").splitlines():
        try:
            r = json.loads(line)
            ts = r.get("ts") or ""
            if ts and datetime.fromisoformat(
                    ts.replace("Z", "+00:00")) < cutoff:
                continue
            rows.append(r)
        except Exception:
            continue
    return rows[-400:]


def llm_review(violations, notes, requests):
    import subprocess as _sp
    _g = _sp.run([str(Path.home() / "bin" / "claude_gate.sh")], capture_output=True, text=True)
    if _g.returncode == 2:
        return "LLM review skipped: consumption gate RED (" + _g.stdout.strip() + ")"
    directives = DIRECTIVES.read_text() if DIRECTIVES.exists() else "(missing)"
    req_lines = "\n".join(
        f"- [{r.get('ts','?')}] {r.get('text','')[:1200]}" for r in requests)
    prompt = f"""You are the directive-compliance auditor for Robert's trading stack.

STANDING ORDERS (DIRECTIVES.md):
{directives}

DETERMINISTIC CHECK RESULTS TODAY:
violations: {violations}
notes: {notes}

EVERY REQUEST ROBERT TYPED IN THE PAST 7 DAYS (raw, chronological):
{req_lines}

Task: verify his orders are being followed to a T.
1. List any request that contains an instruction/standing order NOT already
   captured in DIRECTIVES.md (quote it, propose a one-line directive).
2. List any request whose instruction appears UNFULFILLED or contradicted by
   the check results or by later requests (he repeating himself or expressing
   frustration is a strong signal something was dropped).
3. One-line overall verdict: COMPLIANT / ATTENTION / VIOLATION.
Be terse and concrete. No praise, no filler."""
    prompt = prompt[:180000]

    # Path 1: raw API with a real key.
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if key.startswith("sk-ant-") and len(key) > 30:
        body = json.dumps({
            "model": ANTHROPIC_MODEL,
            "max_tokens": 1500,
            "messages": [{"role": "user", "content": prompt}],
        }).encode()
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages", data=body,
            headers={"content-type": "application/json", "x-api-key": key,
                     "anthropic-version": "2023-06-01"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                out = json.loads(r.read())
            return "".join(b.get("text", "") for b in out.get("content", []))
        except Exception as e:
            api_err = f"API path failed: {e}"
    else:
        api_err = "no valid ANTHROPIC_API_KEY"

    # Path 2: headless claude CLI on the Max subscription — auth via either
    # CLAUDE_CODE_OAUTH_TOKEN (from `claude setup-token`) or the OAuth
    # credentials file copied from the Mac's Keychain (~/.claude/.credentials.json).
    claude_bin = HOME / ".local" / "bin" / "claude"
    has_auth = (os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
                or (HOME / ".claude" / ".credentials.json").exists())
    if claude_bin.exists() and has_auth:
        try:
            # A stale/placeholder ANTHROPIC_API_KEY shadows the claude.ai
            # OAuth login inside the CLI — strip it for the subprocess.
            env = {k: v for k, v in os.environ.items()
                   if k != "ANTHROPIC_API_KEY"}
            r = subprocess.run(
                [str(claude_bin), "-p", "--model", "sonnet"],
                input=prompt, capture_output=True, text=True, timeout=300,
                env=env)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip()
            return f"LLM review FAILED (cli rc={r.returncode}): " \
                   f"{(r.stderr or r.stdout)[:300]} | {api_err}"
        except Exception as e:
            return f"LLM review FAILED (cli): {e} | {api_err}"
    return (f"LLM review skipped: {api_err}; no claude CLI auth. "
            "Re-copy the Mac Keychain credentials to ~/.claude/.credentials.json "
            "on the VM (or run `claude setup-token`).")


# ntfy() removed 2026-08-03 (D28 follow-up): all notifications go through
# ~/bin/quarki_notify.py; delivery failures are logged to notify_failures.log
# instead of falling back to raw ntfy.sh posts.


# ── Plain-English notification layer (2026-08-03, D28) ──────────────────────
# Robert: "show me exactly what the reason for the notification is in plain
# english" + only page when it matters. Every violation is translated before
# it reaches the phone, and a persisting violation reminds once per 24h
# instead of every hourly audit run.
NOTIFY_STATE = Path(__file__).resolve().parent / "notify_state.json"
REMIND_SECS = 24 * 3600

PLAIN_ENGLISH = {
    "D1":  ("Privacy leak risk", "Something owner-private may be reachable by "
            "guests (Linus/dad keys) or may have leaked into the shared repo. "
            "This needs a look even if trading is fine."),
    "D5":  ("IBKR exposure over $20", "The bots have more than $20 total in "
            "IBKR positions, which needs your push approval first."),
    "D6":  ("Live account drawdown", "The live Alpaca account is down from "
            "its peak by enough to trigger your mandatory-review rule."),
    "D13": ("Suspicious directory", "/opt/trading exists again — nothing of "
            "ours should live there; possible misconfigured deploy."),
    "D14": ("A service or timer is down", "Part of the trading stack is not "
            "running. The watchdog usually self-heals this; if it stays "
            "down, something is genuinely broken."),
    "D19": ("Bots are over-trading", "The LIVE account has made more trades "
            "today than your 12-per-day churn limit. Money is real here — "
            "usually means the rebalancer is dribbling tiny orders; check "
            "governor/mirror settings."),
    "D25": ("Usage meter stale/missing", "The Claude-usage meter data on the "
            "VM is old or absent, so consumption tiers may be wrong. Check "
            "the Mac launchd meter and the sync."),
}


def _plain(v: str) -> str:
    code = v.split()[0].split(":")[0].strip()
    title, why = PLAIN_ENGLISH.get(code, ("Directive check failed",
                                          "A standing-order check failed."))
    return f"• {title} [{code}]: {why}\n  Details: {v}"


def _vkey(v: str) -> str:
    """Stable identity for a violation so a changing number (47 -> 52 orders)
    doesn't look like a new problem every hour."""
    toks = v.split()
    return toks[0] + ":" + (toks[1] if len(toks) > 1 else "")


def notify_tiered(level: str, title: str, msg: str) -> None:
    try:
        subprocess.run(["python3", str(HOME / "bin" / "quarki_notify.py"),
                        level, title, msg[:3500]], timeout=20)
    except Exception as exc:
        # Never raw ntfy (global rule): log the delivery failure locally so
        # the daily audit surfaces it, rather than bypassing quarki_notify.
        try:
            import time as _t
            with (NOTIFY_STATE.parent / "notify_failures.log").open("a") as f:
                f.write(_t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime())
                        + f" {level} {title}: {exc}\n")
        except Exception:
            pass


def dispatch_violations(violations: list) -> None:
    import time as _t
    now = _t.time()
    try:
        st = json.loads(NOTIFY_STATE.read_text())
    except Exception:
        st = {}
    active = st.get("active", {})
    current = {}
    fresh, reminders = [], []
    for v in violations:
        k = _vkey(v)
        rec = active.get(k)
        if rec is None:
            fresh.append(v)
            current[k] = {"first_seen": now, "last_notified": now, "last": v}
        elif now - rec.get("last_notified", 0) >= REMIND_SECS:
            reminders.append(v)
            current[k] = {**rec, "last_notified": now, "last": v}
        else:
            current[k] = {**rec, "last": v}
    cleared = [active[k].get("last", k) for k in active if k not in current]

    if fresh:
        notify_tiered("action", f"Trading stack: {len(fresh)} new issue" +
                      ("s" if len(fresh) > 1 else ""),
                      "\n".join(_plain(v) for v in fresh[:6]))
    if reminders:
        notify_tiered("action", "Still unresolved after 24h",
                      "\n".join(_plain(v) for v in reminders[:6]))
    if cleared:
        notify_tiered("routine", "Resolved on its own",
                      "These earlier issues are no longer failing:\n" +
                      "\n".join(f"• {c}" for c in cleared[:6]))
    st["active"] = current
    NOTIFY_STATE.write_text(json.dumps(st, indent=2))


def main():
    fast = "--fast" in sys.argv
    now = datetime.now().astimezone()
    violations, notes = check_invariants()
    review = ""
    reqs = []
    if not fast:
        reqs = week_requests()
        review = llm_review(violations, notes, reqs)

    OUTDIR.mkdir(parents=True, exist_ok=True)
    report = [f"# Compliance report — {now.isoformat(timespec='seconds')} "
              f"({'fast' if fast else 'full'})", ""]
    report.append(f"## Violations ({len(violations)})")
    report += [f"- {x}" for x in violations] or ["- none"]
    report.append("")
    report.append("## Checks passed / notes")
    report += [f"- {x}" for x in notes]
    if not fast:
        report += ["", f"## Week in review ({len(reqs)} requests scanned)",
                   "", review]
    text = "\n".join(report) + "\n"
    (OUTDIR / "latest.md").write_text(text)
    if not fast:
        (OUTDIR / f"report-{now:%Y%m%d}.md").write_text(text)

    verdict = re.search(r"\b(COMPLIANT|ATTENTION|VIOLATION)\b", review or "")
    dispatch_violations(violations)
    if not violations and not fast:
        notify_tiered("routine", "Daily audit: all clear",
                      f"All standing-order checks passed. {len(reqs)} requests "
                      f"reviewed, verdict {verdict.group(1) if verdict else 'n/a'}. "
                      f"Report: trading-data/compliance/latest.md")
    print(text)


if __name__ == "__main__":
    main()
