from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from ..lib.command_support import emit
from ..lib.conflict_prediction import ConflictReport, probe_all, report_lines
from ..lib.context import AppContext
from ..lib.git_ops import fetch_remote, has_remote, repo_db_key, repo_root
from ..lib.store import initialize, list_branches


def run(ctx: AppContext, *, as_json: bool, no_fetch_and_pull: bool) -> int:
    initialize(ctx.db_path)
    worktree = repo_root(ctx.cwd)
    repo_key = repo_db_key(ctx.cwd)
    branches = list_branches(ctx.db_path, repo_key)
    fresh_origin = _fetch_origin(ctx, worktree, skip=no_fetch_and_pull, as_json=as_json)
    reports = probe_all(ctx.db_path, worktree, branches, fresh_origin=fresh_origin)

    if as_json:
        json.dump([asdict(report) for report in reports], ctx.stdout)
        ctx.stdout.write("\n")
    else:
        print_reports(ctx, reports)

    if any(report.status == "probe_error" for report in reports):
        return 2
    return 1 if any(report.status == "conflict" for report in reports) else 0


def _fetch_origin(ctx: AppContext, worktree: Path, *, skip: bool, as_json: bool) -> bool:
    if skip or not has_remote(worktree, "origin"):
        return False
    emit_message = _emit_diagnostic if as_json else emit
    emit_message(ctx, "[stackman] Fetching origin for conflict prediction.")
    result = fetch_remote(worktree, "origin")
    if result.returncode == 0:
        return True
    detail = (result.stderr or result.stdout).strip()
    emit_message(ctx, "[stackman] Warning: git fetch origin failed; probing local refs.")
    if detail:
        emit_message(ctx, f"[stackman]   {detail}")
    return False


def _emit_diagnostic(ctx: AppContext, message: str) -> None:
    ctx.stderr.write(f"{message}\n")
    ctx.stderr.flush()


def print_reports(ctx: AppContext, reports: list[ConflictReport]) -> None:
    for line in report_lines(reports):
        emit(ctx, line)
