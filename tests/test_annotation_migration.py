from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from virtuoso.workspace import WorkspaceError, WorkspaceService


class CandidateAnnotationMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve() / "learner"
        # Build the actual historical schema, rather than removing a version
        # marker from a current database that already has newer constraints.
        with mock.patch("virtuoso.workspace._CURRENT_MIGRATION_VERSION", 20):
            self.workspace = WorkspaceService.init(self.root)
        with sqlite3.connect(self.workspace.db_path) as db:
            db.execute("PRAGMA foreign_keys = ON")
            for suffix, network, provider, source in (
                ("local", "none", None, None),
                ("remote", "remote", "fixture-provider", "host"),
            ):
                # These are provenance labels only. No credential is resolved,
                # created or used by this migration fixture.
                db.execute(
                    """INSERT INTO module_run_receipts(
                        receipt_id, module_id, module_version, category, kind,
                        manifest_sha256, stdout_sha256, status, error,
                        duration_ms, started_at, completed_at,
                        credential_provider, credential_source
                    ) VALUES (?, 'rule-annotator', '1.0.0', 'annotator',
                        'annotation-answers', ?, ?, 'succeeded', NULL, 12,
                        '2026-10-01T10:00:00+00:00', '2026-10-01T10:00:01+00:00',
                        ?, ?)""",
                    (f"receipt-{suffix}", "a" * 64, "b" * 64, provider, source),
                )
                db.execute(
                    """INSERT INTO annotations(
                        annotation_id, subject_kind, subject_id, subject_hash,
                        question_set_id, question_set_hash, module_id, module_version,
                        module_network, module_receipt_id, answers_json, occurred_at,
                        surface, created_at, credential_provider, credential_source
                    ) VALUES (?, 'item', 'fixture-item', ?, 'item_quality_v1', ?,
                        'rule-annotator', '1.0.0', ?, ?, ?,
                        '2026-10-01T10:00:01+00:00', 'cli',
                        '2026-10-01 10:00:02', ?, ?)""",
                    (
                        f"annotation-{suffix}", "c" * 64, "d" * 64, network,
                        f"receipt-{suffix}",
                        '{ "atomic" : {"primitive":"noul", "value":0.7500, '
                        '"confidence":0.5}, "synthetic_note":"café" }\n',
                        provider, source,
                    ),
                )

    @staticmethod
    def _rows_as_bytes(db: sqlite3.Connection, table: str) -> list[tuple]:
        columns = [row[1] for row in db.execute(f'PRAGMA table_info("{table}")')]
        projection = ", ".join(f'CAST("{column}" AS BLOB)' for column in columns)
        return db.execute(f'SELECT {projection} FROM "{table}" ORDER BY rowid').fetchall()

    @staticmethod
    def _schema(db: sqlite3.Connection) -> list[tuple]:
        return db.execute(
            "SELECT type, name, sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
        ).fetchall()

    def test_v20_rebuild_keeps_annotation_bytes_receipts_guards_and_indexes(self) -> None:
        with sqlite3.connect(self.workspace.db_path) as db:
            self.assertEqual(db.execute("SELECT MAX(version) FROM schema_migrations").fetchone(), (20,))
            annotations_before = self._rows_as_bytes(db, "annotations")
            receipts_before = self._rows_as_bytes(db, "module_run_receipts")
            guards_before = db.execute(
                "SELECT name, type, sql FROM sqlite_master "
                "WHERE tbl_name = 'annotations' AND type IN ('trigger','index') "
                "ORDER BY name"
            ).fetchall()

        migrated = WorkspaceService.open(self.root)
        fresh = WorkspaceService.init(Path(self.tmp.name).resolve() / "fresh")
        with sqlite3.connect(migrated.db_path) as db:
            db.execute("PRAGMA foreign_keys = ON")
            self.assertEqual(db.execute("SELECT MAX(version) FROM schema_migrations").fetchone(), (21,))
            self.assertEqual(self._rows_as_bytes(db, "annotations"), annotations_before)
            self.assertEqual(self._rows_as_bytes(db, "module_run_receipts"), receipts_before)
            self.assertEqual(
                db.execute(
                    "SELECT name, type, sql FROM sqlite_master "
                    "WHERE tbl_name = 'annotations' AND type IN ('trigger','index') "
                    "ORDER BY name"
                ).fetchall(),
                guards_before,
            )
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
            with sqlite3.connect(fresh.db_path) as fresh_db:
                self.assertEqual(self._schema(db), self._schema(fresh_db))
            for statement in (
                "UPDATE annotations SET surface = 'agent'",
                "DELETE FROM annotations",
                "UPDATE module_run_receipts SET duration_ms = 0",
                "DELETE FROM module_run_receipts",
            ):
                with self.subTest(statement=statement):
                    with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                        db.execute(statement)
            # The migrated CHECK admits candidates while retaining its boundary.
            insert = """INSERT INTO annotations(
                annotation_id, subject_kind, subject_id, subject_hash,
                question_set_id, question_set_hash, module_id, module_version,
                module_network, module_receipt_id, answers_json, occurred_at, surface
            ) VALUES (?, ?, 'fixture-candidate', ?, 'intake_triage_v1', ?,
                'rule-annotator', '1.0.0', 'none', 'receipt-local', '{}',
                '2026-10-01T11:00:00+00:00', 'cli')"""
            db.execute(insert, ("candidate-annotation", "candidate", "e" * 64, "f" * 64))
            with self.assertRaisesRegex(sqlite3.IntegrityError, "CHECK constraint"):
                db.execute(insert, ("invalid-annotation", "attempt", "e" * 64, "f" * 64))
        # Opening again validates the resulting table, named indexes and triggers.
        WorkspaceService.open(self.root)

    def test_invalid_v20_index_is_refused_without_changing_evidence(self) -> None:
        with sqlite3.connect(self.workspace.db_path) as db:
            db.execute("DROP INDEX annotations_by_subject")
            db.execute("CREATE INDEX annotations_by_subject ON annotations(subject_id)")
            before = self._rows_as_bytes(db, "annotations")
        database_before = self.workspace.db_path.read_bytes()
        with self.assertRaisesRegex(WorkspaceError, "index annotations_by_subject definition"):
            WorkspaceService.open(self.root)
        self.assertEqual(self.workspace.db_path.read_bytes(), database_before)
        with sqlite3.connect(self.workspace.db_path) as db:
            self.assertEqual(self._rows_as_bytes(db, "annotations"), before)
            self.assertEqual(db.execute("SELECT MAX(version) FROM schema_migrations").fetchone(), (20,))

    def test_failed_v21_validation_rolls_back_the_whole_rebuild(self) -> None:
        with sqlite3.connect(self.workspace.db_path) as db:
            schema_before = self._schema(db)
            annotations_before = self._rows_as_bytes(db, "annotations")
            receipts_before = self._rows_as_bytes(db, "module_run_receipts")
        validate = WorkspaceService._validate_database_schema

        def fail_after_rebuild(db: sqlite3.Connection, *, statements: tuple[str, ...]) -> None:
            validate(db, statements=statements)
            schema = db.execute(
                "SELECT sql FROM sqlite_master WHERE name = 'annotations'"
            ).fetchone()[0]
            if "'candidate'" in schema:
                raise WorkspaceError("synthetic migration validation failure")

        with mock.patch.object(
            WorkspaceService, "_validate_database_schema", side_effect=fail_after_rebuild
        ):
            with self.assertRaisesRegex(WorkspaceError, "synthetic migration validation failure"):
                WorkspaceService.open(self.root)
        with sqlite3.connect(self.workspace.db_path) as db:
            self.assertEqual(self._schema(db), schema_before)
            self.assertEqual(self._rows_as_bytes(db, "annotations"), annotations_before)
            self.assertEqual(self._rows_as_bytes(db, "module_run_receipts"), receipts_before)
            self.assertEqual(db.execute("SELECT MAX(version) FROM schema_migrations").fetchone(), (20,))


if __name__ == "__main__":
    unittest.main()
