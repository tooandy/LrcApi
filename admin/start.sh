#!/bin/bash
# Home-LAN-only editor; no public Nginx route or wildcard bind.
set -euo pipefail
umask 077
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export HOME_SERVER_MUSIC_ROOT="/Users/Shared/nfs/v2/Music"
export LRCAPI_DB_PATH="${LRCAPI_DB_PATH:-$HOME/Library/Application Support/HomeServer/LrcAPI/overrides.sqlite3}"
SECRETS="$HOME/.config/home-server/lrcapi-editor.env"
test -r "$SECRETS" || { echo "Missing private editor credentials" >&2; exit 1; }
set -a
. "$SECRETS"
set +a
test "${#LRCAPI_EDITOR_PASSWORD}" -ge 24 || { echo "Editor password too short" >&2; exit 1; }
test -f "$ROOT/uv.lock"
test -x "$ROOT/.venv/bin/python"
cd "$ROOT"
exec "$ROOT/.venv/bin/python" -m admin.server
