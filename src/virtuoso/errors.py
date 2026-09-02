"""Shared public error contract for Virtuoso domain operations."""


class VirtuosoError(RuntimeError):
    """A user-facing Virtuoso operation failed without producing a result."""
