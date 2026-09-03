"""Acadine Virtuoso domain package."""

from importlib.metadata import PackageNotFoundError, version

from .errors import VirtuosoError

try:
    __version__ = version("acadine-virtuoso")
except PackageNotFoundError:
    __version__ = "0+unknown"

__all__ = ["VirtuosoError", "__version__"]
