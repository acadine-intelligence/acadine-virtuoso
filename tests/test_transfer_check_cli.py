from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from virtuoso.workspace import DelayedTransferCheck, TransferEvidence, WorkspaceService


class TransferCheckCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name).resolve() / "learner"
        self.service = WorkspaceService.init(self.workspace)
        self.item = self.service.add_item(
            item_id="testing-effect",
            title="Explain the testing effect",
            focus="learning-science",
            prompt="Why does retrieval strengthen memory?",
            answer="ANSWER-SECRET: retrieval changes memory.",
        )
        self.event = self.service.record_transfer(
            item_id=self.item.item_id,
            project_id="virtuoso-cli",
            use_case="SOURCE-CONTENT-SECRET: applied retrieval practice.",
            outcome="partial",
            independence="guided",
            artifact_reference="SOURCE-ARTIFACT-SECRET",
            reflection="SOURCE-REFLECTION-SECRET",
            occurred_at=datetime.now(timezone.utc) - timedelta(days=8),
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _run(
        self, *args: str, expected: int = 0
    ) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "virtuoso.cli",
                "--workspace",
                str(self.workspace),
                *args,
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            expected,
            msg=f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}",
        )
        return completed

    def _create_check_cli(self, event: TransferEvidence | None = None) -> dict[str, object]:
        source = event or self.event
        completed = self._run(
            "transfer",
            "check",
            "create",
            "--event",
            source.event_id,
            "--context-kind",
            "changed",
            "--context",
            "Same distinction in a changed research policy.",
            "--prompt",
            "Classify two artifacts and propose one refresh rule.",
            "--acceptance-criteria",
            "Classify both artifacts and state one testable rule.",
            "--scorer-kind",
            "human",
            "--scorer-reference",
            "reviewer-jonathan",
            "--json",
        )
        return json.loads(completed.stdout)

    def _create_check_domain(
        self, event: TransferEvidence, *, suffix: str
    ) -> DelayedTransferCheck:
        return self.service.create_transfer_check(
            transfer_event_id=event.event_id,
            context_kind="novel",
            context_description=f"Novel context {suffix}.",
            challenge_prompt=f"Challenge {suffix}.",
            acceptance_criteria=f"Criterion {suffix}.",
            scorer_kind="self",
            scorer_reference=f"rubric-{suffix}",
            now=datetime.now(timezone.utc),
        )

    def test_cli_transfer_check_create_due_begin_complete_journey(self) -> None:
        created = self._create_check_cli()
        self.assertEqual(
            set(created),
            {
                "acceptance_criteria",
                "challenge_prompt",
                "check_id",
                "claims_mastery",
                "context_description",
                "context_kind",
                "created_at",
                "due_at",
                "scorer_kind",
                "scorer_reference",
                "transfer_event_id",
            },
        )
        self.assertEqual(created["transfer_event_id"], self.event.event_id)
        self.assertEqual(created["due_at"], self.event.delayed_check_due_at)
        self.assertFalse(created["claims_mastery"])
        check_id = str(created["check_id"])

        due = json.loads(
            self._run(
                "transfer",
                "check",
                "due",
                "--as-of",
                self.event.delayed_check_due_at,
                "--json",
            ).stdout
        )
        self.assertEqual(due["as_of"], self.event.delayed_check_due_at)
        self.assertEqual([entry["check_id"] for entry in due["checks"]], [check_id])
        pending = due["checks"][0]
        self.assertEqual(pending["status"], "pending")
        self.assertEqual(pending["item_content_hash"], self.item.content_hash)
        self.assertEqual(pending["source_outcome"], "partial")
        self.assertEqual(pending["source_independence"], "guided")
        self.assertIsNone(pending["prediction_recorded_at"])
        self.assertFalse(pending["claims_mastery"])

        begun = json.loads(
            self._run(
                "transfer",
                "check",
                "begin",
                "--check",
                check_id,
                "--prediction",
                "I expect the distinction to transfer, but cadence may be weak.",
                "--json",
            ).stdout
        )
        self.assertEqual(begun["check_id"], check_id)
        self.assertEqual(
            begun["pre_attempt_prediction"],
            "I expect the distinction to transfer, but cadence may be weak.",
        )
        self.assertFalse(begun["claims_mastery"])
        prediction_at = datetime.fromisoformat(begun["recorded_at"])
        self.assertIsNotNone(prediction_at.utcoffset())

        started = json.loads(
            self._run("transfer", "check", "due", "--json").stdout
        )["checks"][0]
        self.assertEqual(started["status"], "started")
        self.assertEqual(started["prediction_recorded_at"], begun["recorded_at"])

        completed = json.loads(
            self._run(
                "transfer",
                "check",
                "complete",
                "--check",
                check_id,
                "--attempt",
                "My independent classification and cadence rule.",
                "--assistance",
                "none",
                "--acceptance-evidence",
                "The configured criteria were met.",
                "--teach-back",
                "Retrievability stayed separate from project urgency.",
                "--outcome",
                "successful",
                "--artifact",
                "git:abc123",
                "--json",
            ).stdout
        )
        self.assertEqual(completed["check_id"], check_id)
        self.assertEqual(completed["assistance_level"], "none")
        self.assertIsNone(completed["assistance_detail"])
        self.assertEqual(completed["scorer_kind"], "human")
        self.assertEqual(completed["scorer_reference"], "reviewer-jonathan")
        self.assertEqual(
            completed["acceptance_criteria"],
            "Classify both artifacts and state one testable rule.",
        )
        self.assertEqual(completed["artifact_reference"], "git:abc123")
        self.assertEqual(completed["prediction_recorded_at"], begun["recorded_at"])
        self.assertGreaterEqual(
            datetime.fromisoformat(completed["completed_at"]), prediction_at
        )
        self.assertFalse(completed["claims_mastery"])
        self.assertEqual(
            json.loads(
                self._run("transfer", "check", "due", "--json").stdout
            )["checks"],
            [],
        )

    def test_cli_empty_due_is_success_and_invalid_state_is_plain_exit_two(self) -> None:
        future_event = self.service.record_transfer(
            item_id=self.item.item_id,
            project_id="future-project",
            use_case="Synthetic future transfer.",
            outcome="successful",
            independence="independent",
            occurred_at=datetime.now(timezone.utc),
        )
        future_check = self._create_check_domain(future_event, suffix="future")

        empty_json = json.loads(
            self._run(
                "transfer",
                "check",
                "due",
                "--as-of",
                datetime.now(timezone.utc).isoformat(),
                "--json",
            ).stdout
        )
        self.assertEqual(empty_json["checks"], [])
        empty_human = self._run(
            "transfer",
            "check",
            "due",
            "--as-of",
            datetime.now(timezone.utc).isoformat(),
        )
        self.assertEqual(empty_human.stdout, "No delayed transfer checks are due.\n")
        self.assertEqual(empty_human.stderr, "")

        early = self._run(
            "transfer",
            "check",
            "begin",
            "--check",
            future_check.check_id,
            "--prediction",
            "Too early.",
            "--json",
            expected=2,
        )
        self.assertEqual(early.stdout, "")
        self.assertIn("not due until", early.stderr)
        self.assertNotIn("Traceback", early.stderr)

        past_check = self._create_check_domain(self.event, suffix="unstarted")
        missing_prediction = self._run(
            "transfer",
            "check",
            "complete",
            "--check",
            past_check.check_id,
            "--attempt",
            "Independent attempt.",
            "--assistance",
            "none",
            "--acceptance-evidence",
            "Evidence.",
            "--teach-back",
            "Teach-back.",
            "--outcome",
            "partial",
            "--json",
            expected=2,
        )
        self.assertEqual(missing_prediction.stdout, "")
        self.assertIn("pre-attempt prediction", missing_prediction.stderr)
        self.assertNotIn("Traceback", missing_prediction.stderr)

        naive_as_of = self._run(
            "transfer",
            "check",
            "due",
            "--as-of",
            "2026-08-27T09:00:00",
            "--json",
            expected=2,
        )
        self.assertEqual(naive_as_of.stdout, "")
        self.assertIn("timezone", naive_as_of.stderr)
        self.assertNotIn("Traceback", naive_as_of.stderr)

    def test_transfer_check_json_excludes_answer_workspace_path_and_source_content(
        self,
    ) -> None:
        created = self._create_check_cli()
        due = self._run("transfer", "check", "due", "--json").stdout
        disclosed = json.dumps(created, sort_keys=True) + due

        self.assertNotIn("ANSWER-SECRET", disclosed)
        self.assertNotIn("SOURCE-CONTENT-SECRET", disclosed)
        self.assertNotIn("SOURCE-ARTIFACT-SECRET", disclosed)
        self.assertNotIn("SOURCE-REFLECTION-SECRET", disclosed)
        self.assertNotIn(str(self.workspace), disclosed)
        self.assertNotIn(str(self.item.path), disclosed)
        self.assertIn(self.item.content_hash, disclosed)

    def test_begin_help_and_human_completion_preserve_learning_boundary(self) -> None:
        help_text = self._run("transfer", "check", "begin", "--help").stdout.lower()
        self.assertIn("before attempting", help_text)
        self.assertIn("requesting help", help_text)

        second_event = self.service.record_transfer(
            item_id=self.item.item_id,
            project_id="human-output",
            use_case="Synthetic past transfer for human output.",
            outcome="unsuccessful",
            independence="agent-produced",
            occurred_at=datetime.now(timezone.utc) - timedelta(days=8),
        )
        second_check = self._create_check_domain(second_event, suffix="human")
        due_at = datetime.fromisoformat(second_event.delayed_check_due_at)
        self.service.begin_transfer_check(
            check_id=second_check.check_id,
            pre_attempt_prediction="Human-output prediction.",
            now=due_at,
        )

        human = self._run(
            "transfer",
            "check",
            "complete",
            "--check",
            second_check.check_id,
            "--attempt",
            "Independent human-output attempt.",
            "--assistance",
            "substantial",
            "--assistance-detail",
            "An agent helped after the attempt.",
            "--acceptance-evidence",
            "The self rubric was applied.",
            "--teach-back",
            "Assisted success is not independent capability.",
            "--outcome",
            "successful",
        )
        self.assertTrue(
            human.stdout.endswith(
                "Delayed transfer evidence recorded. No capability or mastery state changed.\n"
            )
        )
        self.assertEqual(human.stderr, "")


if __name__ == "__main__":
    unittest.main()
