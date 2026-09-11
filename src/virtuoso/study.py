"""Hash-bound study contracts for local user interfaces."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from .review import ReviewContractError, ReviewError
from .workspace import WorkspaceError, WorkspaceService


class StudyReviewService:
    def __init__(self, workspace: WorkspaceService) -> None:
        self.workspace = workspace

    def record(self, raw: str) -> dict[str, object]:
        if len(raw.encode("utf-8")) > 65_536:
            raise ReviewContractError("Study request exceeds 64 KiB.")
        try:
            request = json.loads(raw)
        except (ValueError, RecursionError) as exc:
            raise ReviewContractError("Study request must be a JSON object.") from exc
        fields = {
            "schema", "item_id", "item_content_hash", "learning_unit_hash",
            "completed", "surface",
        }
        if not isinstance(request, dict) or set(request) != fields:
            raise ReviewContractError("Study request fields do not match the contract.")
        if request["schema"] != "virtuoso/study-completion@0.1":
            raise ReviewContractError("Unsupported study completion schema.")
        if request["completed"] is not True:
            raise ReviewContractError("Study completion requires completed: true.")
        for field, pattern in (
            ("item_id", r"[a-z0-9]+(?:-[a-z0-9]+)*"),
            ("item_content_hash", r"[0-9a-f]{64}"),
            ("learning_unit_hash", r"[0-9a-f]{64}"),
        ):
            if not isinstance(request[field], str) or not re.fullmatch(pattern, request[field]):
                raise ReviewContractError(f"Invalid {field}.")
        if request["surface"] not in ("hermes-desktop", "obsidian-plugin", "cli"):
            raise ReviewContractError("Unsupported study surface.")
        try:
            event = self.workspace.record_study_completion(
                item_id=request["item_id"],
                item_content_hash=request["item_content_hash"],
                learning_unit_hash=request["learning_unit_hash"],
                occurred_at=datetime.now(timezone.utc),
                surface=request["surface"],
            )
        except WorkspaceError as exc:
            # The core validates current file and database hashes before its
            # uniqueness check. Return the original event after a lost reply.
            if "already completed" not in str(exc):
                raise
            event = next((
                row for row in self.workspace.list_study_events()
                if all(row[key] == request[key] for key in (
                    "item_id", "item_content_hash", "learning_unit_hash",
                ))
            ), None)
            if event is None:
                raise
        return {"schema": "virtuoso/study-result@0.1", "study": event}

    def load(self, item_id: str) -> dict[str, object]:
        item = self.workspace.load_item(item_id)
        if item.entry_mode != "learn-first" or item.learning_unit is None:
            raise ReviewError(
                "This item does not require a learning step.",
                code="invalid-request", recovery="check-contract",
            )
        if self.workspace.learning_state(item_id).action != "learn":
            raise ReviewError(
                "Study is already complete for this item version.",
                code="already-recorded", recovery="advance-card",
            )
        return {
            "schema": "virtuoso/study-item@0.1",
            "item": {
                "item_id": item.item_id,
                "title": item.title,
                "focus": item.focus,
                "content_hash": item.content_hash,
                "learning_unit_hash": item.learning_unit_hash,
                "learning_unit": item.learning_unit,
            },
        }
