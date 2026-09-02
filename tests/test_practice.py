from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from virtuoso.practice import AttemptRecord, PracticeError, PracticeService
from virtuoso.workspace import WorkspaceError, WorkspaceService


class ScriptedIO:
    def __init__(self, answers: list[str]) -> None:
        self.answers = iter(answers)
        self.events: list[tuple[str, str]] = []

    def write(self, text: str) -> None:
        self.events.append(("write", text))

    def ask(self, prompt: str) -> str:
        self.events.append(("ask", prompt))
        return next(self.answers)

    def text_before(self, event_index: int) -> str:
        return "\n".join(value for kind, value in self.events[:event_index] if kind == "write")

    def rendered(self) -> str:
        return "\n".join(value for kind, value in self.events if kind == "write")


class FakeClock:
    def __init__(self, values: list[float]) -> None:
        self.values = iter(values)

    def monotonic(self) -> float:
        return next(self.values)


class PracticeServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve() / "learner"
        self.workspace = WorkspaceService.init(self.root)
        self.workspace.add_item(
            item_id="testing-effect",
            title="Explain the testing effect",
            focus="learning-science",
            prompt="Why does retrieval strengthen later recall?",
            answer="Retrieval changes memory and improves later access.",
            hint="Contrast effortful retrieval with rereading.",
            follow_up="Give one coding-project example.",
        )
        self.now = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_successful_attempt_hides_answer_then_records_fsrs_proposal(self) -> None:
        io = ScriptedIO(
            [
                "n",
                "Retrieval exercises and strengthens access paths.",
                "reveal",
                "demonstrated",
                "4",
            ]
        )
        result = PracticeService(
            self.workspace, clock=FakeClock([100.0, 101.25, 102.0])
        ).run(
            item_id="testing-effect",
            io=io,
            now=self.now,
            agent_help="none",
        )

        recall_ask = next(
            i
            for i, event in enumerate(io.events)
            if event == ("ask", "Your recall: ")
        )
        before_recall = io.text_before(recall_ask)
        self.assertIn("Why does retrieval strengthen later recall?", before_recall)
        self.assertNotIn("Retrieval changes memory", before_recall)
        self.assertNotIn("Contrast effortful retrieval", before_recall)

        rendered = io.rendered()
        self.assertIn("Answer\nRetrieval changes memory", rendered)
        self.assertIn("Initial recall time: 1250 ms", rendered)
        self.assertNotIn("master", rendered.lower())

        self.assertEqual(result.attempt.result, "demonstrated")
        self.assertEqual(result.attempt.initial_latency_ms, 1250)
        self.assertEqual(result.attempt.confidence, 4)
        self.assertFalse(result.attempt.open_notes)
        self.assertEqual(
            [action.kind for action in result.attempt.support_actions],
            ["worked-feedback"],
        )
        self.assertEqual(result.proposal.algorithm, "fsrs")
        self.assertEqual(result.proposal.algorithm_version, "6.3.2")
        self.assertEqual(result.proposal.learning_context, "atomic-recall")
        self.assertEqual(result.proposal.source_event_id, result.attempt.event_id)
        self.assertGreater(result.proposal.due_at, self.now)
        self.assertIn("desired_retention", result.proposal.configuration)

        with sqlite3.connect(self.workspace.db_path) as db:
            attempt = db.execute(
                "SELECT result, initial_latency_ms, agent_help FROM attempts"
            ).fetchone()
            proposal = db.execute(
                "SELECT algorithm, algorithm_version, learning_context, source_event_id "
                "FROM scheduler_proposals"
            ).fetchone()
            state = db.execute(
                "SELECT algorithm, algorithm_version, source_event_id FROM scheduler_state"
            ).fetchone()
        self.assertEqual(attempt, ("demonstrated", 1250, "none"))
        self.assertEqual(proposal, ("fsrs", "6.3.2", "atomic-recall", result.attempt.event_id))
        self.assertEqual(state, ("fsrs", "6.3.2", result.attempt.event_id))

    def test_failed_attempt_records_retry_hint_and_follow_up_without_capability_claim(self) -> None:
        io = ScriptedIO(
            [
                "n",
                "I do not know.",
                "retry",
                "It may be effort.",
                "hint",
                "Retrieval is effortful.",
                "not-demonstrated",
                "1",
                "A smaller response.",
            ]
        )
        result = PracticeService(
            self.workspace,
            clock=FakeClock([0.0, 1.0, 2.0, 4.0, 5.0, 8.0, 9.0, 10.5, 11.0]),
        ).run(
            item_id="testing-effect",
            io=io,
            now=self.now,
            agent_help="light",
        )

        self.assertEqual(
            tuple(action.kind for action in result.attempt.support_actions),
            ("retry-unaided", "hint", "worked-feedback", "follow-up"),
        )
        self.assertEqual(result.attempt.support_actions[-1].response, "A smaller response.")
        self.assertEqual(result.attempt.support_actions[-1].latency_ms, 1500)
        self.assertEqual(result.attempt.result, "not-demonstrated")
        self.assertEqual(result.attempt.initial_latency_ms, 1000)
        self.assertEqual(result.attempt.agent_help, "light")
        self.assertIn("Hint\nContrast effortful retrieval", io.rendered())
        self.assertIn("Follow-up challenge\nGive one coding-project example.", io.rendered())
        self.assertIn("Evidence: not demonstrated", io.rendered())
        self.assertNotIn("master", io.rendered().lower())

        attempts = self.workspace.list_attempts()
        self.assertEqual(len(attempts), 1)
        stored_support = json.loads(attempts[0]["support_json"])
        self.assertEqual(
            [entry["kind"] for entry in stored_support],
            ["retry-unaided", "hint", "worked-feedback", "follow-up"],
        )

    def test_persists_actual_attempt_timing_reveal_and_follow_up_attribution(self) -> None:
        io = ScriptedIO(
            [
                "n",
                "initial response",
                "reveal",
                "partial",
                "3",
                "smaller follow-up response",
            ]
        )
        result = PracticeService(
            self.workspace,
            clock=FakeClock([10.0, 11.25, 12.0, 13.5, 14.0]),
        ).run(
            item_id="testing-effect",
            io=io,
            now=self.now,
            agent_help="none",
        )

        with sqlite3.connect(self.workspace.db_path) as db:
            timing = db.execute(
                "SELECT started_at, completed_at FROM attempt_timings "
                "WHERE event_id = ?",
                (result.attempt.event_id,),
            ).fetchone()
        stored = self.workspace.list_attempts()[0]
        observed = {
            "support": [
                (action.kind, action.response, action.latency_ms)
                for action in result.attempt.support_actions
            ],
            "timing": timing,
            "listed_started_at": stored.get("started_at"),
            "listed_completed_at": stored.get("completed_at"),
            "occurred_at": result.attempt.occurred_at.isoformat(),
        }
        expected_started = self.now.isoformat()
        expected_completed = (self.now + timedelta(seconds=4)).isoformat()
        self.assertEqual(
            observed,
            {
                "support": [
                    ("worked-feedback", None, None),
                    ("follow-up", "smaller follow-up response", 1500),
                ],
                "timing": (expected_started, expected_completed),
                "listed_started_at": expected_started,
                "listed_completed_at": expected_completed,
                "occurred_at": expected_completed,
            },
        )

    def test_changed_markdown_fails_stale_instead_of_recording_against_wrong_prompt(self) -> None:
        item_path = self.root / "items" / "testing-effect.md"
        item_path.write_text(item_path.read_text() + "\nLearner edit.\n")
        io = ScriptedIO([])

        with self.assertRaisesRegex(PracticeError, "stale"):
            PracticeService(self.workspace, clock=FakeClock([])).run(
                item_id="testing-effect", io=io, now=self.now
            )
        self.assertEqual(self.workspace.list_attempts(), [])

    def test_blank_unaided_recall_cannot_be_recorded_as_demonstrated(self) -> None:
        io = ScriptedIO(["n", "", "reveal", "demonstrated", "4"])
        with self.assertRaisesRegex(PracticeError, "blank recall"):
            PracticeService(
                self.workspace, clock=FakeClock([100.0, 101.0])
            ).run(
                item_id="testing-effect",
                io=io,
                now=self.now,
                agent_help="none",
            )
        self.assertEqual(self.workspace.list_attempts(), [])

    def test_invalid_scheduler_configuration_fails_actionably(self) -> None:
        config = self.workspace.configuration()
        config["scheduler"]["desired_retention"] = "very high"
        self.workspace.config_path.write_text(json.dumps(config))
        io = ScriptedIO(
            ["n", "answer", "reveal", "partial", "3", "follow-up response"]
        )
        with self.assertRaisesRegex(PracticeError, "desired_retention"):
            PracticeService(
                self.workspace, clock=FakeClock([0.0, 1.0, 2.0, 3.0, 4.0])
            ).run(item_id="testing-effect", io=io, now=self.now)

    def test_stale_concurrent_scheduler_update_is_rejected_atomically(self) -> None:
        item = self.workspace.load_item("testing-effect")
        service = PracticeService(self.workspace)
        attempts = [
            AttemptRecord(
                event_id=f"attempt-concurrent-{index}",
                item_id=item.item_id,
                item_content_hash=item.content_hash,
                occurred_at=self.now,
                started_at=self.now - timedelta(seconds=1),
                completed_at=self.now,
                initial_response="retrieval changes memory",
                initial_latency_ms=1000,
                result="demonstrated",
                confidence=4,
                open_notes=False,
                agent_help="none",
                support_actions=(),
            )
            for index in (1, 2)
        ]
        proposals = [service._schedule(item=item, attempt=value) for value in attempts]

        service._persist(attempt=attempts[0], proposal=proposals[0])
        with self.assertRaisesRegex(PracticeError, "scheduler state changed"):
            service._persist(attempt=attempts[1], proposal=proposals[1])
        self.assertEqual(len(self.workspace.list_attempts()), 1)

    def test_cross_item_attempt_and_scheduler_transition_is_rejected_atomically(self) -> None:
        other = self.workspace.add_item(
            item_id="other-item",
            title="Other item",
            focus="integrity",
            prompt="Which item owns this transition?",
            answer="Only the attempted item.",
        )
        attempted = self.workspace.load_item("testing-effect")
        attempt = AttemptRecord(
            event_id="attempt-cross-item",
            item_id=attempted.item_id,
            item_content_hash=attempted.content_hash,
            occurred_at=self.now,
            started_at=self.now - timedelta(seconds=1),
            completed_at=self.now,
            initial_response="synthetic response",
            initial_latency_ms=100,
            result="partial",
            confidence=3,
            open_notes=False,
            agent_help="none",
            support_actions=(),
        )
        service = PracticeService(self.workspace)
        proposal = service._schedule(
            item=self.workspace.load_item(other.item_id),
            attempt=attempt,
        )

        with self.assertRaisesRegex(PracticeError, "attempt and scheduler item identity"):
            service._persist(attempt=attempt, proposal=proposal)
        self.assertEqual(self.workspace.list_attempts(), [])
        self.assertEqual(self.workspace.list_proposals(), [])

    def test_malformed_due_timestamp_is_rejected_before_any_transition_is_written(self) -> None:
        item = self.workspace.load_item("testing-effect")
        attempt = AttemptRecord(
            event_id="attempt-malformed-due",
            item_id=item.item_id,
            item_content_hash=item.content_hash,
            occurred_at=self.now,
            started_at=self.now - timedelta(seconds=1),
            completed_at=self.now,
            initial_response="synthetic response",
            initial_latency_ms=100,
            result="partial",
            confidence=3,
            open_notes=False,
            agent_help="none",
            support_actions=(),
        )
        proposal = PracticeService(self.workspace)._schedule(item=item, attempt=attempt)
        attempt_payload = {
            "event_id": attempt.event_id,
            "item_id": attempt.item_id,
            "item_content_hash": attempt.item_content_hash,
            "occurred_at": attempt.occurred_at.isoformat(),
            "started_at": attempt.started_at.isoformat(),
            "completed_at": attempt.completed_at.isoformat(),
            "initial_response": attempt.initial_response,
            "initial_latency_ms": attempt.initial_latency_ms,
            "result": attempt.result,
            "confidence": attempt.confidence,
            "open_notes": attempt.open_notes,
            "agent_help": attempt.agent_help,
            "support_actions": [asdict(action) for action in attempt.support_actions],
            "administered": attempt.administered,
        }
        proposal_payload = {
            "proposal_id": proposal.proposal_id,
            "source_event_id": proposal.source_event_id,
            "item_id": proposal.item_id,
            "algorithm": proposal.algorithm,
            "algorithm_version": proposal.algorithm_version,
            "learning_context": proposal.learning_context,
            "configuration": proposal.configuration,
            "previous_state_json": proposal.previous_state_json,
            "previous_source_event_id": proposal.previous_source_event_id,
            "due_at": "not-a-date",
            "rationale": proposal.rationale,
            "created_at": proposal.created_at.isoformat(),
        }

        with self.assertRaisesRegex(WorkspaceError, "scheduler due timestamp"):
            self.workspace.record_attempt(
                attempt=attempt_payload,
                proposal=proposal_payload,
                state_json=proposal.proposed_state_json,
            )
        self.assertEqual(self.workspace.list_attempts(), [])
        self.assertEqual(self.workspace.list_proposals(), [])

    def test_incompatible_stored_fsrs_version_and_configuration_fail_closed(self) -> None:
        PracticeService(self.workspace, clock=FakeClock([0.0, 1.0, 2.0])).run(
            item_id="testing-effect",
            io=ScriptedIO(["n", "answer", "reveal", "demonstrated", "4"]),
            now=self.now,
        )
        later = self.now + timedelta(days=1)

        with sqlite3.connect(self.workspace.db_path) as db:
            db.execute("UPDATE scheduler_state SET algorithm_version = '0.0.0'")
        with self.assertRaisesRegex(PracticeError, "incompatible.*version"):
            PracticeService(
                self.workspace, clock=FakeClock([0.0, 1.0, 2.0, 3.0, 4.0])
            ).run(
                item_id="testing-effect",
                io=ScriptedIO(
                    [
                        "n",
                        "answer",
                        "reveal",
                        "partial",
                        "3",
                        "follow-up",
                    ]
                ),
                now=later,
            )

        with sqlite3.connect(self.workspace.db_path) as db:
            db.execute(
                "UPDATE scheduler_state SET algorithm_version = '6.3.2', "
                "configuration_json = ?",
                (json.dumps({"desired_retention": 0.1, "enable_fuzzing": False}),),
            )
        with self.assertRaisesRegex(PracticeError, "incompatible.*configuration"):
            PracticeService(
                self.workspace, clock=FakeClock([0.0, 1.0, 2.0, 3.0, 4.0])
            ).run(
                item_id="testing-effect",
                io=ScriptedIO(
                    [
                        "n",
                        "answer",
                        "reveal",
                        "partial",
                        "3",
                        "follow-up",
                    ]
                ),
                now=later,
            )
        self.assertEqual(len(self.workspace.list_attempts()), 1)


