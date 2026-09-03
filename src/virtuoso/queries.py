"""Read-only analytics queries over an existing Virtuoso workspace database.

Database support (public PR): one module, zero migrations, zero new tables.
Every query opens the SQLite database in read-only mode (URI
``file:...?mode=ro``), never writes, and works against any workspace the
CLI already maintains. The workspace service remains the single writer.

Schema tolerance: the ``administered`` attempts column (agent-relayed
practice) ships on a newer branch than this module's first release. The
module detects it once per query and reports ``administered=False``
against older databases rather than failing.

Queries answer the questions a learner or agent actually asks:

- focus performance (per-focus attempt counts, results, confidence mean)
- item history (every attempt for one item, newest first)
- due workload per focus
- typed learning action state for every active item
- stale source links (the same finding ``doctor`` reports, queryable)

Honesty rules carried over from the CLI: administered attempts are
flagged, and means are ``None`` (never zero) when no real value exists.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .errors import VirtuosoError
from .learning_state import LearningStateDataError, current_learning_states
from .workload import WorkloadDataError, current_schedules, summarize_workload


class QueryError(VirtuosoError):
    """A read-only query cannot be answered against this workspace."""


@dataclass(frozen=True)
class FocusSummary:
    focus: str
    items: int
    attempts: int
    demonstrated: int
    partial: int
    not_demonstrated: int
    administered: int
    mean_confidence: float | None
    mean_latency_ms: float | None


@dataclass(frozen=True)
class ItemAttempt:
    event_id: str
    item_id: str
    result: str
    confidence: int
    administered: bool
    latency_ms: int | None
    agent_help: str
    occurred_at: str


def _connect_read_only(db_path: Path) -> sqlite3.Connection:
    if not db_path.is_file():
        raise QueryError(f"workspace database not found: {db_path}")
    uri = f"{db_path.resolve().as_uri()}?mode=ro"
    db: sqlite3.Connection | None = None
    try:
        db = sqlite3.connect(uri, uri=True)
        db.execute("PRAGMA query_only = 1")
    except sqlite3.Error as exc:
        if db is not None:
            db.close()
        raise QueryError(f"cannot open workspace database read-only: {exc}") from exc
    db.row_factory = sqlite3.Row
    return db


def _has_administered(db: sqlite3.Connection) -> bool:
    columns = {
        row["name"] for row in db.execute("PRAGMA table_info(attempts)").fetchall()
    }
    return "administered" in columns


def focus_performance(db_path: Path | str) -> list[FocusSummary]:
    """Per-focus attempt outcomes. NULL latencies are excluded from the
    latency mean rather than counted as zero (administered attempts)."""
    db = _connect_read_only(Path(db_path))
    try:
        administered_expr = (
            "SUM(a.administered)" if _has_administered(db) else "0"
        )
        rows = db.execute(
            f"""
            SELECT i.focus AS focus,
                   COUNT(DISTINCT i.item_id) AS items,
                   COUNT(a.event_id) AS attempts,
                   SUM(CASE WHEN a.result = 'demonstrated' THEN 1 ELSE 0 END) AS demonstrated,
                   SUM(CASE WHEN a.result = 'partial' THEN 1 ELSE 0 END) AS partial,
                   SUM(CASE WHEN a.result = 'not-demonstrated' THEN 1 ELSE 0 END) AS not_demonstrated,
                   {administered_expr} AS administered,
                   AVG(a.confidence) AS mean_confidence,
                   AVG(a.initial_latency_ms) AS mean_latency_ms
            FROM items AS i
            LEFT JOIN attempts AS a ON a.item_id = i.item_id
            GROUP BY i.focus
            ORDER BY i.focus
            """
        ).fetchall()
    except sqlite3.Error as exc:
        raise QueryError(f"focus query failed: {exc}") from exc
    finally:
        db.close()
    return [
        FocusSummary(
            focus=row["focus"],
            items=row["items"],
            attempts=row["attempts"],
            demonstrated=row["demonstrated"] or 0,
            partial=row["partial"] or 0,
            not_demonstrated=row["not_demonstrated"] or 0,
            administered=row["administered"] or 0,
            mean_confidence=(
                round(row["mean_confidence"], 2)
                if row["mean_confidence"] is not None
                else None
            ),
            mean_latency_ms=(
                round(row["mean_latency_ms"], 1)
                if row["mean_latency_ms"] is not None
                else None
            ),
        )
        for row in rows
    ]


def item_history(db_path: Path | str, item_id: str) -> list[ItemAttempt]:
    """Every recorded attempt for one item, newest first."""
    db = _connect_read_only(Path(db_path))
    try:
        administered_supported = _has_administered(db)
        administered_select = (
            ", administered" if administered_supported else ", 0 AS administered"
        )
        rows = db.execute(
            f"""
            SELECT event_id, item_id, result, confidence,
                   initial_latency_ms, agent_help, occurred_at{administered_select}
            FROM attempts
            WHERE item_id = ?
            ORDER BY occurred_at DESC, event_id
            """,
            (item_id,),
        ).fetchall()
    except sqlite3.Error as exc:
        raise QueryError(f"item history query failed: {exc}") from exc
    finally:
        db.close()
    return [
        ItemAttempt(
            event_id=row["event_id"],
            item_id=row["item_id"],
            result=row["result"],
            confidence=row["confidence"],
            administered=bool(row["administered"]),
            latency_ms=row["initial_latency_ms"],
            agent_help=row["agent_help"],
            occurred_at=row["occurred_at"],
        )
        for row in rows
    ]


def workload_by_focus(
    db_path: Path | str,
    *,
    now: datetime | None = None,
    algorithm: str = "fsrs",
    learning_context: str = "atomic-recall",
) -> list[dict[str, Any]]:
    """Due-now and scheduled counts per focus (mirrors doctor workload:
    each item's current scheduler state decides due-now)."""
    observed_at = now or datetime.now(timezone.utc)
    db = _connect_read_only(Path(db_path))
    try:
        schedules = current_schedules(
            db,
            algorithm=algorithm,
            learning_context=learning_context,
        )
        summary = summarize_workload(schedules, now=observed_at)
    except sqlite3.Error as exc:
        raise QueryError(f"workload query failed: {exc}") from exc
    except WorkloadDataError as exc:
        raise QueryError(str(exc)) from exc
    finally:
        db.close()
    return [
        {
            "focus": entry.focus,
            "items": entry.items,
            "due_now": entry.due_now,
            "scheduled": entry.scheduled,
        }
        for entry in summary.focuses
    ]


def learning_state(db_path: Path | str) -> list[dict[str, Any]]:
    """Typed next action and exact study identity for each active item."""
    db = _connect_read_only(Path(db_path))
    try:
        states = current_learning_states(db)
    except sqlite3.Error as exc:
        raise QueryError(f"learning-state query failed: {exc}") from exc
    except LearningStateDataError as exc:
        raise QueryError(str(exc)) from exc
    finally:
        db.close()
    return [state.to_dict() for state in states]


def stale_links(db_path: Path | str) -> list[dict[str, str]]:
    """Source links whose indexed hash no longer matches the stored link
    hash (the same finding doctor reports, in queryable form)."""
    db = _connect_read_only(Path(db_path))
    try:
        rows = db.execute(
            """
            SELECT l.item_id, l.source_id, l.source_relative_path
            FROM item_source_links AS l
            LEFT JOIN source_documents AS d
              ON d.source_id = l.source_id
             AND d.relative_path = l.source_relative_path
            WHERE d.content_hash IS NULL
               OR d.content_hash != l.source_content_hash
            ORDER BY l.item_id
            """
        ).fetchall()
    except sqlite3.Error as exc:
        raise QueryError(f"stale-link query failed: {exc}") from exc
    finally:
        db.close()
    return [
        {
            "item_id": row["item_id"],
            "source_id": row["source_id"],
            "relative_path": row["source_relative_path"],
        }
        for row in rows
    ]
