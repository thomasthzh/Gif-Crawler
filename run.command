#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

if [[ "$#" -eq 0 ]]; then
  /usr/bin/env python3 "$SCRIPT_DIR/bookmark_gif_scraper.py" --output "$SCRIPT_DIR/scrape-report.html"
elif [[ "${1:-}" != -* && -f "${1:-}" ]]; then
  INPUT_FILE="$1"
  shift
  OUTPUT_FILE="$SCRIPT_DIR/scrape-report.html"
  if [[ "$#" -gt 0 && "${1:-}" != -* ]]; then
    OUTPUT_FILE="$1"
    shift
  fi
  /usr/bin/env python3 "$SCRIPT_DIR/bookmark_gif_scraper.py" --input "$INPUT_FILE" --output "$OUTPUT_FILE" "$@"
else
  /usr/bin/env python3 "$SCRIPT_DIR/bookmark_gif_scraper.py" "$@"
fi

echo
echo "任务执行完成。"
if [[ -t 0 ]]; then
  echo "按回车退出..."
  read -r
fi
