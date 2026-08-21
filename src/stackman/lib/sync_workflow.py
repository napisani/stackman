from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .command_support import emit as _emit
from .conflict_resolver import RebaseConflictContext, resolve_rebase_conflict
from .context import AppContext
from .git_ops import (
    branch_exists,
    checkout,
    commits_since,
    create_branch_worktree,
    current_branch,
    fetch_remote,
    has_remote,
    is_ancestor,
    merge_base,
    pull_ff_only,
    push_force_with_lease_current_branch,
    rebase_in_progress_any_linked,
    rebase_onto,
    remote_tracking_branch,
    remove_worktree,
    repo_db_key,
    repo_root,
    rev_parse,
    rev_parse_or_none,
    squash_commits_since,
    sync_relevant_worktrees,
    upstream_branch,
    worktree_dirty_preview,
    worktree_path_for_branch,
)
from .models import BranchRecord
from .store import (
    create_stack,
    get_branch,
    get_stack,
    initialize,
    list_branch_labels,
    list_branch_names_with_stack_label,
    list_branches,
    update_branch_fork_point,
)
from .sync_plan import SyncPlan, build_sync_plan


@dataclass(frozen=True, slots=True)
class SyncOptions:
    """Named options shared by single-stack and conflict-selected sync workflows."""

    dry_run: bool = False
    verbose: bool = False
    squash: bool = False
    allow_dirty: bool = False
    resolver: str | None = None
    no_wait: bool = False
    no_fetch_and_pull: bool = False


@dataclass(frozen=True, slots=True)
class PreparedSync:
    """A fully resolved stack that has not yet been changed."""

    worktree: Path
    repo_key: str
    original_branch: str
    plan: SyncPlan
    all_branches: list[BranchRecord]


def sync_one(ctx: AppContext, *, branch: str | None, options: SyncOptions) -> int:
    """Prepare and execute the full stack containing one selected branch."""
    validate_sync_options(options)
    prepared = prepare_sync(ctx, branch=branch)
    preflight_sync(ctx, prepared, options)
    if options.dry_run:
        return execute_prepared_sync(ctx, prepared, options, use_origin_anchor=False)
    use_origin_anchor = fetch_and_pull(ctx, prepared.worktree, skip=options.no_fetch_and_pull)
    return execute_prepared_sync(ctx, prepared, options, use_origin_anchor=use_origin_anchor)


def prepare_sync(ctx: AppContext, *, branch: str | None) -> PreparedSync:
    """Resolve a branch selector to one complete sync plan without rebasing it."""
    initialize(ctx.db_path)

    worktree = repo_root(ctx.cwd)
    repo_key = repo_db_key(ctx.cwd)
    original_branch = current_branch(worktree)
    selector_branch = branch or original_branch
    if branch is not None and not branch_exists(worktree, branch):
        raise SystemExit(f"Branch {branch!r} does not exist in this Git repository.")
    all_branches = list_branches(ctx.db_path, repo_key)
    if not all_branches:
        raise SystemExit("No branches are tracked for this repository.")

    resolved_stack = _resolve_stack_id(ctx, repo_key, selector_branch)
    labeled_names = list_branch_names_with_stack_label(ctx.db_path, repo_key, resolved_stack)
    stack = get_stack(ctx.db_path, resolved_stack)
    stored_anchor = stack.anchor_branch_name if stack is not None else None
    plan = build_sync_plan(
        resolved_stack,
        all_branches,
        labeled_names,
        anchor_branch_name=stored_anchor,
    )
    if not plan.sync_branches:
        raise SystemExit(
            f"Stack {resolved_stack!r} resolved to an empty sync set (nothing to update)."
        )
    anchor_branch_name = _resolve_stack_anchor(ctx, plan, all_branches)
    if anchor_branch_name != stored_anchor:
        create_stack(ctx.db_path, resolved_stack, anchor_branch_name=anchor_branch_name)
        plan = build_sync_plan(
            resolved_stack,
            all_branches,
            labeled_names,
            anchor_branch_name=anchor_branch_name,
        )

    return PreparedSync(
        worktree=worktree,
        repo_key=repo_key,
        original_branch=original_branch,
        plan=plan,
        all_branches=all_branches,
    )


