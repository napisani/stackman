from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from ..context import AppContext
from ..git_ops import (
    abort_rebase,
    checkout_detached,
    conflicted_files,
    create_detached_worktree,
    fetch_remote,
    has_remote,
    is_ancestor,
    merge_base,
    rebase_in_progress,
    rebase_onto,
    remote_tracking_branch,
    remove_worktree,
    repo_db_key,
    repo_root,
    rev_parse,
)
from ..models import BranchRecord
from ..store import get_stack, initialize, list_branch_names_with_stack_label, list_branches
from ..sync_plan import SyncPlan, build_sync_plan
from .shared import emit as _emit


@dataclass(frozen=True)
class ConflictReport:
    stack: str
    status: str
    branch: str | None = None
    parent: str | None = None
    files: tuple[str, ...] = ()
    detail: str | None = None


def run(ctx: AppContext, *, as_json: bool, no_fetch_and_pull: bool) -> int:
    initialize(ctx.db_path)
    worktree = repo_root(ctx.cwd)
    repo_key = repo_db_key(ctx.cwd)
    branches = list_branches(ctx.db_path, repo_key)
    stack_ids = sorted({str(branch.stack_id) for branch in branches if branch.stack_id is not None})

    fresh_origin = _fetch_origin(ctx, worktree, skip=no_fetch_and_pull, as_json=as_json)
    reports = [
        _probe_stack(ctx, worktree, branches, stack_id, fresh_origin) for stack_id in stack_ids
    ]

    if as_json:
        json.dump([asdict(report) for report in reports], ctx.stdout)
        ctx.stdout.write("\n")
    else:
        _print_reports(ctx, reports)

    if any(report.status == "probe_error" for report in reports):
        return 2
    return 1 if any(report.status == "conflict" for report in reports) else 0


def _fetch_origin(ctx: AppContext, worktree: Path, *, skip: bool, as_json: bool) -> bool:
    if skip or not has_remote(worktree, "origin"):
        return False
    emit = _emit_diagnostic if as_json else _emit
    emit(ctx, "[stackman] Fetching origin for conflict prediction.")
    result = fetch_remote(worktree, "origin")
    if result.returncode == 0:
        return True
    detail = (result.stderr or result.stdout).strip()
    emit(ctx, "[stackman] Warning: git fetch origin failed; probing local refs.")
    if detail:
        emit(ctx, f"[stackman]   {detail}")
    return False


def _emit_diagnostic(ctx: AppContext, message: str) -> None:
    ctx.stderr.write(f"{message}\n")
    ctx.stderr.flush()


def _probe_stack(
    ctx: AppContext,
    worktree: Path,
    branches: list[BranchRecord],
    stack_id: str,
    fresh_origin: bool,
) -> ConflictReport:
    stack = get_stack(ctx.db_path, stack_id)
    plan = build_sync_plan(
        stack_id,
        branches,
        list_branch_names_with_stack_label(ctx.db_path, repo_db_key(worktree), stack_id),
        anchor_branch_name=stack.anchor_branch_name if stack else None,
    )
    if not plan.order:
        return ConflictReport(stack=stack_id, status="clean")

    try:
        probe_dir = Path(tempfile.mkdtemp(prefix="stackman-conflicts-", dir=worktree.parent))
        probe_dir.rmdir()
    except OSError as exc:
        return ConflictReport(stack=stack_id, status="probe_error", detail=str(exc))

    created = create_detached_worktree(worktree, probe_dir, plan.order[0])
    if created.returncode != 0:
        detail = (created.stderr or created.stdout).strip() or "could not create probe worktree"
        shutil.rmtree(probe_dir, ignore_errors=True)
        return ConflictReport(stack=stack_id, status="probe_error", detail=detail)

    report: ConflictReport | None = None
    cleanup_error: str | None = None
    try:
        try:
            report = _run_probe_rebase(worktree, probe_dir, branches, stack_id, plan, fresh_origin)
        except (OSError, subprocess.CalledProcessError) as exc:
            report = ConflictReport(stack=stack_id, status="probe_error", detail=str(exc))
    finally:
        cleanup_error = _cleanup_probe_worktree(worktree, probe_dir)

    assert report is not None
    if cleanup_error:
        return ConflictReport(
            stack=stack_id,
            status="probe_error",
            branch=report.branch,
            detail=cleanup_error,
        )
    return report


