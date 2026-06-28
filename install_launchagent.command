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
BACKEND_PORT="$(uv run python -c 'from config import BACKEND_PORT; print(BACKEND_PORT)')"

mkdir -p "$TARGET_DIR"
cp "$SOURCE_PLIST" "$TARGET_PLIST"

python3 - "$TARGET_PLIST" "$SCRIPT_DIR" "$BACKEND_PORT" <<'PY'
from pathlib import Path
import sys

plist_path = Path(sys.argv[1])
project_dir = sys.argv[2]
backend_port = sys.argv[3]
content = plist_path.read_text(encoding='utf-8')

if '__PROJECT_DIR__' not in content:
    raise SystemExit('Could not locate __PROJECT_DIR__ in plist')
content = content.replace('__PROJECT_DIR__', project_dir)

old = """<key>SockServiceName</key>
  <string>__BACKEND_PORT__</string>"""
new = f"""<key>SockServiceName</key>
  <string>{backend_port}</string>"""
if old not in content:
    raise SystemExit('Could not locate SockServiceName in plist')
content = content.replace(old, new)

plist_path.write_text(content, encoding='utf-8')
PY

launchctl bootout "gui/$UID" "$TARGET_PLIST" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$UID" "$TARGET_PLIST"

echo "LaunchAgent installed: $TARGET_PLIST"
echo "On-demand wakeup is active. The backend will start when localhost:$BACKEND_PORT is accessed."
echo "To remove it later, run: launchctl bootout gui/$UID $TARGET_PLIST"
echo
read "_input?Press Enter to close..."