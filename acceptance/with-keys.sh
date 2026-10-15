#!/usr/bin/env bash
# The only reader of machine-local credentials. Never enable shell tracing here.
set +x
set -euo pipefail
# Keys come from the environment, or from files the maintainer names explicitly: FISH_ACCEPTANCE_KEY_FILE (a
# FISH_API_KEY=... file) and FISH_ACCEPTANCE_GLM_ENV (an env file holding one GLM_API_KEY= line). There are no
# default paths, so nothing here reads a profile's .env unless asked to.
KEY_FILE=${FISH_ACCEPTANCE_KEY_FILE:-}
GLM_ENV=${FISH_ACCEPTANCE_GLM_ENV:-}
if [[ $(uname) == Darwin ]]; then
  if [[ -z ${FISH_API_KEY:-} && -n "$KEY_FILE" && -f "$KEY_FILE" ]]; then
    set -a
    source "$KEY_FILE"
    set +a
  fi
  if [[ -z ${GLM_API_KEY:-} && -n "$GLM_ENV" && -f "$GLM_ENV" ]]; then
    GLM_API_KEY=$(python3 - "$GLM_ENV" <<'PY'
import pathlib, sys
rows = [s.split('=', 1)[1].strip() for s in pathlib.Path(sys.argv[1]).read_text().splitlines()
        if s.startswith('GLM_API_KEY=')]
if len(rows) == 1:
    print(rows[0].strip("\"'"), end='')
PY
)
    export GLM_API_KEY
  fi
fi
if [[ -z ${FISH_API_KEY:-} ]]; then
  echo "with-keys.sh: set FISH_API_KEY, or FISH_ACCEPTANCE_KEY_FILE to a file that sets it" >&2
  exit 2
fi
export HERMES_DISABLE_LAZY_INSTALLS=1
export GLM_BASE_URL=https://api.z.ai/api/coding/paas/v4
exec "$@"
