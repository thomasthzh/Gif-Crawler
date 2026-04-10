#!/usr/bin/env python3
from __future__ import annotations

import os
import platform
import shutil
from pathlib import Path

import PyInstaller.__main__


def main() -> int:
    root = Path(__file__).resolve().parent
    script = root / "gif_crawler_gui.py"
    icon = root / "app.icns"
    gif = root / "爱丽丝.gif"
    name = "GIF-Crawler"

    sep = ";" if os.name == "nt" else ":"
    opts = [
        "--clean",
        "--noconfirm",
        "--windowed",
        "--onefile",
        "--name",
        name,
        "--add-data",
        f"{gif}{sep}.",
    ]

    if os.name == "nt":
        ico_path = root / "app.ico"
        if ico_path.exists():
            opts.extend(["--icon", str(ico_path)])
    elif icon.exists():
        opts.extend(["--icon", str(icon)])

    opts.append(str(script))
    PyInstaller.__main__.run(opts)

    dist = root / "dist"
    if platform.system() == "Windows":
        src = dist / f"{name}.exe"
        dst = dist / "GIF-Crawler-Windows.exe"
        if src.exists():
            shutil.copy2(src, dst)
    elif platform.system() == "Linux":
        src = dist / name
        dst = dist / "GIF-Crawler-Linux"
        if src.exists():
            shutil.copy2(src, dst)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
