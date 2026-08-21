from __future__ import annotations

from ..lib.context import AppContext
from ..lib.sync_workflow import SyncOptions, sync_one


def run(
    ctx: AppContext,
    *,
    branch: str | None,
    dry_run: bool,
    verbose: bool,
    squash: bool,
    allow_dirty: bool,
    resolver: str | None = None,
    no_wait: bool = False,
    no_fetch_and_pull: bool = False,
) -> int:
    """Sync the complete stack containing ``branch`` (or the current branch)."""
    return sync_one(
        ctx,
        branch=branch,
        options=SyncOptions(
            dry_run=dry_run,
            verbose=verbose,
            squash=squash,
            allow_dirty=allow_dirty,
            resolver=resolver,
            no_wait=no_wait,
            no_fetch_and_pull=no_fetch_and_pull,
        ),
    )
