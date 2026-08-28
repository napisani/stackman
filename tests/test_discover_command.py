from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path

from click.testing import CliRunner

from stackman.cli import cli
from stackman.commands.app import StackmanApp
from stackman.lib.store import get_branch


def _write_fake_gh(tmp_path: Path, monkeypatch, prs: list[dict]) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    prs_path = tmp_path / "prs.json"
    prs_path.write_text(json.dumps(prs))
    gh = bin_dir / "gh"
    gh.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "prs = json.load(open(os.environ['FAKE_GH_PRS']))\n"
        "args = sys.argv[1:]\n"
        "if args[:2] == ['pr', 'view']:\n"
        "    number = int(args[2])\n"
        "    for pr in prs:\n"
        "        if int(pr['number']) == number:\n"
        "            print(json.dumps(pr))\n"
        "            raise SystemExit(0)\n"
        "    print('not found', file=sys.stderr)\n"
        "    raise SystemExit(1)\n"
        "if args[:2] == ['pr', 'list']:\n"
        "    result = [pr for pr in prs if pr.get('state') == 'OPEN']\n"
        "    try:\n"
        "        author_idx = args.index('--author')\n"
        "        author = args[author_idx + 1]\n"
        "        if author == '@me':\n"
        "            author = 'me'\n"
        "        result = [pr for pr in result if pr.get('authorLogin', '') == author]\n"
        "    except ValueError:\n"
        "        pass\n"
        "    print(json.dumps(result))\n"
        "    raise SystemExit(0)\n"
        "print('unexpected gh args: ' + ' '.join(args), file=sys.stderr)\n"
        "raise SystemExit(2)\n"
    )
    gh.chmod(0o755)
    monkeypatch.setenv("FAKE_GH_PRS", str(prs_path))
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")


def _stack_prs() -> list[dict]:
    return [
        {
            "number": 1,
            "headRefName": "feature-a",
            "baseRefName": "main",
            "title": "feature a",
            "url": "https://example.test/1",
            "state": "OPEN",
            "authorLogin": "me",
        },
        {
            "number": 2,
            "headRefName": "feature-b",
            "baseRefName": "feature-a",
            "title": "feature b",
            "url": "https://example.test/2",
            "state": "OPEN",
            "authorLogin": "me",
        },
        {
            "number": 3,
            "headRefName": "feature-c",
            "baseRefName": "feature-b",
            "title": "feature c",
            "url": "https://example.test/3",
            "state": "OPEN",
            "authorLogin": "me",
        },
        {
            "number": 4,
            "headRefName": "feature-z",
            "baseRefName": "feature-a",
            "title": "feature z",
            "url": "https://example.test/4",
            "state": "OPEN",
            "authorLogin": "me",
        },
        {
            "number": 5,
            "headRefName": "unrelated",
            "baseRefName": "main",
            "title": "unrelated root PR",
            "url": "https://example.test/5",
            "state": "OPEN",
            "authorLogin": "colleague",
        },
    ]


def _make_local_stack(git_repo) -> None:
    git_repo.checkout_new("feature-a", from_ref="main")
    git_repo.commit("a", filename="a.txt", content="a\n")
    git_repo.checkout_new("feature-b", from_ref="feature-a")
    git_repo.commit("b", filename="b.txt", content="b\n")
    git_repo.checkout_new("feature-c", from_ref="feature-b")
    git_repo.commit("c", filename="c.txt", content="c\n")
    git_repo.checkout("feature-a")
    git_repo.checkout_new("feature-z", from_ref="feature-a")
    git_repo.commit("z", filename="z.txt", content="z\n")
    git_repo.checkout("main")


def test_discover_help_requires_pr_number() -> None:
    runner = CliRunner()

    help_result = runner.invoke(cli, ["gh", "discover", "--help"])
    assert help_result.exit_code == 0
    assert "PR_NUMBER" in help_result.output
    assert "--apply" in help_result.output

    missing_arg = runner.invoke(cli, ["gh", "discover"])
    assert missing_arg.exit_code != 0
    assert "Missing argument 'PR_NUMBER'" in missing_arg.output


def test_gh_group_help_lists_gh_subcommands() -> None:
    runner = CliRunner()

    result = runner.invoke(cli, ["gh", "--help"])
    assert result.exit_code == 0
    assert "discover" in result.output
    assert "discover-mine" in result.output


def test_top_level_help_hides_gh_subcommands() -> None:
    runner = CliRunner()

    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "discover" not in result.output
    assert "gh" in result.output


