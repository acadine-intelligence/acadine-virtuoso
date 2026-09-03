from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any


class LearningCliJourneyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name).resolve() / "learner"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _run(
        self, *args: str, input_text: str | None = None, expected: int = 0
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
            input=input_text,
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

    def _init_and_add(self) -> dict[str, Any]:
        self._run("init", "--json")
        return json.loads(
            self._run(
                "add",
                "--id",
                "gradient-descent",
                "--title",
                "Understand gradient descent",
                "--focus",
                "machine-learning",
                "--entry-mode",
                "learn-first",
                "--learning-unit",
                "Gradient descent follows the negative gradient to reduce an objective.",
                "--prompt",
                "Which direction does gradient descent follow?",
                "--answer",
                "The negative gradient of the objective.",
                "--json",
            ).stdout
        )

    def test_clean_learn_then_practice_journey_separates_evidence(self) -> None:
        added = self._init_and_add()
        self.assertEqual(added["entry_mode"], "learn-first")
        self.assertEqual(len(added["learning_unit_hash"]), 64)

        selected = json.loads(self._run("next", "--json").stdout)
        self.assertEqual(selected["schema"], "virtuoso/next-action@0.1")
        self.assertEqual(selected["action"], "learn")
        self.assertEqual(selected["item_id"], "gradient-descent")
        self.assertIsNone(selected["prompt"])
        self.assertEqual(selected["item_content_hash"], added["content_hash"])
        self.assertEqual(selected["learning_unit_hash"], added["learning_unit_hash"])
        self.assertNotIn("answer", selected)
        self.assertNotIn("negative gradient", json.dumps(selected).lower())

        stopped = self._run(
            "learn", "--item", "gradient-descent", input_text="stop\n"
        )
        self.assertIn("No study event recorded", stopped.stdout)
        empty = json.loads(self._run("attempts", "--json").stdout)
        self.assertEqual(empty["study_events"], [])
        self.assertEqual(empty["attempts"], [])
        self.assertEqual(empty["proposals"], [])

        learned = self._run(
            "learn", "--item", "gradient-descent", input_text="finish\n"
        )
        self.assertIn(
            "Gradient descent follows the negative gradient to reduce an objective.",
            learned.stdout,
        )
        self.assertNotIn("Which direction", learned.stdout)
        self.assertNotIn("The negative gradient of the objective", learned.stdout)
        self.assertNotIn("Initial recall time", learned.stdout)
        self.assertIn("Study completion recorded", learned.stdout)

        evidence = json.loads(self._run("attempts", "--json").stdout)
        self.assertEqual(len(evidence["study_events"]), 1)
        self.assertEqual(evidence["attempts"], [])
        self.assertEqual(evidence["proposals"], [])

        ready = json.loads(self._run("next", "--json").stdout)
        self.assertEqual(ready["action"], "practice")
        self.assertEqual(
            ready["prompt"], "Which direction does gradient descent follow?"
        )
        self.assertNotIn("answer", ready)

        practised = json.loads(
            self._run(
                "practice",
                "--item",
                "gradient-descent",
                "--administer",
                "--response",
                "It follows the negative gradient.",
                "--result",
                "demonstrated",
                "--confidence",
                "4",
                "--agent-help",
                "none",
                "--json",
            ).stdout
        )
        self.assertEqual(practised["item_id"], "gradient-descent")
        evidence = json.loads(self._run("attempts", "--json").stdout)
        self.assertEqual(len(evidence["study_events"]), 1)
        self.assertEqual(len(evidence["attempts"]), 1)
        self.assertEqual(len(evidence["proposals"]), 1)

        learning = json.loads(
            self._run("queries", "learning", "--json").stdout
        )
        self.assertEqual(learning["schema"], "virtuoso/learning-state@0.1")
        self.assertEqual(learning["items"][0]["action"], "practice")
        doctor = json.loads(self._run("doctor", "--json").stdout)
        self.assertEqual(doctor["study_events"], 1)
        self.assertEqual(doctor["learning"]["waiting_for_learning"], 0)

    def test_pending_learn_first_item_cannot_enter_practice_or_review(self) -> None:
        added = self._init_and_add()

        interactive = self._run(
            "practice",
            "--item",
            "gradient-descent",
            input_text="",
            expected=2,
        )
        self.assertEqual(interactive.stdout, "")
        self.assertIn("requires learning", interactive.stderr)

        blocked = self._run(
            "practice",
            "--item",
            "gradient-descent",
            "--administer",
            "--response",
            "Guess",
            "--result",
            "partial",
            "--confidence",
            "2",
            "--json",
            expected=2,
        )
        self.assertEqual(blocked.stdout, "")
        self.assertIn("requires learning", blocked.stderr)
        self.assertNotIn("Traceback", blocked.stderr)

        queue = json.loads(self._run("review", "due", "--json").stdout)
        self.assertEqual(queue["items"], [])

        blocked_load = self._run(
            "review",
            "load",
            "--item",
            "gradient-descent",
            "--json",
            expected=2,
        )
        self.assertIn("requires learning", blocked_load.stderr)

        review_attempt = {
            "schema": "virtuoso/review-attempt@0.1",
            "submission_id": "0123456789abcdef0123456789abcdef",
            "item_id": "gradient-descent",
            "item_content_hash": added["content_hash"],
            "started_at": "2026-09-03T12:00:00+00:00",
            "initial_answered_at": "2026-09-03T12:00:01+00:00",
            "completed_at": "2026-09-03T12:00:02+00:00",
            "initial_response": "A guess",
            "retry": None,
            "hint_used": False,
            "answer_revealed": True,
            "result": "partial",
            "confidence": 2,
            "open_notes": False,
        }
        blocked_record = self._run(
            "review",
            "record",
            "--json",
            input_text=json.dumps(review_attempt),
            expected=2,
        )
        self.assertIn("requires learning", blocked_record.stderr)

        review_skip = {
            "schema": "virtuoso/review-skip@0.1",
            "submission_id": "11111111111111111111111111111111",
            "item_id": "gradient-descent",
            "item_content_hash": added["content_hash"],
            "occurred_at": "2026-09-03T12:00:00+00:00",
            "surface": "obsidian-plugin",
        }
        blocked_skip = self._run(
            "review",
            "skip",
            "--json",
            input_text=json.dumps(review_skip),
            expected=2,
        )
        self.assertIn("requires learning", blocked_skip.stderr)

        evidence = json.loads(self._run("attempts", "--json").stdout)
        self.assertEqual(evidence["attempts"], [])
        self.assertEqual(evidence["proposals"], [])
        self.assertEqual(evidence["skips"], [])
        self.assertEqual(evidence["study_events"], [])

    def test_eof_returns_clean_error_and_writes_no_event(self) -> None:
        self._init_and_add()

        failed = self._run(
            "learn", "--item", "gradient-descent", input_text="", expected=2
        )
        self.assertEqual(failed.stdout.splitlines()[0], "Learning: Understand gradient descent")
        self.assertIn("stopped before completion", failed.stderr)
        self.assertNotIn("Traceback", failed.stderr)
        evidence = json.loads(self._run("attempts", "--json").stdout)
        self.assertEqual(evidence["study_events"], [])

    def test_invalid_add_contract_writes_no_file_or_row(self) -> None:
        self._run("init", "--json")
        failed = self._run(
            "add",
            "--id",
            "missing-unit",
            "--title",
            "Missing unit",
            "--focus",
            "validation",
            "--entry-mode",
            "learn-first",
            "--prompt",
            "Prompt",
            "--answer",
            "Answer",
            "--json",
            expected=2,
        )
        self.assertEqual(failed.stdout, "")
        self.assertIn("learning unit", failed.stderr.lower())
        self.assertFalse((self.workspace / "items" / "missing-unit.md").exists())
        doctor = json.loads(self._run("doctor", "--json").stdout)
        self.assertEqual(doctor["items"], 0)


if __name__ == "__main__":
    unittest.main()
