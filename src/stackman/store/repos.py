from __future__ import annotations

import sqlite3
import subprocess
from collections import defaultdict
from pathlib import Path

from ..git_ops import repo_db_key
from ..models import RepoRecord
from .connection import connect, normalize_path
from .rows import repo_from_row


def merge_repo_records(
    conn: sqlite3.Connection,
    *,
    survivor_id: int,
    victim_id: int,
) -> None:
    """Point victim branches at survivor, dedupe same branch name, then drop victim repo row."""
    victim_branches = conn.execute(
        "SELECT id, branch_name FROM branches WHERE repo_id = ?",
        (victim_id,),
    ).fetchall()
    for branch_row in victim_branches:
        bid = branch_row["id"]
        bname = branch_row["branch_name"]
        existing = conn.execute(
            "SELECT id FROM branches WHERE repo_id = ? AND branch_name = ?",
            (survivor_id, bname),
        ).fetchone()
        if existing is None:
            conn.execute(
                "UPDATE branches SET repo_id = ? WHERE id = ?",
                (survivor_id, bid),
            )
        else:
            # Survivor keeps its own stack label if it has one; otherwise
            # inherit the victim's (a branch has at most one stack label).
            conn.execute(
                """
                UPDATE branches SET stack_id = COALESCE(
                    (SELECT stack_id FROM branches WHERE id = ?),
                    (SELECT stack_id FROM branches WHERE id = ?)
                )
                WHERE id = ?
                """,
                (existing["id"], bid, existing["id"]),
            )
            conn.execute("DELETE FROM branches WHERE id = ?", (bid,))
    conn.execute("DELETE FROM repos WHERE id = ?", (victim_id,))


def migrate_repo_roots_to_git_common_dir(conn: sqlite3.Connection) -> None:
    """Collapse worktree-specific paths to ``git rev-parse --git-common-dir`` (one row per Git repo)."""
    rows = list(conn.execute("SELECT id, root_path FROM repos").fetchall())
    id_to_target: dict[int, str] = {}
    for row in rows:
        rid = row["id"]
        old_path = row["root_path"]
        candidate = Path(old_path)
        if not candidate.exists():
            id_to_target[rid] = normalize_path(old_path)
            continue
        git_cwd = candidate if candidate.is_dir() else candidate.parent
        try:
            new_key = repo_db_key(git_cwd)
        except (OSError, subprocess.CalledProcessError):
            id_to_target[rid] = normalize_path(old_path)
            continue
        new_norm = normalize_path(new_key)
        id_to_target[rid] = new_norm

    by_target: dict[str, list[int]] = defaultdict(list)
    for row in rows:
        by_target[id_to_target[row["id"]]].append(row["id"])

    for target_path, repo_ids in by_target.items():
        ids = sorted(set(repo_ids))
        survivor = ids[0]
        for victim in ids[1:]:
            merge_repo_records(conn, survivor_id=survivor, victim_id=victim)
        conn.execute(
            "UPDATE repos SET root_path = ? WHERE id = ?",
            (target_path, survivor),
        )


def upsert_repo(db_path: Path | str, root_path: Path | str) -> RepoRecord:
    normalized = normalize_path(root_path)
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO repos(root_path) VALUES (?) "
            "ON CONFLICT(root_path) DO UPDATE SET root_path=excluded.root_path",
            (normalized,),
        )
        row = conn.execute(
            "SELECT id, root_path, created_at FROM repos WHERE root_path = ?",
            (normalized,),
        ).fetchone()
    return repo_from_row(row)


def get_repo(db_path: Path | str, root_path: Path | str) -> RepoRecord | None:
    normalized = normalize_path(root_path)
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT id, root_path, created_at FROM repos WHERE root_path = ?",
            (normalized,),
        ).fetchone()
    return repo_from_row(row) if row else None
