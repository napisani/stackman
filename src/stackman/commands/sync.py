from __future__ import annotations

from typing import Literal

from ..lib.context import AppContext
from ..lib.sync_workflow import SyncOptions, sync_one


def run(
    ctx: AppContext,
    *,
    branch: str | None,
    strategy: Literal["rebase", "merge"],
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
            strategy=strategy,
            dry_run=dry_run,
            verbose=verbose,
            squash=squash,
            allow_dirty=allow_dirty,
            resolver=resolver,
            no_wait=no_wait,
            no_fetch_and_pull=no_fetch_and_pull,
        ),
    )
