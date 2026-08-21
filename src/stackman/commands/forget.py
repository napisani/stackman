from __future__ import annotations

from collections import defaultdict

from ..lib.command_support import resolve_repo
from ..lib.context import AppContext
from ..lib.git_ops import format_repo_key_for_display, repo_db_key
from ..lib.store import (
    delete_all_branches,
    delete_all_branches_global,
    delete_branch,
    get_branch,
    initialize,
    list_all_branches,
    list_branches,
    list_branches_with_parent,
)


def run(ctx: AppContext, *, branch: str | None) -> int:
    """Stop tracking a branch without changing child lineage."""
    _, repo_key, branch_name = resolve_repo(ctx, branch)
    tracked = get_branch(ctx.db_path, repo_key, branch_name)
    if tracked is None:
        raise SystemExit(f"Branch {branch_name!r} is not tracked in this repository.")

    children = list_branches_with_parent(ctx.db_path, repo_key, branch_name)
    if not delete_branch(ctx.db_path, repo_key, branch_name):
        raise SystemExit(f"Failed to remove branch {branch_name!r} from stackman tracking.")

    ctx.stdout.write(f"Forgot branch {branch_name!r} (Git branches unchanged).\n")
    if children:
        names = ", ".join(sorted(row.branch_name for row in children))
        ctx.stdout.write(
            f"Note: child branch(es) [{names}] still record {branch_name!r} as their parent. "
            f"Use `stackman done {branch_name}` instead when a merged branch should reparent its children.\n"
        )
    return 0


def run_all(ctx: AppContext, *, is_global: bool, assume_yes: bool, dry_run: bool = False) -> int:
    """Forget every tracked branch — for the current repo, or globally with is_global."""
    initialize(ctx.db_path)

    if is_global:
        doomed = list_all_branches(ctx.db_path)
        scope = "ALL repositories"
    else:
        repo_key = repo_db_key(ctx.cwd)
        doomed = list_branches(ctx.db_path, repo_key)
        if not doomed:
            ctx.stdout.write("Nothing to forget: no tracked branches in this repository.\n")
            return 0
        scope = f"{format_repo_key_for_display(repo_key)} ({len(doomed)} branch(es))"

    if dry_run:
        return _report_dry_run(ctx, doomed=doomed, is_global=is_global, scope=scope)

    if not assume_yes:
        if not _stdin_is_tty(ctx):
            # A TTY is never required: refuse loudly instead of blocking on a prompt
            # that nothing will answer. Scripts/agents pass --yes to proceed.
            raise SystemExit(
                "Refusing to forget all tracking without confirmation: stdin is not a TTY. "
                "Re-run with --yes to proceed non-interactively."
            )
        if not _confirm(ctx, scope, is_global=is_global):
            ctx.stderr.write("Aborted; nothing was forgotten.\n")
            return 1

    if is_global:
        removed = delete_all_branches_global(ctx.db_path)
        ctx.stdout.write(
            f"Forgot {removed} tracked branch(es) across all repositories "
            f"(Git branches unchanged).\n"
        )
    else:
        removed = delete_all_branches(ctx.db_path, repo_key)
        ctx.stdout.write(
            f"Forgot {removed} tracked branch(es) in this repository (Git branches unchanged).\n"
        )
    return 0


def _report_dry_run(ctx: AppContext, *, doomed, is_global: bool, scope: str) -> int:
    if not doomed:
        ctx.stdout.write("Dry run: nothing to forget (no tracked branches).\n")
        return 0
    ctx.stdout.write(
        f"Dry run: would forget all Stackman tracking for {scope} (Git branches unchanged):\n"
    )
    if is_global:
        by_repo: dict[str, list] = defaultdict(list)
        for row in doomed:
            by_repo[row.repo_root].append(row)
        for repo_root in sorted(by_repo):
            ctx.stdout.write(f"  {format_repo_key_for_display(repo_root)}\n")
            for row in by_repo[repo_root]:
                ctx.stdout.write(
                    f"    - {row.branch_name} (parent {row.parent_branch_name or '<none>'})\n"
                )
    else:
        for row in doomed:
            ctx.stdout.write(
                f"  - {row.branch_name} (parent {row.parent_branch_name or '<none>'})\n"
            )
    ctx.stdout.write("Dry run complete (no changes). Re-run with --yes to apply.\n")
    return 0


def _stdin_is_tty(ctx: AppContext) -> bool:
    isatty = getattr(ctx.stdin, "isatty", None)
    try:
        return bool(isatty()) if callable(isatty) else False
    except Exception:
        return False


def _confirm(ctx: AppContext, scope: str, *, is_global: bool) -> bool:
    # Prompts and diagnostics go to stderr so stdout stays clean for piping.
    ctx.stderr.write(f"About to forget all Stackman tracking for {scope}.\n")
    ctx.stderr.write("This cannot be undone (Git branches are untouched).\n")
    if is_global:
        # Bigger blast radius (every repo) demands the full word, not a bare 'y'.
        ctx.stderr.write("Type 'yes' to confirm wiping EVERY repository: ")
        ctx.stderr.flush()
        return ctx.stdin.readline().strip().lower() == "yes"
    ctx.stderr.write("Continue? [y/N]: ")
    ctx.stderr.flush()
    return ctx.stdin.readline().strip().lower() in {"y", "yes"}
