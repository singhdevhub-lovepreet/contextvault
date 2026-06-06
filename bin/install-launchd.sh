#!/usr/bin/env bash
# Install ContextVault sweeper daemon via launchd

set -euo pipefail

PLIST_SRC="$(dirname "$0")/../Library/LaunchAgents/com.contextvault.sweeper.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.contextvault.sweeper.plist"

if [[ ! -f "$PLIST_SRC" ]]; then
    echo "Error: plist not found at $PLIST_SRC" >&2
    exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents"

# Unload existing if present
launchctl unload "$PLIST_DST" 2>/dev/null || true

# Copy and load
cp "$PLIST_SRC" "$PLIST_DST"
launchctl load "$PLIST_DST"

echo "Installed and loaded sweeper: $PLIST_DST"
echo "Logs: /tmp/contextvault-sweeper.*.log"