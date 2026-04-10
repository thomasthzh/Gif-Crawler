#!/usr/bin/env python3
from __future__ import annotations

import sqlite3
import time
from pathlib import Path


def _db_path(base_dir: Path) -> Path:
    return base_dir / "task-history.sqlite3"


def init_db(base_dir: Path) -> Path:
    path = _db_path(base_dir)
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS task_history (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              created_at TEXT NOT NULL,
              status TEXT NOT NULL,
              total INTEGER NOT NULL,
              ok_count INTEGER NOT NULL,
              failed_count INTEGER NOT NULL,
              gif_total INTEGER NOT NULL,
              output_path TEXT NOT NULL,
              note TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()
    return path


def add_record(
    base_dir: Path,
    status: str,
    total: int,
    ok_count: int,
    failed_count: int,
    gif_total: int,
    output_path: str,
    note: str = "",
) -> None:
    path = init_db(base_dir)
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            INSERT INTO task_history
            (created_at, status, total, ok_count, failed_count, gif_total, output_path, note)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                time.strftime("%Y-%m-%d %H:%M:%S"),
                status,
                int(total),
                int(ok_count),
                int(failed_count),
                int(gif_total),
                output_path,
                note,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def list_recent(base_dir: Path, limit: int = 30) -> list[dict[str, object]]:
    path = init_db(base_dir)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT id, created_at, status, total, ok_count, failed_count, gif_total, output_path, note
            FROM task_history
            ORDER BY id DESC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()
