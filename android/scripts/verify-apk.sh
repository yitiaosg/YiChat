#!/usr/bin/env bash
set -euo pipefail
: "${ANDROID_HOME:?Set ANDROID_HOME}"
APK_PATH=${1:?APK file required}
"$ANDROID_HOME/build-tools/36.0.0/apksigner" verify --verbose --print-certs "$APK_PATH"
"$ANDROID_HOME/build-tools/36.0.0/zipalign" -c -P 16 -v 4 "$APK_PATH"
sha256sum "$APK_PATH"
