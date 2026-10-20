#!/bin/bash
# Build assets in an unprivileged checkout, then atomically promote only web code.
set -euo pipefail
repo=$(cd "$(dirname "$0")/../.." && pwd)
cd "$repo/yichat/client"
npm ci --no-audit --no-fund
npm test
npm run build
printf 'Built %s\n' "$repo/yichat/client/dist"
printf 'Deploy the reviewed Git commit, then set YICHAT_WEB_ROOT to a versioned copy of dist. See docs/WEB-DEPLOYMENT.md.\n'
