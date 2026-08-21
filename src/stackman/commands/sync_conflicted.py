from __future__ import annotations

from ..lib.command_support import emit as _emit
from ..lib.conflict_prediction import probe_all, report_lines
from ..lib.context import AppContext
from ..lib.git_ops import repo_db_key, repo_root
from ..lib.store import initialize, list_branches
from ..lib.sync_workflow import (
    SyncOptions,
    execute_prepared_sync,
    fetch_origin,
    preflight_sync,
    prepare_sync,
    validate_sync_options,
)


def run(
    ctx: AppContext,
    *,
    dry_run: bool,
    verbose: bool,
    squash: bool,
    allow_dirty: bool,
    resolver: str | None = None,
    no_wait: bool = False,
    no_fetch_and_pull: bool = False,
) -> int:
    """Probe all stacks, then sync only the stacks with predicted rebases conflicts."""
    options = SyncOptions(
        dry_run=dry_run,
        verbose=verbose,
        squash=squash,
        allow_dirty=allow_dirty,
        resolver=resolver,
        no_wait=no_wait,
        no_fetch_and_pull=no_fetch_and_pull,
    )
    validate_sync_options(options)
    initialize(ctx.db_path)

    worktree = repo_root(ctx.cwd)
    branches = list_branches(ctx.db_path, repo_db_key(worktree))
    use_origin_anchor = (
        False if options.dry_run else fetch_origin(ctx, worktree, skip=options.no_fetch_and_pull)
    )
    reports = probe_all(ctx.db_path, worktree, branches, fresh_origin=use_origin_anchor)
    probe_errors = [report for report in reports if report.status == "probe_error"]
    if probe_errors:
        for line in report_lines(reports):
            _emit(ctx, line)
        return 2

    conflicted = [report for report in reports if report.status == "conflict"]
    if not conflicted:
        _emit(ctx, "No predicted rebase conflicts.")
        return 0

    prepared = [
        prepare_sync(ctx, branch=report.branch)
        for report in conflicted
        if report.branch is not None
    ]
    for operation in prepared:
        preflight_sync(ctx, operation, options)

    stack_ids = ", ".join(report.stack for report in conflicted)
    _emit(ctx, f"[stackman] Syncing {len(prepared)} conflicted stack(s): {stack_ids}")
    for operation in prepared:
        result = execute_prepared_sync(
            ctx,
            operation,
            options,
            use_origin_anchor=use_origin_anchor,
        )
        if result != 0:
            return result

    _emit(ctx, "[stackman] Conflicted stacks synced successfully.")
    return 0
