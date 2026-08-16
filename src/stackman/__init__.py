"""stackman package."""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

from .app import StackmanApp

try:
    __version__ = _pkg_version("stackman")
except PackageNotFoundError:  # running from a source tree without an install
    __version__ = "0.0.0+source"

__all__ = ["StackmanApp", "__version__"]
