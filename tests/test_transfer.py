from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from virtuoso.workspace import WorkspaceError, WorkspaceService


class ProjectTransferEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve() / "learner"
        self.service = WorkspaceService.init(self.root)
        self.item = self.service.add_item(
            item_id="testing-effect",
            title="Explain the testing effect",
            focus="learning-science",
            prompt="Why does retrieval strengthen memory?",
            answer="Retrieval changes memory and improves later access.",
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_record_transfer_preserves_attribution_and_delayed_check(self) -> None:
        occurred_at = datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc)

        event = self.service.record_transfer(
            item_id="testing-effect",
            project_id="virtuoso-cli",
            use_case="Applied the testing effect to the active-recall journey.",
            outcome="successful",
            independence="guided",
            artifact_reference="git:abc123",
            reflection="Needed one design hint; implementation was mine.",
            occurred_at=occurred_at,
        )

        self.assertEqual(event.item_content_hash, self.item.content_hash)
        self.assertEqual(event.project_id, "virtuoso-cli")
        self.assertEqual(event.independence, "guided")
        self.assertEqual(event.outcome, "successful")
        self.assertEqual(event.artifact_reference, "git:abc123")
        self.assertEqual(event.delayed_check_due_at, "2026-08-27T09:00:00+00:00")
        self.assertFalse(event.claims_mastery)
        self.assertEqual(self.service.list_transfer_events(), [event])

    def test_transfer_rejects_invalid_or_empty_evidence(self) -> None:
        valid = {
            "item_id": "testing-effect",
            "project_id": "virtuoso-cli",
            "use_case": "Applied retrieval practice.",
            "outcome": "partial",
            "independence": "independent",
            "artifact_reference": None,
            "reflection": None,
        }
        for field, value, message in (
            ("project_id", "../escape", "project id"),
            ("use_case", "   ", "use case"),
            ("outcome", "mastered", "outcome"),
            ("independence", "fully-autonomous", "independence"),
        ):
            payload = dict(valid)
            payload[field] = value
            with self.subTest(field=field), self.assertRaisesRegex(WorkspaceError, message):
                self.service.record_transfer(**payload)

    def test_transfer_rejects_missing_or_stale_item(self) -> None:
        with self.assertRaisesRegex(WorkspaceError, "no learning item"):
            self.service.record_transfer(
                item_id="missing",
                project_id="virtuoso-cli",
                use_case="Applied retrieval practice.",
                outcome="partial",
                independence="independent",
            )

        self.item.path.write_text(self.item.path.read_text() + "\nChanged.\n", encoding="utf-8")
        with self.assertRaisesRegex(WorkspaceError, "stale"):
            self.service.record_transfer(
                item_id="testing-effect",
                project_id="virtuoso-cli",
                use_case="Applied retrieval practice.",
                outcome="partial",
                independence="independent",
            )

    def test_transfer_events_are_append_only_and_schema_is_validated(self) -> None:
        self.service.record_transfer(
            item_id="testing-effect",
            project_id="virtuoso-cli",
            use_case="Applied retrieval practice.",
            outcome="partial",
            independence="independent",
        )
        with sqlite3.connect(self.service.db_path) as db:
            migration = db.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
            db.execute("DROP TABLE transfer_events")
            db.execute("CREATE TABLE transfer_events(event_id TEXT)")
        self.assertEqual(migration, 16)
        with self.assertRaisesRegex(WorkspaceError, "transfer_events"):
            WorkspaceService.open(self.root)


if __name__ == "__main__":
    unittest.main()
