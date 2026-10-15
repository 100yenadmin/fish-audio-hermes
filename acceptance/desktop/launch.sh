#!/bin/bash
# Layer B: start (or stop) a Desktop dev build in token mode against the host gateway.
#   launch.sh up   --tree <dir> --out <dir> --gateway <url> --token-file <f> [--vite-port 5194] [--cdp-port 9281]
#                  [--desktop-half <plugin.js>]
#   launch.sh down --out <dir>
# Vite runs on its own port with --strictPort (npm run dev hardcodes 5174). PIDs go to <out>/desktop-pids.txt;
# `down` stops only those recorded trees (by parent PID, never by name).
set -euo pipefail
CMD=$1; shift
TREE=""; OUT=""; GW=""; TOKF=""; VPORT=5194; CPORT=9281; HALF=""
while [ $# -gt 0 ]; do case "$1" in
  --tree) TREE=$2; shift 2;; --out) OUT=$2; shift 2;; --gateway) GW=$2; shift 2;; --token-file) TOKF=$2; shift 2;;
  --vite-port) VPORT=$2; shift 2;; --cdp-port) CPORT=$2; shift 2;; --desktop-half) HALF=$2; shift 2;;
  *) echo "unknown arg $1" >&2; exit 2;; esac; done
mkdir -p "$OUT"; OUT=$(cd "$OUT" && pwd); PIDS="$OUT/desktop-pids.txt"
collect() { local p=$1; for c in $(pgrep -P "$p"); do collect "$c"; done; echo "$p"; }
if [ "$CMD" = down ]; then
  [ -f "$PIDS" ] || exit 0
  for root in $(awk '{print $1}' "$PIDS"); do
    cmd=$(ps -p "$root" -o command= 2>/dev/null) || continue
    # A recorded PID may have been reused since: stop it only while it is still a vite or electron process.
    case "$cmd" in *vite*|*electron*) ;; *) echo "skip pid $root: no longer a Desktop process" >&2; continue;; esac
    all=$(collect "$root"); kill $all 2>/dev/null || true; sleep 2
    for p in $all; do ps -p "$p" >/dev/null 2>&1 && kill -9 "$p" 2>/dev/null || true; done
  done
  : > "$PIDS"; exit 0
fi
for p in $VPORT $CPORT; do lsof -nP -iTCP:"$p" -sTCP:LISTEN >/dev/null 2>&1 && { echo "port $p busy" >&2; exit 1; }; done
NODE_BIN="${ACCEPTANCE_NODE_BIN:-/opt/homebrew/bin}"
CACHE="${FISH_ACCEPTANCE_CACHE:-$HOME/.cache/fish-audio-acceptance}"
export PATH="$NODE_BIN:/usr/bin:/bin:/usr/sbin:/sbin" HERMES_DISABLE_LAZY_INSTALLS=1 npm_config_cache="$CACHE/npm-cache" \
  ELECTRON_CACHE="$CACHE/electron-cache"
unset NODE_ENV FISH_API_KEY GLM_API_KEY
DH="$OUT/desk-home"; UD="$OUT/desk-userdata"; rm -rf "$DH" "$UD"; mkdir -p "$DH" "$UD"  # fresh Desktop state per run
# Desktop half of the plugin under test, materialized the way Install from Git does (desktop-plugins/<id> + marker).
if [ -n "$HALF" ]; then
  mkdir -p "$DH/desktop-plugins/fish-audio"; cp "$HALF" "$DH/desktop-plugins/fish-audio/plugin.js"
  mt=$(/usr/bin/stat -f %m "$HALF")000
  printf '{"package":"fish-audio","source":"%s","sourceMtimeMs":%s}\n' "$(cd "$(dirname "$HALF")" && pwd)" "$mt" > "$DH/desktop-plugins/fish-audio/.hermes-package.json"
fi
cd "$TREE/apps/desktop"
node scripts/assert-root-install.mjs >&2
( exec npx vite --host 127.0.0.1 --port "$VPORT" --strictPort ) > "$OUT/vite.log" 2>&1 &
echo "$! vite" >> "$PIDS"
npx tsc --build tsconfig.electron.json >&2
npx wait-on -t 60000 "http://127.0.0.1:$VPORT" >&2
node scripts/bundle-electron-main.mjs --dev > "$OUT/bundle.log" 2>&1
( exec env XCURSOR_SIZE=24 HERMES_DESKTOP_DEV_SERVER="http://127.0.0.1:$VPORT" HERMES_ACCEPTANCE_FAKE_MIC=1 \
    HERMES_HOME="$DH" HERMES_DESKTOP_USER_DATA_DIR="$UD" HERMES_DESKTOP_CDP_PORT="$CPORT" \
    HERMES_DESKTOP_CWD="$OUT" HERMES_DESKTOP_REMOTE_URL="$GW" HERMES_DESKTOP_REMOTE_TOKEN="$(cat "$TOKF")" npx electron . ) > "$OUT/desktop.log" 2>&1 &
echo "$! electron" >> "$PIDS"
for i in $(seq 1 60); do curl -s -o /dev/null "http://127.0.0.1:$CPORT/json/version" && break; sleep 1; done
curl -s -o /dev/null "http://127.0.0.1:$CPORT/json/version" || { echo "CDP did not come up" >&2; exit 1; }
echo "desktop up: vite :$VPORT cdp :$CPORT"
