#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
: "${ANDROID_HOME:?Set ANDROID_HOME to an installed SDK (platform 36, build-tools 36.0.0)}"
: "${JAVA_HOME:?Set JAVA_HOME to JDK21}"
: "${YICHAT_SIGNING_CONFIG:?Path to private signing-private.json outside this source checkout}"
if [[ ! -f "$YICHAT_SIGNING_CONFIG" ]]; then echo 'Private signing configuration missing' >&2; exit 1; fi
./gradlew --dependency-verification strict :app:assembleRelease :app:testDebugUnitTest "$@"
