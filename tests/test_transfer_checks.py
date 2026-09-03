from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast

from conftest import downgrade_attempt_chain_to_v9
from virtuoso.workspace import TransferEvidence, WorkspaceError, WorkspaceService


class DelayedTransferCheckMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve() / "learner"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _workspace_with_transfer(
        self,
    ) -> tuple[WorkspaceService, TransferEvidence]:
        service = WorkspaceService.init(self.root)
        service.add_item(
            item_id="testing-effect",
            title="Explain the testing effect",
            focus="learning-science",
            prompt="Why does retrieval strengthen memory?",
            answer="Retrieval changes memory and improves later access.",
        )
        event = service.record_transfer(
            item_id="testing-effect",
            project_id="virtuoso-cli",
            use_case="Applied the testing effect to a real CLI workflow.",
            outcome="partial",
            independence="guided",
            artifact_reference="git:source123",
            reflection="The source transfer remains historical evidence.",
            occurred_at=datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc),
        )
        return service, event

    @staticmethod
    def _versions(service: WorkspaceService) -> list[int]:
        with sqlite3.connect(service.db_path) as db:
            return [
                row[0]
                for row in db.execute(
                    "SELECT version FROM schema_migrations ORDER BY version"
                ).fetchall()
            ]

    @staticmethod
    def _downgrade_fixture_to_v5(service: WorkspaceService) -> None:
        with sqlite3.connect(service.db_path) as db:
            trigger_names = [
                row[0]
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'trigger' ORDER BY name"
                ).fetchall()
            ]
            for trigger_name in trigger_names:
                db.execute(f'DROP TRIGGER "{trigger_name}"')
            for table in (
                "candidate_source_refs",
                "review_candidates",
                "candidate_runs",
                "candidate_decisions",
            ):
                db.execute(f'DROP TABLE "{table}"')
            # v5 predates migration 10's attempt-chain rebuild.
            downgrade_attempt_chain_to_v9(db)
            db.execute("PRAGMA legacy_alter_table = ON")
            db.execute("ALTER TABLE items RENAME TO items_with_retired")
            db.execute(
                """CREATE TABLE items (
                    item_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    focus TEXT NOT NULL,
                    relative_path TEXT NOT NULL UNIQUE,
                    content_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )"""
            )
            db.execute(
                "INSERT INTO items(item_id, title, focus, relative_path, "
                "content_hash, created_at) "
                "SELECT item_id, title, focus, relative_path, content_hash, "
                "created_at FROM items_with_retired"
            )
            db.execute("DROP TABLE items_with_retired")
            db.execute("PRAGMA legacy_alter_table = OFF")
            db.execute("DELETE FROM schema_migrations WHERE version > 5")

    @staticmethod
    def _downgrade_fixture_to_v4(service: WorkspaceService) -> None:
        DelayedTransferCheckMigrationTests._downgrade_fixture_to_v5(service)
        with sqlite3.connect(service.db_path) as db:
            for table in (
                "candidate_source_refs",
                "review_candidates",
                "candidate_runs",
                "candidate_decisions",
                "transfer_check_completions",
                "transfer_check_predictions",
                "transfer_checks",
            ):
                db.execute(f'DROP TABLE IF EXISTS "{table}"')
            db.execute("DELETE FROM schema_migrations WHERE version > 4")

    def test_v4_workspace_migrates_to_v6_without_changing_existing_transfer_evidence(
        self,
    ) -> None:
        service, event = self._workspace_with_transfer()
        self._downgrade_fixture_to_v4(service)
        with sqlite3.connect(service.db_path) as db:
            before = db.execute(
                "SELECT * FROM transfer_events WHERE event_id = ?", (event.event_id,)
            ).fetchone()

        reopened = WorkspaceService.open(self.root)

        self.assertEqual(self._versions(reopened), [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13])
        with sqlite3.connect(reopened.db_path) as db:
            tables = {
                row[0]
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            after = db.execute(
                "SELECT * FROM transfer_events WHERE event_id = ?", (event.event_id,)
            ).fetchone()
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM transfer_checks").fetchone()[0], 0
            )
            check_sql = db.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'transfer_checks'"
            ).fetchone()[0]
            completion_sql = db.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'transfer_check_completions'"
            ).fetchone()[0]
            unique_columns = {
                tuple(
                    column[2]
                    for column in db.execute(
                        f'PRAGMA index_info("{index[1]}")'
                    ).fetchall()
                )
                for index in db.execute('PRAGMA index_list("transfer_checks")').fetchall()
                if index[2]
            }
        self.assertEqual(before, after)
        self.assertTrue(
            {
                "transfer_checks",
                "transfer_check_predictions",
                "transfer_check_completions",
            }.issubset(tables)
        )
        self.assertIn(("transfer_event_id",), unique_columns)
        normalized_check_sql = "".join(check_sql.lower().split())
        normalized_completion_sql = "".join(completion_sql.lower().split())
        self.assertIn("check(claims_mastery=0)", normalized_check_sql)
        self.assertIn("check(context_kindin('changed','novel'))", normalized_check_sql)
        self.assertIn(
            "check(assistance_levelin('none','light','substantial','unknown'))",
            normalized_completion_sql,
        )
        self.assertIn("check(claims_mastery=0)", normalized_completion_sql)

    def test_v4_open_rejects_missing_transfer_events_before_v5_creation(
        self,
    ) -> None:
        service, _event = self._workspace_with_transfer()
        self._downgrade_fixture_to_v4(service)
        with sqlite3.connect(service.db_path) as db:
            db.execute("DROP TABLE transfer_events")

        with self.assertRaisesRegex(
            WorkspaceError, "missing objects.*transfer_events|transfer_events.*missing"
        ):
            WorkspaceService.open(self.root)

        with sqlite3.connect(service.db_path) as db:
            object_names = {
                row[0]
                for row in db.execute(
                    """SELECT name FROM sqlite_master
                       WHERE name NOT LIKE 'sqlite_%'
                         AND type IN ('table', 'trigger')"""
                ).fetchall()
            }
        self.assertNotIn("transfer_events", object_names)
        self.assertNotIn("transfer_checks", object_names)
        self.assertNotIn("transfer_check_predictions", object_names)
        self.assertNotIn("transfer_check_completions", object_names)
        self.assertEqual(self._versions(service), [1, 2, 3, 4])

    def test_v5_workspace_migrates_to_v6_without_changing_delayed_evidence(
        self,
    ) -> None:
        service, event = self._workspace_with_transfer()
        check = service.create_transfer_check(
            transfer_event_id=event.event_id,
            context_kind="changed",
            context_description="A preserved v5 changed context.",
            challenge_prompt="Complete the preserved v5 challenge.",
            acceptance_criteria="Meet the preserved v5 criterion.",
            scorer_kind="human",
            scorer_reference="v5-preservation-reviewer",
            now=datetime(2026, 8, 21, 9, 0, tzinfo=timezone.utc),
        )
        due_at = datetime.fromisoformat(event.delayed_check_due_at)
        service.begin_transfer_check(
            check_id=check.check_id,
            pre_attempt_prediction="A preserved v5 prediction.",
            now=due_at,
        )
        service.complete_transfer_check(
            check_id=check.check_id,
            independent_attempt="A preserved v5 independent attempt.",
            assistance_level="none",
            assistance_detail=None,
            acceptance_evidence="The preserved v5 criterion was met.",
            teach_back="Preserved evidence remains append-only.",
            outcome="successful",
            now=due_at + timedelta(minutes=1),
        )
        evidence_tables = (
            "transfer_events",
            "transfer_checks",
            "transfer_check_predictions",
            "transfer_check_completions",
        )
        with sqlite3.connect(service.db_path) as db:
            before = {
                table: db.execute(f'SELECT * FROM "{table}" ORDER BY 1').fetchall()
                for table in evidence_tables
            }
        self._downgrade_fixture_to_v5(service)

        reopened = WorkspaceService.open(self.root)

        with sqlite3.connect(reopened.db_path) as db:
            after = {
                table: db.execute(f'SELECT * FROM "{table}" ORDER BY 1').fetchall()
                for table in evidence_tables
            }
            trigger_count = db.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type = 'trigger'"
            ).fetchone()[0]
        self.assertEqual(after, before)
        self.assertEqual(trigger_count, 16)
        self.assertEqual(
            self._versions(reopened), [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13]
        )

    def test_v5_open_rejects_missing_historical_table_before_v6_creation(
        self,
    ) -> None:
        service, event = self._workspace_with_transfer()
        self._downgrade_fixture_to_v5(service)
        with sqlite3.connect(service.db_path) as db:
            transfer_before = db.execute(
                "SELECT * FROM transfer_events WHERE event_id = ?", (event.event_id,)
            ).fetchone()
            db.execute("DROP TABLE attempts")

        with self.assertRaisesRegex(
            WorkspaceError, "missing objects.*attempts|attempts.*missing"
        ):
            WorkspaceService.open(self.root)

        with sqlite3.connect(service.db_path) as db:
            attempts = db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'attempts'"
            ).fetchone()
            transfer_after = db.execute(
                "SELECT * FROM transfer_events WHERE event_id = ?", (event.event_id,)
            ).fetchone()
            triggers = db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            ).fetchall()
        self.assertIsNone(attempts)
        self.assertEqual(transfer_after, transfer_before)
        self.assertEqual(triggers, [])
        self.assertEqual(self._versions(service), [1, 2, 3, 4, 5])

    def test_failed_v5_migration_rolls_back_all_transfer_check_tables_and_version(
        self,
    ) -> None:
        service, _event = self._workspace_with_transfer()
        self.assertEqual(self._versions(service), [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13])
        self._downgrade_fixture_to_v4(service)
        with sqlite3.connect(service.db_path) as db:
            db.execute(
                "CREATE TABLE transfer_check_predictions(check_id TEXT PRIMARY KEY)"
            )

        with self.assertRaisesRegex(
            WorkspaceError, "transfer_check_predictions|incompatible database schema"
        ):
            WorkspaceService.open(self.root)

        with sqlite3.connect(service.db_path) as db:
            tables = {
                row[0]
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            versions = [
                row[0]
                for row in db.execute(
                    "SELECT version FROM schema_migrations ORDER BY version"
                ).fetchall()
            ]
        self.assertIn("transfer_check_predictions", tables)
        self.assertNotIn("transfer_checks", tables)
        self.assertNotIn("transfer_check_completions", tables)
        self.assertEqual(versions, [1, 2, 3, 4])

    def test_open_rejects_constraint_free_or_wrong_fk_transfer_check_schema(
        self,
    ) -> None:
        service, _event = self._workspace_with_transfer()
        self.assertEqual(self._versions(service), [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13])
        with sqlite3.connect(service.db_path) as db:
            db.execute("PRAGMA foreign_keys = OFF")
            db.execute("DROP TABLE transfer_check_completions")
            db.execute("DROP TABLE transfer_check_predictions")
            db.execute("DROP TABLE transfer_checks")
            db.execute(
                """CREATE TABLE transfer_checks (
                    check_id TEXT PRIMARY KEY,
                    transfer_event_id TEXT NOT NULL,
                    context_kind TEXT NOT NULL,
                    context_description TEXT NOT NULL,
                    challenge_prompt TEXT NOT NULL,
                    acceptance_criteria TEXT NOT NULL,
                    scorer_kind TEXT NOT NULL,
                    scorer_reference TEXT NOT NULL,
                    due_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    claims_mastery INTEGER NOT NULL DEFAULT 0
                )"""
            )
            db.execute(
                """CREATE TABLE transfer_check_predictions (
                    check_id TEXT PRIMARY KEY REFERENCES transfer_events(event_id),
                    pre_attempt_prediction TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    claims_mastery INTEGER NOT NULL DEFAULT 0
                )"""
            )
            db.execute(
                """CREATE TABLE transfer_check_completions (
                    check_id TEXT PRIMARY KEY REFERENCES transfer_checks(check_id),
                    independent_attempt TEXT NOT NULL,
                    assistance_level TEXT NOT NULL,
                    assistance_detail TEXT,
                    acceptance_evidence TEXT NOT NULL,
                    teach_back TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    artifact_reference TEXT,
                    completed_at TEXT NOT NULL,
                    claims_mastery INTEGER NOT NULL DEFAULT 0
                )"""
            )

        with self.assertRaisesRegex(WorkspaceError, "transfer_checks|transfer_check"):
            WorkspaceService.open(self.root)

    def test_v4_migration_rejects_well_formed_transfer_check_name_collision(
        self,
    ) -> None:
        service, _event = self._workspace_with_transfer()
        self._downgrade_fixture_to_v5(service)
        with sqlite3.connect(service.db_path) as db:
            for table in (
                "candidate_source_refs",
                "review_candidates",
                "candidate_runs",
                "candidate_decisions",
                "transfer_check_completions",
                "transfer_check_predictions",
            ):
                db.execute(f'DROP TABLE IF EXISTS "{table}"')
            db.execute("DELETE FROM schema_migrations WHERE version >= 5")

        with self.assertRaisesRegex(
            WorkspaceError, "incompatible database schema|unexpected objects.*transfer_checks"
        ):
            WorkspaceService.open(self.root)

        with sqlite3.connect(service.db_path) as db:
            tables = {
                row[0]
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
        self.assertIn("transfer_checks", tables)
        self.assertNotIn("transfer_check_predictions", tables)
        self.assertNotIn("transfer_check_completions", tables)
        self.assertEqual(self._versions(service), [1, 2, 3, 4])

    def test_current_open_rejects_missing_transfer_check_table_instead_of_repairing(
        self,
    ) -> None:
        service, _event = self._workspace_with_transfer()
        with sqlite3.connect(service.db_path) as db:
            for table in (
                "candidate_source_refs",
                "review_candidates",
                "candidate_runs",
                "candidate_decisions",
            ):
                db.execute(f'DROP TABLE "{table}"')
            # v7 predates migration 10's attempt-chain rebuild.
            downgrade_attempt_chain_to_v9(db)
            db.execute("PRAGMA legacy_alter_table = ON")
            db.execute("ALTER TABLE items RENAME TO items_with_retired")
            db.execute(
                """CREATE TABLE items (
                    item_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    focus TEXT NOT NULL,
                    relative_path TEXT NOT NULL UNIQUE,
                    content_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )"""
            )
            db.execute(
                "INSERT INTO items(item_id, title, focus, relative_path, "
                "content_hash, created_at) "
                "SELECT item_id, title, focus, relative_path, content_hash, "
                "created_at FROM items_with_retired"
            )
            db.execute("DROP TABLE items_with_retired")
            db.execute("PRAGMA legacy_alter_table = OFF")
            db.execute("DELETE FROM schema_migrations WHERE version >= 8")
            db.execute("DROP TABLE transfer_check_completions")

        with self.assertRaisesRegex(
            WorkspaceError, "missing objects|transfer_check_completions"
        ):
            WorkspaceService.open(self.root)

        with sqlite3.connect(service.db_path) as db:
            completion = db.execute(
                """SELECT name FROM sqlite_master
                   WHERE type = 'table' AND name = 'transfer_check_completions'"""
            ).fetchone()
        self.assertIsNone(completion)
        self.assertEqual(self._versions(service), [1, 2, 3, 4, 5, 6, 7])

    def test_open_rejects_altered_append_only_trigger_definition(self) -> None:
        service, _event = self._workspace_with_transfer()
        with sqlite3.connect(service.db_path) as db:
            db.execute("DROP TRIGGER transfer_checks_reject_delete")
            db.execute(
                """CREATE TRIGGER transfer_checks_reject_delete
                   BEFORE DELETE ON transfer_checks
                   BEGIN
                       SELECT RAISE(ABORT, 'altered trigger');
                   END"""
            )

        with self.assertRaisesRegex(
            WorkspaceError, "trigger transfer_checks_reject_delete definition"
        ):
            WorkspaceService.open(self.root)

        with sqlite3.connect(service.db_path) as db:
            sql = db.execute(
                """SELECT sql FROM sqlite_master
                   WHERE type = 'trigger' AND name = 'transfer_checks_reject_delete'"""
            ).fetchone()[0]
        self.assertIn("altered trigger", sql)
        self.assertEqual(self._versions(service), [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13])


class DelayedTransferCheckCreationTests(unittest.TestCase):
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
        self.event = self.service.record_transfer(
            item_id="testing-effect",
            project_id="virtuoso-cli",
            use_case="Applied retrieval practice to a real CLI workflow.",
            outcome="partial",
            independence="guided",
            artifact_reference="git:source123",
            reflection="One design hint was used.",
            occurred_at=datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc),
        )
        self.creation_time = datetime(2026, 8, 21, 10, 30, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _create(self, **overrides: object) -> Any:
        method = getattr(self.service, "create_transfer_check", None)
        if not callable(method):
            self.fail("create_transfer_check is not implemented")
        payload: dict[str, object] = {
            "transfer_event_id": self.event.event_id,
            "context_kind": "changed",
            "context_description": "  A changed macro-research freshness policy.  ",
            "challenge_prompt": "  Classify two artifacts and propose a refresh rule.  ",
            "acceptance_criteria": "  Classify both and state one falsifiable cadence rule.  ",
            "scorer_kind": "human",
            "scorer_reference": "  jonathan  ",
            "now": self.creation_time,
        }
        payload.update(overrides)
        return method(**payload)

    def test_create_check_links_exact_transfer_hash_and_inherits_due_date(self) -> None:
        check = self._create()

        self.assertRegex(check.check_id, r"^transfer-check-[0-9a-f]{32}$")
        self.assertEqual(check.transfer_event_id, self.event.event_id)
        self.assertEqual(check.context_kind, "changed")
        self.assertEqual(
            check.context_description, "A changed macro-research freshness policy."
        )
        self.assertEqual(check.scorer_reference, "jonathan")
        self.assertEqual(check.due_at, self.event.delayed_check_due_at)
        self.assertEqual(check.created_at, "2026-08-21T10:30:00+00:00")
        self.assertFalse(check.claims_mastery)
        with sqlite3.connect(self.service.db_path) as db:
            row = db.execute(
                """SELECT c.transfer_event_id, c.due_at, c.claims_mastery,
                          e.item_content_hash
                   FROM transfer_checks AS c
                   JOIN transfer_events AS e ON e.event_id = c.transfer_event_id
                   WHERE c.check_id = ?""",
                (check.check_id,),
            ).fetchone()
        self.assertEqual(row[0], self.event.event_id)
        self.assertEqual(row[1], self.event.delayed_check_due_at)
        self.assertEqual(row[2], 0)
        self.assertEqual(row[3], self.item.content_hash)

    def test_create_check_rejects_missing_invalid_or_duplicate_transfer_event(
        self,
    ) -> None:
        invalid_cases = (
            ("transfer-ABC", "transfer event id must match"),
            ("transfer-" + "0" * 32, "no transfer event with id"),
        )
        for event_id, message in invalid_cases:
            with self.subTest(event_id=event_id), self.assertRaisesRegex(
                WorkspaceError, message
            ):
                self._create(transfer_event_id=event_id)
        self.assertEqual(self._transfer_check_count(), 0)

        self._create()
        with self.assertRaisesRegex(
            WorkspaceError,
            "delayed transfer check already exists for transfer event",
        ):
            self._create()
        self.assertEqual(self._transfer_check_count(), 1)

    def test_create_check_rejects_empty_oversized_control_character_and_invalid_enum_fields(
        self,
    ) -> None:
        invalid_cases = (
            ("context_kind", "same", "context kind"),
            ("scorer_kind", "oracle", "scorer kind"),
            ("context_description", "   ", "context description"),
            ("challenge_prompt", "x" * 20_001, "challenge prompt"),
            ("acceptance_criteria", "bad\x1bvalue", "acceptance criteria"),
            ("scorer_reference", "line one\nline two", "scorer reference"),
            ("scorer_reference", 42, "scorer reference"),
            ("now", datetime(2026, 8, 21, 10, 30), "timezone"),
        )
        for field, value, message in invalid_cases:
            with self.subTest(field=field), self.assertRaisesRegex(
                WorkspaceError, message
            ):
                self._create(**{field: value})
            self.assertEqual(self._transfer_check_count(), 0)

    def test_create_check_rejects_timestamp_before_source_event_without_write(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            WorkspaceError, "creation timestamp cannot precede its source transfer event"
        ):
            self._create(
                now=datetime(2026, 8, 20, 8, 59, 59, tzinfo=timezone.utc)
            )

        self.assertEqual(self._transfer_check_count(), 0)

    def test_create_check_accepts_stale_current_item_as_historical_source_evidence(
        self,
    ) -> None:
        self.item.path.write_text(
            self.item.path.read_text(encoding="utf-8") + "\nChanged later.\n",
            encoding="utf-8",
        )

        check = self._create(context_kind="novel")

        with sqlite3.connect(self.service.db_path) as db:
            historical_hash = db.execute(
                """SELECT e.item_content_hash
                   FROM transfer_checks AS c
                   JOIN transfer_events AS e ON e.event_id = c.transfer_event_id
                   WHERE c.check_id = ?""",
                (check.check_id,),
            ).fetchone()[0]
        self.assertEqual(historical_hash, self.item.content_hash)

    def _transfer_check_count(self) -> int:
        with sqlite3.connect(self.service.db_path) as db:
            return db.execute("SELECT COUNT(*) FROM transfer_checks").fetchone()[0]


class DueTransferCheckAndPredictionTests(unittest.TestCase):
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
        self.event = self._record_event(
            occurred_at=datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc),
            project_id="primary-project",
        )
        self.check = self._create_check(self.event.event_id, suffix="primary")
        self.due_at = datetime(2026, 8, 27, 9, 0, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _record_event(
        self, *, occurred_at: datetime, project_id: str
    ) -> TransferEvidence:
        return self.service.record_transfer(
            item_id="testing-effect",
            project_id=project_id,
            use_case=f"Applied retrieval practice in {project_id}.",
            outcome="partial",
            independence="guided",
            occurred_at=occurred_at,
        )

    def _create_check(self, event_id: str, *, suffix: str):
        return self.service.create_transfer_check(
            transfer_event_id=event_id,
            context_kind="changed",
            context_description=f"Changed context {suffix}.",
            challenge_prompt=f"Complete challenge {suffix}.",
            acceptance_criteria=f"Meet criterion {suffix}.",
            scorer_kind="tool",
            scorer_reference=f"synthetic-scorer:{suffix}",
            now=datetime(2026, 8, 21, 10, 0, tzinfo=timezone.utc),
        )

    def _list_due(self, as_of: datetime) -> list[Any]:
        method = getattr(self.service, "list_due_transfer_checks", None)
        if not callable(method):
            self.fail("list_due_transfer_checks is not implemented")
        return cast(list[Any], method(as_of=as_of))

    def _begin(self, **overrides: object) -> Any:
        method = getattr(self.service, "begin_transfer_check", None)
        if not callable(method):
            self.fail("begin_transfer_check is not implemented")
        payload: dict[str, object] = {
            "check_id": self.check.check_id,
            "pre_attempt_prediction": "  I expect the distinction to transfer.  ",
            "now": self.due_at,
        }
        payload.update(overrides)
        return method(**payload)

    def _new_table_snapshot(self) -> dict[str, list[tuple[object, ...]]]:
        with sqlite3.connect(self.service.db_path) as db:
            return {
                table: [
                    tuple(row)
                    for row in db.execute(
                        f'SELECT * FROM "{table}" ORDER BY 1'
                    ).fetchall()
                ]
                for table in (
                    "transfer_checks",
                    "transfer_check_predictions",
                    "transfer_check_completions",
                )
            }

    def test_due_list_is_empty_before_due_and_ordered_at_due(self) -> None:
        tied_event = self._record_event(
            occurred_at=datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc),
            project_id="tied-project",
        )
        tied_check = self._create_check(tied_event.event_id, suffix="tied")
        early_event = self._record_event(
            occurred_at=datetime(2026, 8, 20, 8, 0, tzinfo=timezone.utc),
            project_id="early-project",
        )
        early_check = self._create_check(early_event.event_id, suffix="early")

        self.assertEqual(
            self._list_due(datetime(2026, 8, 27, 7, 59, tzinfo=timezone.utc)), []
        )
        snapshot = self._new_table_snapshot()
        at_first_due = self._list_due(
            datetime(2026, 8, 27, 8, 0, tzinfo=timezone.utc)
        )
        self.assertEqual([entry.check_id for entry in at_first_due], [early_check.check_id])
        at_all_due = self._list_due(self.due_at)
        expected_tied = sorted([self.check.check_id, tied_check.check_id])
        self.assertEqual(
            [entry.check_id for entry in at_all_due],
            [early_check.check_id, *expected_tied],
        )
        self.assertEqual(self._new_table_snapshot(), snapshot)
        first = at_all_due[0]
        self.assertEqual(first.status, "pending")
        self.assertEqual(first.transfer_event_id, early_event.event_id)
        self.assertEqual(first.item_id, self.item.item_id)
        self.assertEqual(first.item_content_hash, self.item.content_hash)
        self.assertEqual(first.project_id, "early-project")
        self.assertEqual(first.source_outcome, "partial")
        self.assertEqual(first.source_independence, "guided")
        self.assertEqual(first.prediction_recorded_at, None)
        self.assertFalse(first.claims_mastery)
        self.assertNotIn("answer", first.__dict__)
        self.assertNotIn("path", first.__dict__)

    def test_due_list_rejects_check_created_before_source_event(self) -> None:
        with sqlite3.connect(self.service.db_path) as db:
            trigger_sql = db.execute(
                """SELECT sql FROM sqlite_master
                   WHERE type = 'trigger'
                     AND name = 'transfer_checks_reject_update'"""
            ).fetchone()[0]
            db.execute("DROP TRIGGER transfer_checks_reject_update")
            db.execute(
                "UPDATE transfer_checks SET created_at = ? WHERE check_id = ?",
                (
                    datetime(2026, 8, 20, 8, 59, 59, tzinfo=timezone.utc).isoformat(),
                    self.check.check_id,
                ),
            )
            db.execute(trigger_sql)

        with self.assertRaisesRegex(
            WorkspaceError, "creation timestamp predates its source transfer event"
        ):
            self._list_due(self.due_at)

    def test_due_list_fails_closed_on_malformed_or_mismatched_stored_timestamp(
        self,
    ) -> None:
        def corrupt_due_at(value: str) -> None:
            with sqlite3.connect(self.service.db_path) as db:
                trigger_sql = db.execute(
                    """SELECT sql FROM sqlite_master
                       WHERE type = 'trigger'
                         AND name = 'transfer_checks_reject_update'"""
                ).fetchone()[0]
                db.execute("DROP TRIGGER transfer_checks_reject_update")
                db.execute(
                    "UPDATE transfer_checks SET due_at = ? WHERE check_id = ?",
                    (value, self.check.check_id),
                )
                db.execute(trigger_sql)

        corrupt_due_at("not-a-timestamp")
        with self.assertRaisesRegex(WorkspaceError, "timestamp"):
            self._list_due(self.due_at + timedelta(days=1))

        corrupt_due_at((self.due_at + timedelta(seconds=1)).isoformat())
        with self.assertRaisesRegex(WorkspaceError, "does not match|corrupt"):
            self._list_due(self.due_at + timedelta(days=1))

    def test_due_list_rejects_prediction_recorded_before_check_creation(self) -> None:
        event = self._record_event(
            occurred_at=datetime(2026, 8, 20, 9, 45, tzinfo=timezone.utc),
            project_id="corrupt-prediction-project",
        )
        due_at = datetime.fromisoformat(event.delayed_check_due_at)
        created_at = due_at + timedelta(days=1)
        check = self.service.create_transfer_check(
            transfer_event_id=event.event_id,
            context_kind="changed",
            context_description="A late-authored read-validation check.",
            challenge_prompt="Complete the read-validation challenge.",
            acceptance_criteria="Meet the read-validation criterion.",
            scorer_kind="tool",
            scorer_reference="read-validation-scorer",
            now=created_at,
        )
        self.service.begin_transfer_check(
            check_id=check.check_id,
            pre_attempt_prediction="Initially recorded with valid chronology.",
            now=created_at,
        )
        with sqlite3.connect(self.service.db_path) as db:
            trigger_sql = db.execute(
                """SELECT sql FROM sqlite_master
                   WHERE type = 'trigger'
                     AND name = 'transfer_check_predictions_reject_update'"""
            ).fetchone()[0]
            db.execute("DROP TRIGGER transfer_check_predictions_reject_update")
            db.execute(
                "UPDATE transfer_check_predictions SET recorded_at = ? WHERE check_id = ?",
                (due_at.isoformat(), check.check_id),
            )
            db.execute(trigger_sql)

        with self.assertRaisesRegex(
            WorkspaceError, "prediction predates the check creation"
        ):
            self._list_due(created_at)

    def test_late_created_check_cannot_begin_before_its_creation(self) -> None:
        event = self._record_event(
            occurred_at=datetime(2026, 8, 20, 9, 30, tzinfo=timezone.utc),
            project_id="late-created-project",
        )
        due_at = datetime.fromisoformat(event.delayed_check_due_at)
        late_created_utc = due_at + timedelta(days=1)
        late_created_offset = late_created_utc.astimezone(
            timezone(timedelta(hours=2))
        )
        check = self.service.create_transfer_check(
            transfer_event_id=event.event_id,
            context_kind="novel",
            context_description="A check authored after its inherited due time.",
            challenge_prompt="Complete the late-authored changed challenge.",
            acceptance_criteria="Meet the late-authored criterion.",
            scorer_kind="human",
            scorer_reference="late-reviewer",
            now=late_created_offset,
        )
        self.assertEqual(check.created_at, late_created_utc.isoformat())

        with self.assertRaisesRegex(
            WorkspaceError, "cannot begin before its creation"
        ):
            self.service.begin_transfer_check(
                check_id=check.check_id,
                pre_attempt_prediction="A retroactive prediction must be refused.",
                now=due_at,
            )

        prediction = self.service.begin_transfer_check(
            check_id=check.check_id,
            pre_attempt_prediction="A prediction recorded after authoring.",
            now=late_created_offset,
        )
        self.assertEqual(prediction.recorded_at, late_created_utc.isoformat())

    def test_begin_requires_due_check_and_records_prediction_before_completion(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            WorkspaceError, "delayed transfer check is not due until"
        ):
            self._begin(now=self.due_at - timedelta(seconds=1))
        with sqlite3.connect(self.service.db_path) as db:
            self.assertEqual(
                db.execute(
                    "SELECT COUNT(*) FROM transfer_check_predictions"
                ).fetchone()[0],
                0,
            )

        prediction = self._begin(now=self.due_at)
        self.assertEqual(prediction.check_id, self.check.check_id)
        self.assertEqual(
            prediction.pre_attempt_prediction,
            "I expect the distinction to transfer.",
        )
        self.assertEqual(prediction.recorded_at, self.due_at.isoformat())
        self.assertFalse(prediction.claims_mastery)
        with sqlite3.connect(self.service.db_path) as db:
            persisted = db.execute(
                "SELECT * FROM transfer_check_predictions WHERE check_id = ?",
                (self.check.check_id,),
            ).fetchone()
            completion_count = db.execute(
                "SELECT COUNT(*) FROM transfer_check_completions"
            ).fetchone()[0]
        self.assertEqual(persisted[1], prediction.pre_attempt_prediction)
        self.assertEqual(persisted[2], prediction.recorded_at)
        self.assertEqual(persisted[3], 0)
        self.assertEqual(completion_count, 0)

        with self.assertRaisesRegex(
            WorkspaceError, "pre-attempt prediction already recorded"
        ):
            self._begin(
                pre_attempt_prediction="A replacement prediction.",
                now=self.due_at + timedelta(minutes=1),
            )
        with sqlite3.connect(self.service.db_path) as db:
            unchanged = db.execute(
                "SELECT pre_attempt_prediction, recorded_at FROM transfer_check_predictions"
            ).fetchone()
        self.assertEqual(
            unchanged,
            (prediction.pre_attempt_prediction, prediction.recorded_at),
        )

    def test_begin_validates_check_id_prediction_and_application_clock(self) -> None:
        invalid_cases = (
            ("check_id", "transfer-check-ABC", "transfer check id must match"),
            (
                "check_id",
                "transfer-check-" + "0" * 32,
                "no delayed transfer check with id",
            ),
            ("pre_attempt_prediction", "   ", "pre-attempt prediction"),
            ("pre_attempt_prediction", "bad\x00prediction", "pre-attempt prediction"),
            ("pre_attempt_prediction", "x" * 10_001, "pre-attempt prediction"),
            ("now", datetime(2026, 8, 27, 9, 0), "timezone"),
        )
        for field, value, message in invalid_cases:
            with self.subTest(field=field), self.assertRaisesRegex(
                WorkspaceError, message
            ):
                self._begin(**{field: value})
            with sqlite3.connect(self.service.db_path) as db:
                self.assertEqual(
                    db.execute(
                        "SELECT COUNT(*) FROM transfer_check_predictions"
                    ).fetchone()[0],
                    0,
                )


if __name__ == "__main__":
    unittest.main()
