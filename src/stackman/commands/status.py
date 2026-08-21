from __future__ import annotations

import json

from ..lib.command_support import resolve_repo
from ..lib.context import AppContext
from ..lib.git_ops import branch_exists, format_repo_key_for_display
from ..lib.store import get_branch


def run(ctx: AppContext, *, branch: str | None = None, as_json: bool = False) -> int:
    worktree, repo_key, branch_name = resolve_repo(ctx, branch)
    if branch is not None and not branch_exists(worktree, branch_name):
        raise SystemExit(f"Branch {branch_name!r} does not exist in this Git repository.")

    tracked = get_branch(ctx.db_path, repo_key, branch_name)

    if as_json:
        payload = {
            "branch": branch_name,
            "tracked": tracked is not None,
            "parent": tracked.parent_branch_name if tracked else None,
            "fork_point": tracked.fork_point_sha if tracked else None,
            "worktree": str(worktree),
            "repo": format_repo_key_for_display(repo_key),
        }
        json.dump(payload, ctx.stdout)
        ctx.stdout.write("\n")
        # A successful query is exit 0 whether or not the branch is tracked, so
        # scripts/agents can parse the result without branching on exit code.
        return 0

    if tracked is None:
        ctx.stdout.write(
            f"Branch {branch_name!r} is not tracked in this Git repository "
            f"({format_repo_key_for_display(repo_key)}; worktree {worktree}).\n"
        )
        return 1

    parent_display = tracked.parent_branch_name or "<none>"
    ctx.stdout.write(f"branch: {tracked.branch_name}\n")
    ctx.stdout.write(f"worktree: {worktree}\n")
    ctx.stdout.write(f"parent: {parent_display}\n")
    ctx.stdout.write(f"fork-point: {tracked.fork_point_sha}\n")
    return 0
