"""Tests for virtuoso.queries: read-only analytics over a live workspace."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from virtuoso.practice import PracticeService
from virtuoso.queries import (
    QueryError,
    _connect_read_only,
    focus_performance,
    item_history,
    stale_links,
    workload_by_focus,
)
from virtuoso.workspace import WorkspaceService


class _ScriptedIO:
    def __init__(self, answers: list[str]) -> None:
        self.answers = iter(answers)

    def write(self, text: str) -> None: ...

    def ask(self, prompt: str) -> str:
        return next(self.answers)


class _ZeroClock:
    def monotonic(self) -> float:
        return 0.0


class QueryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve() / "learner"
        self.workspace = WorkspaceService.init(self.root)
        self.workspace.add_item(
            item_id="item-a",
            title="Item A",
            focus="focus-one",
            prompt="PA?",
            answer="AA.",
        )
        self.workspace.add_item(
            item_id="item-b",
            title="Item B",
            focus="focus-two",
            prompt="PB?",
            answer="AB.",
        )
        self.now = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _practice(self, item_id: str, result: str, confidence: int) -> None:
        PracticeService(self.workspace, clock=_ZeroClock()).run(
            item_id=item_id,
            io=_ScriptedIO(["n", "a real recalled answer", "reveal", result, str(confidence)]),
            now=self.now,
        )

    def test_focus_performance_reports_counts(self) -> None:
        self._practice("item-a", "demonstrated", 4)
        self._practice("item-a", "partial", 3)
        self._practice("item-b", "demonstrated", 5)

        summaries = focus_performance(self.workspace.db_path)
        by_focus = {s.focus: s for s in summaries}
        self.assertEqual(by_focus["focus-one"].attempts, 2)
        self.assertEqual(by_focus["focus-one"].demonstrated, 1)
        self.assertEqual(by_focus["focus-one"].partial, 1)
        self.assertEqual(by_focus["focus-two"].attempts, 1)

    def test_focus_performance_includes_focuses_with_no_attempts(self) -> None:
        self._practice("item-a", "demonstrated", 4)
        summaries = focus_performance(self.workspace.db_path)
        by_focus = {s.focus: s for s in summaries}
        self.assertEqual(by_focus["focus-two"].attempts, 0)
        self.assertIsNone(by_focus["focus-two"].mean_confidence)

    def test_item_history_newest_first(self) -> None:
        self._practice("item-a", "demonstrated", 4)
        self.now = datetime(2026, 9, 2, 13, 0, tzinfo=timezone.utc)
        self._practice("item-a", "partial", 2)
        history = item_history(self.workspace.db_path, "item-a")
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0].result, "partial")  # newest first
        self.assertTrue(all(entry.latency_ms is not None for entry in history))

    def test_item_history_unknown_item_is_empty_not_error(self) -> None:
        self.assertEqual(item_history(self.workspace.db_path, "nope"), [])

    def test_workload_by_focus_counts_items_and_scheduled(self) -> None:
        self._practice("item-a", "demonstrated", 4)
        rows = workload_by_focus(self.workspace.db_path)
        by_focus = {row["focus"]: row for row in rows}
        self.assertEqual(by_focus["focus-one"]["items"], 1)
        self.assertEqual(by_focus["focus-one"]["scheduled"], 1)
        self.assertEqual(by_focus["focus-two"]["items"], 1)
        self.assertEqual(by_focus["focus-two"]["scheduled"], 0)

    def test_stale_links_empty_on_healthy_workspace(self) -> None:
        self.assertEqual(stale_links(self.workspace.db_path), [])

    def test_queries_support_reserved_characters_in_workspace_path(self) -> None:
        temp_root = Path(self.tmp.name).resolve()
        misparsed_target = temp_root / "spaces "
        special = WorkspaceService.init(
            temp_root / "spaces #hash?question%percent ünicode" / "learner"
        )
        special.add_item(
            item_id="encoded-path",
            title="Encoded path",
            focus="path-contract",
            prompt="Can every valid local path be queried?",
            answer="Yes, when the SQLite URI encodes reserved characters.",
        )

        summaries = focus_performance(special.db_path)

        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0].focus, "path-contract")
        self.assertEqual(summaries[0].items, 1)
        self.assertEqual(summaries[0].attempts, 0)
        self.assertEqual(item_history(special.db_path, "encoded-path"), [])
        self.assertEqual(
            workload_by_focus(special.db_path),
            [
                {
                    "focus": "path-contract",
                    "items": 1,
                    "due_now": 0,
                    "scheduled": 0,
                }
            ],
        )
        self.assertEqual(stale_links(special.db_path), [])
        self.assertFalse(misparsed_target.exists())

    def test_missing_database_fails_closed(self) -> None:
        missing = Path(self.tmp.name).resolve() / "absent.sqlite3"
        with self.assertRaisesRegex(QueryError, "not found"):
            focus_performance(missing)
        self.assertFalse(missing.exists())

    def test_read_only_connection_enables_query_only(self) -> None:
        db = _connect_read_only(self.workspace.db_path)
        try:
            self.assertEqual(db.execute("PRAGMA query_only").fetchone()[0], 1)
            with self.assertRaises(sqlite3.OperationalError):
                db.execute("CREATE TABLE forbidden(value TEXT)")
        finally:
            db.close()

    def test_queries_never_write(self) -> None:
        self._practice("item-a", "demonstrated", 4)
        before = self.workspace.db_path.read_bytes()
        focus_performance(self.workspace.db_path)
        item_history(self.workspace.db_path, "item-a")
        workload_by_focus(self.workspace.db_path)
        stale_links(self.workspace.db_path)
        self.assertEqual(self.workspace.db_path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
