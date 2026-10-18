"""Optional Virtuoso server: one operation registry over MCP and HTTP (docs/22).

This package imports only the standard library and the core, so the base
install can load it. The transports in ``virtuoso.server.transports`` need
the ``[server]`` extra and are imported only by ``virtuoso serve``.
"""

from __future__ import annotations

import importlib

SERVER_EXTRA_INSTALL = 'pip install "acadine-virtuoso[server]"'
_SERVER_MODULES = ("mcp", "uvicorn", "starlette")


def missing_server_dependencies() -> list[str]:
    """Names of the server extra's modules that cannot be imported."""
    missing: list[str] = []
    for name in _SERVER_MODULES:
        try:
            importlib.import_module(name)
        except ImportError:
            missing.append(name)
    return missing
