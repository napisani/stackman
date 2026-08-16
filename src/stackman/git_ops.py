from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path


def _run_git(
    cwd: Path,
    *args: str,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=check,
        capture_output=True,
        text=True,
    )


def git_output(cwd: Path, *args: str) -> str:
    return _run_git(cwd, *args, check=True).stdout.strip()


def ensure_ref_name_safe(name: str) -> None:
    """Reject ref names that could be misparsed as git options.

    Ref names are passed to git as bare positional args; a name beginning with '-'
    (reachable via plumbing, or from untrusted `gh` PR head/base refs) could be
    read as a flag. Git/GitHub forbid such names via porcelain, so this is a
    defense-in-depth guard at the trust boundary rather than a common case.
    """
    if name.startswith("-"):
        raise ValueError(
            f"Refusing to pass ref name {name!r} to git: names starting with '-' are not allowed."
        )


def rebase_in_progress(cwd: Path) -> bool:
    """Detect rebase state in this checkout (main or linked worktree)."""
    try:
        rel_m = git_output(cwd, "rev-parse", "--git-path", "rebase-merge")
        rel_a = git_output(cwd, "rev-parse", "--git-path", "rebase-apply")
    except subprocess.CalledProcessError:
        return False
    mpath = Path(rel_m)
    apath = Path(rel_a)
    if not mpath.is_absolute():
        mpath = (cwd / mpath).resolve()
    if not apath.is_absolute():
        apath = (cwd / apath).resolve()
    return mpath.is_dir() or apath.is_dir()


def iter_worktree_entries(cwd: Path) -> list[tuple[Path, str | None]]:
    """Each linked worktree: ``(path, branch_name)`` or ``(path, None)`` if detached."""
    text = git_output(cwd, "worktree", "list", "--porcelain")
    entries: list[tuple[Path, str | None]] = []
    cur: Path | None = None
    branch: str | None = None
    for raw in text.splitlines():
        if raw.startswith("worktree "):
            if cur is not None:
                entries.append((cur, branch))
            cur = Path(raw[len("worktree ") :].strip()).resolve()
            branch = None
        elif raw.startswith("branch "):
            ref = raw[len("branch ") :].strip()
            if ref.startswith("refs/heads/"):
                branch = ref.removeprefix("refs/heads/")
    if cur is not None:
        entries.append((cur, branch))
    return entries


def worktree_path_for_branch(cwd: Path, branch: str) -> Path | None:
    """Directory of the worktree that has ``branch`` checked out, if any."""
    for path, br in iter_worktree_entries(cwd):
        if br == branch:
            return path
    return None


def rebase_in_progress_any_linked(cwd: Path) -> bool:
    """True if a rebase is in progress in any worktree of this repository."""
    return any(rebase_in_progress(path) for path, _ in iter_worktree_entries(cwd))


def sync_relevant_worktrees(start_worktree: Path, branch_names: Sequence[str]) -> list[Path]:
    """Worktrees ``stackman sync`` may touch: the starting tree plus each branch's checkout location."""
    root = repo_root(start_worktree)
    by_key: dict[str, Path] = {str(root.resolve()): root}
    for name in branch_names:
        holder = worktree_path_for_branch(root, name) or root
        by_key[str(holder.resolve())] = holder
    return list(by_key.values())


def worktree_dirty_preview(cwd: Path, *, max_lines: int = 20) -> str | None:
    """If the tree is dirty, return a short ``git status --porcelain`` excerpt; otherwise ``None``."""
    text = _run_git(cwd, "status", "--porcelain", check=True).stdout.strip()
    if not text:
        return None
    lines = text.splitlines()
    shown = lines[:max_lines]
    body = "\n".join(f"      {line}" for line in shown)
    extra = len(lines) - len(shown)
    if extra:
        body += f"\n      … ({extra} more porcelain lines)"
    return body


def rev_parse(cwd: Path, ref: str) -> str:
    ensure_ref_name_safe(ref)
    return git_output(cwd, "rev-parse", ref)


def rev_parse_or_none(cwd: Path, ref: str) -> str | None:
    """Resolve a ref to a SHA, or None if it doesn't resolve (e.g. an unfetched upstream)."""
    ensure_ref_name_safe(ref)
    result = _run_git(cwd, "rev-parse", ref, check=False)
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def checkout(cwd: Path, branch: str) -> None:
    ensure_ref_name_safe(branch)
    _run_git(cwd, "checkout", branch, check=True)


def rebase_onto(
    cwd: Path,
    *,
    onto: str,
    upstream: str,
) -> subprocess.CompletedProcess[str]:
    """Run `git rebase --onto onto upstream` on the current branch (non-interactive)."""
    return _run_git(
        cwd,
        "rebase",
        "--onto",
        onto,
        upstream,
        check=False,
    )


