"""Shared fixture helpers for migration tests.

Migration 10 rebuilt the attempt evidence chain (attempts, scheduler_state,
scheduler_proposals, attempt_timings) to support administered attempts with
NULL latency. Fixtures that rewind a freshly initialized workspace to an
older migration version must therefore also restore that chain to its
pre-v10 schema text, exactly as a genuine old database would contain.
"""
from __future__ import annotations

import sqlite3

V9_ATTEMPTS = """CREATE TABLE attempts (
                event_id TEXT PRIMARY KEY,
                item_id TEXT NOT NULL REFERENCES items(item_id),
                item_content_hash TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                initial_response TEXT NOT NULL,
                initial_latency_ms INTEGER NOT NULL CHECK(initial_latency_ms >= 0),
                result TEXT NOT NULL CHECK(result IN ('demonstrated','partial','not-demonstrated')),
                confidence INTEGER NOT NULL CHECK(confidence BETWEEN 1 AND 5),
                open_notes INTEGER NOT NULL CHECK(open_notes IN (0,1)),
                agent_help TEXT NOT NULL CHECK(agent_help IN ('none','light','substantial','unknown')),
                support_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )"""

V9_SCHEDULER_STATE = """CREATE TABLE scheduler_state (
                item_id TEXT NOT NULL REFERENCES items(item_id),
                algorithm TEXT NOT NULL,
                algorithm_version TEXT NOT NULL,
                learning_context TEXT NOT NULL,
                configuration_json TEXT NOT NULL,
                state_json TEXT NOT NULL,
                source_event_id TEXT NOT NULL REFERENCES attempts(event_id),
                updated_at TEXT NOT NULL,
                PRIMARY KEY(item_id, algorithm, learning_context)
            )"""

V9_SCHEDULER_PROPOSALS = """CREATE TABLE scheduler_proposals (
                proposal_id TEXT PRIMARY KEY,
                source_event_id TEXT NOT NULL UNIQUE REFERENCES attempts(event_id),
                item_id TEXT NOT NULL REFERENCES items(item_id),
                algorithm TEXT NOT NULL,
                algorithm_version TEXT NOT NULL,
                learning_context TEXT NOT NULL,
                configuration_json TEXT NOT NULL,
                previous_state_json TEXT,
                proposed_state_json TEXT NOT NULL,
                due_at TEXT NOT NULL,
                rationale TEXT NOT NULL,
                created_at TEXT NOT NULL
            )"""

V9_ATTEMPT_TIMINGS = """CREATE TABLE attempt_timings (
                event_id TEXT PRIMARY KEY REFERENCES attempts(event_id) ON DELETE CASCADE,
                started_at TEXT NOT NULL,
                completed_at TEXT NOT NULL
            )"""

V10_CANDIDATE_DECISIONS = """CREATE TABLE candidate_decisions (
                decision_id TEXT PRIMARY KEY,
                candidate_id TEXT NOT NULL
                    REFERENCES review_candidates(candidate_id) ON DELETE RESTRICT,
                decision TEXT NOT NULL CHECK(decision IN ('accept','reject')),
                note TEXT CHECK(note IS NULL OR length(note) BETWEEN 1 AND 2000),
                decided_at TEXT NOT NULL,
                UNIQUE(candidate_id)
            )"""

V10_CANDIDATE_DECISIONS_REJECT_UPDATE = """CREATE TRIGGER candidate_decisions_reject_update
                BEFORE UPDATE ON candidate_decisions
                BEGIN
                    SELECT RAISE(ABORT, 'candidate_decisions is append-only');
                END"""

V10_CANDIDATE_DECISIONS_REJECT_DELETE = """CREATE TRIGGER candidate_decisions_reject_delete
                BEFORE DELETE ON candidate_decisions
                BEGIN
                    SELECT RAISE(ABORT, 'candidate_decisions is append-only');
                END"""


