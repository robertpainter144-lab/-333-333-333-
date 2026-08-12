#!/usr/bin/env python3
"""One-shot (resumable) Wayback backfill of @tradergrader's X history.

The 2026-08-12 deep-history harvest enumerated 4,019 archived status
snapshots (CDX list cached at site_cache/cdx_twitter_tradergrader_full.json
— each snapshot is the X syndication JSON for one tweet, captured by the
Internet Archive) but wayback serves them at ~40-60s a piece, so the
backfill runs as this standalone long-lived job instead of a subagent.

Design: 4 worker threads (wayback tolerates modest concurrency), 2s
per-worker politeness sleep, 30s backoff on 429/503, 90s request timeout.
Progress checkpoints to wayback_checkpoint.json every flush so the job can
be killed/restarted at any time without refetching; successful records
merge atomically into x_posts_tradergrader.jsonl (dedup by id, sorted by
ts, tagged src=wayback). On completion it re-parses + regrades via
equity_social.py so the catalog row and grades pick up the full history.

Scope note (D44): read-only fetches of ONE handle's archived public posts
from web.archive.org — no X servers touched at all.
"""
from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
CDX = ROOT / "site_cache" / "cdx_twitter_tradergrader_full.json"
POSTS = ROOT / "x_posts_tradergrader.jsonl"
CHECKPOINT = ROOT / "wayback_checkpoint.json"

WORKERS = 4
SLEEP = 2.0
TIMEOUT = 90
UA = {"User-Agent": "quarki-wayback-backfill/1.0 (polite; contact-less research)"}
FLUSH_EVERY = 25

_lock = threading.Lock()
_results: list[dict] = []
_ckpt = {"done": [], "perm_fail": []}
_counts = {"ok": 0, "fail": 0, "skip": 0}


def _log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def _load_posts() -> dict[str, dict]:
    posts = {}
    try:
        for ln in POSTS.read_text().splitlines():
            if ln.strip():
                p = json.loads(ln)
                posts[p["id"]] = p
    except FileNotFoundError:
        pass
    return posts


def _flush(posts: dict[str, dict]) -> None:
    with _lock:
        batch, _results[:] = list(_results), []
    if batch:
        for p in batch:
            posts.setdefault(p["id"], p)
        ordered = sorted(posts.values(), key=lambda p: p["ts"])
        tmp = POSTS.with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(p) + "\n" for p in ordered))
        tmp.replace(POSTS)
    tmp2 = CHECKPOINT.with_suffix(".tmp")
    tmp2.write_text(json.dumps(_ckpt))
    tmp2.replace(CHECKPOINT)
    _log(f"flushed +{len(batch)} (ok={_counts['ok']} fail={_counts['fail']} "
         f"store={len(posts)})")


def _parse_snapshot(raw: str) -> dict | None:
    j = json.loads(raw)
    d = j.get("data") or j
    text = d.get("text") or d.get("full_text") or ""
    tid = str(d.get("id_str") or d.get("id") or "")
    ca = d.get("created_at") or ""
    if not (text and tid.isdigit() and ca):
        return None
    try:
        ts = datetime.fromisoformat(ca.replace("Z", "+00:00"))
    except ValueError:
        ts = datetime.strptime(ca, "%a %b %d %H:%M:%S %z %Y")
    return {"id": tid, "ts": ts.astimezone(timezone.utc).isoformat(timespec="seconds"),
            "text": " ".join(text.split()), "src": "wayback"}


def _worker(q: "queue.Queue[tuple[str, str, str]]") -> None:
    while True:
        try:
            tid, snap_ts, url = q.get_nowait()
        except queue.Empty:
            return
        wb = f"https://web.archive.org/web/{snap_ts}id_/{url}"
        rec, perm = None, False
        for attempt in range(3):
            try:
                r = requests.get(wb, headers=UA, timeout=TIMEOUT)
                if r.status_code in (429, 503):
                    time.sleep(30 * (attempt + 1))
                    continue
                if r.status_code == 404:
                    perm = True
                    break
                r.raise_for_status()
                rec = _parse_snapshot(r.text)
                if rec is None:
                    perm = True   # snapshot exists but isn't parseable tweet JSON
                break
            except Exception:
                time.sleep(10)
        with _lock:
            if rec:
                _results.append(rec)
                _ckpt["done"].append(tid)
                _counts["ok"] += 1
            elif perm:
                _ckpt["perm_fail"].append(tid)
                _counts["fail"] += 1
            else:
                _counts["fail"] += 1   # transient — NOT checkpointed, retried next run
        q.task_done()
        time.sleep(SLEEP)


def main() -> None:
    rows = json.loads(CDX.read_text())
    if rows and rows[0] and rows[0][0] == "urlkey":
        rows = rows[1:]
    cands: dict[str, tuple[str, str]] = {}
    for row in rows:
        try:
            url = row[2]
            tid = url.rstrip("/").rsplit("/", 1)[-1]
            if tid.isdigit() and tid not in cands:
                cands[tid] = (row[1], url)
        except Exception:
            continue

    posts = _load_posts()
    try:
        _ckpt.update(json.loads(CHECKPOINT.read_text()))
    except Exception:
        pass
    skip = set(posts) | set(_ckpt["done"]) | set(_ckpt["perm_fail"])
    todo = [(tid, ts, url) for tid, (ts, url) in cands.items() if tid not in skip]
    _counts["skip"] = len(cands) - len(todo)
    _log(f"candidates={len(cands)} already-have/attempted={_counts['skip']} todo={len(todo)}")

    q: "queue.Queue[tuple[str, str, str]]" = queue.Queue()
    for item in todo:
        q.put(item)
    threads = [threading.Thread(target=_worker, args=(q,), daemon=True)
               for _ in range(WORKERS)]
    for t in threads:
        t.start()
    while any(t.is_alive() for t in threads):
        time.sleep(60)
        _flush(posts)
    _flush(posts)
    _log(f"DONE ok={_counts['ok']} fail={_counts['fail']} store={len(posts)}")

    es = ROOT / "equity_social.py"
    for leg in ("x_fetch", "grade"):
        try:
            subprocess.run([sys.executable, str(es), "--leg", leg], timeout=1800)
        except Exception as e:
            _log(f"post-run {leg} failed: {e!r}")


if __name__ == "__main__":
    main()