def test_discover_is_read_only_by_default(
    git_repo, stackman_db_path, tmp_path: Path, monkeypatch
) -> None:
    _make_local_stack(git_repo)
    _write_fake_gh(tmp_path, monkeypatch, _stack_prs())

    stdout = io.StringIO()
    app = StackmanApp(
        db_path=stackman_db_path,
        cwd=git_repo.root,
        stdin=io.StringIO(""),
        stdout=stdout,
        stderr=io.StringIO(),
    )

    assert app.discover(pr_number=3) == 0

    out = stdout.getvalue()
    assert "Discovered PR stack from PR #3 (feature-c):" in out
    assert "main" in out
    assert "feature-a (#1)" in out
    assert "feature-b (#2)" in out
    assert "feature-c (#3)" in out
    assert "feature-z (#4)" in out
    assert "unrelated" not in out
    assert "stackman track feature-a --parent main" in out
    assert "Run with --apply" in out
    assert not stackman_db_path.exists()


def test_discover_apply_tracks_discovered_pr_tree(
    git_repo, stackman_db_path, tmp_path: Path, monkeypatch
) -> None:
    _make_local_stack(git_repo)
    _write_fake_gh(tmp_path, monkeypatch, _stack_prs())

    stdout = io.StringIO()
    app = StackmanApp(
        db_path=stackman_db_path,
        cwd=git_repo.root,
        stdin=io.StringIO(""),
        stdout=stdout,
        stderr=io.StringIO(),
    )

    assert app.discover(pr_number=3, apply=True) == 0

    repo_key = git_repo.canonical_repo_key()
    feature_a = get_branch(stackman_db_path, repo_key, "feature-a")
    feature_b = get_branch(stackman_db_path, repo_key, "feature-b")
    feature_c = get_branch(stackman_db_path, repo_key, "feature-c")
    feature_z = get_branch(stackman_db_path, repo_key, "feature-z")
    unrelated = get_branch(stackman_db_path, repo_key, "unrelated")

    assert feature_a is not None and feature_a.parent_branch_name == "main"
    assert feature_b is not None and feature_b.parent_branch_name == "feature-a"
    assert feature_c is not None and feature_c.parent_branch_name == "feature-b"
    assert feature_z is not None and feature_z.parent_branch_name == "feature-a"
    assert unrelated is None
    assert "Discovery apply complete: tracked 4, skipped 0." in stdout.getvalue()


def test_discover_apply_skips_missing_local_branches(
    git_repo,
    stackman_db_path,
    tmp_path: Path,
    monkeypatch,
) -> None:
    git_repo.checkout_new("feature-a", from_ref="main")
    git_repo.commit("a", filename="a.txt", content="a\n")
    git_repo.checkout("main")
    _write_fake_gh(tmp_path, monkeypatch, _stack_prs())

    stdout = io.StringIO()
    app = StackmanApp(
        db_path=stackman_db_path,
        cwd=git_repo.root,
        stdin=io.StringIO(""),
        stdout=stdout,
        stderr=io.StringIO(),
    )

    assert app.discover(pr_number=3, apply=True) == 0

    repo_key = git_repo.canonical_repo_key()
    feature_a = get_branch(stackman_db_path, repo_key, "feature-a")
    feature_b = get_branch(stackman_db_path, repo_key, "feature-b")
    assert feature_a is not None and feature_a.parent_branch_name == "main"
    assert feature_b is None
    out = stdout.getvalue()
    assert "skipped 'feature-b': local branch missing." in out
    assert "skipped 'feature-c': local branch missing." in out
    assert "Discovery apply complete: tracked 1, skipped 3." in out


def test_discover_mine_is_read_only_by_default(
    git_repo, stackman_db_path, tmp_path: Path, monkeypatch
) -> None:
    _make_local_stack(git_repo)
    _write_fake_gh(tmp_path, monkeypatch, _stack_prs())

    stdout = io.StringIO()
    app = StackmanApp(
        db_path=stackman_db_path,
        cwd=git_repo.root,
        stdin=io.StringIO(""),
        stdout=stdout,
        stderr=io.StringIO(),
    )

    assert app.discover_mine() == 0

    out = stdout.getvalue()
    assert "Discovered 4 open pull request(s) authored by you:" in out
    assert "feature-a (#1)" in out
    assert "feature-b (#2)" in out
    assert "feature-c (#3)" in out
    assert "feature-z (#4)" in out
    assert "unrelated" not in out  # colleague's PR is filtered out by --author @me
    assert "stackman track feature-a --parent main" in out
    assert "Run with --apply to update Stackman tracking." in out
    assert not stackman_db_path.exists()


