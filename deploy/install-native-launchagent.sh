#!/bin/bash
# Install the Mac user launchd job from the versioned template.
# This script is intended for use after creating a dedicated Python venv
# and a private ~/.config/home-server/lrcapi.env file.
set -euo pipefail
ROOT="${LRCAPI_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
TEMPLATE="$ROOT/deploy/local.home-server.lrcapi.plist"
DEST="${HOME}/Library/LaunchAgents/local.home-server.lrcapi.plist"
mkdir -p "${HOME}/Library/LaunchAgents" "${HOME}/Library/Logs/HomeServer"
test -x "$ROOT/scripts/start-native.sh"
test -r "${HOME}/.config/home-server/lrcapi.env"
umask 077
sed "s|__HOME__|${HOME}|g" "$TEMPLATE" > "$DEST.new"
plutil -lint "$DEST.new"
if [ -f "$DEST" ]; then
    cp -p "$DEST" "$DEST.bak-install"
fi
mv "$DEST.new" "$DEST"
launchctl bootout "gui/$(id -u)/local.home-server.lrcapi" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$DEST"
echo "LrcAPI launchd job installed; check curl http://192.168.3.7:28883/time"
