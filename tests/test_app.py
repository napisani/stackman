from __future__ import annotations

import io
from pathlib import Path


def test_stackman_app_boundary_uses_injected_environment(tmp_path: Path) -> None:
    from stackman.app import StackmanApp

    stdout = io.StringIO()
    stderr = io.StringIO()
    stdin = io.StringIO("")

    app = StackmanApp(
        db_path=tmp_path / "stackman.db",
        cwd=tmp_path,
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
    )

    exit_code = app.status()

    assert exit_code == 1
    assert "not a git repository" in stderr.getvalue().lower()
