from __future__ import annotations

import sys
from pathlib import Path

import pytest

TEST_DIR = Path(__file__).resolve().parent
SRC_DIR = TEST_DIR.parent / "src"
for path in (SRC_DIR, TEST_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from git_repo_fixture import GitRepoFixture


@pytest.fixture()
def git_repo(tmp_path: Path) -> GitRepoFixture:
    return GitRepoFixture.create(tmp_path / "repo")


@pytest.fixture()
def stackman_db_path(tmp_path: Path) -> Path:
    return tmp_path / "stackman.db"
