#!/bin/bash
# mvy_watch.sh — Pushover-notify Robert when new commits land in MVYTraders
# (any branch, any pusher except Robert himself). Read-only deploy key.
set -euo pipefail

DIR=/home/ubuntu/mvy_watch
REPO=$DIR/repo.git
STATE=$DIR/refs.state
SELF_EMAIL=robert.m.painter@gmail.com

source /home/ubuntu/ntfy.env
export GIT_SSH_COMMAND="ssh -i /home/ubuntu/.ssh/mvy_deploy -o StrictHostKeyChecking=accept-new"

snapshot() { git -C "$REPO" for-each-ref --format='%(refname:short) %(objectname)' refs/heads; }

notify() {
    curl -s --max-time 20 -H "Title: MVYTraders" -H "Priority: 4" \
         -d "$1" "https://ntfy.sh/$NTFY_TOPIC" > /dev/null
}

if [ ! -d "$REPO" ]; then
    git init -q --bare "$REPO"
    git -C "$REPO" remote add origin git@github.com:Quarki33/MVYTraders.git
    git -C "$REPO" config remote.origin.fetch "+refs/heads/*:refs/heads/*"
    git -C "$REPO" fetch -q --prune origin
    snapshot > "$STATE"
    exit 0
fi

git -C "$REPO" fetch -q --prune origin
snapshot > "$STATE.new"

msg=""
while read -r branch newsha; do
    oldsha=$(awk -v b="$branch" '$1==b {print $2}' "$STATE")
    if [ -z "$oldsha" ]; then
        range="$newsha"
        label="new branch $branch"
        commits=$(git -C "$REPO" log -5 --format='%h %an: %s' "$range" 2>/dev/null | head -5)
        authors=$(git -C "$REPO" log -5 --format='%ae' "$range" 2>/dev/null)
    elif [ "$oldsha" != "$newsha" ]; then
        range="$oldsha..$newsha"
        label="$branch"
        commits=$(git -C "$REPO" log --format='%h %an: %s' "$range" 2>/dev/null | head -10)
        authors=$(git -C "$REPO" log --format='%ae' "$range" 2>/dev/null)
    else
        continue
    fi
    # Skip updates consisting only of Robert's own commits.
    if [ -n "$authors" ] && ! grep -qv "^$SELF_EMAIL$" <<< "$authors"; then
        continue
    fi
    [ -n "$commits" ] && msg+="[$label]"$'\n'"$commits"$'\n'
done < "$STATE.new"

while read -r branch oldsha; do
    grep -q "^$branch " "$STATE.new" || msg+="[deleted branch $branch]"$'\n'
done < "$STATE"

if [ -n "$msg" ]; then
    notify "$msg"
fi
mv "$STATE.new" "$STATE"