def _run_probe_rebase(
    worktree: Path,
    probe_dir: Path,
    branches: list[BranchRecord],
    stack_id: str,
    plan: SyncPlan,
    fresh_origin: bool,
) -> ConflictReport:
    by_name = {str(branch.branch_name): branch for branch in branches}
    simulated_tips: dict[str, str] = {}
    for branch_name in plan.order:
        branch = by_name[branch_name]
        parent_name = _parent_name(plan, branch)
        if parent_name is None:
            return ConflictReport(
                stack=stack_id, status="probe_error", branch=branch_name, detail="no parent"
            )
        parent_ref = _parent_ref(probe_dir, plan, branch, parent_name, simulated_tips, fresh_origin)
        upstream = branch.fork_point_sha
        if not is_ancestor(probe_dir, upstream, branch_name):
            upstream = merge_base(probe_dir, branch_name, parent_ref)
        parent_tip = rev_parse(probe_dir, parent_ref)
        if upstream == parent_tip:
            simulated_tips[branch_name] = rev_parse(probe_dir, branch_name)
            continue

        checkout = checkout_detached(probe_dir, branch_name)
        if checkout.returncode != 0:
            detail = (checkout.stderr or checkout.stdout).strip()
            return ConflictReport(
                stack=stack_id, status="probe_error", branch=branch_name, detail=detail
            )
        result = rebase_onto(probe_dir, onto=parent_tip, upstream=upstream, update_refs=False)
        if result.returncode != 0:
            files = tuple(conflicted_files(probe_dir))
            if rebase_in_progress(probe_dir):
                abort_rebase(probe_dir)
                return ConflictReport(
                    stack=stack_id,
                    status="conflict",
                    branch=branch_name,
                    parent=parent_name,
                    files=files,
                )
            detail = (result.stderr or result.stdout).strip() or "rebase probe failed"
            return ConflictReport(
                stack=stack_id, status="probe_error", branch=branch_name, detail=detail
            )
        simulated_tips[branch_name] = rev_parse(probe_dir, "HEAD")

    return ConflictReport(stack=stack_id, status="clean")


def _cleanup_probe_worktree(worktree: Path, probe_dir: Path) -> str | None:
    try:
        result = remove_worktree(worktree, probe_dir)
    except OSError as exc:
        return f"could not remove probe worktree {probe_dir}: {exc}"
    if result.returncode == 0:
        return None
    detail = (result.stderr or result.stdout).strip() or "git worktree remove failed"
    return f"could not remove probe worktree {probe_dir}: {detail}"


def _parent_name(plan: SyncPlan, branch: BranchRecord) -> str | None:
    return (
        plan.anchor_branch_name if branch.branch_name in plan.roots else branch.parent_branch_name
    )


def _parent_ref(
    worktree: Path,
    plan: SyncPlan,
    branch: BranchRecord,
    parent_name: str,
    simulated_tips: dict[str, str],
    fresh_origin: bool,
) -> str:
    if branch.branch_name not in plan.roots:
        return simulated_tips.get(parent_name, parent_name)
    if fresh_origin:
        return remote_tracking_branch(worktree, "origin", parent_name) or parent_name
    return parent_name


def _print_reports(ctx: AppContext, reports: list[ConflictReport]) -> None:
    conflicts = [report for report in reports if report.status == "conflict"]
    errors = [report for report in reports if report.status == "probe_error"]
    if not conflicts and not errors:
        _emit(ctx, "No predicted rebase conflicts.")
        return
    for report in conflicts:
        files = ", ".join(report.files) or "(Git reported no unmerged paths)"
        _emit(
            ctx,
            f"{report.stack}: {report.branch} conflicts rebasing onto {report.parent} ({files})",
        )
    for report in errors:
        _emit(ctx, f"{report.stack}: probe error on {report.branch or '<stack>'}: {report.detail}")
