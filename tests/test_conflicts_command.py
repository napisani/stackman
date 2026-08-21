from __future__ import annotations

import io
import json
import subprocess

from stackman import git_ops
from stackman.app import StackmanApp
from stackman.commands import conflicts
from stackman.store import initialize, label_branch, upsert_branch


def test_conflicts_reports_a_predicted_rebase_conflict_without_rewriting_branches(
    git_repo,
    stackman_db_path,
) -> None:
    git_repo.commit("shared base", filename="shared.txt", content="base\n")
    git_repo.checkout_new("feature", from_ref="main")
    git_repo.commit("feature changes shared", filename="shared.txt", content="feature\n")
    fork = git_repo.merge_base("feature", "main")

    initialize(stackman_db_path)
    repo_key = git_repo.canonical_repo_key()
    upsert_branch(
        stackman_db_path,
        repo_root=repo_key,
        branch_name="feature",
        parent_branch_name="main",
        fork_point_sha=fork,
    )
    label_branch(stackman_db_path, repo_key, "feature", "stack-conflict", anchor_branch_name="main")

    git_repo.checkout("main")
    git_repo.commit("main changes shared", filename="shared.txt", content="main\n")
    feature_before = git_repo.rev_parse("feature")

    stdout = io.StringIO()
    app = StackmanApp(
        db_path=stackman_db_path,
        cwd=git_repo.root,
        stdin=io.StringIO(""),
        stdout=stdout,
        stderr=io.StringIO(),
    )

    assert app.conflicts() == 1
    assert "stack-conflict" in stdout.getvalue()
    assert "feature" in stdout.getvalue()
    assert "shared.txt" in stdout.getvalue()
    assert git_repo.rev_parse("feature") == feature_before


def test_conflicts_json_reports_a_clean_stack(
    git_repo,
    stackman_db_path,
) -> None:
    git_repo.checkout_new("feature", from_ref="main")
    git_repo.commit("feature work", filename="feature.txt", content="feature\n")
    fork = git_repo.merge_base("feature", "main")

    initialize(stackman_db_path)
    repo_key = git_repo.canonical_repo_key()
    upsert_branch(
        stackman_db_path,
        repo_root=repo_key,
        branch_name="feature",
        parent_branch_name="main",
        fork_point_sha=fork,
    )
    label_branch(stackman_db_path, repo_key, "feature", "stack-clean", anchor_branch_name="main")

    git_repo.checkout("main")
    git_repo.checkout_new("second", from_ref="main")
    git_repo.commit("second work", filename="second.txt", content="second\n")
    second_fork = git_repo.merge_base("second", "main")
    upsert_branch(
        stackman_db_path,
        repo_root=repo_key,
        branch_name="second",
        parent_branch_name="main",
        fork_point_sha=second_fork,
    )
    label_branch(stackman_db_path, repo_key, "second", "stack-second", anchor_branch_name="main")

    git_repo.checkout("main")
    git_repo.commit("main moves elsewhere", filename="main.txt", content="main\n")
    stdout = io.StringIO()
    app = StackmanApp(
        db_path=stackman_db_path,
        cwd=git_repo.root,
        stdin=io.StringIO(""),
        stdout=stdout,
        stderr=io.StringIO(),
    )

    assert app.conflicts(as_json=True) == 0
    assert json.loads(stdout.getvalue()) == [
        {
            "stack": "stack-clean",
            "status": "clean",
            "branch": None,
            "parent": None,
            "files": [],
            "detail": None,
        },
        {
            "stack": "stack-second",
            "status": "clean",
            "branch": None,
            "parent": None,
            "files": [],
            "detail": None,
        },
    ]


def test_conflicts_does_not_update_refs_when_rebase_update_refs_is_enabled(
    git_repo,
    stackman_db_path,
) -> None:
    git_repo.commit("shared base", filename="shared.txt", content="base\n")
    git_repo.checkout_new("feature", from_ref="main")
    git_repo.commit("feature changes shared", filename="shared.txt", content="feature\n")
    fork = git_repo.merge_base("feature", "main")
    _track_branch(stackman_db_path, git_repo, "feature", "main", fork, "stack-update-refs")

    git_repo.checkout("main")
    git_repo.commit("main changes shared", filename="shared.txt", content="main\n")
    git_repo.git("config", "rebase.updateRefs", "true")
    feature_before = git_repo.rev_parse("feature")

    app, _, _ = _app(git_repo, stackman_db_path)

    assert app.conflicts() == 1
    assert git_repo.rev_parse("feature") == feature_before


