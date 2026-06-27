#!/bin/zsh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

if ! command -v uv >/dev/null 2>&1; then
  echo "Error: uv is not installed or not in PATH."
  echo "Install uv first, then run this file again."
  echo
  read "_input?Press Enter to close..."
  exit 1
fi

SOURCE_PLIST="$SCRIPT_DIR/launchd/com.local.yt-downloader.plist"
TARGET_DIR="$HOME/Library/LaunchAgents"
TARGET_PLIST="$TARGET_DIR/com.local.yt-downloader.plist"

mkdir -p "$TARGET_DIR"
cp "$SOURCE_PLIST" "$TARGET_PLIST"

launchctl bootout "gui/$UID" "$TARGET_PLIST" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$UID" "$TARGET_PLIST"

echo "LaunchAgent installed: $TARGET_PLIST"
echo "On-demand wakeup is active. The backend will start when localhost:5000 is accessed."
echo "To remove it later, run: launchctl bootout gui/$UID $TARGET_PLIST"
echo
read "_input?Press Enter to close..."