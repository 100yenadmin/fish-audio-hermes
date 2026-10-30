#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
PYTHON=python3
# Homebrew Python 3.11+; an upgrade can drop the unversioned python3 link, so fall back to a versioned one.
if [[ $(uname) == Darwin ]]; then
  PYTHON=${FISH_ACCEPTANCE_PYTHON:-}
  for candidate in /opt/homebrew/bin/python3 /opt/homebrew/bin/python3.1[1-9]; do
    if [[ -z $PYTHON && -x $candidate ]]; then PYTHON=$candidate; fi
  done
fi
exec "$ROOT/acceptance/with-keys.sh" "$PYTHON" "$ROOT/acceptance/conftest.py" run "$@"
