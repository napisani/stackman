from __future__ import annotations

import json
import os
from collections import defaultdict

from ..context import AppContext
from ..git_ops import current_branch, format_repo_key_for_display
from ..models import BranchRecord
from ..store import list_branches
from .shared import descendant_lines, resolve_repo

_BOLD = "\033[1m"
_DIM = "\033[2m"
_RESET = "\033[0m"


def _use_color(ctx: AppContext) -> bool:
    """Color only when stdout is a real terminal and NO_COLOR is unset."""
    if os.environ.get("NO_COLOR"):
        return False
    isatty = getattr(ctx.stdout, "isatty", None)
    try:
        return bool(isatty()) if callable(isatty) else False
    except Exception:
        return False


def run_repo_list(ctx: AppContext, *, as_json: bool = False) -> int:
    """List Stackman-tracked branches for the current repository as a stack tree."""
    worktree, repo_key, _ = resolve_repo(ctx)
    branches = list_branches(ctx.db_path, repo_key)

    try:
        current = current_branch(worktree)
    except Exception:
        current = ""

    if as_json:
        payload = {
            "repo": format_repo_key_for_display(repo_key),
            "worktree": str(worktree),
            "current": current or None,
            "branches": [
                {
                    "branch": row.branch_name,
                    "parent": row.parent_branch_name,
                    "fork_point": row.fork_point_sha,
                    "current": row.branch_name == current,
                }
                for row in branches
            ],
        }
        json.dump(payload, ctx.stdout)
        ctx.stdout.write("\n")
        return 0

    ctx.stdout.write(f"Tracked branches in {format_repo_key_for_display(repo_key)}\n")
    ctx.stdout.write(f"Worktree: {worktree}\n")
    if not branches:
        ctx.stdout.write("  (none)\n")
        return 0

    for line in _tree_lines(branches, current, color=_use_color(ctx)):
        ctx.stdout.write(f"{line}\n")
    return 0


def _tree_lines(branches: list[BranchRecord], current: str, *, color: bool = False) -> list[str]:
    tracked = {row.branch_name for row in branches}
    children: dict[str | None, list[BranchRecord]] = defaultdict(list)
    for row in branches:
        children[row.parent_branch_name].append(row)
    for rows in children.values():
        rows.sort(key=lambda row: row.branch_name)

    # Roots are the untracked parents branches hang off (anchors like main/master),
    # plus any tracked branch that records no parent at all.
    anchors = sorted(
        {
            row.parent_branch_name
            for row in branches
            if row.parent_branch_name not in tracked and row.parent_branch_name is not None
        }
    )

    lines: list[str] = []
    visited: set[str] = set()

    def label(name: str) -> str:
        text = f"{name} (current)" if name == current else name
        if color and name == current:
            return f"{_BOLD}{text}{_RESET}"
        return text

    def anchor_label(name: str) -> str:
        return f"{_DIM}{name}{_RESET}" if color else name

    def children_of(parent: str) -> list[tuple[str, BranchRecord]]:
        return [(row.branch_name, row) for row in children.get(parent, [])]

    def label_of(row: BranchRecord) -> str:
        return label(row.branch_name)

    for anchor in anchors:
        lines.append(anchor_label(anchor))
        sub_lines, sub_visited = descendant_lines(anchor, children_of, label_of)
        lines.extend(sub_lines)
        visited.update(sub_visited)

    # Tracked branches whose parent is None sit at the top level with no anchor header.
    for row in children.get(None, []):
        lines.append(label(row.branch_name))
        visited.add(row.branch_name)
        sub_lines, sub_visited = descendant_lines(row.branch_name, children_of, label_of)
        lines.extend(sub_lines)
        visited.update(sub_visited)

    # Anything not reachable from an anchor (disconnected parent or a cycle) still
    # gets shown so nothing is silently dropped.
    leftovers = [row for row in branches if row.branch_name not in visited]
    if leftovers:
        lines.append("(unlinked — parent not tracked or cyclic)")
        for row in sorted(leftovers, key=lambda row: row.branch_name):
            parent = row.parent_branch_name or "<none>"
            lines.append(f"  {label(row.branch_name)} → {parent}")

    return lines
