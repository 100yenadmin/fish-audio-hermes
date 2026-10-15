#!/bin/bash
# Layer B: build or reuse a Hermes Desktop dev tree in the acceptance cache, then apply the scratch-only patches.
#   build.sh --host upstream|evaos --repo <git dir or URL> --ref <tag> [--cache <dir>] [--seed <built tree>]
# Prints the tree root on stdout (last line). Patches touch only the cache copy and are listed in <tree>/.acceptance-patches.
set -euo pipefail
HOST=""; REPO=""; REF=""; CACHE="${FISH_ACCEPTANCE_CACHE:-$HOME/.cache/fish-audio-acceptance}"; SEED=""
while [ $# -gt 0 ]; do case "$1" in
  --host) HOST=$2; shift 2;; --repo) REPO=$2; shift 2;; --ref) REF=$2; shift 2;;
  --cache) CACHE=$2; shift 2;; --seed) SEED=$2; shift 2;; *) echo "unknown arg $1" >&2; exit 2;; esac; done
[ -n "$HOST" ] && [ -n "$REPO" ] && [ -n "$REF" ] || { echo "need --host --repo --ref" >&2; exit 2; }
NODE_BIN="${ACCEPTANCE_NODE_BIN:-/opt/homebrew/bin}"
export PATH="$NODE_BIN:/usr/bin:/bin:/usr/sbin:/sbin" npm_config_cache="$CACHE/npm-cache" ELECTRON_CACHE="$CACHE/electron-cache"
unset NODE_ENV
BASE="$CACHE/desktop-$HOST-$REF"; TREE="$BASE/src"; mkdir -p "$BASE"
log() { echo "[build $HOST $REF] $*" >&2; }

if [ ! -f "$TREE/.acceptance-ready" ]; then
  rm -rf "$TREE"
  if [ -n "$SEED" ]; then
    log "APFS clone of seed tree"; cp -c -R "$SEED" "$TREE"
  else
    SRC="$REPO"
    if [ ! -d "$REPO/.git" ] && [ ! -f "$REPO/HEAD" ]; then
      SRC="$CACHE/repos/desktop-$HOST.git"
      [ -d "$SRC" ] || git clone --bare --filter=blob:none "$REPO" "$SRC" >&2
      git -C "$SRC" fetch --tags origin >&2 || true
    fi
    log "git archive $REF"; mkdir -p "$TREE"; git -C "$SRC" archive "$REF" | tar -x -C "$TREE"
    log "npm ci (root)"; (cd "$TREE" && npm ci --no-audit --no-fund > "$BASE/npm-ci.log" 2>&1) || { log "npm ci failed, see $BASE/npm-ci.log"; exit 1; }
  fi
  touch "$TREE/.acceptance-ready"
fi

MAIN="$TREE/apps/desktop/electron/main.ts"; PATCHES="$TREE/.acceptance-patches"; : > "$PATCHES"
# 1. evaOS: a dev build in token mode needs the managed-build switch off.
if [ "$HOST" = evaos ]; then
  sed -i '' 's/^const EVA_MANAGED_BUILD = true$/const EVA_MANAGED_BUILD = false/' "$MAIN"
  ln=$(grep -n '^const EVA_MANAGED_BUILD = false$' "$MAIN" | cut -d: -f1)
  [ -n "$ln" ] || { log "EVA_MANAGED_BUILD line not found"; exit 1; }
  echo "apps/desktop/electron/main.ts:$ln EVA_MANAGED_BUILD = true -> false" >> "$PATCHES"
fi
# 2. TCC gate: the mic-access IPC returns granted when HERMES_ACCEPTANCE_FAKE_MIC=1 (the renderer mic is a CDP shim).
if ! grep -q 'HERMES_ACCEPTANCE_FAKE_MIC' "$MAIN"; then
  /usr/bin/perl -0pi -e "s/(ipcMain\.handle\('hermes:requestMicrophoneAccess', async \(\) => \{\n)/\$1  if (process.env.HERMES_ACCEPTANCE_FAKE_MIC === '1') return true\n/" "$MAIN"
fi
ln=$(grep -n "HERMES_ACCEPTANCE_FAKE_MIC === '1'" "$MAIN" | cut -d: -f1)
[ -n "$ln" ] || { log "mic-access handler not found"; exit 1; }
echo "apps/desktop/electron/main.ts:$ln requestMicrophoneAccess returns true when HERMES_ACCEPTANCE_FAKE_MIC=1" >> "$PATCHES"
cat "$PATCHES" >&2
echo "$TREE"