def downgrade_scheduler_switches_to_v15(db: sqlite3.Connection) -> None:
    """Remove migration 16 scheduler-switch state from a fresh test database."""
    db.execute("DROP TRIGGER IF EXISTS scheduler_switches_reject_update")
    db.execute("DROP TRIGGER IF EXISTS scheduler_switches_reject_delete")
    db.execute("DROP TABLE IF EXISTS scheduler_switches")
    db.execute("DELETE FROM schema_migrations WHERE version >= 16")


def downgrade_benchmark_to_v14(db: sqlite3.Connection) -> None:
    """Remove migration 15 benchmark state from a fresh test database."""
    downgrade_scheduler_switches_to_v15(db)
    for trigger in (
        "benchmark_runs_reject_update",
        "benchmark_runs_reject_delete",
        "benchmark_observations_reject_update",
        "benchmark_observations_reject_delete",
        "benchmark_reruns_reject_update",
        "benchmark_reruns_reject_delete",
    ):
        db.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    db.execute("DROP TABLE IF EXISTS benchmark_proposals")
    db.execute("DROP TABLE IF EXISTS benchmark_reruns")
    db.execute("DROP TABLE IF EXISTS benchmark_observations")
    db.execute("DROP TABLE IF EXISTS benchmark_runs")
    db.execute("DELETE FROM schema_migrations WHERE version >= 15")


def downgrade_composition_to_v13(db: sqlite3.Connection) -> None:
    """Remove migration 14 composition state from a fresh test database."""
    downgrade_benchmark_to_v14(db)
    for trigger in (
        "composition_proposals_reject_update",
        "composition_proposals_reject_delete",
        "composition_proposal_items_reject_update",
        "composition_proposal_items_reject_delete",
        "composition_decisions_reject_update",
        "composition_decisions_reject_delete",
    ):
        db.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    db.execute("DROP TABLE IF EXISTS composition_decisions")
    db.execute("DROP TABLE IF EXISTS composition_proposal_items")
    db.execute("DROP TABLE IF EXISTS composition_proposals")
    db.execute("DELETE FROM schema_migrations WHERE version >= 14")


def downgrade_learning_to_v12(db: sqlite3.Connection) -> None:
    """Remove migration 13 learning state from a fresh test database."""
    downgrade_composition_to_v13(db)
    db.execute("DROP TRIGGER IF EXISTS study_events_reject_update")
    db.execute("DROP TRIGGER IF EXISTS study_events_reject_delete")
    db.execute("DROP TABLE IF EXISTS study_events")
    columns = {
        row[1] for row in db.execute("PRAGMA table_info(items)").fetchall()
    }
    if "learning_unit_hash" in columns:
        db.execute("ALTER TABLE items DROP COLUMN learning_unit_hash")
    if "entry_mode" in columns:
        db.execute("ALTER TABLE items DROP COLUMN entry_mode")
    db.execute("DELETE FROM schema_migrations WHERE version >= 13")


def downgrade_candidate_decisions_to_v10(db: sqlite3.Connection) -> None:
    """Restore the candidate decision table to its pre-migration-11 shape."""
    downgrade_learning_to_v12(db)
    db.execute("DROP TRIGGER IF EXISTS review_skips_reject_update")
    db.execute("DROP TRIGGER IF EXISTS review_skips_reject_delete")
    db.execute("DROP TABLE IF EXISTS review_skips")
    exists = (
        db.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table'"
            " AND name = 'candidate_decisions'"
        ).fetchone()
        is not None
    )
    if not exists:
        return
    for trigger in (
        "candidate_decisions_validate_action",
        "candidate_decisions_reject_update",
        "candidate_decisions_reject_delete",
    ):
        db.execute(f'DROP TRIGGER IF EXISTS "{trigger}"')
    db.execute("PRAGMA legacy_alter_table = ON")
    db.execute(
        "ALTER TABLE candidate_decisions RENAME TO candidate_decisions_v11_fixture"
    )
    db.execute(V10_CANDIDATE_DECISIONS)
    db.execute(
        """INSERT INTO candidate_decisions(
               decision_id, candidate_id, decision, note, decided_at)
           SELECT decision_id, candidate_id, decision, note, decided_at
           FROM candidate_decisions_v11_fixture"""
    )
    db.execute("DROP TABLE candidate_decisions_v11_fixture")
    db.execute(V10_CANDIDATE_DECISIONS_REJECT_UPDATE)
    db.execute(V10_CANDIDATE_DECISIONS_REJECT_DELETE)
    db.execute("PRAGMA legacy_alter_table = OFF")


