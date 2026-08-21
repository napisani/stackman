from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


def normalize_path(value: Path | str) -> str:
    return str(Path(value).expanduser().resolve())


@contextmanager
def connect(db_path: Path | str) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(Path(db_path))
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        # Wait for a contending writer instead of failing immediately with
        # "database is locked" — stackman is designed to run across worktrees.
        conn.execute("PRAGMA busy_timeout = 5000")
        # WAL lets readers (e.g. shell completion) proceed while a writer holds the db.
        conn.execute("PRAGMA journal_mode = WAL")
        yield conn
        conn.commit()
    finally:
        conn.close()
