"""Shared typed learning-action projection over current item versions."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class LearningStateDataError(ValueError):
    """Stored learning state cannot produce an honest next action."""


@dataclass(frozen=True)
class LearningActionState:
    item_id: str
    focus: str
    entry_mode: str
    item_content_hash: str
    learning_unit_hash: str | None
    action: str
    reason_code: str
    rationale: str
    study_completed_at: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "focus": self.focus,
            "entry_mode": self.entry_mode,
            "item_content_hash": self.item_content_hash,
            "learning_unit_hash": self.learning_unit_hash,
            "action": self.action,
            "reason_code": self.reason_code,
            "rationale": self.rationale,
            "study_completed_at": self.study_completed_at,
        }


def _stored_timestamp(value: object, *, item_id: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise LearningStateDataError(
            f"invalid study completion timestamp for item {item_id}: {value!r}"
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise LearningStateDataError(
            f"invalid study completion timestamp for item {item_id}: {value!r}"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise LearningStateDataError(
            f"invalid study completion timestamp for item {item_id}: {value!r}"
        )
    return parsed.astimezone(timezone.utc).isoformat()


def current_learning_states(
    db: sqlite3.Connection,
    *,
    focus: str | None = None,
    item_id: str | None = None,
) -> tuple[LearningActionState, ...]:
    query = """
        SELECT i.item_id,
               i.focus,
               i.content_hash,
               i.entry_mode,
               i.learning_unit_hash,
               s.occurred_at AS study_completed_at
        FROM items AS i
        LEFT JOIN study_events AS s
          ON s.item_id = i.item_id
         AND s.item_content_hash = i.content_hash
         AND s.learning_unit_hash = i.learning_unit_hash
        WHERE i.retired_at IS NULL
    """
    parameters: list[object] = []
    if focus is not None:
        query += " AND i.focus = ?"
        parameters.append(focus)
    if item_id is not None:
        query += " AND i.item_id = ?"
        parameters.append(item_id)
    query += " ORDER BY i.item_id"
    rows = db.execute(query, parameters).fetchall()

    states: list[LearningActionState] = []
    for row in rows:
        mode = row["entry_mode"]
        content_hash = row["content_hash"]
        unit_hash = row["learning_unit_hash"]
        if not isinstance(content_hash, str) or _SHA256.fullmatch(content_hash) is None:
            raise LearningStateDataError(
                f"invalid item content hash for item {row['item_id']}"
            )
        if mode == "recall-first":
            if unit_hash is not None:
                raise LearningStateDataError(
                    f"recall-first item {row['item_id']} has learning-unit state"
                )
            action = "practice"
            reason_code = "recall-first"
            rationale = "Practice is ready because this item is recall-first."
        elif mode == "learn-first":
            if not isinstance(unit_hash, str) or _SHA256.fullmatch(unit_hash) is None:
                raise LearningStateDataError(
                    f"learn-first item {row['item_id']} has an invalid learning-unit hash"
                )
            if row["study_completed_at"] is None:
                action = "learn"
                reason_code = "study-required"
                rationale = (
                    "Learning is required because this learn-first item has no completed "
                    "study event for its current item and learning-unit hashes."
                )
            else:
                action = "practice"
                reason_code = "study-current"
                rationale = (
                    "Practice is ready because this exact item and learning-unit version "
                    "has a completed study event."
                )
        else:
            raise LearningStateDataError(
                f"invalid entry mode for item {row['item_id']}: {mode!r}"
            )
        states.append(
            LearningActionState(
                item_id=row["item_id"],
                focus=row["focus"],
                entry_mode=mode,
                item_content_hash=content_hash,
                learning_unit_hash=unit_hash,
                action=action,
                reason_code=reason_code,
                rationale=rationale,
                study_completed_at=_stored_timestamp(
                    row["study_completed_at"], item_id=row["item_id"]
                ),
            )
        )
    return tuple(states)