def commits_since(cwd: Path, upstream: str, *, ref: str = "HEAD") -> list[str]:
    output = git_output(cwd, "rev-list", "--reverse", f"{upstream}..{ref}")
    return [line for line in output.splitlines() if line]


def commit_message(cwd: Path, commit: str) -> str:
    return _run_git(cwd, "log", "-1", "--format=%B", commit, check=True).stdout


def squash_commits_since(
    cwd: Path, upstream: str
) -> tuple[int, subprocess.CompletedProcess[str] | None]:
    commits = commits_since(cwd, upstream, ref="HEAD")
    if len(commits) < 2:
        return len(commits), None

    first_message = commit_message(cwd, commits[0])
    original_head = git_output(cwd, "rev-parse", "HEAD")
    reset_result = _run_git(cwd, "reset", "--soft", upstream, check=False)
    if reset_result.returncode != 0:
        return len(commits), reset_result

    commit_result = subprocess.run(
        ["git", "commit", "--file", "-"],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        input=first_message,
    )
    if commit_result.returncode != 0:
        # Commit failed (e.g. a commit-msg hook) after the soft reset already moved
        # HEAD; restore the original commits so the branch is left as we found it.
        _run_git(cwd, "reset", "--soft", original_head, check=False)
    return len(commits), commit_result


def upstream_branch(cwd: Path, branch: str) -> str | None:
    result = _run_git(
        cwd,
        "rev-parse",
        "--abbrev-ref",
        f"{branch}@{{upstream}}",
        check=False,
    )
    if result.returncode != 0:
        return None
    ref = result.stdout.strip()
    return ref or None


def push_force_with_lease_current_branch(cwd: Path) -> subprocess.CompletedProcess[str]:
    """Push current HEAD using its configured @{upstream} (if any)."""
    return _run_git(cwd, "push", "--force-with-lease", check=False)


def repo_root(cwd: Path) -> Path:
    """Top-level directory of the current worktree (where checkout/rebase run)."""
    return Path(git_output(cwd, "rev-parse", "--show-toplevel"))


def repo_db_key(cwd: Path) -> str:
    """Stable key for one Git repository across linked worktrees (shared object database)."""
    raw = git_output(cwd, "rev-parse", "--git-common-dir")
    path = Path(raw)
    resolved = path.resolve() if path.is_absolute() else (cwd / path).resolve()
    return str(resolved)


def format_repo_key_for_display(repo_key: str) -> str:
    """Prefer showing the main checkout path instead of the bare ``…/.git`` directory when possible."""
    path = Path(repo_key)
    if path.name == ".git":
        return str(path.parent)
    return repo_key


def current_branch(cwd: Path) -> str:
    return git_output(cwd, "branch", "--show-current")


def local_branches(cwd: Path) -> list[str]:
    output = git_output(cwd, "for-each-ref", "--format=%(refname:short)", "refs/heads")
    return [line for line in output.splitlines() if line]


def branch_exists(cwd: Path, branch: str) -> bool:
    if branch.startswith("-"):
        return False
    result = subprocess.run(
        ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def merge_base(cwd: Path, left: str, right: str) -> str:
    ensure_ref_name_safe(left)
    ensure_ref_name_safe(right)
    return git_output(cwd, "merge-base", left, right)


def is_ancestor(cwd: Path, older: str, newer: str) -> bool:
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", older, newer],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def get_git_config(cwd: Path, key: str) -> str | None:
    """Get a git config value; return None if not set or on error."""
    result = _run_git(cwd, "config", "--get", key, check=False)
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value if value else None


def get_pr_number(cwd: Path, branch: str) -> int | None:
    """Get GitHub PR number for a branch; return None if not found or on error."""
    try:
        result = subprocess.run(
            ["gh", "pr", "view", "--branch", branch, "--json", "number"],
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return None
        import json

        data = json.loads(result.stdout)
        pr_number = data.get("number")
        return int(pr_number) if pr_number is not None else None
    except Exception:
        return None


def create_branch_worktree(
    repo_root: Path, worktree_path: Path, branch: str
) -> subprocess.CompletedProcess[str]:
    """Check out an existing branch in a temporary linked worktree.

    Keeping the worktree attached to the branch ensures rebases, squashes, and
    pushes update the named ref rather than only a disposable detached HEAD.
    """
    return _run_git(repo_root, "worktree", "add", str(worktree_path), branch, check=False)


def remove_worktree(repo_root: Path, worktree_path: Path) -> subprocess.CompletedProcess[str]:
    """Remove a worktree (can be in any state).

    Uses --force to remove even if the worktree is in an inconsistent state.
    Returns the subprocess result; check returncode to verify success.
    """
    return _run_git(repo_root, "worktree", "remove", "--force", str(worktree_path), check=False)
