from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class GitRepoFixture:
    root: Path

    @classmethod
    def create(cls, root: Path, *, initial_branch: str = "main") -> GitRepoFixture:
        root.mkdir(parents=True, exist_ok=True)
        fixture = cls(root=root)
        fixture.git("init")
        fixture.git("checkout", "-b", initial_branch)
        fixture.configure_identity()
        fixture.commit("initial commit")
        return fixture

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=self.root,
            check=True,
            capture_output=True,
            text=True,
        )

    def git(self, *args: str) -> str:
        return self._run(*args).stdout.strip()

    def configure_identity(self) -> None:
        self.git("config", "user.name", "Stackman Test")
        self.git("config", "user.email", "stackman@example.com")

    def write_file(self, name: str, content: str) -> Path:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    def add(self, *paths: str) -> None:
        self.git("add", *paths)

    def commit(
        self,
        message: str,
        *,
        filename: str = "test.txt",
        content: str | None = None,
    ) -> str:
        if content is None:
            content = f"{message}\n"
        self.write_file(filename, content)
        self.add(filename)
        self.git("commit", "-m", message)
        return self.rev_parse("HEAD")

    def checkout_new(self, branch: str, *, from_ref: str = "HEAD") -> None:
        self.git("checkout", "-b", branch, from_ref)

    def checkout(self, ref: str) -> None:
        self.git("checkout", ref)

    def current_branch(self) -> str:
        return self.git("branch", "--show-current")

    def rev_parse(self, ref: str) -> str:
        return self.git("rev-parse", ref)

    def merge_base(self, left: str, right: str) -> str:
        return self.git("merge-base", left, right)

    def is_ancestor(self, older: str, newer: str) -> bool:
        result = subprocess.run(
            ["git", "merge-base", "--is-ancestor", older, newer],
            cwd=self.root,
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

    def branch_exists(self, branch: str) -> bool:
        result = subprocess.run(
            ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
            cwd=self.root,
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

    def local_branches(self) -> list[str]:
        output = self.git("for-each-ref", "--format=%(refname:short)", "refs/heads")
        return [line for line in output.splitlines() if line]

    def canonical_repo_key(self) -> str:
        """Stable ``repos.root_path`` value (shared across linked worktrees)."""
        from stackman.lib.git_ops import repo_db_key

        return repo_db_key(self.root)

    def add_worktree(self, path: Path, *, new_branch: str, from_ref: str = "HEAD") -> Path:
        """Create a linked worktree at ``path`` with a new branch based on ``from_ref``."""
        path.parent.mkdir(parents=True, exist_ok=True)
        self._run("worktree", "add", str(path), "-b", new_branch, from_ref)
        return path
