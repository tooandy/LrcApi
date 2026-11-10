#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOME_LAUNCH="$HOME/Library/LaunchAgents"
LABEL="local.home-server.lrcapi-editor"
DEST="$HOME_LAUNCH/$LABEL.plist"
umask 077
mkdir -p "$HOME_LAUNCH" "$HOME/Library/Logs/HomeServer"
test -x "$ROOT/admin/start.sh"
test -x "$ROOT/.venv/bin/python"
# Confirm preconditions before touching an existing job.
cd "$ROOT"
/opt/homebrew/bin/uv run --locked --no-sync python -m compileall -q admin/server.py
sed -e "s|__HOME__|$HOME|g" -e "s|__ROOT__|$ROOT|g"   deploy/local.home-server.lrcapi-editor.plist > "$DEST.new"
plutil -lint "$DEST.new"
if [ -f "$DEST" ]; then cp -p "$DEST" "$DEST.bak"; fi
mv "$DEST.new" "$DEST"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$DEST"
echo "Editor only on http://127.0.0.1:28886"
