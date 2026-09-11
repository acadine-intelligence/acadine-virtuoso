from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from virtuoso.workspace import WorkspaceService


class StudyReviewCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve() / "learning"
        self.workspace = WorkspaceService.init(self.root)
        self.workspace.add_item(
            item_id="retrieval",
            title="Study retrieval",
            focus="learning",
            entry_mode="learn-first",
            learning_unit="Retrieval strengthens later access to a memory.",
            prompt="Why practice retrieval?",
            answer="Answer kept separate from the study snapshot.",
        )

    def run_cli(self, *args: str, body: dict | None = None, expected: int = 0) -> dict:
        process = subprocess.run(
            [sys.executable, "-m", "virtuoso.cli", f"--workspace={self.root}", *args],
            input=json.dumps(body) if body is not None else "",
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(process.returncode, expected, process.stderr)
        return json.loads(process.stdout if expected == 0 else process.stderr)

    def test_study_load_returns_only_the_hash_bound_learning_unit_without_writes(self) -> None:
        before = self.workspace.db_path.read_bytes()
        payload = self.run_cli("review", "study-load", "--item=retrieval", "--json")
        item = self.workspace.load_item("retrieval")
        self.assertEqual(payload, {
            "schema": "virtuoso/study-item@0.1",
            "item": {
                "item_id": item.item_id,
                "title": item.title,
                "focus": item.focus,
                "content_hash": item.content_hash,
                "learning_unit_hash": item.learning_unit_hash,
                "learning_unit": item.learning_unit,
            },
        })
        self.assertNotIn("prompt", payload["item"])
        self.assertNotIn("answer", payload["item"])
        self.assertEqual(self.workspace.db_path.read_bytes(), before)

    def completion(self) -> dict:
        item = self.workspace.load_item("retrieval")
        return {
            "schema": "virtuoso/study-completion@0.1",
            "item_id": item.item_id,
            "item_content_hash": item.content_hash,
            "learning_unit_hash": item.learning_unit_hash,
            "completed": True,
            "surface": "hermes-desktop",
        }

    def test_explicit_completion_and_retry_record_one_study_event_only(self) -> None:
        request = self.completion()
        first = self.run_cli("review", "study-record", "--json", body=request)
        second = self.run_cli("review", "study-record", "--json", body=request)
        self.assertEqual(first, second)
        self.assertEqual(first["schema"], "virtuoso/study-result@0.1")
        self.assertEqual(first["study"]["surface"], "hermes-desktop")
        self.assertIs(first["study"]["claims_mastery"], False)
        self.assertEqual(self.workspace.list_study_events(), [first["study"]])
        self.assertEqual(self.workspace.learning_state("retrieval").action, "practice")
        self.assertEqual(self.workspace.list_attempts(), [])
        self.assertEqual(self.workspace.list_proposals(), [])

    def test_invalid_completion_and_stale_content_write_nothing(self) -> None:
        valid = self.completion()
        cases = [
            {**valid, "completed": False},
            {**valid, "completed": 1},
            {**valid, "schema": "future"},
            {**valid, "workspace": "/untrusted"},
            {**valid, "item_content_hash": "0" * 64},
            {**valid, "learning_unit_hash": "0" * 64},
        ]
        before = self.workspace.db_path.read_bytes()
        for request in cases:
            with self.subTest(request=request):
                error = self.run_cli(
                    "review", "study-record", "--json", body=request, expected=2,
                )
                self.assertEqual(error["schema"], "virtuoso/review-error@0.1")
                self.assertIn(error["error"]["code"], ("invalid-request", "stale-content"))
                self.assertEqual(self.workspace.db_path.read_bytes(), before)

    def test_desktop_skip_records_its_actual_surface_without_an_attempt(self) -> None:
        self.run_cli("review", "study-record", "--json", body=self.completion())
        item = self.workspace.load_item("retrieval")
        request = {
            "schema": "virtuoso/review-skip@0.1",
            "submission_id": "b" * 32,
            "item_id": item.item_id,
            "item_content_hash": item.content_hash,
            "occurred_at": "2026-09-11T08:00:00+00:00",
            "surface": "hermes-desktop",
        }
        payload = self.run_cli("review", "skip", "--json", body=request)
        self.assertEqual(payload["skip"]["surface"], "hermes-desktop")
        self.assertEqual(self.workspace.list_attempts(), [])


if __name__ == "__main__":
    unittest.main()
