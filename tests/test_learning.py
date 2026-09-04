from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from conftest import (
    downgrade_benchmark_to_v14,
    downgrade_composition_to_v13,
    downgrade_learning_to_v12,
)
from virtuoso.learning import LearningError, LearningService
from virtuoso.practice import PracticeError, PracticeService
from virtuoso.queries import QueryError, learning_state
from virtuoso.review import ReviewService
from virtuoso.workspace import WorkspaceError, WorkspaceService


class _IO:
    def __init__(self, answers: list[str]) -> None:
        self.answers = iter(answers)
        self.output: list[str] = []
        self.prompts: list[str] = []

    def write(self, text: str) -> None:
        self.output.append(text)

    def ask(self, prompt: str) -> str:
        self.prompts.append(prompt)
        try:
            return next(self.answers)
        except StopIteration as exc:
            raise EOFError from exc


class _InterruptedIO(_IO):
    def ask(self, prompt: str) -> str:
        self.prompts.append(prompt)
        raise KeyboardInterrupt


class LearningDomainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve() / "learner"
        self.workspace = WorkspaceService.init(self.root)
        self.now = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _add_learn_first(self, *, item_id: str = "bayes-rule"):
        return self.workspace.add_item(
            item_id=item_id,
            title="Understand Bayes rule",
            focus="statistics",
            learning_unit=(
                "Bayes rule updates a prior belief with the likelihood of observed evidence.\n\n"
                "Example: combine a test's sensitivity with disease prevalence."
            ),
            entry_mode="learn-first",
            prompt="What does Bayes rule update?",
            answer="A prior belief, using the likelihood of observed evidence.",
        )

    def _table_count(self, table: str) -> int:
        with sqlite3.connect(self.workspace.db_path) as db:
            return db.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]

    def _finish(self, *, item_id: str = "bayes-rule"):
        io = _IO(["finish"])
        result = LearningService(self.workspace).run(
            item_id=item_id,
            io=io,
            now=self.now,
            surface="cli",
        )
        return result, io

    def test_learn_first_item_round_trips_v02_and_hashes(self) -> None:
        summary = self._add_learn_first()
        item = self.workspace.load_item(summary.item_id)
        text = summary.path.read_text(encoding="utf-8")

        self.assertIn("schema: virtuoso/item@0.2", text)
        self.assertIn("entry-mode: learn-first", text)
        self.assertIn("# Learning unit\n\nBayes rule updates", text)
        self.assertEqual(item.entry_mode, "learn-first")
        self.assertEqual(
            item.learning_unit,
            "Bayes rule updates a prior belief with the likelihood of observed evidence.\n\n"
            "Example: combine a test's sensitivity with disease prevalence.",
        )
        self.assertEqual(
            item.learning_unit_hash,
            hashlib.sha256(item.learning_unit.encode("utf-8")).hexdigest(),
        )

    def test_default_item_remains_byte_compatible_recall_first_v01(self) -> None:
        summary = self.workspace.add_item(
            item_id="existing-shape",
            title="Existing shape",
            focus="compatibility",
            prompt="What stays compatible?",
            answer="The recall-first item format.",
        )
        item = self.workspace.load_item(summary.item_id)
        text = summary.path.read_text(encoding="utf-8")

        self.assertIn("schema: virtuoso/item@0.1", text)
        self.assertNotIn("entry-mode:", text)
        self.assertNotIn("# Learning unit", text)
        self.assertEqual(item.entry_mode, "recall-first")
        self.assertIsNone(item.learning_unit)
        self.assertIsNone(item.learning_unit_hash)

        with self.assertRaisesRegex(LearningError, "recall-first"):
            LearningService(self.workspace).run(
                item_id=item.item_id,
                io=_IO(["finish"]),
                now=self.now,
                surface="cli",
            )

    def test_invalid_mode_and_learning_unit_combinations_write_nothing(self) -> None:
        cases = (
            ("missing-unit", "learn-first", None),
            ("recall-with-unit", "recall-first", "Extra lesson"),
            ("unknown-mode", "cold-start", None),
            ("unsafe-unit", "learn-first", "# Hidden top-level heading"),
        )
        for item_id, entry_mode, learning_unit in cases:
            with self.subTest(item_id=item_id):
                with self.assertRaises(WorkspaceError):
                    self.workspace.add_item(
                        item_id=item_id,
                        title="Invalid",
                        focus="validation",
                        entry_mode=entry_mode,
                        learning_unit=learning_unit,
                        prompt="Prompt",
                        answer="Answer",
                    )
                self.assertFalse((self.workspace.items_dir / f"{item_id}.md").exists())
        self.assertEqual(self._table_count("items"), 0)

    def test_selection_changes_from_learn_to_practice_after_completion(self) -> None:
        self._add_learn_first()

        before = self.workspace.select_next(self.now)
        self.assertEqual(before.action, "learn")
        self.assertIn("study", before.rationale.lower())

        result, io = self._finish()

        self.assertTrue(result.completed)
        self.assertIsNotNone(result.event)
        self.assertEqual(io.output[0], "Learning: Understand Bayes rule")
        output = "\n".join(io.output)
        self.assertIn("Bayes rule updates a prior belief", output)
        self.assertNotIn("What does Bayes rule update?", output)
        self.assertNotIn("A prior belief, using", output)
        after = self.workspace.select_next(self.now)
        self.assertEqual(after.action, "practice")
        self.assertIn("completed study", after.rationale.lower())

    def test_finish_appends_only_one_hash_bound_study_event(self) -> None:
        self._add_learn_first()
        item = self.workspace.load_item("bayes-rule")

        result, _io = self._finish()
        events = self.workspace.list_study_events()

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_id"], result.event["event_id"])
        self.assertEqual(events[0]["item_id"], item.item_id)
        self.assertEqual(events[0]["item_content_hash"], item.content_hash)
        self.assertEqual(events[0]["learning_unit_hash"], item.learning_unit_hash)
        self.assertEqual(events[0]["occurred_at"], self.now.isoformat())
        self.assertIs(events[0]["claims_mastery"], False)
        self.assertEqual(events[0]["surface"], "cli")
        for table in (
            "attempts",
            "attempt_timings",
            "scheduler_proposals",
            "scheduler_state",
            "transfer_events",
            "review_skips",
        ):
            self.assertEqual(self._table_count(table), 0, table)

        with self.assertRaisesRegex(LearningError, "already completed"):
            self._finish()
        with self.assertRaisesRegex(WorkspaceError, "already completed"):
            self.workspace.record_study_completion(
                item_id=item.item_id,
                item_content_hash=item.content_hash,
                learning_unit_hash=item.learning_unit_hash,
                occurred_at=self.now,
                surface="cli",
            )
        self.assertEqual(self._table_count("study_events"), 1)

    def test_stop_eof_and_interrupt_append_no_study_event(self) -> None:
        self._add_learn_first()

        stopped = LearningService(self.workspace).run(
            item_id="bayes-rule",
            io=_IO(["stop"]),
            now=self.now,
            surface="cli",
        )
        self.assertFalse(stopped.completed)
        self.assertIsNone(stopped.event)
        self.assertEqual(self._table_count("study_events"), 0)

        for io in (_IO([]), _InterruptedIO([])):
            with self.subTest(io=type(io).__name__):
                with self.assertRaisesRegex(LearningError, "stopped before completion"):
                    LearningService(self.workspace).run(
                        item_id="bayes-rule",
                        io=io,
                        now=self.now,
                        surface="cli",
                    )
                self.assertEqual(self._table_count("study_events"), 0)

    def test_completion_timestamp_is_sampled_after_finish_is_accepted(self) -> None:
        self._add_learn_first()
        completed_at = datetime(2026, 9, 3, 13, 45, tzinfo=timezone.utc)
        io = _IO(["finish"])
        original_ask = io.ask

        with patch("virtuoso.learning.datetime") as clock:
            clock.now.return_value = completed_at

            def finish(prompt: str) -> str:
                self.assertEqual(clock.now.call_count, 0)
                return original_ask(prompt)

            with patch.object(io, "ask", side_effect=finish):
                result = LearningService(self.workspace).run(
                    item_id="bayes-rule",
                    io=io,
                    surface="cli",
                )

        self.assertEqual(clock.now.call_count, 1)
        assert result.event is not None
        self.assertEqual(result.event["occurred_at"], completed_at.isoformat())

    def test_content_change_before_finish_appends_no_study_event(self) -> None:
        summary = self._add_learn_first()
        io = _IO([])

        def change_then_finish(_prompt: str) -> str:
            summary.path.write_text(
                summary.path.read_text(encoding="utf-8").replace(
                    "Bayes rule updates", "Bayes rule changed"
                ),
                encoding="utf-8",
            )
            return "finish"

        with patch.object(io, "ask", side_effect=change_then_finish):
            with self.assertRaisesRegex(LearningError, "stale"):
                LearningService(self.workspace).run(
                    item_id="bayes-rule",
                    io=io,
                    now=self.now,
                    surface="cli",
                )
        self.assertEqual(self._table_count("study_events"), 0)

    def test_every_practice_writer_rejects_pending_learning(self) -> None:
        self._add_learn_first()

        with self.assertRaisesRegex(PracticeError, "requires learning"):
            PracticeService(self.workspace).run_administered(
                item_id="bayes-rule",
                response="A guessed answer",
                result="partial",
                confidence=2,
                now=self.now,
            )
        with self.assertRaisesRegex(WorkspaceError, "requires learning"):
            self.workspace.record_review_skip(
                event_id="skip-0123456789abcdef0123456789abcdef",
                item_id="bayes-rule",
                item_content_hash=self.workspace.load_item("bayes-rule").content_hash,
                occurred_at=self.now.isoformat(),
                surface="obsidian-plugin",
            )
        self.assertEqual(ReviewService(self.workspace).due(now=self.now), [])
        for table in ("attempts", "scheduler_proposals", "scheduler_state", "review_skips"):
            self.assertEqual(self._table_count(table), 0, table)

        self._finish()
        PracticeService(self.workspace).run_administered(
            item_id="bayes-rule",
            response="It updates a prior using evidence.",
            result="demonstrated",
            confidence=4,
            now=self.now,
        )
        self.assertEqual(self._table_count("attempts"), 1)
        self.assertEqual(self._table_count("scheduler_proposals"), 1)

    def test_changed_current_hashes_require_fresh_learning_and_keep_history(self) -> None:
        self._add_learn_first()
        self._finish()
        original = self.workspace.load_item("bayes-rule")

        changed_prompt = "How does Bayes rule use evidence?"
        prompt_text = original.path.read_text(encoding="utf-8").replace(
            original.prompt,
            changed_prompt,
        )
        prompt_hash = hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()
        original.path.write_text(prompt_text, encoding="utf-8")
        with sqlite3.connect(self.workspace.db_path) as db:
            db.execute(
                "UPDATE items SET content_hash = ? WHERE item_id = ?",
                (prompt_hash, "bayes-rule"),
            )

        prompt_changed = self.workspace.load_item("bayes-rule")
        self.assertEqual(prompt_changed.content_hash, prompt_hash)
        self.assertEqual(prompt_changed.learning_unit_hash, original.learning_unit_hash)
        self.assertEqual(self.workspace.select_next(self.now).action, "learn")
        self.assertEqual(self._table_count("study_events"), 1)
        self._finish()
        self.assertEqual(self._table_count("study_events"), 2)

        replacement_unit = "Bayes rule combines a prior with new likelihood evidence."
        current_unit = prompt_changed.learning_unit
        self.assertIsNotNone(current_unit)
        assert current_unit is not None
        replacement_text = prompt_changed.path.read_text(encoding="utf-8").replace(
            current_unit,
            replacement_unit,
        )
        replacement_hash = hashlib.sha256(replacement_text.encode("utf-8")).hexdigest()
        replacement_unit_hash = hashlib.sha256(
            replacement_unit.encode("utf-8")
        ).hexdigest()
        prompt_changed.path.write_text(replacement_text, encoding="utf-8")
        with sqlite3.connect(self.workspace.db_path) as db:
            db.execute(
                "UPDATE items SET content_hash = ?, learning_unit_hash = ? WHERE item_id = ?",
                (replacement_hash, replacement_unit_hash, "bayes-rule"),
            )

        unit_changed = self.workspace.load_item("bayes-rule")
        self.assertEqual(unit_changed.content_hash, replacement_hash)
        self.assertEqual(unit_changed.learning_unit_hash, replacement_unit_hash)
        self.assertEqual(self.workspace.select_next(self.now).action, "learn")
        self.assertEqual(self._table_count("study_events"), 2)

        self._finish()
        self.assertEqual(self._table_count("study_events"), 3)
        self.assertEqual(self.workspace.select_next(self.now).action, "practice")

    def test_study_events_are_append_only(self) -> None:
        self._add_learn_first()
        self._finish()
        with sqlite3.connect(self.workspace.db_path) as db:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                db.execute("UPDATE study_events SET surface = 'other'")
            with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                db.execute("DELETE FROM study_events")

    def test_open_rejects_altered_study_event_guard(self) -> None:
        with sqlite3.connect(self.workspace.db_path) as db:
            db.execute("DROP TRIGGER study_events_reject_delete")
            db.execute(
                """CREATE TRIGGER study_events_reject_delete
                   BEFORE DELETE ON study_events
                   BEGIN
                       SELECT RAISE(ABORT, 'altered study guard');
                   END"""
            )

        with self.assertRaisesRegex(WorkspaceError, "study_events_reject_delete"):
            WorkspaceService.open(self.root)

    def test_record_attempt_transaction_keeps_the_learning_guard(self) -> None:
        self._add_learn_first()
        with patch.object(self.workspace, "require_practice_ready"):
            with self.assertRaisesRegex(PracticeError, "requires learning"):
                PracticeService(self.workspace).run_administered(
                    item_id="bayes-rule",
                    response="A guessed answer",
                    result="partial",
                    confidence=2,
                    now=self.now,
                )
        for table in ("attempts", "scheduler_proposals", "scheduler_state"):
            self.assertEqual(self._table_count(table), 0, table)

    def test_item_learning_metadata_must_match_its_markdown_schema(self) -> None:
        learned = self._add_learn_first(item_id="learn-contract")
        recall = self.workspace.add_item(
            item_id="recall-contract",
            title="Recall contract",
            focus="validation",
            prompt="Prompt",
            answer="Answer",
        )
        malformed_learn = learned.path.read_text(encoding="utf-8").replace(
            "entry-mode: learn-first\n", ""
        )
        learned.path.write_text(malformed_learn, encoding="utf-8")
        malformed_recall = recall.path.read_text(encoding="utf-8") + (
            "\n# Learning unit\n\nAmbiguous prose.\n"
        )
        recall.path.write_text(malformed_recall, encoding="utf-8")
        with sqlite3.connect(self.workspace.db_path) as db:
            db.execute(
                "UPDATE items SET content_hash = ? WHERE item_id = 'learn-contract'",
                (hashlib.sha256(malformed_learn.encode("utf-8")).hexdigest(),),
            )
            db.execute(
                "UPDATE items SET content_hash = ? WHERE item_id = 'recall-contract'",
                (hashlib.sha256(malformed_recall.encode("utf-8")).hexdigest(),),
            )

        with self.assertRaisesRegex(WorkspaceError, "requires entry-mode"):
            self.workspace.load_item("learn-contract")
        with self.assertRaisesRegex(WorkspaceError, "must not declare learn-first metadata"):
            self.workspace.load_item("recall-contract")

    def test_v12_workspace_migrates_without_inventing_study_evidence(self) -> None:
        item = self.workspace.add_item(
            item_id="legacy-recall",
            title="Legacy recall",
            focus="migration",
            prompt="What must migration preserve?",
            answer="The item and its absence of study evidence.",
        )
        item_bytes = item.path.read_bytes()
        with sqlite3.connect(self.workspace.db_path) as db:
            downgrade_learning_to_v12(db)
            self.assertEqual(
                db.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0],
                12,
            )

        migrated = WorkspaceService.open(self.root)
        loaded = migrated.load_item("legacy-recall")
        fresh = WorkspaceService.init(Path(self.tmp.name).resolve() / "fresh")

        def schema(service: WorkspaceService) -> dict[tuple[str, str], str]:
            with sqlite3.connect(service.db_path) as db:
                return {
                    (row[0], row[1]): WorkspaceService._normalized_schema_sql(row[2])
                    for row in db.execute(
                        """SELECT name, type, sql FROM sqlite_master
                           WHERE name NOT LIKE 'sqlite_%'
                             AND type IN ('table', 'trigger', 'index')
                           ORDER BY name, type"""
                    ).fetchall()
                }

        with sqlite3.connect(migrated.db_path) as db:
            versions = [
                row[0]
                for row in db.execute(
                    "SELECT version FROM schema_migrations ORDER BY version"
                ).fetchall()
            ]
            row = db.execute(
                "SELECT entry_mode, learning_unit_hash FROM items WHERE item_id = ?",
                ("legacy-recall",),
            ).fetchone()
            study_count = db.execute("SELECT COUNT(*) FROM study_events").fetchone()[0]
            triggers = {
                row[0]
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'trigger'"
                    " AND name LIKE 'study_events_reject_%'"
                ).fetchall()
            }
        self.assertEqual(versions, list(range(1, 16)))
        self.assertEqual(row, ("recall-first", None))
        self.assertEqual(study_count, 0)
        self.assertEqual(
            triggers,
            {"study_events_reject_update", "study_events_reject_delete"},
        )
        self.assertEqual(loaded.entry_mode, "recall-first")
        self.assertEqual(item.path.read_bytes(), item_bytes)
        self.assertEqual(schema(migrated), schema(fresh))

    def test_v13_workspace_migrates_without_inventing_composition_evidence(self) -> None:
        item = self.workspace.add_item(
            item_id="legacy-recall",
            title="Legacy recall",
            focus="migration",
            prompt="What must migration preserve?",
            answer="The item and its absence of composition evidence.",
        )
        item_bytes = item.path.read_bytes()
        with sqlite3.connect(self.workspace.db_path) as db:
            downgrade_composition_to_v13(db)
            self.assertEqual(
                db.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0],
                13,
            )

        migrated = WorkspaceService.open(self.root)
        loaded = migrated.load_item("legacy-recall")
        fresh = WorkspaceService.init(Path(self.tmp.name).resolve() / "fresh")

        def schema(service: WorkspaceService) -> dict[tuple[str, str], str]:
            with sqlite3.connect(service.db_path) as db:
                return {
                    (row[0], row[1]): WorkspaceService._normalized_schema_sql(row[2])
                    for row in db.execute(
                        """SELECT name, type, sql FROM sqlite_master
                           WHERE name NOT LIKE 'sqlite_%'
                             AND type IN ('table', 'trigger', 'index')
                           ORDER BY name, type"""
                    ).fetchall()
                }

        with sqlite3.connect(migrated.db_path) as db:
            versions = [
                row[0]
                for row in db.execute(
                    "SELECT version FROM schema_migrations ORDER BY version"
                ).fetchall()
            ]
            proposal_count = db.execute(
                "SELECT COUNT(*) FROM composition_proposals"
            ).fetchone()[0]
            decision_count = db.execute(
                "SELECT COUNT(*) FROM composition_decisions"
            ).fetchone()[0]
            triggers = {
                row[0]
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'trigger'"
                    " AND name LIKE 'composition_%'"
                ).fetchall()
            }
        self.assertEqual(versions, list(range(1, 16)))
        self.assertEqual(proposal_count, 0)
        self.assertEqual(decision_count, 0)
        self.assertEqual(
            triggers,
            {
                "composition_proposals_reject_update",
                "composition_proposals_reject_delete",
                "composition_proposal_items_reject_update",
                "composition_proposal_items_reject_delete",
                "composition_decisions_reject_update",
                "composition_decisions_reject_delete",
            },
        )
        self.assertEqual(loaded.item_id, "legacy-recall")
        self.assertEqual(item.path.read_bytes(), item_bytes)
        self.assertEqual(schema(migrated), schema(fresh))

    def test_v14_workspace_migrates_without_inventing_benchmark_evidence(self) -> None:
        item = self.workspace.add_item(
            item_id="legacy-recall",
            title="Legacy recall",
            focus="migration",
            prompt="What must migration preserve?",
            answer="The item and its absence of benchmark evidence.",
        )
        item_bytes = item.path.read_bytes()
        with sqlite3.connect(self.workspace.db_path) as db:
            downgrade_benchmark_to_v14(db)
            self.assertEqual(
                db.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0],
                14,
            )

        migrated = WorkspaceService.open(self.root)
        loaded = migrated.load_item("legacy-recall")
        fresh = WorkspaceService.init(Path(self.tmp.name).resolve() / "fresh-v14")

        with sqlite3.connect(migrated.db_path) as db:
            versions = [
                row[0]
                for row in db.execute(
                    "SELECT version FROM schema_migrations ORDER BY version"
                ).fetchall()
            ]
            runs = db.execute("SELECT COUNT(*) FROM benchmark_runs").fetchone()[0]
            observations = db.execute(
                "SELECT COUNT(*) FROM benchmark_observations"
            ).fetchone()[0]
            reruns = db.execute(
                "SELECT COUNT(*) FROM benchmark_reruns"
            ).fetchone()[0]
            triggers = {
                row[0]
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'trigger'"
                    " AND name LIKE 'benchmark_%'"
                ).fetchall()
            }
        self.assertEqual(versions, list(range(1, 16)))
        self.assertEqual((runs, observations, reruns), (0, 0, 0))
        self.assertEqual(
            triggers,
            {
                "benchmark_runs_reject_update",
                "benchmark_runs_reject_delete",
                "benchmark_observations_reject_update",
                "benchmark_observations_reject_delete",
                "benchmark_reruns_reject_update",
                "benchmark_reruns_reject_delete",
            },
        )
        self.assertEqual(loaded.item_id, "legacy-recall")
        self.assertEqual(item.path.read_bytes(), item_bytes)

        def schema(service: WorkspaceService) -> dict[tuple[str, str], str]:
            with sqlite3.connect(service.db_path) as db:
                return {
                    (row[0], row[1]): WorkspaceService._normalized_schema_sql(row[2])
                    for row in db.execute(
                        """SELECT name, type, sql FROM sqlite_master
                           WHERE name NOT LIKE 'sqlite_%'
                             AND type IN ('table', 'trigger', 'index')
                           ORDER BY name, type"""
                    ).fetchall()
                }

        self.assertEqual(schema(migrated), schema(fresh))

    def test_doctor_and_read_only_query_explain_learning_state(self) -> None:
        self._add_learn_first()
        self.workspace.add_item(
            item_id="recall-ready",
            title="Recall ready",
            focus="statistics",
            prompt="What is already ready?",
            answer="This recall-first item.",
        )

        rows = learning_state(self.workspace.db_path)
        by_id = {row["item_id"]: row for row in rows}
        self.assertEqual(by_id["bayes-rule"]["action"], "learn")
        self.assertEqual(by_id["bayes-rule"]["reason_code"], "study-required")
        self.assertIsNone(by_id["bayes-rule"]["study_completed_at"])
        self.assertEqual(by_id["recall-ready"]["action"], "practice")
        self.assertEqual(by_id["recall-ready"]["reason_code"], "recall-first")
        report = self.workspace.doctor(now=self.now)
        self.assertEqual(report["study_events"], 0)
        self.assertEqual(
            report["learning"],
            {"waiting_for_learning": 1, "ready_for_practice": 1},
        )

        self._finish()
        rows = learning_state(self.workspace.db_path)
        by_id = {row["item_id"]: row for row in rows}
        self.assertEqual(by_id["bayes-rule"]["action"], "practice")
        self.assertEqual(by_id["bayes-rule"]["reason_code"], "study-current")
        self.assertEqual(by_id["bayes-rule"]["study_completed_at"], self.now.isoformat())
        report = self.workspace.doctor(now=self.now)
        self.assertEqual(report["study_events"], 1)
        self.assertEqual(
            report["learning"],
            {"waiting_for_learning": 0, "ready_for_practice": 2},
        )

    def test_invalid_current_study_timestamp_fails_every_learning_surface(self) -> None:
        self._add_learn_first()
        self._finish()
        with sqlite3.connect(self.workspace.db_path) as db:
            db.execute("DROP TRIGGER study_events_reject_update")
            db.execute("UPDATE study_events SET occurred_at = 'not-a-date'")

        with self.assertRaisesRegex(WorkspaceError, "invalid study completion timestamp"):
            self.workspace.learning_state("bayes-rule")
        with self.assertRaisesRegex(WorkspaceError, "invalid study completion timestamp"):
            self.workspace.doctor(now=self.now)
        with self.assertRaisesRegex(QueryError, "invalid study completion timestamp"):
            learning_state(self.workspace.db_path)


if __name__ == "__main__":
    unittest.main()
