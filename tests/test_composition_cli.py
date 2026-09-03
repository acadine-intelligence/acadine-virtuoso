from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class ComposeCliJourneyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.workspace = self.root / "learner"
        self.repo = Path(__file__).resolve().parents[1]

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _run(self, *args: str, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "virtuoso.cli",
                "--workspace",
                str(self.workspace),
                *args,
            ],
            cwd=self.repo,
            input=input_text,
            text=True,
            capture_output=True,
            check=True,
        )

    def _add(self, item_id: str, answer: str) -> dict[str, object]:
        return json.loads(
            self._run(
                "add",
                "--id",
                item_id,
                "--title",
                f"Title {item_id}",
                "--focus",
                "ml",
                "--prompt",
                f"Prompt {item_id}?",
                "--answer",
                answer,
                "--json",
            ).stdout
        )

    def test_compose_decide_practice_journey(self) -> None:
        self._run("init", "--json")
        self._add("item-a", "ANSWER-MARKER-A")
        self._add("item-b", "ANSWER-MARKER-B")
        self._run(
            "practice",
            "--item",
            "item-a",
            "--administer",
            "--response",
            "A partial answer.",
            "--result",
            "partial",
            "--confidence",
            "3",
            "--json",
        )

        proposal = json.loads(self._run("compose", "--json").stdout)
        self.assertEqual(proposal["schema"], "virtuoso/focus-proposal@0.1")
        self.assertEqual(proposal["primary"]["item_id"], "item-a")
        self.assertEqual(proposal["action"], "practice")
        self.assertTrue(proposal["source_event_ids"])
        self.assertNotIn("ANSWER-MARKER-A", json.dumps(proposal))
        self.assertNotIn("ANSWER-MARKER-B", json.dumps(proposal))
        self.assertIn("prompt", proposal["primary"])
        self.assertIn("rationale", proposal)
        self.assertIn("alternatives", proposal)
        self.assertIn("skipped", proposal)

        proposal_id = proposal["proposal_id"]

        decision = json.loads(
            self._run(
                "compose",
                "decide",
                "--id",
                proposal_id,
                "--decision",
                "accept",
                "--json",
            ).stdout
        )
        self.assertEqual(decision["schema"], "virtuoso/learner-decision@0.1")
        self.assertEqual(decision["decision"], "accept")
        self.assertEqual(decision["chosen_item_id"], "item-a")
        self.assertTrue(decision["chosen_item_content_hash"])

        shown = json.loads(self._run("compose", "show", "--id", proposal_id, "--json").stdout)
        self.assertEqual(shown["proposal"]["proposal_id"], proposal_id)
        self.assertEqual(shown["decision"]["decision"], "accept")

        listed = json.loads(
            self._run("compose", "list", "--status", "decided", "--json").stdout
        )
        self.assertEqual(len(listed["proposals"]), 1)
        self.assertEqual(listed["proposals"][0]["decision"], "accept")

        pending = json.loads(self._run("compose", "list", "--status", "pending", "--json").stdout)
        self.assertEqual(pending["proposals"], [])

        practice = json.loads(
            self._run(
                "practice",
                "--item",
                "item-a",
                "--administer",
                "--response",
                "The recalled answer.",
                "--result",
                "demonstrated",
                "--confidence",
                "4",
                "--agent-help",
                "none",
                "--json",
            ).stdout
        )
        self.assertEqual(practice["item_id"], "item-a")

        second = subprocess.run(
            [
                sys.executable,
                "-m",
                "virtuoso.cli",
                "--workspace",
                str(self.workspace),
                "compose",
                "decide",
                "--id",
                proposal_id,
                "--decision",
                "reject",
                "--json",
            ],
            cwd=self.repo,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(second.returncode, 2)
        self.assertIn("already decided", second.stderr)

    def test_compose_without_evidence_falls_back_with_uncertainty(self) -> None:
        self._run("init", "--json")
        self._add("item-b", "answer b")
        self._add("item-a", "answer a")

        proposal = json.loads(self._run("compose", "--json").stdout)

        self.assertEqual(proposal["primary"]["item_id"], "item-a")
        self.assertIsNotNone(proposal["uncertainty"])


if __name__ == "__main__":
    unittest.main()
