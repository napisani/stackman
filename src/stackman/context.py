from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO


@dataclass(slots=True)
class AppContext:
    db_path: Path
    cwd: Path
    stdin: TextIO
    stdout: TextIO
    stderr: TextIO
    stack_id_factory: Callable[[], str] | None = None
    resolver: str | None = None
