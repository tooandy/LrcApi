#!/bin/bash
# Mac mini launchd native LrcAPI. Secrets stay outside the source checkout.
set -euo pipefail
umask 077
ROOT="${HOME}/Documents/Code/LrcApi"
SECRETS="${HOME}/.config/home-server/lrcapi.env"
test -r "$SECRETS" || { echo "Missing LrcAPI auth configuration" >&2; exit 1; }
set -a
# Stored with mode 0600, generated using shlex.quote.
. "$SECRETS"
set +a
test -n "${API_AUTH:-}" || { echo "LrcAPI auth must be configured" >&2; exit 1; }
export HOME_SERVER_MUSIC_ROOT="/Users/Shared/nfs/v2/Music"
export LRCAPI_READ_ONLY=1
export LRCAPI_CHECK_UPDATES=0
export LRCAPI_CACHE_DIR="${HOME}/Library/Caches/HomeServer/LrcAPI"
export LRCAPI_LOG_DIR="${HOME}/Library/Logs/HomeServer/LrcAPI"
mkdir -p "$LRCAPI_CACHE_DIR" "$LRCAPI_LOG_DIR"
cd "$ROOT"
exec "$ROOT/.venv/bin/python" "$ROOT/app.py" --ip 192.168.3.7 --port "${LRCAPI_LISTEN_PORT:-28884}"
