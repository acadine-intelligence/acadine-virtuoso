from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from virtuoso.practice import PracticeService
from virtuoso.workspace import WorkspaceService


class _IO:
    def __init__(self, answers: list[str]) -> None:
        self.answers = iter(answers)

    def write(self, text: str) -> None:
        pass

    def ask(self, prompt: str) -> str:
        return next(self.answers)


class _Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def monotonic(self) -> float:
        self.value += 1.0
        return self.value


class SelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve() / "learner"
        self.workspace = WorkspaceService.init(self.root)
        for item_id in ("beta", "alpha"):
            self.workspace.add_item(
                item_id=item_id,
                title=item_id.title(),
                focus="test",
                prompt=f"Prompt {item_id}",
                answer=f"Answer {item_id}",
            )
        self.now = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_new_item_selection_is_deterministic_and_explained(self) -> None:
        first = self.workspace.select_next(self.now)
        second = self.workspace.select_next(self.now)

        self.assertEqual(first.item.item_id, "alpha")
        self.assertEqual(second.item.item_id, "alpha")
        self.assertIn("new item", first.rationale)
        self.assertEqual(first.alternatives, ("beta",))
        self.assertIsNone(first.uncertainty)

    def test_scheduled_future_item_yields_to_other_new_item(self) -> None:
        PracticeService(self.workspace, clock=_Clock()).run(
            item_id="alpha",
            io=_IO(["n", "Recall", "reveal", "demonstrated", "4"]),
            now=self.now,
        )
        selected = self.workspace.select_next(self.now)
        self.assertEqual(selected.item.item_id, "beta")

    def test_healthy_doctor_reports_owned_state_without_mutating_evidence(self) -> None:
        before = self.workspace.list_attempts()
        report = self.workspace.doctor()
        after = self.workspace.list_attempts()

        self.assertEqual(report["status"], "healthy")
        self.assertEqual(report["workspace_schema"], "virtuoso/workspace@0.1")
        self.assertEqual(report["database"], "ok")
        self.assertEqual(report["items"], 2)
        self.assertEqual(report["stale_items"], [])
        self.assertEqual(before, after)

    def test_selection_uses_configured_learning_context(self) -> None:
        config = self.workspace.configuration()
        config["scheduler"]["context"] = "project-transfer"
        self.workspace.config_path.write_text(json.dumps(config))
        PracticeService(self.workspace, clock=_Clock()).run(
            item_id="alpha",
            io=_IO(["n", "answer", "reveal", "demonstrated", "4"]),
            now=self.now,
        )

        selection = self.workspace.select_next(self.now)
        self.assertEqual(selection.item.item_id, "beta")


if __name__ == "__main__":
    unittest.main()