def downgrade_attempt_chain_to_v9(db: sqlite3.Connection) -> None:
    """Rebuild the attempt evidence chain in its pre-migration-10 shape,
    preserving every row. Interactive rows only: administered rows cannot
    exist in a database being rewound below version 10."""
    downgrade_candidate_decisions_to_v10(db)
    db.execute("DROP TRIGGER IF EXISTS attempt_timings_reject_administered")
    has_timings = (
        db.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table'"
            " AND name = 'attempt_timings'"
        ).fetchone()
        is not None
    )
    db.execute("PRAGMA legacy_alter_table = ON")
    db.execute("ALTER TABLE attempts RENAME TO attempts_v10_fixture")
    db.execute("ALTER TABLE scheduler_state RENAME TO scheduler_state_v10_fixture")
    db.execute(
        "ALTER TABLE scheduler_proposals RENAME TO scheduler_proposals_v10_fixture"
    )
    if has_timings:
        db.execute(
            "ALTER TABLE attempt_timings RENAME TO attempt_timings_v10_fixture"
        )
    db.execute(V9_ATTEMPTS)
    db.execute(V9_SCHEDULER_STATE)
    db.execute(V9_SCHEDULER_PROPOSALS)
    db.execute(
        """INSERT INTO attempts(
               event_id, item_id, item_content_hash, occurred_at,
               initial_response, initial_latency_ms, result, confidence,
               open_notes, agent_help, support_json, created_at)
           SELECT event_id, item_id, item_content_hash, occurred_at,
                  initial_response, initial_latency_ms, result, confidence,
                  open_notes, agent_help, support_json, created_at
           FROM attempts_v10_fixture"""
    )
    db.execute(
        """INSERT INTO scheduler_state(
               item_id, algorithm, algorithm_version, learning_context,
               configuration_json, state_json, source_event_id, updated_at)
           SELECT item_id, algorithm, algorithm_version, learning_context,
                  configuration_json, state_json, source_event_id, updated_at
           FROM scheduler_state_v10_fixture"""
    )
    db.execute(
        """INSERT INTO scheduler_proposals(
               proposal_id, source_event_id, item_id, algorithm,
               algorithm_version, learning_context, configuration_json,
               previous_state_json, proposed_state_json, due_at,
               rationale, created_at)
           SELECT proposal_id, source_event_id, item_id, algorithm,
                  algorithm_version, learning_context, configuration_json,
                  previous_state_json, proposed_state_json, due_at,
                  rationale, created_at
           FROM scheduler_proposals_v10_fixture"""
    )
    if has_timings:
        db.execute(V9_ATTEMPT_TIMINGS)
        db.execute(
            """INSERT INTO attempt_timings(event_id, started_at, completed_at)
               SELECT event_id, started_at, completed_at
               FROM attempt_timings_v10_fixture"""
        )
        db.execute("DROP TABLE attempt_timings_v10_fixture")
    db.execute("DROP TABLE scheduler_proposals_v10_fixture")
    db.execute("DROP TABLE scheduler_state_v10_fixture")
    db.execute("DROP TABLE attempts_v10_fixture")
    db.execute("PRAGMA legacy_alter_table = OFF")
