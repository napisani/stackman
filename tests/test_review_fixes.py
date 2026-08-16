from __future__ import annotations

import io
from pathlib import Path

from stackman.app import StackmanApp
from stackman.commands.runner import run_safely
from stackman.context import AppContext
from stackman.store import initialize, list_branch_labels
from stackman.store.connection import connect, normalize_path
from stackman.store.repos import merge_repo_records, migrate_repo_roots_to_git_common_dir

# --- F6: XDG_DATA_HOME ------------------------------------------------------


def test_default_db_path_honors_xdg_data_home(monkeypatch, tmp_path) -> None:
    from stackman.cli import _default_db_path

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert _default_db_path() == tmp_path / "xdg" / "stackman" / "stackman.db"

    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    assert _default_db_path() == Path("~/.local/share/stackman/stackman.db").expanduser()


# --- F2: error boundary catches unexpected internal errors ------------------


def test_run_safely_renders_unexpected_exception(tmp_path) -> None:
    err = io.StringIO()
    ctx = AppContext(
        db_path=tmp_path / "s.db",
        cwd=tmp_path,
        stdin=io.StringIO(""),
        stdout=io.StringIO(),
        stderr=err,
    )

    def boom(_c: AppContext) -> int:
        raise ValueError("kaboom")

    assert run_safely(ctx, boom) == 1
    assert "kaboom" in err.getvalue()


# --- F7: repo migration / merge logic ---------------------------------------


def test_merge_repo_records_moves_and_dedupes_branches(tmp_path) -> None:
    db = tmp_path / "s.db"
    initialize(db)
    with connect(db) as conn:
        conn.execute("INSERT INTO repos(root_path) VALUES ('/survivor')")
        conn.execute("INSERT INTO repos(root_path) VALUES ('/victim')")
        survivor = conn.execute("SELECT id FROM repos WHERE root_path='/survivor'").fetchone()["id"]
        victim = conn.execute("SELECT id FROM repos WHERE root_path='/victim'").fetchone()["id"]
        for repo_id, name, sha in (
            (survivor, "feature", "aaa"),
            (victim, "feature", "bbb"),  # collides with survivor's 'feature'
            (victim, "other", "ccc"),
        ):
            conn.execute(
                "INSERT INTO branches(repo_id, branch_name, parent_branch_name, fork_point_sha) VALUES (?,?,?,?)",
                (repo_id, name, "main", sha),
            )

        merge_repo_records(conn, survivor_id=survivor, victim_id=victim)

        assert (
            conn.execute("SELECT COUNT(*) c FROM repos WHERE id=?", (victim,)).fetchone()["c"] == 0
        )
        names = {
            r["branch_name"]
            for r in conn.execute("SELECT branch_name FROM branches WHERE repo_id=?", (survivor,))
        }
        assert names == {"feature", "other"}
        # The colliding 'feature' was deduped, not duplicated.
        assert (
            conn.execute("SELECT COUNT(*) c FROM branches WHERE branch_name='feature'").fetchone()[
                "c"
            ]
            == 1
        )


def test_migrate_collapses_worktree_path_to_common_dir(tmp_path, git_repo) -> None:
    db = tmp_path / "s.db"
    initialize(db)
    common_key = git_repo.canonical_repo_key()
    with connect(db) as conn:
        # A legacy row keyed by the worktree root rather than the git-common-dir.
        conn.execute("INSERT INTO repos(root_path) VALUES (?)", (str(git_repo.root),))
        migrate_repo_roots_to_git_common_dir(conn)
        paths = {r["root_path"] for r in conn.execute("SELECT root_path FROM repos")}
    assert paths == {common_key}


def test_migrate_leaves_nonexistent_path_untouched(tmp_path) -> None:
    db = tmp_path / "s.db"
    initialize(db)
    bogus = str(tmp_path / "gone")
    with connect(db) as conn:
        conn.execute("INSERT INTO repos(root_path) VALUES (?)", (bogus,))
        migrate_repo_roots_to_git_common_dir(conn)
        paths = {r["root_path"] for r in conn.execute("SELECT root_path FROM repos")}
    assert normalize_path(bogus) in paths


# --- F12: re-tracking an interior branch keeps descendants in one stack ------


def test_retracking_interior_branch_keeps_descendants_in_one_stack(
    git_repo, stackman_db_path
) -> None:
    git_repo.checkout_new("b", from_ref="main")
    git_repo.commit("b", filename="b.txt", content="b\n")
    git_repo.checkout_new("c", from_ref="b")
    git_repo.commit("c", filename="c.txt", content="c\n")
    git_repo.checkout("main")

    ids = iter(["sm_first", "sm_second"])
    app = StackmanApp(
        db_path=stackman_db_path,
        cwd=git_repo.root,
        stdin=io.StringIO(""),
        stdout=io.StringIO(),
        stderr=io.StringIO(),
        stack_id_factory=lambda: next(ids),
    )
    repo_key = git_repo.canonical_repo_key()

    assert app.track(branch="b", parent="main") == 0  # b -> sm_first (main untracked)
    assert app.track(branch="c", parent="b") == 0  # c inherits sm_first
    assert app.track(branch="b", parent="main") == 0  # b -> sm_second; must propagate to c

    b_labels = list_branch_labels(stackman_db_path, repo_key, "b")
    c_labels = list_branch_labels(stackman_db_path, repo_key, "c")
    assert b_labels == ["sm_second"]
    assert c_labels == ["sm_second"]