def test_conflicts_json_keeps_fetch_diagnostics_off_stdout(
    git_repo,
    stackman_db_path,
    tmp_path,
) -> None:
    remote = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "--bare", str(remote)], check=True, capture_output=True, text=True
    )
    git_repo.git("remote", "add", "origin", str(remote))
    git_repo.checkout_new("feature", from_ref="main")
    git_repo.commit("feature work", filename="feature.txt", content="feature\n")
    fork = git_repo.merge_base("feature", "main")
    _track_branch(stackman_db_path, git_repo, "feature", "main", fork, "stack-json-origin")

    app, stdout, stderr = _app(git_repo, stackman_db_path)

    assert app.conflicts(as_json=True) == 0
    assert json.loads(stdout.getvalue())[0]["status"] == "clean"
    assert "Fetching origin" in stderr.getvalue()


def test_conflicts_reports_probe_setup_errors_as_json(
    git_repo,
    stackman_db_path,
    monkeypatch,
) -> None:
    git_repo.checkout_new("feature", from_ref="main")
    git_repo.commit("feature work", filename="feature.txt", content="feature\n")
    fork = git_repo.merge_base("feature", "main")
    _track_branch(stackman_db_path, git_repo, "feature", "main", fork, "stack-setup-error")
    monkeypatch.setattr(conflicts.tempfile, "mkdtemp", _raise_permission_denied)

    app, stdout, _ = _app(git_repo, stackman_db_path)

    assert app.conflicts(as_json=True) == 2
    report = json.loads(stdout.getvalue())[0]
    assert report["status"] == "probe_error"
    assert "denied" in report["detail"]


def test_fetch_remote_disables_interactive_prompts(monkeypatch, tmp_path) -> None:
    calls: list[dict[str, object]] = []

    def fake_run(*args, **kwargs):
        calls.append(kwargs)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(git_ops.subprocess, "run", fake_run)

    assert git_ops.fetch_remote(tmp_path, "origin").returncode == 0
    assert calls[0]["stdin"] is subprocess.DEVNULL
    assert calls[0]["env"]["GIT_TERMINAL_PROMPT"] == "0"


def test_conflicts_reports_failed_probe_cleanup(
    git_repo,
    stackman_db_path,
    monkeypatch,
) -> None:
    git_repo.checkout_new("feature", from_ref="main")
    git_repo.commit("feature work", filename="feature.txt", content="feature\n")
    fork = git_repo.merge_base("feature", "main")
    _track_branch(stackman_db_path, git_repo, "feature", "main", fork, "stack-cleanup-error")
    real_remove_worktree = conflicts.remove_worktree

    def remove_then_report_failure(repo_root, worktree_path):
        real_remove_worktree(repo_root, worktree_path)
        return subprocess.CompletedProcess([], 1, "", "remove failed")

    monkeypatch.setattr(conflicts, "remove_worktree", remove_then_report_failure)
    app, stdout, _ = _app(git_repo, stackman_db_path)

    assert app.conflicts() == 2
    assert "remove failed" in stdout.getvalue()


def _track_branch(stackman_db_path, git_repo, branch, parent, fork, stack_id) -> None:
    initialize(stackman_db_path)
    repo_key = git_repo.canonical_repo_key()
    upsert_branch(
        stackman_db_path,
        repo_root=repo_key,
        branch_name=branch,
        parent_branch_name=parent,
        fork_point_sha=fork,
    )
    label_branch(stackman_db_path, repo_key, branch, stack_id, anchor_branch_name=parent)


def _app(git_repo, stackman_db_path):
    stdout = io.StringIO()
    stderr = io.StringIO()
    return (
        StackmanApp(
            db_path=stackman_db_path,
            cwd=git_repo.root,
            stdin=io.StringIO(""),
            stdout=stdout,
            stderr=stderr,
        ),
        stdout,
        stderr,
    )


def _raise_permission_denied(*_args, **_kwargs):
    raise PermissionError("denied")
