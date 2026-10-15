#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
PYTHON=python3
if [[ $(uname) == Darwin ]]; then PYTHON=/opt/homebrew/bin/python3; fi
exec "$ROOT/acceptance/with-keys.sh" "$PYTHON" "$ROOT/acceptance/conftest.py" run "$@"
