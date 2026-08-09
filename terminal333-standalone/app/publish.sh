#!/bin/bash
# publish.sh — bump versionCode/Name, build release APK, upload to Hetzner,
# write manifest. Phone's UpdateChecker sees the new version on next launch.
set -euo pipefail

ANDROID_DIR=/Users/quarki3/trading_android
APP_GRADLE=$ANDROID_DIR/app/build.gradle.kts
VPS=ubuntu@129.153.96.201
SSH_KEY=/Users/quarki3/.ssh/oracle_trading
DIST_REMOTE=/home/ubuntu/trading-dist  # NEVER /opt/trading — creates data-dir hijack (see CONTEXT.md 2026-07-29)
APK_LOCAL=$ANDROID_DIR/app/build/outputs/apk/release/app-release.apk

cd "$ANDROID_DIR"

# 1. Bump versionCode + versionName via Python (BSD sed is unreliable here).
OUTPUT=$(python3 <<PY
import re, pathlib
p = pathlib.Path("$APP_GRADLE")
t = p.read_text()
m = re.search(r'versionCode\s*=\s*(\d+)', t)
old_code = int(m.group(1))
new_code = old_code + 1
m2 = re.search(r'versionName\s*=\s*"([^"]+)"', t)
old_name = m2.group(1)
parts = old_name.split('.')
parts[-1] = str(int(parts[-1]) + 1)
new_name = '.'.join(parts)
t = re.sub(r'versionCode\s*=\s*\d+', f'versionCode = {new_code}', t)
t = re.sub(r'versionName\s*=\s*"[^"]+"', f'versionName = "{new_name}"', t)
p.write_text(t)
print(f"{old_code} {new_code} {old_name} {new_name}")
PY
)
read -r OLD_CODE NEW_CODE OLD_NAME NEW_NAME <<< "$OUTPUT"
echo "Bumped: versionCode $OLD_CODE → $NEW_CODE, versionName $OLD_NAME → $NEW_NAME"

# 2. Build.
export JAVA_HOME=/opt/homebrew/opt/openjdk@21
./gradlew assembleRelease 2>&1 | tail -3

# 3. SHA256.
SHA256=$(shasum -a 256 "$APK_LOCAL" | awk '{print $1}')

# 4. Stage + upload.
mkdir -p "$ANDROID_DIR/dist"
cp "$APK_LOCAL" "$ANDROID_DIR/dist/trading-$NEW_NAME.apk"
scp -q -i "$SSH_KEY" "$APK_LOCAL" "$VPS:$DIST_REMOTE/trading-latest.apk"

# 5. Manifest.
NOTES="${1:-update available}"
ssh -i "$SSH_KEY" "$VPS" "cat > $DIST_REMOTE/manifest.json" <<EOF
{
  "version_code": $NEW_CODE,
  "version_name": "$NEW_NAME",
  "url": "http://100.73.225.4:8080/api/apk",
  "sha256": "$SHA256",
  "notes": "$NOTES"
}
EOF

echo "Published v$NEW_NAME (code $NEW_CODE)"
echo "Phone will pick it up on next app launch."