def test_discover_mine_applies_all_my_prs(
    git_repo, stackman_db_path, tmp_path: Path, monkeypatch
) -> None:
    _make_local_stack(git_repo)
    _write_fake_gh(tmp_path, monkeypatch, _stack_prs())

    stdout = io.StringIO()
    app = StackmanApp(
        db_path=stackman_db_path,
        cwd=git_repo.root,
        stdin=io.StringIO(""),
        stdout=stdout,
        stderr=io.StringIO(),
    )

    assert app.discover_mine(apply=True) == 0

    repo_key = git_repo.canonical_repo_key()
    feature_a = get_branch(stackman_db_path, repo_key, "feature-a")
    feature_b = get_branch(stackman_db_path, repo_key, "feature-b")
    feature_c = get_branch(stackman_db_path, repo_key, "feature-c")
    feature_z = get_branch(stackman_db_path, repo_key, "feature-z")
    unrelated = get_branch(stackman_db_path, repo_key, "unrelated")

    assert feature_a is not None and feature_a.parent_branch_name == "main"
    assert feature_b is not None and feature_b.parent_branch_name == "feature-a"
    assert feature_c is not None and feature_c.parent_branch_name == "feature-b"
    assert feature_z is not None and feature_z.parent_branch_name == "feature-a"
    assert unrelated is None
    assert "Discovery apply complete: tracked 4, skipped 0." in stdout.getvalue()


def test_discover_mine_skips_missing_local_branches(
    git_repo,
    stackman_db_path,
    tmp_path: Path,
    monkeypatch,
) -> None:
    # Only feature-a exists locally; feature-b/c/z are absent.
    git_repo.checkout_new("feature-a", from_ref="main")
    git_repo.commit("a", filename="a.txt", content="a\n")
    git_repo.checkout("main")
    _write_fake_gh(tmp_path, monkeypatch, _stack_prs())

    stdout = io.StringIO()
    app = StackmanApp(
        db_path=stackman_db_path,
        cwd=git_repo.root,
        stdin=io.StringIO(""),
        stdout=stdout,
        stderr=io.StringIO(),
    )

    assert app.discover_mine(apply=True) == 0

    repo_key = git_repo.canonical_repo_key()
    feature_a = get_branch(stackman_db_path, repo_key, "feature-a")
    feature_b = get_branch(stackman_db_path, repo_key, "feature-b")
    assert feature_a is not None and feature_a.parent_branch_name == "main"
    assert feature_b is None
    out = stdout.getvalue()
    assert "skipped 'feature-b': local branch missing." in out
    assert "skipped 'feature-c': local branch missing." in out
    assert "skipped 'feature-z': local branch missing." in out
    assert "Discovery apply complete: tracked 1, skipped 3." in out


def test_discover_mine_no_open_prs_authored_by_me(
    git_repo, stackman_db_path, tmp_path: Path, monkeypatch
) -> None:
    # Every PR in the repo is authored by someone else, so @me matches nothing.
    colleague_prs = [
        {
            "number": 42,
            "headRefName": "colleague-work",
            "baseRefName": "main",
            "title": "colleague work",
            "url": "https://example.test/42",
            "state": "OPEN",
            "authorLogin": "colleague",
        }
    ]
    _write_fake_gh(tmp_path, monkeypatch, colleague_prs)

    stdout = io.StringIO()
    app = StackmanApp(
        db_path=stackman_db_path,
        cwd=git_repo.root,
        stdin=io.StringIO(""),
        stdout=stdout,
        stderr=io.StringIO(),
    )

    assert app.discover_mine() == 0
    assert "No open pull requests authored by you in this repository." in stdout.getvalue()
    assert not stackman_db_path.exists()


def test_discover_mine_cli_invocation(
    git_repo, stackman_db_path, tmp_path: Path, monkeypatch
) -> None:
    _make_local_stack(git_repo)
    _write_fake_gh(tmp_path, monkeypatch, _stack_prs())

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "gh",
            "discover-mine",
            "--apply",
            "--db-path",
            str(stackman_db_path),
            "--repo",
            str(git_repo.root),
        ],
    )
    assert result.exit_code == 0
    assert "Discovery apply complete: tracked 4, skipped 0." in result.output
