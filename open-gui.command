#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_PATH="$SCRIPT_DIR/dist/GIF-Crawler.app"

if [[ ! -d "$APP_PATH" ]]; then
  echo "未找到应用: $APP_PATH"
  exit 1
fi

# 避免 macOS 隔离属性导致“无法启动”
xattr -dr com.apple.quarantine "$APP_PATH" 2>/dev/null || true

open "$APP_PATH"
