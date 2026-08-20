from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class CliJourneyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name) / "learner"

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

    def test_clean_cli_journey_records_attributable_attempt_and_health(self) -> None:
        initialized = json.loads(self._run("init", "--json").stdout)
        self.assertEqual(initialized["status"], "initialized")

        added = json.loads(
            self._run(
                "add",
                "--id",
                "testing-effect",
                "--title",
                "Explain the testing effect",
                "--focus",
                "learning-science",
                "--prompt",
                "Why does active recall improve memory?",
                "--answer",
                "Retrieval changes memory and strengthens later access.",
                "--hint",
                "Compare retrieval with rereading.",
                "--follow-up",
                "Give one coding example.",
                "--json",
            ).stdout
        )
        self.assertEqual(added["item_id"], "testing-effect")

        next_item = json.loads(self._run("next", "--json").stdout)
        self.assertEqual(next_item["item_id"], "testing-effect")
        self.assertNotIn("answer", next_item)

        practised = self._run(
            "practice",
            "--item",
            "testing-effect",
            "--agent-help",
            "none",
            input_text=(
                "n\n"
                "Retrieval strengthens access paths.\n"
                "reveal\n"
                "demonstrated\n"
                "4\n"
            ),
        )
        self.assertIn("Challenge: Explain the testing effect", practised.stdout)
        prompt_at = practised.stdout.index("Why does active recall improve memory?")
        answer_at = practised.stdout.index("Retrieval changes memory")
        self.assertLess(prompt_at, answer_at)
        self.assertIn("Initial recall time:", practised.stdout)
        self.assertIn("via fsrs 6.3.2", practised.stdout)
        self.assertNotIn("master", practised.stdout.lower())

        attempts = json.loads(self._run("attempts", "--json").stdout)
        self.assertEqual(len(attempts["attempts"]), 1)
        self.assertEqual(attempts["attempts"][0]["result"], "demonstrated")
        self.assertEqual(attempts["attempts"][0]["agent_help"], "none")
        self.assertGreaterEqual(attempts["attempts"][0]["initial_latency_ms"], 0)
        self.assertEqual(attempts["proposals"][0]["algorithm"], "fsrs")
        self.assertEqual(attempts["proposals"][0]["algorithm_version"], "6.3.2")

        doctor = json.loads(self._run("doctor", "--json").stdout)
        self.assertEqual(doctor["status"], "healthy")
        self.assertEqual(doctor["attempts"], 1)

    def test_missing_workspace_returns_plain_actionable_error(self) -> None:
        failed = self._run("doctor", "--json", expected=2)
        self.assertEqual(failed.stdout, "")
        self.assertIn("run 'virtuoso init' first", failed.stderr)
        self.assertNotIn("Traceback", failed.stderr)


if __name__ == "__main__":
    unittest.main()
