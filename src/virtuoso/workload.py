"""Shared current-scheduler workload projection and counting."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone


class WorkloadDataError(ValueError):
    """Stored scheduler data cannot produce an honest workload view."""


@dataclass(frozen=True)
class CurrentSchedule:
    item_id: str
    focus: str
    due_at: datetime | None


@dataclass(frozen=True)
class FocusWorkload:
    focus: str
    items: int
    due_now: int
    scheduled: int


@dataclass(frozen=True)
class WorkloadSummary:
    due_now: int
    scheduled_total: int
    new_items: int
    focuses: tuple[FocusWorkload, ...]


def require_aware_utc(value: datetime, *, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise WorkloadDataError(f"{label} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _parse_due_at(value: object, *, item_id: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise WorkloadDataError(
            f"invalid scheduler due timestamp for item {item_id}: {value!r}"
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise WorkloadDataError(
            f"invalid scheduler due timestamp for item {item_id}: {value!r}"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise WorkloadDataError(
            f"invalid scheduler due timestamp for item {item_id}: {value!r}"
        )
    return parsed.astimezone(timezone.utc)


def current_schedules(
    db: sqlite3.Connection,
    *,
    algorithm: str,
    learning_context: str,
    focus: str | None = None,
) -> tuple[CurrentSchedule, ...]:
    query = """
        SELECT i.item_id, i.focus, p.due_at
        FROM items AS i
        LEFT JOIN scheduler_state AS s
          ON s.item_id = i.item_id
         AND s.algorithm = ?
         AND s.learning_context = ?
        LEFT JOIN scheduler_proposals AS p
          ON p.source_event_id = s.source_event_id
        WHERE i.retired_at IS NULL
    """
    parameters: list[object] = [algorithm, learning_context]
    if focus is not None:
        query += " AND i.focus = ?"
        parameters.append(focus)
    query += " ORDER BY i.item_id"
    rows = db.execute(query, parameters).fetchall()
    return tuple(
        CurrentSchedule(
            item_id=row["item_id"],
            focus=row["focus"],
            due_at=_parse_due_at(row["due_at"], item_id=row["item_id"]),
        )
        for row in rows
    )


def summarize_workload(
    schedules: tuple[CurrentSchedule, ...], *, now: datetime
) -> WorkloadSummary:
    observed_at = require_aware_utc(now, label="workload timestamp")
    by_focus: dict[str, dict[str, int]] = {}
    due_now = 0
    scheduled_total = 0
    for schedule in schedules:
        counts = by_focus.setdefault(
            schedule.focus,
            {"items": 0, "due_now": 0, "scheduled": 0},
        )
        counts["items"] += 1
        if schedule.due_at is None:
            continue
        scheduled_total += 1
        counts["scheduled"] += 1
        if schedule.due_at <= observed_at:
            due_now += 1
            counts["due_now"] += 1
    focuses = tuple(
        FocusWorkload(
            focus=focus,
            items=counts["items"],
            due_now=counts["due_now"],
            scheduled=counts["scheduled"],
        )
        for focus, counts in sorted(by_focus.items())
    )
    return WorkloadSummary(
        due_now=due_now,
        scheduled_total=scheduled_total,
        new_items=sum(entry.items - entry.scheduled for entry in focuses),
        focuses=focuses,
    )
