from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from virtuoso.workspace import (
    DelayedTransferCheck,
    TransferEvidence,
    WorkspaceError,
    WorkspaceService,
)


class TransferCheckCompletionTests(unittest.TestCase):
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
        self.event = self._record_event("primary", minute=0)
        self.check = self._create_check(self.event, "primary")
        self.due_at = self._timestamp(self.event.delayed_check_due_at)
        self.prediction = self.service.begin_transfer_check(
            check_id=self.check.check_id,
            pre_attempt_prediction="I expect the scheduling distinction to transfer.",
            now=self.due_at,
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    @staticmethod
    def _timestamp(value: str) -> datetime:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    def _record_event(self, suffix: str, *, minute: int) -> TransferEvidence:
        return self.service.record_transfer(
            item_id=self.item.item_id,
            project_id=f"project-{suffix}",
            use_case=f"Applied retrieval practice in changed context {suffix}.",
            outcome="partial",
            independence="guided",
            occurred_at=datetime(2026, 8, 20, 9, minute, tzinfo=timezone.utc),
        )

    def _create_check(
        self, event: TransferEvidence, suffix: str
    ) -> DelayedTransferCheck:
        return self.service.create_transfer_check(
            transfer_event_id=event.event_id,
            context_kind="novel" if suffix == "novel" else "changed",
            context_description=f"Changed context {suffix}.",
            challenge_prompt=f"Classify artifacts and propose rule {suffix}.",
            acceptance_criteria=f"Classify both and state rule {suffix}.",
            scorer_kind="human",
            scorer_reference=f"reviewer-{suffix}",
            now=datetime(2026, 8, 21, 10, 0, tzinfo=timezone.utc),
        )

    def _started_check(
        self, suffix: str, *, minute: int
    ) -> tuple[TransferEvidence, DelayedTransferCheck, datetime]:
        event = self._record_event(suffix, minute=minute)
        check = self._create_check(event, suffix)
        due_at = self._timestamp(event.delayed_check_due_at)
        self.service.begin_transfer_check(
            check_id=check.check_id,
            pre_attempt_prediction=f"Prediction {suffix}.",
            now=due_at,
        )
        return event, check, due_at

    def _complete(self, **overrides: object) -> Any:
        method = getattr(self.service, "complete_transfer_check", None)
        if not callable(method):
            self.fail("complete_transfer_check is not implemented")
        payload: dict[str, object] = {
            "check_id": self.check.check_id,
            "independent_attempt": "  My independent classification and cadence rule.  ",
            "assistance_level": "light",
            "assistance_detail": "  Consulted one syntax note after the attempt.  ",
            "acceptance_evidence": "  The configured reviewer accepted both classifications.  ",
            "teach_back": "  Retrievability stays separate from project urgency.  ",
            "outcome": "partial",
            "artifact_reference": "  git:check123  ",
            "now": self.due_at + timedelta(minutes=1),
        }
        payload.update(overrides)
        return method(**payload)

    def _table_rows(self, table: str) -> list[tuple[object, ...]]:
        with sqlite3.connect(self.service.db_path) as db:
            return [
                tuple(row)
                for row in db.execute(f'SELECT * FROM "{table}" ORDER BY 1').fetchall()
            ]

    def test_complete_requires_prediction_and_records_full_attribution(self) -> None:
        unstarted_event = self._record_event("unstarted", minute=1)
        unstarted_check = self._create_check(unstarted_event, "unstarted")
        unstarted_due = self._timestamp(unstarted_event.delayed_check_due_at)
        with self.assertRaisesRegex(
            WorkspaceError,
            "record a pre-attempt prediction before completing delayed transfer check",
        ):
            self._complete(
                check_id=unstarted_check.check_id,
                now=unstarted_due + timedelta(minutes=1),
            )
        self.assertEqual(self._table_rows("transfer_check_completions"), [])

        completion = self._complete()

        self.assertEqual(completion.check_id, self.check.check_id)
        self.assertEqual(
            completion.independent_attempt,
            "My independent classification and cadence rule.",
        )
        self.assertEqual(completion.assistance_level, "light")
        self.assertEqual(
            completion.assistance_detail,
            "Consulted one syntax note after the attempt.",
        )
        self.assertEqual(
            completion.acceptance_evidence,
            "The configured reviewer accepted both classifications.",
        )
        self.assertEqual(completion.acceptance_criteria, self.check.acceptance_criteria)
        self.assertEqual(completion.scorer_kind, self.check.scorer_kind)
        self.assertEqual(completion.scorer_reference, self.check.scorer_reference)
        self.assertEqual(
            completion.teach_back,
            "Retrievability stays separate from project urgency.",
        )
        self.assertEqual(completion.outcome, "partial")
        self.assertEqual(completion.artifact_reference, "git:check123")
        self.assertEqual(completion.prediction_recorded_at, self.prediction.recorded_at)
        self.assertEqual(
            completion.completed_at,
            (self.due_at + timedelta(minutes=1)).isoformat(),
        )
        self.assertFalse(completion.claims_mastery)
        with sqlite3.connect(self.service.db_path) as db:
            row = db.execute(
                "SELECT * FROM transfer_check_completions WHERE check_id = ?",
                (self.check.check_id,),
            ).fetchone()
        self.assertEqual(
            tuple(row),
            (
                self.check.check_id,
                completion.independent_attempt,
                "light",
                completion.assistance_detail,
                completion.acceptance_evidence,
                completion.teach_back,
                "partial",
                "git:check123",
                completion.completed_at,
                0,
            ),
        )

    def test_completion_assistance_validation_matrix(self) -> None:
        invalid_cases = (
            ("none", "Used notes.", "must be omitted"),
            ("light", None, "is required"),
            ("substantial", "   ", "is required"),
            ("unknown", None, "is required"),
        )
        for assistance, detail, message in invalid_cases:
            with self.subTest(assistance=assistance), self.assertRaisesRegex(
                WorkspaceError, message
            ):
                self._complete(
                    assistance_level=assistance,
                    assistance_detail=detail,
                )
            self.assertEqual(self._table_rows("transfer_check_completions"), [])

        valid_cases = (
            ("none", None, "unsuccessful"),
            ("light", "Used one note.", "partial"),
            ("substantial", "A human reworked the approach.", "successful"),
            ("unknown", "Attribution could not be reconstructed.", "partial"),
        )
        for index, (assistance, detail, outcome) in enumerate(valid_cases, start=10):
            _event, check, due_at = self._started_check(
                f"matrix-{index}", minute=index
            )
            completion = self._complete(
                check_id=check.check_id,
                assistance_level=assistance,
                assistance_detail=detail,
                outcome=outcome,
                artifact_reference="   ",
                now=due_at + timedelta(minutes=1),
            )
            self.assertEqual(completion.assistance_level, assistance)
            self.assertEqual(completion.assistance_detail, detail)
            self.assertEqual(completion.outcome, outcome)
            self.assertIsNone(completion.artifact_reference)
            self.assertFalse(completion.claims_mastery)

    def test_successful_assisted_completion_never_claims_mastery(self) -> None:
        completion = self._complete(
            assistance_level="substantial",
            assistance_detail="An agent proposed the final cadence rule.",
            outcome="successful",
        )
        self.assertEqual(completion.outcome, "successful")
        self.assertEqual(completion.assistance_level, "substantial")
        self.assertFalse(completion.claims_mastery)
        with sqlite3.connect(self.service.db_path) as db:
            claims = {
                table: db.execute(
                    f'SELECT claims_mastery FROM "{table}"'
                ).fetchall()
                for table in (
                    "transfer_events",
                    "transfer_checks",
                    "transfer_check_predictions",
                    "transfer_check_completions",
                )
            }
        self.assertTrue(all(value == 0 for rows in claims.values() for (value,) in rows))

    def test_transfer_evidence_lineage_rejects_direct_update_and_delete(
        self,
    ) -> None:
        self._complete()
        probes = (
            (
                "transfer_events",
                "UPDATE transfer_events SET item_content_hash = 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff' WHERE event_id = ?",
                self.event.event_id,
            ),
            (
                "transfer_checks",
                "UPDATE transfer_checks SET transfer_event_id = 'transfer-00000000000000000000000000000000' WHERE check_id = ?",
                self.check.check_id,
            ),
            (
                "transfer_check_predictions",
                "UPDATE transfer_check_predictions SET pre_attempt_prediction = 'rewritten prediction' WHERE check_id = ?",
                self.check.check_id,
            ),
            (
                "transfer_check_completions",
                "UPDATE transfer_check_completions SET outcome = 'successful' WHERE check_id = ?",
                self.check.check_id,
            ),
            (
                "transfer_events",
                "DELETE FROM transfer_events WHERE event_id = ?",
                self.event.event_id,
            ),
            (
                "transfer_checks",
                "DELETE FROM transfer_checks WHERE check_id = ?",
                self.check.check_id,
            ),
            (
                "transfer_check_predictions",
                "DELETE FROM transfer_check_predictions WHERE check_id = ?",
                self.check.check_id,
            ),
            (
                "transfer_check_completions",
                "DELETE FROM transfer_check_completions WHERE check_id = ?",
                self.check.check_id,
            ),
        )
        with sqlite3.connect(self.service.db_path) as db:
            db.execute("PRAGMA foreign_keys = OFF")
            for table, statement, record_id in probes:
                with self.subTest(table=table, operation=statement.split()[0]):
                    db.execute("SAVEPOINT immutable_probe")
                    try:
                        with self.assertRaisesRegex(
                            sqlite3.IntegrityError, f"{table} is append-only"
                        ):
                            db.execute(statement, (record_id,))
                    finally:
                        db.execute("ROLLBACK TO immutable_probe")
                        db.execute("RELEASE immutable_probe")

    def test_completion_is_single_insert_and_concurrent_retry_cannot_overwrite(
        self,
    ) -> None:
        first = self._complete()
        independent_service = WorkspaceService.open(self.root)
        retry = getattr(independent_service, "complete_transfer_check", None)
        if not callable(retry):
            self.fail("complete_transfer_check is not implemented")

        with self.assertRaisesRegex(
            WorkspaceError, "delayed transfer check already completed"
        ):
            retry(
                check_id=self.check.check_id,
                independent_attempt="Replacement attempt.",
                assistance_level="none",
                assistance_detail=None,
                acceptance_evidence="Replacement evidence.",
                teach_back="Replacement teach-back.",
                outcome="successful",
                artifact_reference="git:replacement",
                now=self.due_at + timedelta(minutes=2),
            )

        with sqlite3.connect(self.service.db_path) as db:
            row = db.execute(
                """SELECT independent_attempt, outcome, artifact_reference, completed_at
                   FROM transfer_check_completions WHERE check_id = ?""",
                (self.check.check_id,),
            ).fetchone()
        self.assertEqual(
            row,
            (
                first.independent_attempt,
                first.outcome,
                first.artifact_reference,
                first.completed_at,
            ),
        )

    def test_completion_does_not_change_memory_scheduler_or_project_selection(
        self,
    ) -> None:
        separated_event = self._record_event("separation", minute=20)
        self.service.record_attempt(
            attempt={
                "event_id": "attempt-" + "a" * 32,
                "item_id": self.item.item_id,
                "item_content_hash": self.item.content_hash,
                "started_at": "2026-08-25T08:59:59+00:00",
                "completed_at": "2026-08-25T09:00:00+00:00",
                "occurred_at": "2026-08-25T09:00:00+00:00",
                "initial_response": "Synthetic independent response.",
                "initial_latency_ms": 10,
                "result": "partial",
                "confidence": 3,
                "open_notes": False,
                "agent_help": "none",
                "support_actions": [],
                "administered": False,
            },
            proposal={
                "proposal_id": "proposal-" + "b" * 32,
                "source_event_id": "attempt-" + "a" * 32,
                "item_id": self.item.item_id,
                "algorithm": "fsrs",
                "algorithm_version": "synthetic",
                "learning_context": "atomic-recall",
                "configuration": {"synthetic": True},
                "previous_state_json": None,
                "previous_source_event_id": None,
                "due_at": "2026-08-26T09:00:00+00:00",
                "rationale": "Synthetic scheduler fixture.",
                "created_at": "2026-08-25T09:00:00+00:00",
            },
            state_json='{"due": "2026-08-26T09:00:00+00:00", "synthetic": true}',
        )
        protected_tables = ("attempts", "scheduler_state", "scheduler_proposals")
        before = {table: self._table_rows(table) for table in protected_tables}
        selected_before = self.service.select_next(self.due_at)
        with sqlite3.connect(self.service.db_path) as db:
            source_before = db.execute(
                "SELECT * FROM transfer_events WHERE event_id = ?",
                (separated_event.event_id,),
            ).fetchone()
            new_counts_before = {
                table: db.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                for table in (
                    "transfer_checks",
                    "transfer_check_predictions",
                    "transfer_check_completions",
                )
            }

        separated_check = self._create_check(separated_event, "separation")
        separated_due = self._timestamp(separated_event.delayed_check_due_at)
        self.service.begin_transfer_check(
            check_id=separated_check.check_id,
            pre_attempt_prediction="Separation prediction.",
            now=separated_due,
        )
        self._complete(
            check_id=separated_check.check_id,
            assistance_level="none",
            assistance_detail=None,
            now=separated_due + timedelta(minutes=1),
        )

        after = {table: self._table_rows(table) for table in protected_tables}
        selected_after = self.service.select_next(self.due_at)
        with sqlite3.connect(self.service.db_path) as db:
            source_after = db.execute(
                "SELECT * FROM transfer_events WHERE event_id = ?",
                (separated_event.event_id,),
            ).fetchone()
            new_counts_after = {
                table: db.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                for table in (
                    "transfer_checks",
                    "transfer_check_predictions",
                    "transfer_check_completions",
                )
            }
        self.assertEqual(after, before)
        self.assertEqual(selected_after, selected_before)
        self.assertEqual(source_after, source_before)
        self.assertEqual(
            new_counts_after,
            {table: count + 1 for table, count in new_counts_before.items()},
        )

    def test_changed_item_after_check_creation_does_not_rebind_historical_evidence(
        self,
    ) -> None:
        self.item.path.write_text(
            self.item.path.read_text(encoding="utf-8") + "\nChanged later.\n",
            encoding="utf-8",
        )

        completion = self._complete(outcome="successful")

        with sqlite3.connect(self.service.db_path) as db:
            source_hash = db.execute(
                """SELECT e.item_content_hash
                   FROM transfer_check_completions AS completed
                   JOIN transfer_checks AS c ON c.check_id = completed.check_id
                   JOIN transfer_events AS e ON e.event_id = c.transfer_event_id
                   WHERE completed.check_id = ?""",
                (completion.check_id,),
            ).fetchone()[0]
        self.assertEqual(source_hash, self.item.content_hash)

    def test_started_check_remains_due_and_completed_check_is_excluded(self) -> None:
        pending_event = self._record_event("status", minute=21)
        pending_check = self._create_check(pending_event, "status")
        pending_due = self._timestamp(pending_event.delayed_check_due_at)

        due = self.service.list_due_transfer_checks(as_of=pending_due)
        pending = next(entry for entry in due if entry.check_id == pending_check.check_id)
        self.assertEqual(pending.status, "pending")

        prediction = self.service.begin_transfer_check(
            check_id=pending_check.check_id,
            pre_attempt_prediction="Status prediction.",
            now=pending_due,
        )
        due = self.service.list_due_transfer_checks(as_of=pending_due)
        started = next(entry for entry in due if entry.check_id == pending_check.check_id)
        self.assertEqual(started.status, "started")
        self.assertEqual(started.prediction_recorded_at, prediction.recorded_at)

        self._complete(
            check_id=pending_check.check_id,
            now=pending_due + timedelta(minutes=1),
        )
        self.assertNotIn(
            pending_check.check_id,
            [
                entry.check_id
                for entry in self.service.list_due_transfer_checks(
                    as_of=pending_due + timedelta(minutes=1)
                )
            ],
        )

    def test_open_rejects_stored_completion_before_prediction(self) -> None:
        self._complete()
        invalid_completed_at = self.due_at - timedelta(seconds=1)
        with sqlite3.connect(self.service.db_path) as db:
            trigger_sql = db.execute(
                """SELECT sql FROM sqlite_master
                   WHERE type = 'trigger'
                     AND name = 'transfer_check_completions_reject_update'"""
            ).fetchone()[0]
            db.execute("DROP TRIGGER transfer_check_completions_reject_update")
            db.execute(
                "UPDATE transfer_check_completions SET completed_at = ? WHERE check_id = ?",
                (invalid_completed_at.isoformat(), self.check.check_id),
            )
            db.execute(trigger_sql)

        with self.assertRaisesRegex(
            WorkspaceError, "stored transfer check completion predates its prediction"
        ):
            WorkspaceService.open(self.root)

        with sqlite3.connect(self.service.db_path) as db:
            stored = db.execute(
                """SELECT completed_at FROM transfer_check_completions
                   WHERE check_id = ?""",
                (self.check.check_id,),
            ).fetchone()[0]
        self.assertEqual(stored, invalid_completed_at.isoformat())

    def test_completion_cannot_precede_late_check_creation(self) -> None:
        event = self._record_event("late-completion", minute=23)
        due_at = self._timestamp(event.delayed_check_due_at)
        created_at = due_at + timedelta(days=1)
        check = self.service.create_transfer_check(
            transfer_event_id=event.event_id,
            context_kind="changed",
            context_description="A late-authored completion chronology check.",
            challenge_prompt="Complete the late-authored challenge.",
            acceptance_criteria="Meet the late-authored criterion.",
            scorer_kind="human",
            scorer_reference="late-completion-reviewer",
            now=created_at,
        )
        self.service.begin_transfer_check(
            check_id=check.check_id,
            pre_attempt_prediction="A valid prediction recorded when the check was authored.",
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
                """UPDATE transfer_check_predictions
                   SET recorded_at = ? WHERE check_id = ?""",
                (due_at.isoformat(), check.check_id),
            )
            db.execute(trigger_sql)

        with self.assertRaisesRegex(
            WorkspaceError, "completion timestamp cannot precede its check creation"
        ):
            self._complete(
                check_id=check.check_id,
                now=due_at + timedelta(minutes=1),
            )
        self.assertNotIn(
            check.check_id,
            [row[0] for row in self._table_rows("transfer_check_completions")],
        )

        with self.assertRaisesRegex(
            WorkspaceError, "prediction predates the check creation"
        ):
            self._complete(
                check_id=check.check_id,
                now=created_at + timedelta(minutes=1),
            )
        self.assertNotIn(
            check.check_id,
            [row[0] for row in self._table_rows("transfer_check_completions")],
        )

    def test_completion_validates_fields_and_timestamp_order_without_partial_write(
        self,
    ) -> None:
        invalid_cases = (
            ("check_id", "transfer-check-ABC", "transfer check id must match"),
            (
                "check_id",
                "transfer-check-" + "0" * 32,
                "no delayed transfer check with id",
            ),
            ("independent_attempt", "   ", "independent attempt"),
            ("acceptance_evidence", "bad\x1bevidence", "acceptance evidence"),
            ("teach_back", "   ", "teach-back"),
            ("outcome", "mastered", "outcome"),
            ("artifact_reference", "line one\nline two", "artifact reference"),
            ("artifact_reference", 42, "artifact reference"),
            ("artifact_reference", "x" * 2_049, "artifact reference"),
            ("now", datetime(2026, 8, 27, 9, 1), "timezone"),
        )
        for field, value, message in invalid_cases:
            with self.subTest(field=field), self.assertRaisesRegex(
                WorkspaceError, message
            ):
                self._complete(**{field: value})
            self.assertEqual(self._table_rows("transfer_check_completions"), [])

        later_event, later_check, later_due = self._started_check(
            "later-prediction", minute=22
        )
        later_prediction_time = later_due + timedelta(minutes=5)
        with sqlite3.connect(self.service.db_path) as db:
            trigger_sql = db.execute(
                """SELECT sql FROM sqlite_master
                   WHERE type = 'trigger'
                     AND name = 'transfer_check_predictions_reject_update'"""
            ).fetchone()[0]
            db.execute("DROP TRIGGER transfer_check_predictions_reject_update")
            db.execute(
                "UPDATE transfer_check_predictions SET recorded_at = ? WHERE check_id = ?",
                (later_prediction_time.isoformat(), later_check.check_id),
            )
            db.execute(trigger_sql)
        with self.assertRaisesRegex(WorkspaceError, "cannot precede|prediction"):
            self._complete(
                check_id=later_check.check_id,
                now=later_prediction_time - timedelta(seconds=1),
            )
        self.assertNotIn(
            later_check.check_id,
            [row[0] for row in self._table_rows("transfer_check_completions")],
        )
        self.assertEqual(later_event.item_content_hash, self.item.content_hash)


if __name__ == "__main__":
    unittest.main()
