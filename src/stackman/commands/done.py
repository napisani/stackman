from __future__ import annotations

from pathlib import Path

from ..lib.command_support import emit as _emit
from ..lib.command_support import resolve_repo
from ..lib.context import AppContext
from ..lib.git_ops import merge_base
from ..lib.store import (
    get_branch,
    list_branches_with_parent,
    reparent_children_and_delete_branch,
)


def run(ctx: AppContext, *, branch: str | None, dry_run: bool = False) -> int:
    """Mark a tracked branch as done and lift its children onto its parent."""
    worktree, repo_key, branch_name = resolve_repo(ctx, branch)
    tracked = get_branch(ctx.db_path, repo_key, branch_name)
    if tracked is None:
        raise SystemExit(
            f"Branch {branch_name!r} is not tracked in this repository. "
            "Pass the branch that was merged, e.g. `stackman done feature-name`."
        )

    parent_name = tracked.parent_branch_name
    if parent_name is None:
        raise SystemExit(
            f"Branch {branch_name!r} has no recorded parent; use `stackman forget {branch_name}` "
            "if you only want to remove tracking."
        )

    return _drop_branch_and_reparent_children(
        ctx,
        worktree=worktree,
        repo_key=repo_key,
        branch_name=branch_name,
        parent_name=parent_name,
        dry_run=dry_run,
    )


def _drop_branch_and_reparent_children(
    ctx: AppContext,
    *,
    worktree: Path,
    repo_key: str,
    branch_name: str,
    parent_name: str,
    dry_run: bool,
) -> int:
    children = list_branches_with_parent(ctx.db_path, repo_key, branch_name)

    if dry_run:
        _emit(
            ctx,
            f"[stackman] Dry run: would mark {branch_name!r} done, remove it from tracking, "
            f"and reparent {len(children)} child branch(es) onto {parent_name!r}:",
        )
        for row in children:
            _emit(ctx, f"  - {row.branch_name}: parent {branch_name!r} → {parent_name!r}")
        if not children:
            _emit(ctx, f"  (no branches stacked on {branch_name!r})")
        _emit(ctx, "[stackman] Dry run complete (no database changes).")
        return 0

    # Compute fork-points (git) up front, then commit the reparent + delete as one
    # transaction so an interruption can't leave a half-reparented graph.
    reparents = [
        (str(row.branch_name), parent_name, merge_base(worktree, row.branch_name, parent_name))
        for row in children
    ]
    if not reparent_children_and_delete_branch(
        ctx.db_path, repo_key, branch_name=branch_name, reparents=reparents
    ):
        raise SystemExit(f"Failed to remove branch {branch_name!r} from stackman tracking.")

    if children:
        names = ", ".join(sorted(row.branch_name for row in children))
        ctx.stdout.write(
            f"Marked {branch_name!r} done: reparented [{names}] onto {parent_name!r} "
            "and removed it from stackman tracking (Git branches unchanged).\n"
        )
    else:
        ctx.stdout.write(
            f"Marked {branch_name!r} done: removed it from stackman tracking "
            "(Git branches unchanged).\n"
        )
    return 0
