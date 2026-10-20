#!/bin/bash
set -euo pipefail
YC_PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$YC_PROJECT_DIR"
YC_PYTHON="${YCARDS_PYTHON:-/opt/vps-python/bin/python}"
YC_CHROME="${YCARDS_CHROME:?Set YCARDS_CHROME to Chromium}"
YC_PROFILE="$(mktemp -d /tmp/ycards-optics-chromium-XXXXXX)"
YC_ARTIFACTS="$YC_PROJECT_DIR/tests/ycards-optics-artifacts"
mkdir -p "$YC_ARTIFACTS"
"$YC_PYTHON" tests/ycards_v2_fixture.py > "$YC_ARTIFACTS/fixture.log" 2>&1 &
YC_FIXTURE_PID=$!
"$YC_CHROME" --no-sandbox --disable-gpu --remote-debugging-port=9225 --user-data-dir="$YC_PROFILE" about:blank > "$YC_ARTIFACTS/chromium.log" 2>&1 &
YC_BROWSER_PID=$!
trap 'kill "$YC_FIXTURE_PID" "$YC_BROWSER_PID" 2>/dev/null || true; wait "$YC_FIXTURE_PID" "$YC_BROWSER_PID" 2>/dev/null || true; rm -rf -- "$YC_PROFILE"' EXIT
sleep 1
node tests/ycards_optics_check.mjs > "$YC_ARTIFACTS/optics.json"
"$YC_PYTHON" - <<'CHECK'
from PIL import Image
p=Image.open('tests/ycards-optics-artifacts/background-negative.png').getpixel((200,320))
# Actual background (40,80,120) through glass tint (166,215,243,.12), then inverted.
expected=[255-(bg*.88+fg*.12) for bg,fg in zip((40,80,120),(166,215,243))]
assert all(abs(a-b)<3 for a,b in zip(p,expected)), (p,expected)
CHECK
node tests/ycards_render_check.mjs > "$YC_ARTIFACTS/render.json"
YCARDS_ARTIFACTS="$YC_ARTIFACTS" node tests/ycards_v2_check.mjs > "$YC_ARTIFACTS/v2.json"
printf 'Optics and editor regression checks passed: %s\n' "$YC_ARTIFACTS"
