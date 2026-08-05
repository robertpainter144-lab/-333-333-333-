#!/bin/bash
# Quarki VM autopush — commit+push tracked changes across VM-hosted repos.
# Pull-rebase first so a push can never fail on remote drift; HTTPS remotes
# with stored credential; never deletes anything.
LOG=/home/ubuntu/trading-data/vm-autopush.log
STAMP=$(date -u +%Y-%m-%dT%H:%M:%S)

# Mirror loose real-money infra into trading-stack so it is versioned
# privately (Quarki33/trading-stack). NEVER mirror *.env — secrets stay
# out of git; trading-stack/.gitignore is the second line of defense.
TSM=/home/ubuntu/trading-stack
mkdir -p $TSM/vm/bin $TSM/vm/systemd $TSM/vm/ibkr_bot
cp -p /home/ubuntu/bin/mvy_watch.sh /home/ubuntu/bin/vm_watchdog.py \
      /home/ubuntu/bin/stack_watchdog.py /home/ubuntu/bin/vm-autopush.sh \
      $TSM/vm/bin/ 2>>"$LOG"
cp -p /home/ubuntu/ibkr_bot/notify.py $TSM/vm/ibkr_bot/ 2>>"$LOG"
cp -p /home/ubuntu/alpaca_paper_arm.py $TSM/vm/ 2>>"$LOG"
cp -p /home/ubuntu/directive_auditor/auditor.py $TSM/vm/directive_auditor/ 2>>"$LOG"
cp -p /home/ubuntu/scanner/state/autopilot_catalog.json $TSM/vm/ 2>>"$LOG"
cp -p /etc/systemd/system/quarki-*.service /etc/systemd/system/quarki-*.timer /etc/systemd/system/directive-audit*.service /etc/systemd/system/directive-audit*.timer \
      $TSM/vm/systemd/ 2>>"$LOG"
# D1 GUARD (2026-07-29): never mirror post-Linus private work to the shared
# MVYTraders repo. If any private marker is in the trading-gui tree, skip the
# mirror AND the MVYTraders commit/push leg, and alert — the 5-min cadence
# shipped a 20-min leak to GitHub on 2026-07-29 before anyone noticed.
PRIVATE_MARKERS='autopilot_cat|/api/catalog|owner_catalog|directive_auditor|DIRECTIVES\.md'
MIRROR_OK=1
if [ -f /home/ubuntu/.mvy_mirror_paused ]; then MIRROR_OK=0; fi
if grep -rIlE "$PRIVATE_MARKERS" /home/ubuntu/trading-gui/src /home/ubuntu/trading-gui/*.md 2>/dev/null | grep -qv node_modules; then
    MIRROR_OK=0
    echo "$STAMP  MVYTraders mirror SKIPPED - private markers in trading-gui" >> "$LOG"
    NTFY_TOPIC=$(sed -n 's/^NTFY_TOPIC=//p' /home/ubuntu/ntfy.env 2>/dev/null | head -1)
    [ -n "$NTFY_TOPIC" ] && curl -s -m 10 -H "Title: D1 PRIVACY GUARD" \
        -d "autopush: private markers found in trading-gui - MVYTraders mirror+push BLOCKED. Clean the tree." \
        "https://ntfy.sh/$NTFY_TOPIC" >/dev/null
fi

# One-way mirror: trading-gui source -> shared MVYTraders repo (frontend-only
# collab with Linus; scanner/companion stay private). No dist, node_modules, *.env.
[ "$MIRROR_OK" = 1 ] && rsync -a --delete \
    --exclude ".git" --exclude "node_modules" --exclude "dist*" --exclude "*.env" \
    /home/ubuntu/trading-gui/ /home/ubuntu/MVYTraders/trading-gui/ 2>>"$LOG"

for name in scanner ng_dashboard qc_portfolio trading-gui patent_cliff trading-stack whale_tracker insider_bot MVYTraders; do
    if [ "$name" = MVYTraders ] && [ "$MIRROR_OK" != 1 ]; then continue; fi
    d=/home/ubuntu/$name
    [ -d "$d/.git" ] || continue
    cd "$d" || continue
    git add -A 2>>"$LOG"
    if ! git diff --cached --quiet 2>>"$LOG"; then
        git commit -q -m "auto-push $STAMP (vm)" 2>>"$LOG"
    fi
    if ! git diff --quiet HEAD origin/HEAD 2>/dev/null; then
        git pull --rebase --autostash -q origin HEAD 2>>"$LOG" || {
            echo "$STAMP  $name: REBASE CONFLICT — manual attention" >> "$LOG"
            git rebase --abort 2>>"$LOG"
            continue
        }
        git push -q origin HEAD 2>>"$LOG" && echo "$STAMP  $name: pushed" >> "$LOG" || echo "$STAMP  $name: PUSH FAILED" >> "$LOG"
    fi
done
