#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

/usr/bin/env python3 "$SCRIPT_DIR/gif_crawler_gui.py"
