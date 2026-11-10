#!/bin/bash
# Separate loopback-only editor, never exposed by Debian Nginx.
set -euo pipefail
umask 077
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export HOME_SERVER_MUSIC_ROOT="/Users/Shared/nfs/v2/Music"
export LRCAPI_DB_PATH="${LRCAPI_DB_PATH:-$HOME/Library/Application Support/HomeServer/LrcAPI/overrides.sqlite3}"
test -f "$ROOT/uv.lock"
test -x "$ROOT/.venv/bin/python"
cd "$ROOT"
exec "$ROOT/.venv/bin/python" -m admin.server
