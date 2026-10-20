#!/bin/bash
set -euo pipefail
YC_PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
YC_PYTHON="${YCARDS_PYTHON:-/opt/vps-python/bin/python}"
YC_CHROME="${YCARDS_CHROME:?Set YCARDS_CHROME to Chromium}"
YC_ARTIFACTS="${YCARDS_ARTIFACTS:-$YC_PROJECT_DIR/tests/ycards-library-artifacts}"
YC_PROFILE="$(mktemp -d /tmp/ycards-v2-chromium-XXXXXX)"
mkdir -p "$YC_ARTIFACTS"
"$YC_PYTHON" "$YC_PROJECT_DIR/tests/ycards_library_fixture.py" > "$YC_ARTIFACTS/fixture.log" 2>&1 &
YC_FIXTURE_PID=$!
"$YC_CHROME" --no-sandbox --disable-gpu --remote-debugging-port=9225 --user-data-dir="$YC_PROFILE" about:blank > "$YC_ARTIFACTS/chromium.log" 2>&1 &
YC_BROWSER_PID=$!
trap 'kill "$YC_FIXTURE_PID" "$YC_BROWSER_PID" 2>/dev/null || true; wait "$YC_FIXTURE_PID" "$YC_BROWSER_PID" 2>/dev/null || true; rm -rf -- "$YC_PROFILE"' EXIT
for attempt in {1..60}; do
 if curl -fsS http://127.0.0.1:5079/test-library >/dev/null 2>&1; then break; fi
 sleep 0.25
done
curl -fsS http://127.0.0.1:5079/test-library >/dev/null
YCARDS_ARTIFACTS="$YC_ARTIFACTS" node "$YC_PROJECT_DIR/tests/ycards_library_check.mjs" > "$YC_ARTIFACTS/v2-result.json"
printf 'Library browser checks passed: %s\n' "$YC_ARTIFACTS"
