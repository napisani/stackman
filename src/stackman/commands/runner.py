from __future__ import annotations

import subprocess
from collections.abc import Callable

from ..context import AppContext


def run_safely(ctx: AppContext, fn: Callable[[AppContext], int]) -> int:
    try:
        return fn(ctx)
    except SystemExit as exc:
        message = exc.code if isinstance(exc.code, str) else ""
        if message:
            ctx.stderr.write(f"{message}\n")
        return exc.code if isinstance(exc.code, int) else 1
    except subprocess.CalledProcessError as exc:
        error_output = exc.stderr.strip() if exc.stderr else str(exc)
        ctx.stderr.write(f"{error_output}\n")
        return 1
    except Exception as exc:  # noqa: BLE001
        # Final safety net: internal errors (bad `gh` JSON, an inconsistent stored
        # graph, an unsafe ref name) must surface as a clean message + exit 1, not a
        # raw traceback past the injected stderr.
        ctx.stderr.write(f"stackman: unexpected error: {exc}\n")
        return 1