def validate_sync_options(options: SyncOptions) -> None:
    if options.allow_dirty and options.squash:
        raise SystemExit("`--allow-dirty` cannot be combined with `--squash`.")


def preflight_sync(ctx: AppContext, prepared: PreparedSync, options: SyncOptions) -> None:
    """Reject unsafe syncs before any selected stack starts changing refs."""
    if options.dry_run:
        return
    if options.allow_dirty:
        _emit(
            ctx,
            "[stackman] Warning: --allow-dirty skips the dirty-worktree preflight; "
            "Git may still abort checkout or rebase.",
        )
        return

    involved = sync_relevant_worktrees(prepared.worktree, prepared.plan.order)
    dirty_blocks: list[str] = []
    for path in involved:
        preview = worktree_dirty_preview(path)
        if preview is not None:
            dirty_blocks.append(f"  {path}\n{preview}")
    if dirty_blocks:
        raise SystemExit(
            "These worktrees used by this sync are dirty; commit or stash, "
            "or pass --dry-run to inspect the plan only.\n"
            + "\n".join(dirty_blocks)
            + "\n(Other linked worktrees do not need to be clean.)"
        )


def execute_prepared_sync(
    ctx: AppContext,
    prepared: PreparedSync,
    options: SyncOptions,
    *,
    use_origin_anchor: bool,
) -> int:
    """Render and execute a previously prepared sync plan."""
    if options.dry_run:
        _print_plan(ctx, prepared.plan, prepared.worktree, dry_run=True)
        return _run_dry_run(
            ctx,
            prepared.plan,
            prepared.all_branches,
            prepared.worktree,
            squash=options.squash,
        )

    _print_plan(ctx, prepared.plan, prepared.worktree, dry_run=False)
    return _apply_sync(
        ctx,
        prepared.plan,
        prepared.all_branches,
        prepared.worktree,
        prepared.repo_key,
        prepared.original_branch,
        squash=options.squash,
        verbose=options.verbose,
        resolver=options.resolver,
        no_wait=options.no_wait,
        use_origin_anchor=use_origin_anchor,
    )


def fetch_origin(ctx: AppContext, worktree: Path, *, skip: bool) -> bool:
    """Best-effort fetch for callers that must not move the current branch."""
    if skip:
        _emit(ctx, "[stackman] Skipping origin fetch (--no-fetch-and-pull).")
        return False
    if not has_remote(worktree, "origin"):
        return False
    return _fetch_origin(ctx, worktree)


def fetch_and_pull(ctx: AppContext, worktree: Path, *, skip: bool) -> bool:
    """Best-effort origin refresh and current-branch fast-forward for ``sync``."""
    if skip:
        _emit(ctx, "[stackman] Skipping origin fetch and pull (--no-fetch-and-pull).")
        return False
    if not has_remote(worktree, "origin"):
        return False

    fetched = _fetch_origin(ctx, worktree)
    _emit(ctx, "[stackman] Pulling the current branch from its upstream (--ff-only).")
    pull_result = pull_ff_only(worktree)
    if pull_result.returncode != 0:
        _warn_git_failure(ctx, "pull --ff-only", pull_result.stdout, pull_result.stderr)
    return fetched


def _fetch_origin(ctx: AppContext, worktree: Path) -> bool:
    _emit(ctx, "[stackman] Fetching origin.")
    fetch_result = fetch_remote(worktree, "origin")
    fetched = fetch_result.returncode == 0
    if not fetched:
        _warn_git_failure(ctx, "fetch origin", fetch_result.stdout, fetch_result.stderr)
    return fetched


def _warn_git_failure(ctx: AppContext, command: str, stdout: str, stderr: str) -> None:
    _emit(ctx, f"[stackman] Warning: git {command} failed; continuing with local refs.")
    detail = (stderr or stdout).strip()
    if detail:
        _emit(ctx, f"[stackman]   {detail}")


