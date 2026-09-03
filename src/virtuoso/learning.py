"""Bounded study journey for learn-first item versions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from .errors import VirtuosoError
from .workspace import WorkspaceError, WorkspaceService


class LearningError(VirtuosoError):
    """A learning step cannot complete without trustworthy activity evidence."""


class LearningIO(Protocol):
    def write(self, text: str) -> None: ...

    def ask(self, prompt: str) -> str: ...


@dataclass(frozen=True)
class LearningResult:
    completed: bool
    event: dict[str, object] | None


class LearningService:
    def __init__(self, workspace: WorkspaceService) -> None:
        self.workspace = workspace

    def run(
        self,
        *,
        item_id: str,
        io: LearningIO,
        now: datetime | None = None,
        surface: str = "cli",
    ) -> LearningResult:
        try:
            item = self.workspace.load_item(item_id)
            state = self.workspace.learning_state(item_id)
        except WorkspaceError as exc:
            raise LearningError(str(exc)) from exc
        if item.entry_mode != "learn-first" or item.learning_unit is None:
            raise LearningError(f"item is recall-first and does not require learning: {item_id}")
        if state.action != "learn":
            raise LearningError(
                f"learning already completed for current item version: {item_id}"
            )

        io.write(f"Learning: {item.title}")
        io.write(item.learning_unit)
        while True:
            try:
                decision = io.ask("Finish this learning step? [finish / stop]: ").strip().lower()
            except (EOFError, KeyboardInterrupt) as exc:
                raise LearningError(
                    "learning stopped before completion; no study event recorded"
                ) from exc
            if decision == "stop":
                io.write("Learning stopped. No study event recorded.")
                return LearningResult(completed=False, event=None)
            if decision == "finish":
                break
            io.write("Enter finish or stop.")

        occurred_at = now or datetime.now(timezone.utc)
        if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
            raise LearningError("study completion timestamp must be timezone-aware")
        occurred_at = occurred_at.astimezone(timezone.utc)

        try:
            event = self.workspace.record_study_completion(
                item_id=item.item_id,
                item_content_hash=item.content_hash,
                learning_unit_hash=item.learning_unit_hash,
                occurred_at=occurred_at,
                surface=surface,
            )
        except WorkspaceError as exc:
            raise LearningError(str(exc)) from exc
        io.write(
            f"Study completion recorded for {item.item_id} at {event['occurred_at']}."
        )
        return LearningResult(completed=True, event=event)
