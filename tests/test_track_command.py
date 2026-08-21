from __future__ import annotations

import io

from stackman.commands.app import StackmanApp
from stackman.lib.store import get_branch, get_stack, initialize, list_branch_labels


def _app(git_repo, stackman_db_path, *, stack_id_factory=None, stdout=None) -> StackmanApp:
    return StackmanApp(
        db_path=stackman_db_path,
        cwd=git_repo.root,
        stdin=io.StringIO(""),
        stdout=stdout or io.StringIO(),
        stderr=io.StringIO(),
        stack_id_factory=stack_id_factory,
    )


def test_track_with_parent_persists_branch_lineage(git_repo, stackman_db_path) -> None:
    git_repo.checkout_new("feature", from_ref="main")
    git_repo.commit("feature commit", filename="feature.txt", content="feature\n")

    stdout = io.StringIO()
    app = _app(git_repo, stackman_db_path, stack_id_factory=lambda: "sm_feature", stdout=stdout)

    assert app.track(parent="main") == 0

    out = stdout.getvalue()
    assert "Tracked branch 'feature' with parent 'main'" in out
    assert "sm_feature" not in out

    status_stdout = io.StringIO()
    status_app = _app(git_repo, stackman_db_path, stdout=status_stdout)
    assert status_app.status() == 0
    rendered = status_stdout.getvalue()
    assert "branch: feature" in rendered
    assert "parent: main" in rendered
    assert "sm_feature" not in rendered


def test_track_named_branch_without_checking_it_out(git_repo, stackman_db_path) -> None:
    git_repo.checkout_new("feature", from_ref="main")
    git_repo.commit("feature commit", filename="feature.txt", content="feature\n")
    git_repo.checkout("main")

    app = _app(git_repo, stackman_db_path, stack_id_factory=lambda: "sm_feature")

    assert app.track(branch="feature", parent="main") == 0
    tracked = get_branch(stackman_db_path, git_repo.canonical_repo_key(), "feature")
    assert tracked is not None
    assert tracked.parent_branch_name == "main"
    assert tracked.fork_point_sha == git_repo.merge_base("feature", "main")
    assert git_repo.current_branch() == "main"


def test_chain_tracks_lineage_under_one_stack_label(git_repo, stackman_db_path) -> None:
    git_repo.checkout_new("stack1", from_ref="main")
    git_repo.commit("stack1 commit", filename="stack1.txt", content="stack1\n")
    git_repo.checkout_new("stack2", from_ref="stack1")
    git_repo.commit("stack2 commit", filename="stack2.txt", content="stack2\n")

    calls: list[str] = []

    def factory() -> str:
        calls.append("x")
        return "sm_chain"

    stdout = io.StringIO()
    app = _app(git_repo, stackman_db_path, stack_id_factory=factory, stdout=stdout)

    assert app.chain(anchor="main", branches=("stack1", "stack2")) == 0
    assert calls == ["x"]

    repo_key = git_repo.canonical_repo_key()
    stack1 = get_branch(stackman_db_path, repo_key, "stack1")
    stack2 = get_branch(stackman_db_path, repo_key, "stack2")
    assert stack1 is not None
    assert stack1.parent_branch_name == "main"
    assert stack1.fork_point_sha == git_repo.merge_base("stack1", "main")
    assert stack2 is not None
    assert stack2.parent_branch_name == "stack1"
    assert stack2.fork_point_sha == git_repo.merge_base("stack2", "stack1")
    assert list_branch_labels(stackman_db_path, repo_key, "stack1") == ["sm_chain"]
    assert list_branch_labels(stackman_db_path, repo_key, "stack2") == ["sm_chain"]

    stack = get_stack(stackman_db_path, "sm_chain")
    assert stack is not None
    assert stack.anchor_branch_name == "main"
    out = stdout.getvalue()
    assert "Tracked stack chain 'main' -> 'stack1' -> 'stack2'." in out
    assert "sm_chain" not in out


def test_track_inherits_all_stack_labels_from_tracked_parent(git_repo, stackman_db_path) -> None:
    git_repo.checkout_new("parent_br", from_ref="main")
    git_repo.commit("p", filename="p.txt", content="p\n")
    git_repo.checkout_new("child_br", from_ref="parent_br")
    git_repo.commit("c", filename="c.txt", content="c\n")

    calls: list[str] = []

    def factory() -> str:
        calls.append("factory")
        return "sm_shared_line"

    app = _app(git_repo, stackman_db_path, stack_id_factory=factory)
    git_repo.checkout("parent_br")
    assert app.track(parent="main") == 0

    git_repo.checkout("child_br")
    assert app.track(parent="parent_br") == 0
    assert calls == ["factory"]

    initialize(stackman_db_path)
    assert list_branch_labels(stackman_db_path, git_repo.canonical_repo_key(), "child_br") == [
        "sm_shared_line"
    ]


