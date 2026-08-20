from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from virtuoso.practice import AttemptRecord, PracticeError, PracticeService
from virtuoso.workspace import WorkspaceService


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
        self.root = Path(self.tmp.name) / "learner"
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
            self.workspace, clock=FakeClock([100.0, 101.25])
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
        self.assertEqual(result.attempt.support_actions, ())
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
            ]
        )
        result = PracticeService(
            self.workspace,
            clock=FakeClock([0.0, 1.0, 2.0, 4.0, 5.0, 8.0]),
        ).run(
            item_id="testing-effect",
            io=io,
            now=self.now,
            agent_help="light",
        )

        self.assertEqual(
            tuple(action.kind for action in result.attempt.support_actions),
            ("retry-unaided", "hint", "follow-up-offered"),
        )
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
            ["retry-unaided", "hint", "follow-up-offered"],
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
        io = ScriptedIO(["n", "answer", "reveal", "partial", "3"])
        with self.assertRaisesRegex(PracticeError, "desired_retention"):
            PracticeService(
                self.workspace, clock=FakeClock([0.0, 1.0])
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


if __name__ == "__main__":
    unittest.main()
