#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

PYINSTALLER_CONFIG_DIR="$SCRIPT_DIR/.pyinstaller"
export PYINSTALLER_CONFIG_DIR

/usr/bin/env python3 -m PyInstaller \
  --clean \
  --noconfirm \
  --windowed \
  --icon "$SCRIPT_DIR/app.icns" \
  --add-data "$SCRIPT_DIR/爱丽丝.gif:." \
  --name GIF-Crawler \
  gif_crawler_gui.py

echo
echo "GUI 打包完成: $SCRIPT_DIR/dist/GIF-Crawler.app"
