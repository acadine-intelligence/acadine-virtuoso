"""Synthetic fixtures shared by the server tests (issue #69, docs/22).

Every workspace here is a temporary directory with invented items. Nothing
reads a personal workspace, the active Hermes configuration, or the network
beyond a loopback port the test opens itself.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from virtuoso.workspace import WorkspaceService

RECALL_PROMPT = "Which data structure gives constant-time average lookup by key?"
RECALL_ANSWER = "A hash table keyed by the lookup value."
RECALL_HINT = "Think of buckets chosen by a function of the key."
LEARN_UNIT = "A stack is last in, first out. Push adds to the top; pop removes it."
LEARN_PROMPT = "What order does a stack remove items in?"
LEARN_ANSWER = "Last in, first out."

# The fixed clock starts well before the real date, so a test that moves it
# forward by weeks still writes no pending attempt that the database sees as
# created in the future (migration 20 compares created_at with its own clock).
START = datetime(2025, 10, 1, 9, 0, tzinfo=timezone.utc)


def server_extra_available() -> bool:
    return all(
        importlib.util.find_spec(name) is not None
        for name in ("mcp", "uvicorn", "starlette")
    )


def require_server_extra(test_case: type) -> type:
    """Skip transport tests without the extra, unless CI demands them."""
    if server_extra_available():
        return test_case
    if os.environ.get("VIRTUOSO_REQUIRE_SERVER_TESTS") == "1":
        raise RuntimeError(
            "VIRTUOSO_REQUIRE_SERVER_TESTS=1 but the server extra is not installed"
        )
    return unittest.skip("the [server] extra is not installed")(test_case)


class FixedClock:
    """A server clock the test moves by hand."""

    def __init__(self, start: datetime = START) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now = self.now + timedelta(**delta)


def make_workspace(parent: Path, name: str = "learner") -> WorkspaceService:
    workspace = WorkspaceService.init(parent / name)
    workspace.add_item(
        item_id="hash-lookup",
        title="Hash lookup",
        focus="data-structures",
        prompt=RECALL_PROMPT,
        answer=RECALL_ANSWER,
        hint=RECALL_HINT,
    )
    workspace.add_item(
        item_id="stack-order",
        title="Stack order",
        focus="data-structures",
        prompt=LEARN_PROMPT,
        answer=LEARN_ANSWER,
        entry_mode="learn-first",
        learning_unit=LEARN_UNIT,
    )
    return workspace


def database_digest(db_path: Path) -> str:
    """A digest of every row in the workspace database."""
    with sqlite3.connect(db_path) as db:
        dump = "\n".join(db.iterdump())
    return hashlib.sha256(dump.encode("utf-8")).hexdigest()


def rows(db_path: Path, query: str, parameters: tuple = ()) -> list[dict]:
    with sqlite3.connect(db_path) as db:
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute(query, parameters).fetchall()]


class TemporaryWorkspaceCase(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.parent = Path(temporary.name).resolve()
        self.workspace = make_workspace(self.parent)
        self.clock = FixedClock()
