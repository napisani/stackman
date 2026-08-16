from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Sequence
from pathlib import Path

from ..context import AppContext
from ..git_ops import branch_exists, merge_base
from ..stack_ids import new_auto_stack_id
from ..store import (
    clear_branch_labels,
    get_branch,
    label_branch,
    list_branch_labels,
    list_branches,
    upsert_branch,
)
from .shared import resolve_repo


def run_track(ctx: AppContext, *, branch: str | None, parent: str) -> int:
    worktree, repo_key, branch_name = resolve_repo(ctx, branch)
    _ensure_branch_exists(worktree, branch_name, role="branch")
    _ensure_branch_exists(worktree, parent, role="parent")

    fork_point_sha = merge_base(worktree, branch_name, parent)
    upsert_branch(
        ctx.db_path,
        repo_root=repo_key,
        branch_name=branch_name,
        parent_branch_name=parent,
        fork_point_sha=fork_point_sha,
    )

    stack_ids, label_mode = _stack_labels_for_tracked_branch(ctx, repo_key, parent)
    anchor_branch_name = None if label_mode == "inherited" else parent
    _relabel(ctx, repo_key, branch_name, stack_ids, anchor_branch_name)
    # Keep the connected line in one stack: re-tracking an interior branch (e.g. onto a
    # now-untracked parent, which mints a new stack id) must not orphan descendants that
    # still carry the old label.
    _propagate_labels_to_descendants(ctx, repo_key, branch_name, stack_ids, anchor_branch_name)

    ctx.stdout.write(
        f"Tracked branch {branch_name!r} with parent {parent!r} at {fork_point_sha[:7]}.\n"
    )
    return 0


def _relabel(
    ctx: AppContext, repo_key: str, branch_name: str, stack_ids: list[str], anchor: str | None
) -> None:
    clear_branch_labels(ctx.db_path, repo_key, branch_name)
    for stack_id in stack_ids:
        label_branch(ctx.db_path, repo_key, branch_name, stack_id, anchor_branch_name=anchor)


def _propagate_labels_to_descendants(
    ctx: AppContext, repo_key: str, root: str, stack_ids: list[str], anchor: str | None
) -> None:
    children: dict[str, list[str]] = defaultdict(list)
    for record in list_branches(ctx.db_path, repo_key):
        if record.parent_branch_name is not None:
            children[record.parent_branch_name].append(record.branch_name)

    queue: deque[str] = deque(children.get(root, []))
    seen: set[str] = set()
    while queue:
        name = queue.popleft()
        if name in seen:
            continue
        seen.add(name)
        _relabel(ctx, repo_key, name, stack_ids, anchor)
        queue.extend(children.get(name, []))


def run_chain(ctx: AppContext, *, anchor: str, branches: Sequence[str]) -> int:
    worktree, repo_key, _ = resolve_repo(ctx)
    chain = [anchor, *branches]
    _validate_chain(chain)
    for branch_name in chain:
        _ensure_branch_exists(worktree, branch_name, role="branch")

    stack_id = _new_stack_id(ctx)
    for parent_branch, branch_name in zip(chain, chain[1:], strict=False):
        fork_point_sha = merge_base(worktree, branch_name, parent_branch)
        upsert_branch(
            ctx.db_path,
            repo_root=repo_key,
            branch_name=branch_name,
            parent_branch_name=parent_branch,
            fork_point_sha=fork_point_sha,
        )
        clear_branch_labels(ctx.db_path, repo_key, branch_name)
        label_branch(ctx.db_path, repo_key, branch_name, stack_id, anchor_branch_name=anchor)

    rendered_chain = " -> ".join(repr(branch_name) for branch_name in chain)
    ctx.stdout.write(f"Tracked stack chain {rendered_chain}.\n")
    return 0


def _ensure_branch_exists(worktree: Path, branch_name: str, *, role: str) -> None:
    if not branch_exists(worktree, branch_name):
        raise SystemExit(f"Unknown {role} {branch_name!r} in this Git repository.")


def _validate_chain(chain: Sequence[str]) -> None:
    if len(chain) < 2:
        raise SystemExit("chain requires an anchor and at least one stack branch.")
    duplicates = sorted({branch_name for branch_name in chain if chain.count(branch_name) > 1})
    if duplicates:
        joined = ", ".join(repr(branch_name) for branch_name in duplicates)
        raise SystemExit(f"chain must not repeat branch names: {joined}.")


def _stack_labels_for_tracked_branch(
    ctx: AppContext, repo_key: str, parent: str
) -> tuple[list[str], str]:
    parent_tracked = get_branch(ctx.db_path, repo_key, parent)
    parent_labels = (
        list_branch_labels(ctx.db_path, repo_key, parent) if parent_tracked is not None else []
    )
    if parent_labels:
        return list(parent_labels), "inherited"
    return [_new_stack_id(ctx)], "new_auto"


def _new_stack_id(ctx: AppContext) -> str:
    if ctx.stack_id_factory is not None:
        return ctx.stack_id_factory()
    return new_auto_stack_id()