class AdministeredPracticeTests(unittest.TestCase):
    """`run_administered`: agent-mediated attempts with honest attribution.

    The learner answered out-of-band (chat, voice); an agent transcribes the
    answer and grade. Latency was not measured by Virtuoso, so it is stored
    as NULL/unknown — never 0 ms and never fabricated.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve() / "learner"
        self.workspace = WorkspaceService.init(self.root)
        self.workspace.add_item(
            item_id="testing-effect",
            title="Explain the testing effect",
            focus="learning-science",
            prompt="Why does retrieval strengthen later recall?",
            answer="Retrieval changes memory and improves later access.",
            hint="Contrast effortful retrieval with rereading.",
            follow_up="Give one coding-project example.",
        )
        self.now = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_administered_attempt_records_null_latency_and_marker(self) -> None:
        result = PracticeService(self.workspace).run_administered(
            item_id="testing-effect",
            response="Retrieval strengthens access paths.",
            result="demonstrated",
            confidence=4,
            now=self.now,
        )

        self.assertIsNone(result.attempt.initial_latency_ms)
        self.assertTrue(result.attempt.administered)
        self.assertEqual(result.attempt.agent_help, "substantial")
        self.assertEqual(result.attempt.result, "demonstrated")
        self.assertEqual(result.attempt.confidence, 4)
        self.assertFalse(result.attempt.open_notes)
        self.assertEqual(result.attempt.support_actions, ())
        self.assertEqual(result.proposal.algorithm, "fsrs")
        self.assertEqual(result.proposal.source_event_id, result.attempt.event_id)
        self.assertGreater(result.proposal.due_at, self.now)
        self.assertNotIn("0 ms", result.proposal.rationale)
        self.assertIn("unmeasured", result.proposal.rationale)

        with sqlite3.connect(self.workspace.db_path) as db:
            row = db.execute(
                "SELECT initial_latency_ms, administered, agent_help, result"
                " FROM attempts"
            ).fetchone()
            timing = db.execute("SELECT COUNT(*) FROM attempt_timings").fetchone()[0]
            state = db.execute(
                "SELECT source_event_id FROM scheduler_state"
            ).fetchone()
        self.assertEqual(row, (None, 1, "substantial", "demonstrated"))
        self.assertEqual(timing, 0)
        self.assertEqual(state, (result.attempt.event_id,))

    def test_administered_attempt_is_distinguishable_from_interactive(self) -> None:
        PracticeService(self.workspace).run_administered(
            item_id="testing-effect",
            response="An administered answer.",
            result="partial",
            confidence=2,
            now=self.now,
        )
        PracticeService(
            self.workspace, clock=FakeClock([100.0, 101.25, 102.0])
        ).run(
            item_id="testing-effect",
            io=ScriptedIO(
                ["n", "A direct interactive answer.", "reveal", "demonstrated", "4"]
            ),
            now=self.now + timedelta(hours=1),
            agent_help="none",
        )

        attempts = self.workspace.list_attempts()
        self.assertEqual(len(attempts), 2)
        by_marker = {bool(attempt["administered"]): attempt for attempt in attempts}
        self.assertEqual(by_marker[True]["initial_latency_ms"], None)
        self.assertEqual(by_marker[True]["agent_help"], "substantial")
        self.assertEqual(by_marker[False]["initial_latency_ms"], 1250)
        self.assertEqual(by_marker[False]["agent_help"], "none")
        self.assertIsNone(by_marker[True]["started_at"])
        self.assertIsNone(by_marker[True]["completed_at"])
        self.assertIsNotNone(by_marker[False]["started_at"])

    def test_administered_agent_help_override_is_recorded(self) -> None:
        result = PracticeService(self.workspace).run_administered(
            item_id="testing-effect",
            response="Answered with only a nudge.",
            result="demonstrated",
            confidence=3,
            agent_help="light",
            now=self.now,
        )
        self.assertEqual(result.attempt.agent_help, "light")

    def test_administered_rejects_blank_response_for_demonstrated(self) -> None:
        with self.assertRaisesRegex(PracticeError, "blank recall"):
            PracticeService(self.workspace).run_administered(
                item_id="testing-effect",
                response="   ",
                result="demonstrated",
                confidence=4,
                now=self.now,
            )
        self.assertEqual(self.workspace.list_attempts(), [])

    def test_administered_validates_result_confidence_and_help(self) -> None:
        cases = (
            ({"result": "aced-it"}, "result"),
            ({"confidence": 0}, "confidence"),
            ({"confidence": 6}, "confidence"),
            ({"agent_help": "generous"}, "agent_help"),
        )
        base = {
            "item_id": "testing-effect",
            "response": "some answer",
            "result": "partial",
            "confidence": 3,
            "now": self.now,
        }
        for overrides, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(PracticeError, message):
                    PracticeService(self.workspace).run_administered(
                        **{**base, **overrides}
                    )
        self.assertEqual(self.workspace.list_attempts(), [])

    def test_administered_requires_timezone_aware_timestamp(self) -> None:
        with self.assertRaisesRegex(PracticeError, "timezone-aware"):
            PracticeService(self.workspace).run_administered(
                item_id="testing-effect",
                response="answer",
                result="partial",
                confidence=3,
                now=datetime(2026, 8, 19, 12, 0),
            )

    def test_doctor_stays_healthy_with_administered_attempts(self) -> None:
        PracticeService(self.workspace).run_administered(
            item_id="testing-effect",
            response="Administered answer.",
            result="demonstrated",
            confidence=4,
            now=self.now,
        )
        doctor = self.workspace.doctor()
        self.assertEqual(doctor["status"], "healthy")
        self.assertEqual(doctor["attempts"], 1)


if __name__ == "__main__":
    unittest.main()
