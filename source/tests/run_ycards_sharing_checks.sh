#!/bin/bash
set -euo pipefail
YC_PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
YC_PYTHON="${YCARDS_PYTHON:-/opt/vps-python/bin/python}"
YC_CHROME="${YCARDS_CHROME:?Set YCARDS_CHROME to Chromium}"
YC_ARTIFACTS="${YCARDS_ARTIFACTS:-$YC_PROJECT_DIR/tests/ycards-sharing-artifacts}"
YC_PROFILE="$(mktemp -d /tmp/ycards-v2-chromium-XXXXXX)"
mkdir -p "$YC_ARTIFACTS"
"$YC_PYTHON" "$YC_PROJECT_DIR/tests/ycards_sharing_fixture.py" > "$YC_ARTIFACTS/fixture.log" 2>&1 &
YC_FIXTURE_PID=$!
"$YC_CHROME" --no-sandbox --disable-gpu --remote-debugging-port=9225 --user-data-dir="$YC_PROFILE" about:blank > "$YC_ARTIFACTS/chromium.log" 2>&1 &
YC_BROWSER_PID=$!
trap 'kill "$YC_FIXTURE_PID" "$YC_BROWSER_PID" 2>/dev/null || true; wait "$YC_FIXTURE_PID" "$YC_BROWSER_PID" 2>/dev/null || true; rm -rf -- "$YC_PROFILE"' EXIT
sleep 1
YCARDS_ARTIFACTS="$YC_ARTIFACTS" node "$YC_PROJECT_DIR/tests/ycards_sharing_check.mjs" > "$YC_ARTIFACTS/v2-result.json"
"$YC_PYTHON" - "$YC_ARTIFACTS" <<'CHECK'
import sys
from pathlib import Path
import cv2
artifacts=Path(sys.argv[1])
url,_,_=cv2.QRCodeDetector().detectAndDecode(cv2.imread(str(artifacts/'poster.png')))
assert url==(artifacts/'poster-url.txt').read_text(), 'Poster QR does not match public CID URL'
CHECK
printf 'Sharing browser checks passed: %s\n' "$YC_ARTIFACTS"
