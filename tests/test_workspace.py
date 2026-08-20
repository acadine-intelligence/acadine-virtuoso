from __future__ import annotations

import json
import os
import sqlite3
import stat
import tempfile
import time
import unittest
from pathlib import Path

from virtuoso.workspace import WorkspaceError, WorkspaceService


class WorkspaceServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve() / "learner"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_init_creates_owned_markdown_and_sqlite_boundaries(self) -> None:
        summary = WorkspaceService.init(self.root)

        self.assertEqual(summary.root, self.root.resolve())
        self.assertTrue((self.root / "items").is_dir())
        self.assertTrue((self.root / ".virtuoso" / "state.sqlite3").is_file())
        config = json.loads((self.root / "virtuoso.json").read_text())
        self.assertEqual(config["schema"], "virtuoso/workspace@0.1")
        self.assertEqual(config["mode"], "simple")
        self.assertEqual(config["scheduler"]["algorithm"], "fsrs")

        with sqlite3.connect(self.root / ".virtuoso" / "state.sqlite3") as db:
            migration = db.execute(
                "SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1"
            ).fetchone()
            self.assertEqual(migration, (4,))

    def test_init_refuses_to_overwrite_existing_workspace(self) -> None:
        WorkspaceService.init(self.root)
        with self.assertRaisesRegex(WorkspaceError, "already exists"):
            WorkspaceService.init(self.root)

    def test_init_rejects_symlinked_workspace_root(self) -> None:
        target = Path(self.tmp.name).resolve() / "target"
        target.mkdir()
        self.root.symlink_to(target, target_is_directory=True)

        with self.assertRaisesRegex(WorkspaceError, "root must not be a symlink"):
            WorkspaceService.init(self.root)
        self.assertEqual(list(target.iterdir()), [])

    def test_init_and_open_reject_workspace_with_symlinked_ancestor(self) -> None:
        real_parent = Path(self.tmp.name).resolve() / "real-parent"
        real_parent.mkdir()
        alias = Path(self.tmp.name).resolve() / "alias"
        alias.symlink_to(real_parent, target_is_directory=True)

        with self.assertRaisesRegex(WorkspaceError, "symlink ancestor"):
            WorkspaceService.init(alias / "new-workspace")
        self.assertFalse((real_parent / "new-workspace").exists())

        WorkspaceService.init(real_parent / "existing-workspace")
        with self.assertRaisesRegex(WorkspaceError, "symlink ancestor"):
            WorkspaceService.open(alias / "existing-workspace")

    def test_init_and_add_create_private_owned_artifacts(self) -> None:
        previous_umask = os.umask(0)
        try:
            service = WorkspaceService.init(self.root)
            item = service.add_item(
                item_id="private-item",
                title="Private",
                focus="security",
                prompt="What is private?",
                answer="The learner workspace.",
            )
        finally:
            os.umask(previous_umask)

        expected_modes = {
            service.root: 0o700,
            service.items_dir: 0o700,
            service.state_dir: 0o700,
            service.config_path: 0o600,
            service.db_path: 0o600,
            item.path: 0o600,
        }
        for path, expected in expected_modes.items():
            with self.subTest(path=path):
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), expected)

    def test_add_item_rejects_dangling_symlink_without_external_write_or_row(self) -> None:
        service = WorkspaceService.init(self.root)
        outside = Path(self.tmp.name).resolve() / "outside" / "created.md"
        outside.parent.mkdir()
        item_path = service.items_dir / "escape.md"
        item_path.symlink_to(outside)

        with self.assertRaisesRegex(WorkspaceError, "item path must not be a symlink"):
            service.add_item(
                item_id="escape",
                title="Escape",
                focus="security",
                prompt="Where must this remain?",
                answer="Inside the workspace.",
            )

        self.assertFalse(outside.exists())
        with sqlite3.connect(service.db_path) as db:
            self.assertIsNone(
                db.execute("SELECT item_id FROM items WHERE item_id = 'escape'").fetchone()
            )

    def test_doctor_does_not_follow_later_created_item_symlink(self) -> None:
        service = WorkspaceService.init(self.root)
        item = service.add_item(
            item_id="linked-item",
            title="Linked",
            focus="security",
            prompt="Must doctor follow this link?",
            answer="No.",
        )
        original = item.path.read_bytes()
        outside = Path(self.tmp.name).resolve() / "outside.md"
        outside.write_bytes(original)
        item.path.unlink()
        item.path.symlink_to(outside)

        with self.assertRaisesRegex(WorkspaceError, "item path must not be a symlink"):
            service.load_item("linked-item")
        health = service.doctor()
        self.assertEqual(health["status"], "needs-attention")
        self.assertEqual(health["stale_items"], ["linked-item"])

    def test_add_item_writes_human_owned_markdown_and_index(self) -> None:
        service = WorkspaceService.init(self.root)
        item = service.add_item(
            item_id="testing-effect",
            title="Explain the testing effect",
            focus="learning-science",
            prompt="Why does retrieval strengthen later recall?",
            answer="Retrieval itself changes memory and improves later access.",
            hint="Think about effortful retrieval rather than rereading.",
            follow_up="Name one way to use it in a coding project.",
        )

        text = item.path.read_text()
        self.assertIn("schema: virtuoso/item@0.1", text)
        self.assertIn("# Prompt\n\nWhy does retrieval strengthen later recall?", text)
        self.assertIn("# Answer\n\nRetrieval itself changes memory", text)
        self.assertIn("# Hint", text)
        self.assertIn("# Follow-up challenge", text)

        with sqlite3.connect(self.root / ".virtuoso" / "state.sqlite3") as db:
            row = db.execute(
                "SELECT item_id, focus, content_hash FROM items WHERE item_id = ?",
                ("testing-effect",),
            ).fetchone()
        self.assertEqual(row[0:2], ("testing-effect", "learning-science"))
        self.assertEqual(len(row[2]), 64)

    def test_add_item_rejects_unsafe_or_duplicate_ids(self) -> None:
        service = WorkspaceService.init(self.root)
        with self.assertRaisesRegex(WorkspaceError, "lowercase"):
            service.add_item(
                item_id="../escape",
                title="No",
                focus="test",
                prompt="No",
                answer="No",
            )
        service.add_item(
            item_id="safe-id",
            title="Safe",
            focus="test",
            prompt="Prompt",
            answer="Answer",
        )
        with self.assertRaisesRegex(WorkspaceError, "already exists"):
            service.add_item(
                item_id="safe-id",
                title="Duplicate",
                focus="test",
                prompt="Prompt",
                answer="Answer",
            )

    def test_add_item_rejects_symlinked_items_directory(self) -> None:
        service = WorkspaceService.init(self.root)
        outside = Path(self.tmp.name).resolve() / "outside"
        outside.mkdir()
        service.items_dir.rmdir()
        service.items_dir.symlink_to(outside, target_is_directory=True)

        with self.assertRaisesRegex(WorkspaceError, "symlink"):
            service.add_item(
                item_id="escape",
                title="Escape",
                focus="security",
                prompt="What must remain inside the workspace?",
                answer="Virtuoso-owned item files.",
            )
        self.assertFalse((outside / "escape.md").exists())

    def test_item_sections_reject_top_level_heading_injection(self) -> None:
        service = WorkspaceService.init(self.root)
        with self.assertRaisesRegex(WorkspaceError, "top-level Markdown headings"):
            service.add_item(
                item_id="injected",
                title="Injected",
                focus="security",
                prompt="Safe prompt",
                answer="Legitimate answer\n# Answer\nInjected answer",
            )

    def test_open_wraps_corrupt_database_as_workspace_error(self) -> None:
        service = WorkspaceService.init(self.root)
        service.db_path.write_bytes(b"not a sqlite database")
        with self.assertRaisesRegex(WorkspaceError, "database"):
            WorkspaceService.open(self.root)

    def test_configuration_rejects_invalid_utf8_unknown_fields_and_invalid_types(self) -> None:
        service = WorkspaceService.init(self.root)
        service.config_path.write_bytes(b"\xff")
        with self.assertRaisesRegex(WorkspaceError, "invalid workspace configuration"):
            service.configuration()

        valid = {
            "schema": "virtuoso/workspace@0.1",
            "mode": "simple",
            "scheduler": {
                "algorithm": "fsrs",
                "context": "atomic-recall",
                "desired_retention": 0.9,
                "enable_fuzzing": False,
            },
        }
        invalid_values = (
            ({**valid, "unknown": True}, "unknown workspace configuration fields"),
            ({key: value for key, value in valid.items() if key != "mode"}, "missing workspace configuration fields"),
            ({**valid, "mode": "advanced"}, "mode"),
            (
                {
                    **valid,
                    "scheduler": {**valid["scheduler"], "desired_retention": float("nan")},
                },
                "finite JSON",
            ),
        )
        for value, message in invalid_values:
            with self.subTest(message=message):
                service.config_path.write_text(json.dumps(value), encoding="utf-8")
                with self.assertRaisesRegex(WorkspaceError, message):
                    service.configuration()

    def test_open_rejects_future_or_discontinuous_migration_versions(self) -> None:
        service = WorkspaceService.init(self.root)
        with sqlite3.connect(service.db_path) as db:
            db.execute("INSERT INTO schema_migrations(version) VALUES (99)")
        with self.assertRaisesRegex(WorkspaceError, "future migration version"):
            WorkspaceService.open(self.root)

        with sqlite3.connect(service.db_path) as db:
            db.execute("DELETE FROM schema_migrations WHERE version = 99")
            db.execute("DELETE FROM schema_migrations WHERE version = 2")
        with self.assertRaisesRegex(WorkspaceError, "migration history is not contiguous"):
            WorkspaceService.open(self.root)

    def test_open_rejects_constraint_free_schema_with_expected_column_names(self) -> None:
        service = WorkspaceService.init(self.root)
        with sqlite3.connect(service.db_path) as db:
            db.execute("DROP TABLE module_receipts")
            db.execute(
                """CREATE TABLE module_receipts (
                    receipt_id TEXT,
                    module_id TEXT,
                    module_version TEXT,
                    category TEXT,
                    kind TEXT,
                    manifest_sha256 TEXT,
                    stdout_sha256 TEXT,
                    duration_ms INTEGER,
                    occurred_at TEXT
                )"""
            )

        with self.assertRaisesRegex(WorkspaceError, "incompatible database schema"):
            WorkspaceService.open(self.root)

    def test_open_rejects_foreign_key_violations(self) -> None:
        service = WorkspaceService.init(self.root)
        with sqlite3.connect(service.db_path) as db:
            db.execute("PRAGMA foreign_keys = OFF")
            db.execute(
                """INSERT INTO attempts(
                    event_id, item_id, item_content_hash, occurred_at,
                    initial_response, initial_latency_ms, result, confidence,
                    open_notes, agent_help, support_json
                ) VALUES ('orphan', 'missing', 'hash', '2026-08-20T00:00:00+00:00',
                          'answer', 1, 'partial', 3, 0, 'none', '[]')"""
            )

        with self.assertRaisesRegex(WorkspaceError, "foreign key integrity"):
            WorkspaceService.open(self.root)

    def test_malformed_database_json_is_wrapped_as_workspace_error(self) -> None:
        service = WorkspaceService.init(self.root)
        item = service.add_item(
            item_id="json-item",
            title="JSON",
            focus="integrity",
            prompt="What should fail closed?",
            answer="Malformed database JSON.",
        )
        with sqlite3.connect(service.db_path) as db:
            db.execute(
                """INSERT INTO attempts(
                    event_id, item_id, item_content_hash, occurred_at,
                    initial_response, initial_latency_ms, result, confidence,
                    open_notes, agent_help, support_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    "attempt-malformed-json",
                    item.item_id,
                    item.content_hash,
                    "2026-08-20T00:00:00+00:00",
                    "answer",
                    1,
                    "partial",
                    3,
                    0,
                    "none",
                    "{",
                ),
            )

        with self.assertRaisesRegex(WorkspaceError, "attempt support JSON"):
            service.list_attempts()

    def test_add_item_acquires_database_lock_before_creating_markdown(self) -> None:
        service = WorkspaceService.init(self.root)
        holder = sqlite3.connect(service.db_path, timeout=0)
        holder.execute("BEGIN IMMEDIATE")
        started = time.monotonic()
        try:
            with self.assertRaisesRegex(WorkspaceError, "database is locked"):
                service.add_item(
                    item_id="locked-item",
                    title="Locked",
                    focus="integrity",
                    prompt="Should a file be left behind?",
                    answer="No.",
                )
        finally:
            holder.rollback()
            holder.close()

        self.assertLess(time.monotonic() - started, 1.5)
        self.assertFalse((service.items_dir / "locked-item.md").exists())
        with sqlite3.connect(service.db_path) as db:
            self.assertIsNone(
                db.execute("SELECT item_id FROM items WHERE item_id = 'locked-item'").fetchone()
            )

    def test_open_rejects_incompatible_existing_schema(self) -> None:
        service = WorkspaceService.init(self.root)
        with sqlite3.connect(service.db_path) as db:
            db.execute("DROP TABLE attempts")
            db.execute("CREATE VIEW attempts AS SELECT 1 AS wrong_column")
        with self.assertRaisesRegex(WorkspaceError, "incompatible database schema"):
            WorkspaceService.open(self.root)

    def test_failed_migration_rolls_back_new_schema_objects(self) -> None:
        (self.root / "items").mkdir(parents=True)
        state_dir = self.root / ".virtuoso"
        state_dir.mkdir()
        (self.root / "virtuoso.json").write_text(
            json.dumps(
                {
                    "schema": "virtuoso/workspace@0.1",
                    "mode": "simple",
                    "scheduler": {
                        "algorithm": "fsrs",
                        "context": "atomic-recall",
                        "desired_retention": 0.9,
                        "enable_fuzzing": False,
                    },
                }
            ),
            encoding="utf-8",
        )
        db_path = state_dir / "state.sqlite3"
        with sqlite3.connect(db_path) as db:
            db.execute("CREATE VIEW attempts AS SELECT 1 AS wrong_column")

        with self.assertRaisesRegex(WorkspaceError, "migration failed|incompatible"):
            WorkspaceService.open(self.root)

        with sqlite3.connect(db_path) as db:
            objects = {
                row[0]
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
                )
            }
        self.assertEqual(objects, {"attempts"})


if __name__ == "__main__":
    unittest.main()