def _run_dry_run(
    ctx: AppContext,
    plan: SyncPlan,
    all_branches: list[BranchRecord],
    worktree: Path,
    *,
    squash: bool,
) -> int:
    _emit(
        ctx,
        "[stackman] Planned steps (each branch: checkout"
        + (" → optional squash" if squash else "")
        + " → rebase --onto parent tip → push)",
    )
    for branch_name in plan.order:
        record = next(b for b in all_branches if b.branch_name == branch_name)
        parent = _sync_parent_name(plan, record) or "<none>"
        wt_hint = ""
        holder = worktree_path_for_branch(worktree, branch_name)
        if holder is not None and holder != worktree:
            wt_hint = f" (checkout in {holder})"
        _emit(
            ctx,
            f"  - {branch_name}: rebase onto tip of {parent!r} "
            f"(stored fork-point {record.fork_point_sha[:7]}){wt_hint}",
        )
        if squash:
            commit_count = len(commits_since(worktree, record.fork_point_sha, ref=branch_name))
            if commit_count >= 2:
                _emit(
                    ctx,
                    f"    squash: would collapse {commit_count} post-fork commits into one before rebasing",
                )
            else:
                _emit(
                    ctx,
                    f"    squash: skipped ({commit_count} post-fork commit"
                    f"{'' if commit_count == 1 else 's'})",
                )
    _emit(ctx, "[stackman] Dry run complete.")
    return 0


def _apply_sync(
    ctx: AppContext,
    plan: SyncPlan,
    all_branches: list[BranchRecord],
    worktree: Path,
    repo_key: str,
    original_branch: str,
    *,
    squash: bool,
    verbose: bool,
    resolver: str | None = None,
    no_wait: bool = False,
    use_origin_anchor: bool,
) -> int:
    by_name: dict[str, BranchRecord] = {str(b.branch_name): b for b in all_branches}
    try:
        for branch_name in plan.order:
            record = by_name[branch_name]
            if not _sync_one_branch(
                ctx,
                record=record,
                plan=plan,
                worktree=worktree,
                repo_key=repo_key,
                squash=squash,
                verbose=verbose,
                resolver=resolver,
                no_wait=no_wait,
                use_origin_anchor=use_origin_anchor,
            ):
                return 1
    finally:
        if (
            not rebase_in_progress_any_linked(worktree)
            and current_branch(worktree) != original_branch
        ):
            _emit(ctx, f"[stackman] Restoring previous branch {original_branch!r}")
            checkout(worktree, original_branch)

    _emit(ctx, "[stackman] Sync finished successfully.")
    return 0


