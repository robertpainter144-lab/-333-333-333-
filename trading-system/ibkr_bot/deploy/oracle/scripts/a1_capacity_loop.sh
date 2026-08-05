#!/usr/bin/env bash
# Polls Oracle until an Ampere A1.Flex slot opens in PHX (any AD) and launches
# the ibkr-gw VM. Idempotent in the sense that once a VM is successfully
# launched, the loop exits and won't try again.
#
# Runs hands-off; sends a Pushover notification when the VM is up.
#
# Run in the background:
#   nohup ./a1_capacity_loop.sh > /tmp/a1_capacity.log 2>&1 &
#
# Stop:
#   ps aux | grep a1_capacity_loop | grep -v grep   # find the PID
#   kill <pid>

set -uo pipefail

# ── Constants (filled in 2026-06-26 by querying the user's tenancy) ──
TENANCY="ocid1.tenancy.oc1..aaaaaaaaw62hn63wprvhfon2lwqk33a4wrvakronj5ldhgucayinxqalrokq"
COMPARTMENT="$TENANCY"   # root compartment
SUBNET="ocid1.subnet.oc1.phx.aaaaaaaaj6mepmkyn5aswuwqbpelfjlxz5gbk2r5mycoo5garoxafzbvqehq"
IMAGE="ocid1.image.oc1.phx.aaaaaaaaufk34i3h6pc66mifse3yqpstkdbbohmlgpry7gu2ew6fq7faf5pa"  # Ubuntu 22.04 aarch64 2026.04.30-1
SHAPE="VM.Standard.A1.Flex"
OCPU=2
MEM_GB=6
ADS=("YzaV:PHX-AD-1" "YzaV:PHX-AD-2" "YzaV:PHX-AD-3")
DISPLAY_NAME="ibkr-gw"
SSH_KEY_PUB="$HOME/.ssh/oracle_trading.pub"
SLEEP_BETWEEN_ROUNDS=30
STATE_FILE="/tmp/a1_capacity_state"

export OCI_CLI_AUTH=security_token
OCI=/opt/homebrew/bin/oci

# ── ntfy (replaced Pushover 2026-07-28) ──────────────────────────────
NTFY_TOPIC="$(grep ^NTFY_TOPIC ~/ntfy.env | cut -d= -f2)"

push() {
  local msg="$1" title="${2:-A1 capacity loop}"
  curl -s -H "Title: $title" -d "$msg" \
          "https://ntfy.sh/$NTFY_TOPIC" >/dev/null || true
}

# ── Pre-flight ────────────────────────────────────────────────────────
[ -r "$SSH_KEY_PUB" ] || { echo "missing $SSH_KEY_PUB"; exit 1; }
SSH_KEY_CONTENT="$(cat "$SSH_KEY_PUB")"

echo "[$(date '+%F %T')] starting capacity loop for $SHAPE in PHX (ADs: ${ADS[*]})"
push "started — polling for $SHAPE in PHX" "A1 capacity loop"

round=0
while :; do
  round=$((round+1))
  for ad in "${ADS[@]}"; do
    ts="$(date '+%F %T')"
    echo "[$ts] round $round trying $ad ..."

    out=$($OCI --connection-timeout 30 --read-timeout 120 \
      compute instance launch \
      --availability-domain "$ad" \
      --compartment-id "$COMPARTMENT" \
      --shape "$SHAPE" \
      --shape-config "{\"ocpus\": $OCPU, \"memoryInGBs\": $MEM_GB}" \
      --image-id "$IMAGE" \
      --subnet-id "$SUBNET" \
      --assign-public-ip true \
      --display-name "$DISPLAY_NAME" \
      --metadata "{\"ssh_authorized_keys\": \"$SSH_KEY_CONTENT\"}" \
      --wait-for-state RUNNING \
      --max-wait-seconds 600 2>&1)
    rc=$?

    if [ $rc -eq 0 ]; then
      instance_id=$(echo "$out" | python3 -c 'import sys,json; print(json.load(sys.stdin)["data"]["id"])' 2>/dev/null)
      [ -n "$instance_id" ] || instance_id="<see /tmp/a1_capacity.log>"
      pub_ip=$($OCI compute instance list-vnics --instance-id "$instance_id" --query 'data[0]."public-ip"' --raw-output 2>/dev/null)
      echo "[$(date '+%F %T')] SUCCESS in $ad — instance $instance_id  public IP $pub_ip"
      echo "$instance_id" > "$STATE_FILE"
      push "VM up in $ad — public IP $pub_ip — ssh ubuntu@$pub_ip" "ibkr-gw ready"
      exit 0
    fi

    # Detect the specific out-of-capacity error vs other failures
    if echo "$out" | grep -qiE "Out of host capacity|OutOfHostCapacity|out of capacity"; then
      echo "[$(date '+%F %T')]   out-of-capacity in $ad"
    elif echo "$out" | grep -qiE "connection.*timed out|endpoint timed out|temporary failure|connection reset"; then
      echo "[$(date '+%F %T')]   transient network error in $ad — treating as retriable"
    elif echo "$out" | grep -qiE "TooManyRequests|RateLimited|429"; then
      echo "[$(date '+%F %T')]   throttled in $ad — backing off"
      sleep 60
    else
      echo "[$(date '+%F %T')]   FAIL in $ad — not capacity. Output below:"
      echo "$out" | tail -25
      # Non-capacity errors usually mean a permanent misconfig; bail
      push "non-capacity error in $ad — loop stopped, see /tmp/a1_capacity.log" "ibkr-gw FAIL"
      exit 2
    fi
  done

  echo "[$(date '+%F %T')] round $round: no capacity in any AD; sleeping ${SLEEP_BETWEEN_ROUNDS}s"
  sleep $SLEEP_BETWEEN_ROUNDS
done