def test_track_update_replaces_old_internal_stack_label(git_repo, stackman_db_path) -> None:
    git_repo.checkout_new("parent_a", from_ref="main")
    git_repo.commit("a", filename="a.txt", content="a\n")
    git_repo.checkout_new("parent_b", from_ref="main")
    git_repo.commit("b", filename="b.txt", content="b\n")
    git_repo.checkout_new("child", from_ref="parent_a")
    git_repo.commit("child", filename="child.txt", content="child\n")

    generated = iter(["sm_parent_a", "sm_parent_b"])
    app = _app(git_repo, stackman_db_path, stack_id_factory=lambda: next(generated))

    git_repo.checkout("parent_a")
    assert app.track(parent="main") == 0
    git_repo.checkout("parent_b")
    assert app.track(parent="main") == 0
    git_repo.checkout("child")
    assert app.track(parent="parent_a") == 0
    assert list_branch_labels(stackman_db_path, git_repo.canonical_repo_key(), "child") == [
        "sm_parent_a"
    ]

    assert app.track(parent="parent_b") == 0

    repo_key = git_repo.canonical_repo_key()
    tracked = get_branch(stackman_db_path, repo_key, "child")
    assert tracked is not None
    assert tracked.parent_branch_name == "parent_b"
    assert list_branch_labels(stackman_db_path, repo_key, "child") == ["sm_parent_b"]


def test_track_linear_stack_shares_one_auto_id(git_repo, stackman_db_path) -> None:
    git_repo.checkout_new("line_a", from_ref="main")
    git_repo.commit("a", filename="la.txt", content="a\n")
    git_repo.checkout_new("line_b", from_ref="line_a")
    git_repo.commit("b", filename="lb.txt", content="b\n")

    calls: list[str] = []

    def factory() -> str:
        calls.append("x")
        return "sm_line_root"

    app = _app(git_repo, stackman_db_path, stack_id_factory=factory)
    git_repo.checkout("line_a")
    assert app.track(parent="main") == 0
    assert len(calls) == 1

    git_repo.checkout("line_b")
    assert app.track(parent="line_a") == 0
    assert len(calls) == 1

    initialize(stackman_db_path)
    assert list_branch_labels(stackman_db_path, git_repo.canonical_repo_key(), "line_a") == [
        "sm_line_root"
    ]
    assert list_branch_labels(stackman_db_path, git_repo.canonical_repo_key(), "line_b") == [
        "sm_line_root"
    ]


def test_track_records_anchor_for_new_auto_stack(git_repo, stackman_db_path) -> None:
    git_repo.checkout_new("root_feature", from_ref="main")
    git_repo.commit("root", filename="root.txt", content="root\n")

    app = _app(git_repo, stackman_db_path, stack_id_factory=lambda: "sm_root")

    assert app.track(parent="main") == 0
    stack = get_stack(stackman_db_path, "sm_root")
    assert stack is not None
    assert stack.anchor_branch_name == "main"


def test_track_inherited_stack_label_does_not_change_anchor(git_repo, stackman_db_path) -> None:
    git_repo.checkout_new("root_feature", from_ref="main")
    git_repo.commit("root", filename="root.txt", content="root\n")
    git_repo.checkout_new("child_feature", from_ref="root_feature")
    git_repo.commit("child", filename="child.txt", content="child\n")

    root_app = _app(git_repo, stackman_db_path, stack_id_factory=lambda: "sm_root")
    git_repo.checkout("root_feature")
    assert root_app.track(parent="main") == 0

    child_app = _app(git_repo, stackman_db_path)
    git_repo.checkout("child_feature")
    assert child_app.track(parent="root_feature") == 0

    stack = get_stack(stackman_db_path, "sm_root")
    assert stack is not None
    assert stack.anchor_branch_name == "main"


def test_track_requires_explicit_parent(git_repo, stackman_db_path) -> None:
    git_repo.checkout_new("feature", from_ref="main")
    git_repo.commit("feature", filename="feature.txt", content="feature\n")

    result = _app(git_repo, stackman_db_path).track(branch="feature", parent="missing")

    assert result == 1