def _sync_one_branch(
    ctx: AppContext,
    *,
    record: BranchRecord,
    plan: SyncPlan,
    worktree: Path,
    repo_key: str,
    squash: bool,
    verbose: bool,
    resolver: str | None = None,
    no_wait: bool = False,
    use_origin_anchor: bool,
) -> bool:
    """Rebase + push one branch. Returns True to continue, False to abort the sync."""
    branch_name = record.branch_name
    parent_name = _sync_parent_name(plan, record)
    if parent_name is None:
        _emit(ctx, f"[stackman] Skipping {branch_name!r} (no parent recorded).")
        return True

    # Interactive conflict resolution must happen in the invoking worktree so
    # the user's `git rebase --continue` operates on the rebase Stackman began.
    # Resolver and explicitly non-interactive runs can remain isolated.
    existing_wt = worktree_path_for_branch(worktree, branch_name)
    use_invoking_worktree = resolver is None and not no_wait and hasattr(ctx.stdin, "readline")
    temp_wt = None

    if existing_wt:
        branch_wt = existing_wt
        _emit(
            ctx,
            f"[stackman] → Using worktree {branch_wt} (branch {branch_name!r} is checked out there)",
        )
    elif use_invoking_worktree:
        branch_wt = worktree
        _emit(ctx, f"[stackman] → Checking out {branch_name!r}")
        checkout(branch_wt, branch_name)
    else:
        temp_wt = worktree.parent / f"{worktree.name}__rebase__{branch_name}"
        branch_wt = temp_wt

        _emit(
            ctx,
            f"[stackman] → Creating temporary worktree for {branch_name!r} at {temp_wt}",
        )
        result = create_branch_worktree(worktree, temp_wt, branch_name)
        if result.returncode != 0:
            ctx.stderr.write(f"[stackman] Failed to create worktree: {result.stderr}\n")
            return False

    try:
        parent_ref = _rebase_parent_ref(
            branch_wt, plan, record, parent_name, use_origin_anchor=use_origin_anchor
        )
        parent_tip = rev_parse(branch_wt, parent_ref)
        upstream = record.fork_point_sha

        # A parent rebased earlier in this sync no longer contains the stored
        # fork-point, but the child still does; that old parent tip is exactly
        # the boundary Git must replay from. Recalculate only when the branch
        # itself no longer contains the boundary.
        if not is_ancestor(branch_wt, upstream, branch_name):
            _emit(
                ctx,
                f"[stackman] ⚠️  Fork-point {upstream[:7]} is no longer an ancestor of {branch_name!r}. "
                "Recalculating (the branch may have been rewritten).",
            )
            upstream = merge_base(branch_wt, branch_name, parent_ref)
            _emit(
                ctx,
                f"[stackman]    Recalculated fork-point: {upstream[:7]}",
            )
            # Update the record to avoid repeated recalculation on future syncs
            update_branch_fork_point(
                ctx.db_path,
                repo_root=repo_key,
                branch_name=branch_name,
                fork_point_sha=upstream,
            )

        if squash and not _squash_branch(ctx, branch_wt, branch_name, upstream):
            return False

        if upstream != parent_tip:
            if verbose:
                _emit(
                    ctx,
                    f"[stackman]   git rebase --onto {parent_tip} {upstream} "
                    f"(replay commits after stored fork-point onto current {parent_name!r})",
                )
            _emit(
                ctx,
                f"[stackman]   Rebasing {branch_name!r} onto {parent_name!r} "
                f"at {parent_tip[:7]} (fork-point {upstream[:7]})",
            )
            result = rebase_onto(branch_wt, onto=parent_tip, upstream=upstream)
            if result.returncode != 0:
                ctx.stderr.write(f"[stackman] Rebase failed on {branch_name!r}.\n")
                conflict_ctx = RebaseConflictContext(
                    branch_name=branch_name,
                    branch_wt=branch_wt,
                    parent_name=parent_name,
                    parent_tip=parent_tip,
                    fork_point=upstream,
                )
                resolution = resolve_rebase_conflict(
                    ctx,
                    conflict_ctx,
                    resolver=resolver,
                    no_wait=no_wait,
                )
                if resolution.status != "success":
                    return False
        else:
            _emit(
                ctx,
                f"[stackman]   Skipping {branch_name!r}; stored fork-point already matches "
                f"current {parent_name!r} tip {parent_tip[:7]}",
            )

        # The branch is now based on parent_tip; record that (safe to persist before the
        # push because the push decision below is driven by the actual local↔remote diff,
        # not by this fork-point — so a failed push is retried on the next run).
        update_branch_fork_point(
            ctx.db_path,
            repo_root=repo_key,
            branch_name=branch_name,
            fork_point_sha=parent_tip,
        )

        return _push_if_needed(ctx, branch_wt, branch_name)
    finally:
        # Clean up temporary worktree if we created one
        if temp_wt is not None:
            _emit(ctx, f"[stackman] Cleaning up temporary worktree {temp_wt}")
            remove_result = remove_worktree(worktree, temp_wt)
            if remove_result.returncode != 0:
                ctx.stderr.write(
                    f"[stackman] Warning: failed to remove temporary worktree: {remove_result.stderr}\n"
                )


def _squash_branch(ctx: AppContext, branch_wt: Path, branch_name: str, upstream: str) -> bool:
    commit_count, squash_result = squash_commits_since(branch_wt, upstream)
    if commit_count < 2:
        _emit(
            ctx,
            f"[stackman]   Squash skipped for {branch_name!r} "
            f"({commit_count} post-fork commit{'' if commit_count == 1 else 's'})",
        )
        return True
    _emit(
        ctx,
        f"[stackman]   Squashing {branch_name!r}: collapsing {commit_count} "
        "post-fork commits into one",
    )
    if squash_result is None or squash_result.returncode != 0:
        msg = ""
        if squash_result is not None:
            msg = (squash_result.stderr or "").strip() or (squash_result.stdout or "").strip()
        ctx.stderr.write(f"[stackman] Squash failed on {branch_name!r}.\n")
        if msg:
            ctx.stderr.write(f"{msg}\n")
        return False
    return True


