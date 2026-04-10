#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

PYINSTALLER_CONFIG_DIR="$SCRIPT_DIR/.pyinstaller"
export PYINSTALLER_CONFIG_DIR

/usr/bin/env python3 -m PyInstaller \
  --clean \
  --noconfirm \
  --onefile \
  --name gif-crawler \
  bookmark_gif_scraper.py

echo
echo "打包完成: $SCRIPT_DIR/dist/gif-crawler"
