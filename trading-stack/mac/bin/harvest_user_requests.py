#!/usr/bin/env python3
"""Harvest Robert's typed prompts from Claude Code transcripts into an
append-only ledger, then ship it to the Oracle VM for the directive auditor.

Runs from launchd (com.quarki3.request-harvester) every 5 min — no Claude
session involved. Cursor file keeps it incremental; safe to run any time.
"""
import json, os, subprocess, time
from pathlib import Path

PROJECTS = Path.home() / ".claude" / "projects"
OUT = Path.home() / "trading-data" / "user_requests.jsonl"
CURSOR = Path.home() / "trading-data" / ".request_harvest_cursor.json"
VM = "ubuntu@129.153.96.201"
SSH_KEY = str(Path.home() / ".ssh" / "oracle_trading")
LOOKBACK_S = 8 * 86400
MAX_TEXT = 4000

SKIP_PREFIXES = ("<command-name>", "<local-command", "Caveat:",
                 "<system-reminder", "[Request interrupted")


def texts_from(content):
    if isinstance(content, str):
        return [content]
    out = []
    for item in content or []:
        if isinstance(item, dict) and item.get("type") == "text":
            out.append(item.get("text") or "")
    return out


def main():
    cursor = {}
    if CURSOR.exists():
        try:
            cursor = json.loads(CURSOR.read_text())
        except Exception:
            cursor = {}
    seen = set(cursor.get("uuids", [])[-20000:])
    offsets = cursor.get("offsets", {})
    now = time.time()
    new_rows = []

    for f in sorted(PROJECTS.glob("*/*.jsonl")):
        try:
            if now - f.stat().st_mtime > LOOKBACK_S:
                continue
            key = str(f)
            off = offsets.get(key, 0)
            size = f.stat().st_size
            if size < off:
                off = 0
            if size == off:
                continue
            with open(f, "r", errors="replace") as fh:
                fh.seek(off)
                for line in fh:
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    if rec.get("type") != "user" or rec.get("isMeta"):
                        continue
                    msg = rec.get("message") or {}
                    if msg.get("role") != "user":
                        continue
                    uid = rec.get("uuid") or ""
                    if uid and uid in seen:
                        continue
                    parts = []
                    for t in texts_from(msg.get("content")):
                        t = t.strip()
                        if t and not t.startswith(SKIP_PREFIXES):
                            parts.append(t)
                    text = "\n".join(parts).strip()
                    if not text:
                        continue
                    if uid:
                        seen.add(uid)
                    new_rows.append({
                        "ts": rec.get("timestamp"),
                        "session": rec.get("sessionId"),
                        "project": f.parent.name,
                        "text": text[:MAX_TEXT],
                    })
                offsets[key] = fh.tell()
        except Exception:
            continue

    if new_rows:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        with open(OUT, "a") as out:
            for r in new_rows:
                out.write(json.dumps(r, ensure_ascii=False) + "\n")

    CURSOR.write_text(json.dumps(
        {"offsets": offsets, "uuids": list(seen)[-20000:]}))

    # Ship to the VM whether or not this run found new rows (covers a
    # previous run that harvested but failed the copy).
    if OUT.exists():
        subprocess.run(
            ["rsync", "-az", "-e", f"ssh -i {SSH_KEY} -o ConnectTimeout=10",
             str(OUT), f"{VM}:trading-data/user_requests.jsonl"],
            capture_output=True, timeout=60)


if __name__ == "__main__":
    main()
