from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from .commands import discover, done, forget, listing, runner, status, sync, track
from .context import AppContext


@dataclass(slots=True)
class StackmanApp:
    """Thin façade over command modules (keeps CLI and tests on a single injectable object)."""

    db_path: Path
    cwd: Path
    stdin: TextIO
    stdout: TextIO
    stderr: TextIO
    stack_id_factory: Callable[[], str] | None = None
    resolver: str | None = None

    def _ctx(self) -> AppContext:
        return AppContext(
            db_path=self.db_path,
            cwd=self.cwd,
            stdin=self.stdin,
            stdout=self.stdout,
            stderr=self.stderr,
            stack_id_factory=self.stack_id_factory,
            resolver=self.resolver,
        )

    def _run(self, fn: Callable[[AppContext], int]) -> int:
        """Run a command under the shared error boundary against a fresh context."""
        return runner.run_safely(self._ctx(), fn)

    def status(self, *, branch: str | None = None, as_json: bool = False) -> int:
        return self._run(lambda c: status.run(c, branch=branch, as_json=as_json))

    def track(self, *, branch: str | None = None, parent: str) -> int:
        return self._run(lambda c: track.run_track(c, branch=branch, parent=parent))

    def chain(self, *, anchor: str, branches: Sequence[str]) -> int:
        return self._run(lambda c: track.run_chain(c, anchor=anchor, branches=branches))

    def sync(
        self,
        *,
        branch: str | None = None,
        dry_run: bool = False,
        verbose: bool = False,
        squash: bool = False,
        allow_dirty: bool = False,
        resolver: str | None = None,
        no_wait: bool = False,
        no_fetch_and_pull: bool = False,
    ) -> int:
        return self._run(
            lambda c: sync.run(
                c,
                branch=branch,
                dry_run=dry_run,
                verbose=verbose,
                squash=squash,
                allow_dirty=allow_dirty,
                resolver=resolver,
                no_wait=no_wait,
                no_fetch_and_pull=no_fetch_and_pull,
            )
        )

    def list(self, *, as_json: bool = False) -> int:
        return self._run(lambda c: listing.run_repo_list(c, as_json=as_json))

    def discover(self, *, pr_number: int, apply: bool = False) -> int:
        return self._run(lambda c: discover.run(c, pr_number=pr_number, apply=apply))

    def discover_mine(self, *, apply: bool = False) -> int:
        return self._run(lambda c: discover.run_mine(c, apply=apply))

    def done(self, *, branch: str | None = None, dry_run: bool = False) -> int:
        return self._run(lambda c: done.run(c, branch=branch, dry_run=dry_run))

    def forget(self, *, branch: str | None = None) -> int:
        return self._run(lambda c: forget.run(c, branch=branch))

    def forget_all(
        self, *, is_global: bool = False, assume_yes: bool = False, dry_run: bool = False
    ) -> int:
        return self._run(
            lambda c: forget.run_all(c, is_global=is_global, assume_yes=assume_yes, dry_run=dry_run)
        )
