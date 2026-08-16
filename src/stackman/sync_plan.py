from __future__ import annotations

import heapq
from collections import defaultdict, deque
from dataclasses import dataclass

from .models import BranchRecord

DEFAULT_TRUNK_NAMES: frozenset[str] = frozenset({"main", "master"})


@dataclass(frozen=True, slots=True)
class SyncPlan:
    """Resolved sync scope for a repo from a stack label."""

    stack_id: str
    anchor_branch_name: str | None
    labeled_branches: frozenset[str]
    roots: frozenset[str]
    sync_branches: frozenset[str]
    order: tuple[str, ...]


def _parent_name(record: BranchRecord) -> str | None:
    return record.parent_branch_name


def _tracked_names(records: list[BranchRecord]) -> frozenset[str]:
    return frozenset(b.branch_name for b in records)


def _ancestor_chain_toward_trunk(
    start: str,
    by_name: dict[str, BranchRecord],
    tracked: frozenset[str],
    trunk_names: frozenset[str],
    anchor_branch_name: str | None,
) -> list[str]:
    """Walk from start along stored parents until parent is anchor, trunk, untracked, or missing."""
    chain: list[str] = []
    current = start
    while current in tracked:
        chain.append(current)
        parent = _parent_name(by_name[current])
        if parent is None:
            break
        if parent == anchor_branch_name:
            break
        if parent in trunk_names:
            break
        if parent not in tracked:
            break
        current = parent
    return chain


def labeled_branches_in_repo(
    all_branches: list[BranchRecord],
    branches_with_label: list[str],
) -> frozenset[str]:
    tracked = _tracked_names(all_branches)
    return frozenset(b for b in branches_with_label if b in tracked)


def resolve_roots(
    labeled: frozenset[str],
    all_branches: list[BranchRecord],
    *,
    trunk_names: frozenset[str] = DEFAULT_TRUNK_NAMES,
    anchor_branch_name: str | None = None,
) -> frozenset[str]:
    """Branches in the labeled upward closure whose parent is outside that closure (or trunk / untracked)."""
    by_name: dict[str, BranchRecord] = {str(b.branch_name): b for b in all_branches}
    tracked = _tracked_names(all_branches)

    upward: set[str] = set()
    for name in labeled:
        upward.update(
            _ancestor_chain_toward_trunk(
                name,
                by_name,
                tracked,
                trunk_names,
                anchor_branch_name,
            )
        )

    roots: set[str] = set()
    for name in upward:
        parent = _parent_name(by_name[name])
        if (
            parent is None
            or parent == anchor_branch_name
            or parent not in tracked
            or parent in trunk_names
        ):
            roots.add(name)
    return frozenset(roots)


def _children_map(all_branches: list[BranchRecord]) -> dict[str, list[str]]:
    children: dict[str, list[str]] = defaultdict(list)
    for b in all_branches:
        p = b.parent_branch_name
        if p is not None:
            children[p].append(b.branch_name)
    for names in children.values():
        names.sort()
    return dict(children)


def resolve_sync_set(roots: frozenset[str], all_branches: list[BranchRecord]) -> frozenset[str]:
    """All tracked branches reachable from roots following parent→child edges."""
    tracked = _tracked_names(all_branches)
    children = _children_map(all_branches)
    out: set[str] = set()
    queue: deque[str] = deque(roots)
    while queue:
        name = queue.popleft()
        if name not in tracked or name in out:
            continue
        out.add(name)
        for child in children.get(name, ()):
            queue.append(child)
    return frozenset(out)


def topological_sync_order(
    sync_branches: frozenset[str], all_branches: list[BranchRecord]
) -> tuple[str, ...]:
    """Parent before children; stable tie-break by branch name."""
    by_name: dict[str, BranchRecord] = {str(b.branch_name): b for b in all_branches}
    # in-degree = count of sync parents that are also in sync_branches
    indeg: dict[str, int] = {name: 0 for name in sync_branches}
    sync_children: dict[str, list[str]] = defaultdict(list)
    for name in sync_branches:
        parent = _parent_name(by_name[name])
        if parent is not None and parent in sync_branches:
            sync_children[parent].append(name)
            indeg[name] += 1
        else:
            indeg.setdefault(name, 0)

    for names in sync_children.values():
        names.sort()

    # Min-heap keyed by branch name gives parent-before-child order with a stable
    # lexicographic tie-break, without re-sorting a list on every pop.
    ready = [name for name, degree in indeg.items() if degree == 0]
    heapq.heapify(ready)
    order: list[str] = []
    while ready:
        name = heapq.heappop(ready)
        order.append(name)
        for child in sync_children.get(name, ()):
            indeg[child] -= 1
            if indeg[child] == 0:
                heapq.heappush(ready, child)
    if len(order) != len(sync_branches):
        raise ValueError("cycle detected in stored parent graph; database is inconsistent")
    return tuple(order)


def build_sync_plan(
    stack_id: str,
    all_branches: list[BranchRecord],
    branches_with_label: list[str],
    *,
    trunk_names: frozenset[str] = DEFAULT_TRUNK_NAMES,
    anchor_branch_name: str | None = None,
) -> SyncPlan:
    labeled = labeled_branches_in_repo(all_branches, branches_with_label)
    roots = resolve_roots(
        labeled,
        all_branches,
        trunk_names=trunk_names,
        anchor_branch_name=anchor_branch_name,
    )
    sync_branches = resolve_sync_set(roots, all_branches)
    order = topological_sync_order(sync_branches, all_branches)
    return SyncPlan(
        stack_id=stack_id,
        anchor_branch_name=anchor_branch_name,
        labeled_branches=labeled,
        roots=roots,
        sync_branches=sync_branches,
        order=order,
    )