def _push_if_needed(ctx: AppContext, branch_wt: Path, branch_name: str) -> bool:
    """Push only when the local branch actually differs from its upstream."""
    remote_ref = upstream_branch(branch_wt, branch_name)
    if remote_ref is None:
        _emit(ctx, f"[stackman]   No upstream tracking branch for {branch_name!r}; skipping push.")
        return True
    local_sha = rev_parse(branch_wt, branch_name)
    remote_sha = rev_parse_or_none(branch_wt, remote_ref)
    if remote_sha is not None and local_sha == remote_sha:
        _emit(
            ctx,
            f"[stackman]   Remote {remote_ref} already up to date for {branch_name!r}; skipping push.",
        )
        return True
    _emit(
        ctx,
        f"[stackman]   Pushing {branch_name!r} with --force-with-lease (upstream {remote_ref})",
    )
    push_result = push_force_with_lease_current_branch(branch_wt)
    if push_result.returncode != 0:
        msg = (push_result.stderr or "").strip() or (push_result.stdout or "").strip()
        ctx.stderr.write(
            f"[stackman] Push failed for {branch_name!r} (exit {push_result.returncode}).\n"
        )
        if msg:
            ctx.stderr.write(f"{msg}\n")
        return False
    return True


def _rebase_parent_ref(
    worktree: Path,
    plan: SyncPlan,
    record: BranchRecord,
    parent_name: str,
    *,
    use_origin_anchor: bool,
) -> str:
    """Root branches use fetched origin; descendants use the parent just synced locally."""
    if use_origin_anchor and record.branch_name in plan.roots:
        return remote_tracking_branch(worktree, "origin", parent_name) or parent_name
    return parent_name


def _sync_parent_name(plan: SyncPlan, record: BranchRecord) -> str | None:
    if record.branch_name in plan.roots:
        return plan.anchor_branch_name
    return record.parent_branch_name


def _resolve_stack_anchor(
    ctx: AppContext,
    plan: SyncPlan,
    all_branches: list[BranchRecord],
) -> str:
    if plan.anchor_branch_name:
        return plan.anchor_branch_name

    by_name: dict[str, BranchRecord] = {str(branch.branch_name): branch for branch in all_branches}
    parent_names = {by_name[root].parent_branch_name for root in plan.roots if root in by_name}
    if len(parent_names) == 1:
        anchor = next(iter(parent_names))
        if anchor:
            _emit(
                ctx,
                f"[stackman] Inferred anchor branch {anchor!r} from tracked roots.",
            )
            return anchor

    if not parent_names or parent_names == {None}:
        detail = "no recorded parent for the resolved root branch"
    else:
        rendered = ", ".join(sorted(p for p in parent_names if p is not None))
        detail = f"multiple root parents: {rendered}"
    raise SystemExit(
        "The selected branch group has no anchor branch and Stackman could not infer one "
        f"({detail})."
    )


def _resolve_stack_id(ctx: AppContext, repo_key: str, branch_name: str) -> str:
    tracked = get_branch(ctx.db_path, repo_key, branch_name)
    if tracked is None:
        raise SystemExit(
            f"Branch {branch_name!r} is not tracked by stackman. "
            "Run `stackman track <branch> --parent <parent>` first."
        )
    labels = list_branch_labels(ctx.db_path, repo_key, branch_name)
    if not labels:
        raise SystemExit(
            f"Branch {branch_name!r} is missing internal stack metadata. "
            "Run `stackman forget <branch>` and re-track it with `stackman track <branch> --parent <parent>`."
        )
    return labels[0]


def _print_plan(ctx: AppContext, plan: SyncPlan, worktree: Path, *, dry_run: bool) -> None:
    mode = "Dry run — no git changes" if dry_run else "Applying sync"
    _emit(ctx, f"[stackman] {mode} in worktree {worktree}")
    _emit(ctx, f"[stackman] Anchor branch: {plan.anchor_branch_name!r}")
    _emit(ctx, f"[stackman] Resolved roots: {', '.join(sorted(plan.roots)) or '<none>'}")
    _emit(
        ctx,
        f"[stackman] Sync set ({len(plan.sync_branches)}): {', '.join(plan.order)}",
    )
