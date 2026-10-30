#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
PYTHON=${FISH_ACCEPTANCE_PYTHON:-}
# Homebrew Python 3.11+; an upgrade can drop the unversioned python3 link, so fall back to a versioned one.
if [[ -z $PYTHON && $(uname) == Darwin ]]; then
  for candidate in /opt/homebrew/bin/python3 /opt/homebrew/bin/python3.1[1-9]; do
    if [[ -z $PYTHON && -x $candidate ]]; then PYTHON=$candidate; fi
  done
fi
PYTHON=${PYTHON:-python3}
exec "$PYTHON" "$ROOT/acceptance/conftest.py" control "$@"
