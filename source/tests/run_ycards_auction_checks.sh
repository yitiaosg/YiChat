#!/bin/bash
# Optional browser regression suite. All identities and points use a temporary DB.
set -euo pipefail
YC_PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
YC_PYTHON="${YCARDS_PYTHON:-/opt/vps-python/bin/python}"
YC_CHROME="${YCARDS_CHROME:?Set YCARDS_CHROME to a Chromium or chrome-headless-shell executable}"
YC_ARTIFACTS="${YCARDS_ARTIFACTS:-$YC_PROJECT_DIR/tests/ycards-auction-artifacts}"
YC_PROFILE="$(mktemp -d /tmp/ycards-chromium-XXXXXX)"
mkdir -p "$YC_ARTIFACTS"
"$YC_PYTHON" "$YC_PROJECT_DIR/tests/ycards_auction_fixture.py" > "$YC_ARTIFACTS/fixture.log" 2>&1 &
YC_FIXTURE_PID=$!
"$YC_CHROME" --no-sandbox --disable-gpu --remote-debugging-port=9225 --user-data-dir="$YC_PROFILE" about:blank > "$YC_ARTIFACTS/chromium.log" 2>&1 &
YC_BROWSER_PID=$!
trap 'kill "$YC_FIXTURE_PID" "$YC_BROWSER_PID" 2>/dev/null || true; wait "$YC_FIXTURE_PID" "$YC_BROWSER_PID" 2>/dev/null || true; rm -rf -- "$YC_PROFILE"' EXIT
sleep 1
YCARDS_ARTIFACTS="$YC_ARTIFACTS" node "$YC_PROJECT_DIR/tests/ycards_auction_check.mjs" > "$YC_ARTIFACTS/browser-result.json"
printf 'Auction browser checks passed. Artifacts: %s\n' "$YC_ARTIFACTS"
