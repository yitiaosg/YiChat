#!/usr/bin/env bash
# Run as the normal developer, never as root on an existing server.
set -euo pipefail
TASK_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$TASK_ROOT"
if [[ ${EUID} -eq 0 && -e /etc/yichat/yichat.env ]]; then
  echo 'Refusing to bootstrap tests as root on a configured YiChat server.' >&2
  exit 1
fi
python3 -m venv .venv-handoff
.venv-handoff/bin/python -m pip install -r yichat/requirements.lock
.venv-handoff/bin/python -m pip install -r yichat/handoff/requirements-main.in
npm ci --prefix yichat/client --no-audit --no-fund
npm ci --prefix yichat/tests/browser --no-audit --no-fund
(cd yichat/tests/browser && npx --no-install playwright install chromium)
npm test --prefix yichat/client
npm run build --prefix yichat/client
printf '%s\n' 'Dependencies and Web reference ready.' 'Before pytest: install/start dedicated local PostgreSQL, then run:' '.venv-handoff/bin/python yichat/deploy/provision_archive.py --test' 'PYTHONPATH=. .venv-handoff/bin/python -m pytest -q yichat/tests -s' 'Do not run PostgreSQL-dependent suites in parallel. Never use production credentials.'
