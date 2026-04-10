#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageSequence


def main() -> int:
    root = Path(__file__).resolve().parent
    gif = root / "爱丽丝.gif"
    ico = root / "app.ico"
    if not gif.exists():
        return 1
    with Image.open(gif) as im:
        first = next(ImageSequence.Iterator(im)).convert("RGBA")
        sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
        first.save(ico, format="ICO", sizes=sizes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
