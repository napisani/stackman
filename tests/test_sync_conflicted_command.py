from __future__ import annotations

import io
import subprocess

from stackman.commands import sync_conflicted
from stackman.commands.app import StackmanApp
from stackman.lib.conflict_prediction import ConflictReport
from stackman.lib.store import initialize, label_branch, upsert_branch


def test_sync_conflicted_skips_clean_stacks(git_repo, stackman_db_path) -> None:
    git_repo.checkout_new("feature", from_ref="main")
    git_repo.commit("feature work", filename="feature.txt", content="feature\n")
    fork = git_repo.merge_base("feature", "main")
    _track_branch(stackman_db_path, git_repo, "feature", "main", fork, "stack-clean")
    git_repo.checkout("main")
    git_repo.commit("main work", filename="main.txt", content="main\n")
    feature_before = git_repo.rev_parse("feature")

    app, stdout, _ = _app(git_repo, stackman_db_path)

    assert app.sync_conflicted() == 0
    assert "No predicted rebase conflicts" in stdout.getvalue()
    assert git_repo.rev_parse("feature") == feature_before


def test_sync_conflicted_syncs_every_predicted_stack(git_repo, stackman_db_path, tmp_path) -> None:
    _create_two_conflicted_stacks(git_repo, stackman_db_path)
    resolver = _resolver(tmp_path)

    app, stdout, _ = _app(git_repo, stackman_db_path)

    assert app.sync_conflicted(resolver=str(resolver), no_wait=True) == 0
    assert "stack-one" in stdout.getvalue()
    assert "stack-two" in stdout.getvalue()
    assert git_repo.is_ancestor(git_repo.rev_parse("main"), "one")
    assert git_repo.is_ancestor(git_repo.rev_parse("main"), "two")


def test_sync_conflicted_stops_before_syncing_when_a_probe_errors(
    git_repo, stackman_db_path, monkeypatch
) -> None:
    git_repo.checkout_new("feature", from_ref="main")
    git_repo.commit("feature work", filename="feature.txt", content="feature\n")
    fork = git_repo.merge_base("feature", "main")
    _track_branch(stackman_db_path, git_repo, "feature", "main", fork, "stack-probe-error")
    feature_before = git_repo.rev_parse("feature")
    monkeypatch.setattr(
        sync_conflicted,
        "probe_all",
        lambda *_args, **_kwargs: [
            ConflictReport(stack="stack-probe-error", status="probe_error", detail="probe failed")
        ],
    )

    app, stdout, _ = _app(git_repo, stackman_db_path)

    assert app.sync_conflicted() == 2
    assert "probe failed" in stdout.getvalue()
    assert git_repo.rev_parse("feature") == feature_before


def test_sync_conflicted_probe_error_does_not_pull_the_invoking_branch(
    git_repo, stackman_db_path, monkeypatch, tmp_path
) -> None:
    remote = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "--bare", str(remote)], check=True, capture_output=True, text=True
    )
    git_repo.git("remote", "add", "origin", str(remote))
    git_repo.git("push", "-u", "origin", "main")

    git_repo.checkout_new("feature", from_ref="main")
    git_repo.commit("feature work", filename="feature.txt", content="feature\n")
    fork = git_repo.merge_base("feature", "main")
    _track_branch(stackman_db_path, git_repo, "feature", "main", fork, "stack-probe-error")
    git_repo.checkout("main")

    updater = tmp_path / "updater"
    subprocess.run(
        ["git", "clone", str(remote), str(updater)], check=True, capture_output=True, text=True
    )
    subprocess.run(["git", "-C", str(updater), "config", "user.name", "Stackman Test"], check=True)
    subprocess.run(
        ["git", "-C", str(updater), "config", "user.email", "stackman@example.com"], check=True
    )
    (updater / "remote-main.txt").write_text("remote main\n")
    subprocess.run(["git", "-C", str(updater), "add", "remote-main.txt"], check=True)
    subprocess.run(["git", "-C", str(updater), "commit", "-m", "remote main moves"], check=True)
    subprocess.run(["git", "-C", str(updater), "push", "origin", "main"], check=True)
    main_before = git_repo.rev_parse("main")

    monkeypatch.setattr(
        sync_conflicted,
        "probe_all",
        lambda *_args, **_kwargs: [
            ConflictReport(stack="stack-probe-error", status="probe_error", detail="probe failed")
        ],
    )
    app, _, _ = _app(git_repo, stackman_db_path)

    assert app.sync_conflicted() == 2
    assert git_repo.rev_parse("main") == main_before


def test_sync_conflicted_stops_after_the_first_sync_failure(
    git_repo, stackman_db_path, tmp_path
) -> None:
    _create_two_conflicted_stacks(git_repo, stackman_db_path)
    failing_resolver = tmp_path / "fail.sh"
    failing_resolver.write_text("#!/bin/sh\nexit 1\n")
    failing_resolver.chmod(0o755)
    two_before = git_repo.rev_parse("two")

    app, stdout, _ = _app(git_repo, stackman_db_path)

    assert app.sync_conflicted(resolver=str(failing_resolver), no_wait=True) == 1
    assert "stack-one" in stdout.getvalue()
    assert git_repo.rev_parse("two") == two_before


def test_sync_conflicted_dry_run_does_not_rewrite_selected_branches(
    git_repo, stackman_db_path
) -> None:
    _create_two_conflicted_stacks(git_repo, stackman_db_path)
    one_before = git_repo.rev_parse("one")
    two_before = git_repo.rev_parse("two")

    app, stdout, _ = _app(git_repo, stackman_db_path)

    assert app.sync_conflicted(dry_run=True) == 0
    assert "Dry run" in stdout.getvalue()
    assert git_repo.rev_parse("one") == one_before
    assert git_repo.rev_parse("two") == two_before


def _create_two_conflicted_stacks(git_repo, stackman_db_path) -> None:
    git_repo.commit("first base", filename="one.txt", content="base\n")
    git_repo.commit("second base", filename="two.txt", content="base\n")

    git_repo.checkout_new("one", from_ref="main")
    git_repo.commit("one changes", filename="one.txt", content="one\n")
    one_fork = git_repo.merge_base("one", "main")
    _track_branch(stackman_db_path, git_repo, "one", "main", one_fork, "stack-one")

    git_repo.checkout_new("two", from_ref="main")
    git_repo.commit("two changes", filename="two.txt", content="two\n")
    two_fork = git_repo.merge_base("two", "main")
    _track_branch(stackman_db_path, git_repo, "two", "main", two_fork, "stack-two")

    git_repo.checkout("main")
    git_repo.commit("main changes one", filename="one.txt", content="main\n")
    git_repo.commit("main changes two", filename="two.txt", content="main\n")


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


def _resolver(tmp_path):
    resolver = tmp_path / "resolve.sh"
    resolver.write_text(
        "#!/bin/sh\n"
        "git diff --name-only --diff-filter=U | while read -r file; do\n"
        "  printf 'resolved\\n' > \"$file\"\n"
        '  git add "$file"\n'
        "done\n"
        "GIT_EDITOR=true git rebase --continue\n"
    )
    resolver.chmod(0o755)
    return resolver
