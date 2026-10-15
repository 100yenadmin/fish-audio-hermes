#!/usr/bin/env bash
# The only reader of machine-local credentials. Never enable shell tracing here.
set +x
set -euo pipefail
# Maintainer defaults; override with FISH_ACCEPTANCE_KEY_FILE / FISH_ACCEPTANCE_GLM_ENV.
KEY_FILE=${FISH_ACCEPTANCE_KEY_FILE:-$HOME/.config/fish-audio-hermes/test.env}
GLM_ENV=${FISH_ACCEPTANCE_GLM_ENV:-$HOME/.hermes/.env}
if [[ $(uname) == Darwin ]]; then
  if [[ -z ${FISH_API_KEY:-} && -f "$KEY_FILE" ]]; then
    set -a
    source "$KEY_FILE"
    set +a
  fi
  if [[ -z ${GLM_API_KEY:-} && -f "$GLM_ENV" ]]; then
    GLM_API_KEY=$(python3 - "$GLM_ENV" <<'PY'
import pathlib, sys
rows = [s.split('=', 1)[1].strip() for s in pathlib.Path(sys.argv[1]).read_text().splitlines()
        if s.startswith('GLM_API_KEY=')]
if len(rows) == 1:
    print(rows[0].strip(chr(34) + chr(39)), end='')
PY
)
    export GLM_API_KEY
  fi
fi
export HERMES_DISABLE_LAZY_INSTALLS=1
export GLM_BASE_URL=https://api.z.ai/api/coding/paas/v4
exec "$@"
