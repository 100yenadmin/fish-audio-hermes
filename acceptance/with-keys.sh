#!/usr/bin/env bash
# The only reader of machine-local credentials. Never enable shell tracing here.
set +x
set -euo pipefail
# Keys come from the environment, or from files the maintainer names explicitly: FISH_ACCEPTANCE_KEY_FILE (a
# FISH_API_KEY=... file) and FISH_ACCEPTANCE_GLM_ENV (an env file holding one GLM_API_KEY= line). There are no
# default paths, so nothing here reads a profile's .env unless asked to.
KEY_FILE=${FISH_ACCEPTANCE_KEY_FILE:-}
GLM_ENV=${FISH_ACCEPTANCE_GLM_ENV:-}
read_key() {
  python3 - "$1" "$2" <<'PYKEY'
import pathlib, sys
rows = [s.split('=', 1)[1].strip() for s in pathlib.Path(sys.argv[1]).read_text().splitlines()
        if s.startswith(sys.argv[2] + '=')]
if len(rows) == 1:
    print(rows[0].strip("\"'"), end='')
PYKEY
}
for file in "$KEY_FILE" "$GLM_ENV"; do
  if [[ -n "$file" && ! -f "$file" ]]; then echo "with-keys.sh: named key file missing: $file" >&2; exit 2; fi
done
# A named file must supply its key exactly once; an empty GLM key would turn every agent row into an allowed skip.
load_key() {  # load_key FILE NAME
  local value
  value=$(read_key "$1" "$2")
  if [[ -z "$value" ]]; then echo "with-keys.sh: $1 must set $2 exactly once" >&2; exit 2; fi
  export "$2=$value"
}
if [[ -z ${FISH_API_KEY:-} && -n "$KEY_FILE" ]]; then load_key "$KEY_FILE" FISH_API_KEY; fi
if [[ -z ${GLM_API_KEY:-} && -n "$GLM_ENV" ]]; then load_key "$GLM_ENV" GLM_API_KEY; fi
if [[ -z ${FISH_API_KEY:-} ]]; then
  echo "with-keys.sh: set FISH_API_KEY, or FISH_ACCEPTANCE_KEY_FILE to a file that sets it" >&2
  exit 2
fi
export HERMES_DISABLE_LAZY_INSTALLS=1
export GLM_BASE_URL=https://api.z.ai/api/coding/paas/v4
exec "$@"
