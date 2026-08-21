from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path

from .context import AppContext
from .git_ops import current_branch, repo_db_key, repo_root
from .store import initialize


def emit(ctx: AppContext, message: str) -> None:
    """Write a progress/narration line to stdout, ensuring a trailing newline + flush."""
    ctx.stdout.write(message)
    if not message.endswith("\n"):
        ctx.stdout.write("\n")
    ctx.stdout.flush()


def resolve_repo(ctx: AppContext, branch: str | None = None) -> tuple[Path, str, str]:
    """Shared command preamble: initialize the DB and resolve (worktree, repo_key, branch_name).

    ``branch_name`` defaults to the currently checked-out branch when ``branch`` is None.
    """
    initialize(ctx.db_path)
    worktree = repo_root(ctx.cwd)
    repo_key = repo_db_key(ctx.cwd)
    branch_name = branch or current_branch(worktree)
    return worktree, repo_key, branch_name


def descendant_lines[Node](
    parent_key: str,
    children_of: Callable[[str], Iterable[tuple[str, Node]]],
    label_of: Callable[[Node], str],
) -> tuple[list[str], list[str]]:
    """Render the ASCII-tree descendant lines under ``parent_key``.

    ``children_of(key)`` yields ``(child_key, node)`` pairs; ``label_of(node)`` renders
    each node's text. Returns ``(lines, visited_keys)`` — visited_keys are every child_key
    rendered, so callers can detect unreachable/leftover nodes.
    """
    lines: list[str] = []
    visited: list[str] = []

    def visit(key: str, prefix: str) -> None:
        kids = list(children_of(key))
        for index, (child_key, node) in enumerate(kids):
            is_last = index == len(kids) - 1
            connector = "└── " if is_last else "├── "
            lines.append(f"{prefix}{connector}{label_of(node)}")
            visited.append(child_key)
            visit(child_key, prefix + ("    " if is_last else "│   "))

    visit(parent_key, "")
    return lines, visited
