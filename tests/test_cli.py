from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path


class CliJourneyTests(unittest.TestCase):
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
        attempt = attempts["attempts"][0]
        self.assertEqual(attempt["result"], "demonstrated")
        self.assertEqual(attempt["agent_help"], "none")
        self.assertGreaterEqual(attempt["initial_latency_ms"], 0)
        self.assertLessEqual(
            datetime.fromisoformat(attempt["started_at"]),
            datetime.fromisoformat(attempt["completed_at"]),
        )
        self.assertEqual(
            [entry["kind"] for entry in json.loads(attempt["support_json"])],
            ["worked-feedback"],
        )
        self.assertEqual(attempts["proposals"][0]["algorithm"], "fsrs")
        self.assertEqual(attempts["proposals"][0]["algorithm_version"], "6.3.2")

        doctor = json.loads(self._run("doctor", "--json").stdout)
        self.assertEqual(doctor["status"], "healthy")
        self.assertEqual(doctor["attempts"], 1)

    def test_next_focus_flag_scopes_selection_and_errors(self) -> None:
        self._run("init", "--json")
        self._run(
            "add", "--id", "testing-effect", "--title", "Explain the testing effect",
            "--focus", "learning-science",
            "--prompt", "Why does retrieval strengthen memory?",
            "--answer", "Retrieval changes memory.", "--json",
        )
        self._run(
            "add", "--id", "goroutines", "--title", "Goroutines",
            "--focus", "languages-go",
            "--prompt", "What is a goroutine?",
            "--answer", "A lightweight managed execution.", "--json",
        )

        scoped = json.loads(self._run("next", "--focus", "languages-go", "--json").stdout)
        self.assertEqual(scoped["item_id"], "goroutines")
        self.assertEqual(scoped["focus"], "languages-go")
        self.assertIn("languages-go", scoped["rationale"])

        failed = self._run("next", "--focus", "no-such-track", "--json", expected=2)
        self.assertIn("focus 'no-such-track'", failed.stderr)
        self.assertNotIn("Traceback", failed.stderr)

    def test_missing_workspace_returns_plain_actionable_error(self) -> None:
        failed = self._run("doctor", "--json", expected=2)
        self.assertEqual(failed.stdout, "")
        self.assertIn("run 'virtuoso init' first", failed.stderr)
        self.assertNotIn("Traceback", failed.stderr)

    def test_source_cli_connects_scans_and_lists_note_metadata(self) -> None:
        vault = Path(self.tmp.name).resolve() / "vault"
        vault.mkdir()
        (vault / "Testing Effect.md").write_text(
            "# Testing Effect\n\n[[Active Recall]] makes retrieval visible.\n",
            encoding="utf-8",
        )
        self._run("init", "--json")

        added = json.loads(
            self._run(
                "source",
                "add",
                "--id",
                "vault",
                "--kind",
                "obsidian",
                "--path",
                str(vault),
                "--json",
            ).stdout
        )
        self.assertEqual(added["source_id"], "vault")
        self.assertTrue(added["read_only"])

        scanned = json.loads(
            self._run("source", "scan", "--id", "vault", "--json").stdout
        )
        self.assertEqual(scanned["indexed"], 1)

        self._run(
            "add",
            "--id",
            "testing-effect",
            "--title",
            "Explain the testing effect",
            "--focus",
            "learning-science",
            "--prompt",
            "Why does retrieval strengthen memory?",
            "--answer",
            "Retrieval changes memory.",
            "--json",
        )
        linked = json.loads(
            self._run(
                "source",
                "link",
                "--id",
                "vault",
                "--path",
                "Testing Effect.md",
                "--item",
                "testing-effect",
                "--json",
            ).stdout
        )
        self.assertEqual(linked["item_id"], "testing-effect")
        self.assertEqual(linked["relative_path"], "Testing Effect.md")

        notes = json.loads(
            self._run("source", "notes", "--id", "vault", "--json").stdout
        )
        self.assertEqual(notes["documents"][0]["title"], "Testing Effect")
        self.assertEqual(notes["documents"][0]["wikilinks"], ["Active Recall"])
        self.assertNotIn("makes retrieval visible", json.dumps(notes))

    def test_project_transfer_cli_records_attributed_evidence_without_mastery_claim(self) -> None:
        self._run("init", "--json")
        self._run(
            "add",
            "--id",
            "testing-effect",
            "--title",
            "Explain the testing effect",
            "--focus",
            "learning-science",
            "--prompt",
            "Why does retrieval strengthen memory?",
            "--answer",
            "Retrieval changes memory.",
            "--json",
        )

        recorded = json.loads(
            self._run(
                "transfer",
                "record",
                "--item",
                "testing-effect",
                "--project",
                "virtuoso-cli",
                "--use-case",
                "Applied retrieval practice to a real CLI journey.",
                "--outcome",
                "successful",
                "--independence",
                "guided",
                "--artifact",
                "git:abc123",
                "--reflection",
                "One design hint was used.",
                "--json",
            ).stdout
        )
        self.assertEqual(recorded["project_id"], "virtuoso-cli")
        self.assertEqual(recorded["independence"], "guided")
        self.assertFalse(recorded["claims_mastery"])

        listed = json.loads(self._run("transfer", "list", "--json").stdout)
        self.assertEqual(len(listed["events"]), 1)
        self.assertEqual(listed["events"][0]["artifact_reference"], "git:abc123")
        self.assertNotIn("mastered", json.dumps(listed).lower())

    def test_version_flag_prints_package_version_without_workspace(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-m", "virtuoso.cli", "--version"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(
            completed.stdout.strip(),
            __import__("virtuoso").__version__,
        )
        self.assertEqual(completed.stderr, "")


if __name__ == "__main__":
    unittest.main()
