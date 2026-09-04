"""Tests for issue 5: practice context display before answer reveal."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from virtuoso.practice import PracticeService
from virtuoso.workspace import WorkspaceService


class _RecordingIO:
    def __init__(self, answers: list[str]) -> None:
        self.answers = iter(answers)
        self.output: list[str] = []

    def write(self, text: str) -> None:
        self.output.append(text)

    def ask(self, prompt: str) -> str:
        return next(self.answers)


class _ZeroClock:
    def monotonic(self) -> float:
        return 0.0


class PracticeContextDisplayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve() / "learner"
        self.workspace = WorkspaceService.init(self.root)
        self.now = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _add(self, item_id: str = "item-a") -> None:
        self.workspace.add_item(
            item_id=item_id,
            title=f"Title {item_id}",
            focus="test-focus",
            prompt=f"Prompt {item_id}?",
            answer=f"Answer {item_id}.",
        )

    def _practice(self, item_id: str = "item-a", **kwargs: object) -> _RecordingIO:
        io = _RecordingIO(
            ["n", "a real recalled answer", "reveal", "demonstrated", "4"]
        )
        PracticeService(self.workspace, clock=_ZeroClock()).run(
            item_id=item_id, io=io, now=self.now, **kwargs  # type: ignore[arg-type]
        )
        return io

    def test_context_prints_focus_and_reason_before_prompt(self) -> None:
        self._add()
        io = self._practice(
            selection_reason="Selected a new item in deterministic item-id order."
        )

        focus_index = io.output.index("Focus: test-focus")
        why_index = io.output.index(
            "Why now: Selected a new item in deterministic item-id order."
        )
        prompt_index = io.output.index("Prompt item-a?")
        challenge_index = io.output.index("Challenge: Title item-a")
        answer_index = io.output.index("Answer\nAnswer item-a.")

        self.assertLess(focus_index, why_index)
        self.assertLess(why_index, challenge_index)
        self.assertLess(challenge_index, prompt_index)
        self.assertLess(prompt_index, answer_index)

    def test_project_line_requires_explicit_transfer_record(self) -> None:
        self._add()
        io = self._practice(project_ids=("proj-alpha",))

        self.assertIn("Projects: proj-alpha", io.output)
        projects_index = io.output.index("Projects: proj-alpha")
        focus_index = io.output.index("Focus: test-focus")
        self.assertLess(focus_index, projects_index)

    def test_missing_project_context_displays_nothing_invented(self) -> None:
        self._add()
        io = self._practice()

        self.assertFalse(any(line.startswith("Projects:") for line in io.output))

    def test_omitting_reason_omits_line_and_keeps_behavior(self) -> None:
        self._add()
        io = self._practice()

        self.assertFalse(any(line.startswith("Why now:") for line in io.output))
        self.assertIn("Focus: test-focus", io.output)
        self.assertIn("Prompt item-a?", io.output)
        self.assertEqual(len(self.workspace.list_attempts()), 1)

    def test_context_display_writes_no_state(self) -> None:
        self._add()

        attempts_before = self.workspace.list_attempts()
        self._practice(
            selection_reason="Selected the earliest due item; ties use item id.",
            project_ids=("proj-alpha", "proj-beta"),
        )
        attempts_after = self.workspace.list_attempts()

        self.assertEqual(len(attempts_before) + 1, len(attempts_after))

    def test_long_reason_wraps_at_eighty_columns(self) -> None:
        self._add()
        long_reason = (
            "Selected the earliest due item; ties use item id. " * 3
        ).strip()
        io = self._practice(selection_reason=long_reason)

        joined = "\n".join(io.output)
        self.assertIn("Why now: Selected the earliest due item;", joined)
        for line in io.output:
            self.assertLessEqual(len(line), 80)


if __name__ == "__main__":
    unittest.main()
