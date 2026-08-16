from __future__ import annotations

from stackman.models import BranchRecord
from stackman.sync_plan import build_sync_plan


def _branch(
    name: str,
    parent: str | None,
    *,
    repo_id: int = 1,
    root: str = "/repo",
) -> BranchRecord:
    return BranchRecord(
        id=hash(name) % 10000,
        repo_id=repo_id,
        repo_root=root,
        branch_name=name,
        parent_branch_name=parent,
        fork_point_sha=f"{'0' * 39}{name[:1]}",
    )


def test_sync_plan_includes_unlabeled_descendants() -> None:
    """Design: syncing a label must update descendants even if they only carry another label."""
    branches = [
        _branch("branch_a", "main"),
        _branch("branch_b", "branch_a"),
        _branch("branch_c", "branch_b"),
        _branch("branch_z", "branch_b"),
    ]
    plan = build_sync_plan(
        "stack1",
        branches,
        ["branch_a", "branch_b", "branch_c"],
    )
    assert plan.sync_branches == frozenset({"branch_a", "branch_b", "branch_c", "branch_z"})
    assert plan.order.index("branch_a") < plan.order.index("branch_b")
    assert plan.order.index("branch_b") < plan.order.index("branch_c")
    assert plan.order.index("branch_b") < plan.order.index("branch_z")


def test_sync_plan_excludes_unrelated_parallel_line() -> None:
    branches = [
        _branch("branch_a", "main"),
        _branch("branch_b", "branch_a"),
        _branch("branch_c", "main"),
        _branch("branch_d", "branch_c"),
    ]
    plan = build_sync_plan("stack1", branches, ["branch_a", "branch_b"])
    assert plan.sync_branches == frozenset({"branch_a", "branch_b"})
    assert "branch_c" not in plan.sync_branches


def test_roots_are_minimal_above_trunk() -> None:
    branches = [
        _branch("branch_a", "main"),
        _branch("branch_b", "branch_a"),
    ]
    plan = build_sync_plan("s", branches, ["branch_b"])
    assert plan.roots == frozenset({"branch_a"})


def test_sync_plan_stops_root_resolution_at_tracked_anchor() -> None:
    branches = [
        _branch("release_base", "main"),
        _branch("feature_a", "release_base"),
        _branch("feature_b", "feature_a"),
    ]

    plan = build_sync_plan(
        "stack1",
        branches,
        ["feature_a", "feature_b"],
        anchor_branch_name="release_base",
    )

    assert plan.anchor_branch_name == "release_base"
    assert plan.roots == frozenset({"feature_a"})
    assert plan.sync_branches == frozenset({"feature_a", "feature_b"})
    assert "release_base" not in plan.sync_branches


def test_sync_plan_records_anchor_branch_name() -> None:
    branches = [_branch("feature", "main")]
    plan = build_sync_plan("stack1", branches, ["feature"], anchor_branch_name="main")
    assert plan.anchor_branch_name == "main"
