"""Core changes for the server and MCP design (issue #69, docs/22).

Agent-graded attempts are stored as evidence only: no scheduler proposal,
no scheduler state change, and no influence on `next` or `compose`.
Migration 19 adds `graded_by` and `surface` to attempts and the
pending-attempt table the split recall contract will use.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from conftest import downgrade_server_core_to_v18
from virtuoso.composition import SessionComposer
from virtuoso.practice import PracticeService
from virtuoso.queries import focus_performance, item_history
from virtuoso.workspace import WorkspaceError, WorkspaceService


class _IO:
    def __init__(self, answers: list[str]) -> None:
        self.answers = iter(answers)

    def write(self, text: str) -> None:
        pass

    def ask(self, prompt: str) -> str:
        return next(self.answers)


class _ZeroClock:
    def monotonic(self) -> float:
        return 0.0


def _rows(db_path: Path, query: str) -> list[tuple]:
    with sqlite3.connect(db_path) as db:
        return [tuple(row) for row in db.execute(query).fetchall()]


def _scheduler_rows(db_path: Path) -> list[tuple]:
    return _rows(
        db_path,
        "SELECT item_id, algorithm, learning_context, state_json, source_event_id,"
        " updated_at FROM scheduler_state ORDER BY item_id",
    )


def _proposal_rows(db_path: Path) -> list[tuple]:
    return _rows(
        db_path,
        "SELECT proposal_id, source_event_id, due_at, proposed_state_json"
        " FROM scheduler_proposals ORDER BY proposal_id",
    )


class _Fixture(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve() / "learner"
        self.workspace = WorkspaceService.init(self.root)
        self.now = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
        for item_id, focus in (("item-a", "ml"), ("item-b", "ml"), ("item-c", "db")):
            self.workspace.add_item(
                item_id=item_id,
                title=f"Title {item_id}",
                focus=focus,
                prompt=f"Prompt {item_id}?",
                answer=f"Answer {item_id}.",
            )

    def _learner(self, item_id: str, result: str, confidence: int) -> None:
        PracticeService(self.workspace, clock=_ZeroClock()).run(
            item_id=item_id,
            io=_IO(["n", "a real answer", "reveal", result, str(confidence)]),
            now=self.now,
        )

    def _agent(self, item_id: str, result: str = "not-demonstrated", confidence: int = 2):
        return PracticeService(self.workspace).run_administered(
            item_id=item_id,
            response="an answer the agent graded",
            result=result,
            confidence=confidence,
            graded_by="agent",
            now=self.now + timedelta(minutes=5),
        )


class MigrationNineteenTests(_Fixture):
    def test_fresh_workspace_has_migration_19_columns_and_pending_table(self) -> None:
        versions = [row[0] for row in _rows(
            self.workspace.db_path, "SELECT version FROM schema_migrations ORDER BY version"
        )]
        self.assertEqual(versions, list(range(1, 20)))
        columns = {row[1]: row for row in _rows(
            self.workspace.db_path, "PRAGMA table_info(attempts)"
        )}
        self.assertIn("graded_by", columns)
        self.assertIn("surface", columns)
        self.assertEqual(columns["graded_by"][3], 1)  # NOT NULL
        self.assertEqual(columns["surface"][3], 0)  # nullable: NULL means unknown
        pending = {row[1] for row in _rows(
            self.workspace.db_path, "PRAGMA table_info(pending_attempts)"
        )}
        self.assertEqual(pending, {
            "attempt_token", "item_id", "item_content_hash", "prompt_shown",
            "response", "responded_at", "state", "attempt_event_id", "surface",
            "created_at", "expires_at",
        })

    def test_upgrade_from_v18_backfills_learner_and_unknown_surface(self) -> None:
        self._learner("item-a", "demonstrated", 4)
        PracticeService(self.workspace).run_administered(
            item_id="item-b", response="relayed", result="partial", confidence=3,
            now=self.now,
        )
        with sqlite3.connect(self.workspace.db_path) as db:
            downgrade_server_core_to_v18(db)
        legacy = _rows(
            self.workspace.db_path,
            "SELECT event_id, item_id, result, confidence, administered,"
            " initial_latency_ms FROM attempts ORDER BY event_id",
        )
        state_before = _scheduler_rows(self.workspace.db_path)
        proposals_before = _proposal_rows(self.workspace.db_path)
        self.assertEqual(len(legacy), 2)

        reopened = WorkspaceService.open(self.root)
        reopened.doctor()
        upgraded = _rows(
            reopened.db_path,
            "SELECT event_id, item_id, result, confidence, administered,"
            " initial_latency_ms, graded_by, surface FROM attempts ORDER BY event_id",
        )
        self.assertEqual([row[:6] for row in upgraded], legacy)
        self.assertEqual({row[6] for row in upgraded}, {"learner"})
        self.assertEqual({row[7] for row in upgraded}, {None})
        self.assertEqual(_scheduler_rows(reopened.db_path), state_before)
        self.assertEqual(_proposal_rows(reopened.db_path), proposals_before)
        self.assertEqual(
            _rows(reopened.db_path, "SELECT MAX(version) FROM schema_migrations"), [(19,)]
        )
        self.assertEqual(_rows(reopened.db_path, "SELECT COUNT(*) FROM pending_attempts"), [(0,)])

    def test_schema_rejects_unknown_grader_and_measured_agent_grade(self) -> None:
        self._learner("item-a", "demonstrated", 4)
        with sqlite3.connect(self.workspace.db_path) as db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("UPDATE attempts SET graded_by = 'model'")
            with self.assertRaises(sqlite3.IntegrityError):
                # A measured (non-administered) attempt cannot be agent-graded.
                db.execute("UPDATE attempts SET graded_by = 'agent'")

    def test_schema_rejects_scheduler_writes_sourced_from_agent_grades(self) -> None:
        result = self._agent("item-a")
        state_json = json.dumps({"due": self.now.isoformat()})
        with sqlite3.connect(self.workspace.db_path) as db:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "agent-graded"):
                db.execute(
                    """INSERT INTO scheduler_proposals(
                           proposal_id, source_event_id, item_id, algorithm,
                           algorithm_version, learning_context, configuration_json,
                           previous_state_json, proposed_state_json, due_at,
                           rationale, created_at)
                       VALUES ('p-x', ?, 'item-a', 'fsrs', '6.3.2', 'atomic-recall',
                               '{}', NULL, ?, ?, 'r', ?)""",
                    (result.attempt.event_id, state_json, self.now.isoformat(),
                     self.now.isoformat()),
                )
            with self.assertRaisesRegex(sqlite3.IntegrityError, "agent-graded"):
                db.execute(
                    """INSERT INTO scheduler_state(
                           item_id, algorithm, algorithm_version, learning_context,
                           configuration_json, state_json, source_event_id, updated_at)
                       VALUES ('item-a', 'fsrs', '6.3.2', 'atomic-recall', '{}', ?, ?, ?)""",
                    (state_json, result.attempt.event_id, self.now.isoformat()),
                )


class PendingAttemptTableTests(_Fixture):
    def _open(self, db: sqlite3.Connection, token: str = "t" * 32) -> None:
        item = self.workspace.load_item("item-a")
        db.execute(
            """INSERT INTO pending_attempts(
                   attempt_token, item_id, item_content_hash, prompt_shown,
                   surface, created_at, expires_at)
               VALUES (?, 'item-a', ?, ?, 'mcp', ?, ?)""",
            (token, item.content_hash, item.prompt, self.now.isoformat(),
             (self.now + timedelta(minutes=30)).isoformat()),
        )

    def test_forward_lifecycle_open_responded_recorded(self) -> None:
        recorded = self._agent("item-a")
        with sqlite3.connect(self.workspace.db_path) as db:
            db.execute("PRAGMA foreign_keys = ON")
            self._open(db)
            self.assertEqual(
                db.execute("SELECT state, response FROM pending_attempts").fetchone(),
                ("open", None),
            )
            db.execute(
                "UPDATE pending_attempts SET state = 'responded', response = 'x',"
                " responded_at = ?", (self.now.isoformat(),),
            )
            db.execute(
                "UPDATE pending_attempts SET state = 'recorded', attempt_event_id = ?",
                (recorded.attempt.event_id,),
            )
            self.assertEqual(
                db.execute("SELECT state FROM pending_attempts").fetchone(), ("recorded",)
            )

    def test_rejects_invalid_inserts_transitions_and_deletes(self) -> None:
        recorded = self._agent("item-a")
        other = self._agent("item-b")
        item = self.workspace.load_item("item-a")
        with sqlite3.connect(self.workspace.db_path) as db:
            db.execute("PRAGMA foreign_keys = ON")
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute(
                    """INSERT INTO pending_attempts(
                           attempt_token, item_id, item_content_hash, prompt_shown,
                           response, responded_at, state, surface, created_at, expires_at)
                       VALUES (?, 'item-a', ?, 'P', 'x', ?, 'responded', 'mcp', ?, ?)""",
                    ("s" * 32, item.content_hash, self.now.isoformat(),
                     self.now.isoformat(), self.now.isoformat()),
                )
            self._open(db)
            rejected = [
                "UPDATE pending_attempts SET state = 'recorded'",
                "UPDATE pending_attempts SET prompt_shown = 'other prompt'",
                "UPDATE pending_attempts SET expires_at = '2099-01-01T00:00:00+00:00'",
                "UPDATE pending_attempts SET state = 'expired', response = 'late'",
                "DELETE FROM pending_attempts",
            ]
            for statement in rejected:
                with self.subTest(statement=statement):
                    with self.assertRaises(sqlite3.IntegrityError):
                        db.execute(statement)
            db.execute(
                "UPDATE pending_attempts SET state = 'responded', response = 'x',"
                " responded_at = ?", (self.now.isoformat(),),
            )
            for statement, params in [
                ("UPDATE pending_attempts SET response = 'changed'", ()),
                ("UPDATE pending_attempts SET state = 'open', response = NULL,"
                 " responded_at = NULL", ()),
                # The recorded attempt must be for the same item version.
                ("UPDATE pending_attempts SET state = 'recorded', attempt_event_id = ?",
                 (other.attempt.event_id,)),
            ]:
                with self.subTest(statement=statement):
                    with self.assertRaises(sqlite3.IntegrityError):
                        db.execute(statement, params)
            db.execute(
                "UPDATE pending_attempts SET state = 'recorded', attempt_event_id = ?",
                (recorded.attempt.event_id,),
            )
            for statement in (
                "UPDATE pending_attempts SET state = 'expired'",
                "DELETE FROM pending_attempts",
            ):
                with self.subTest(statement=statement):
                    with self.assertRaises(sqlite3.IntegrityError):
                        db.execute(statement)


class AgentGradedWritePathTests(_Fixture):
    def test_agent_grade_stores_attempt_without_proposal_or_state_change(self) -> None:
        self._learner("item-a", "demonstrated", 4)
        state_before = _scheduler_rows(self.workspace.db_path)
        proposals_before = _proposal_rows(self.workspace.db_path)
        due_before = [entry.to_dict() for entry in _review_due(self.workspace, self.now)]

        result = self._agent("item-a")

        self.assertIsNone(result.proposal)
        self.assertEqual(result.attempt.graded_by, "agent")
        self.assertEqual(_scheduler_rows(self.workspace.db_path), state_before)
        self.assertEqual(_proposal_rows(self.workspace.db_path), proposals_before)
        self.assertEqual(
            [entry.to_dict() for entry in _review_due(self.workspace, self.now)], due_before
        )
        stored = [a for a in self.workspace.list_attempts() if a["event_id"] == result.attempt.event_id]
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["graded_by"], "agent")
        self.assertEqual(stored[0]["surface"], "cli")
        self.assertTrue(stored[0]["administered"])
        self.assertIsNone(stored[0]["initial_latency_ms"])

    def test_agent_grade_on_unscheduled_item_creates_no_state(self) -> None:
        self._agent("item-b")
        self.assertEqual(_scheduler_rows(self.workspace.db_path), [])
        self.assertEqual(_proposal_rows(self.workspace.db_path), [])

    def test_learner_grade_records_learner_and_feeds_scheduler(self) -> None:
        explicit = PracticeService(self.workspace).run_administered(
            item_id="item-a", response="r", result="partial", confidence=3,
            graded_by="learner", now=self.now,
        )
        default = PracticeService(self.workspace).run_administered(
            item_id="item-b", response="r", result="partial", confidence=3, now=self.now,
        )
        for result in (explicit, default):
            self.assertEqual(result.attempt.graded_by, "learner")
            self.assertIsNotNone(result.proposal)
        state = _scheduler_rows(self.workspace.db_path)
        self.assertEqual([row[0] for row in state], ["item-a", "item-b"])
        # Same inputs at the same instant give byte-identical scheduler state.
        self.assertEqual(state[0][3], state[1][3])
        self._learner("item-c", "demonstrated", 5)
        by_item = {a["item_id"]: a for a in self.workspace.list_attempts()}
        self.assertEqual(
            {item: (a["graded_by"], a["surface"]) for item, a in by_item.items()},
            {"item-a": ("learner", "cli"), "item-b": ("learner", "cli"),
             "item-c": ("learner", "cli")},
        )

    def test_record_attempt_enforces_grader_and_proposal_pairing(self) -> None:
        learner = PracticeService(self.workspace).run_administered(
            item_id="item-a", response="r", result="partial", confidence=3, now=self.now,
        )
        stored = self.workspace.list_attempts()[0]
        base = {
            "event_id": "attempt-" + "c" * 32,
            "item_id": "item-b",
            "item_content_hash": self.workspace.load_item("item-b").content_hash,
            "occurred_at": self.now.isoformat(),
            "started_at": None,
            "completed_at": None,
            "initial_response": "r",
            "initial_latency_ms": None,
            "result": "partial",
            "confidence": 3,
            "open_notes": False,
            "agent_help": "substantial",
            "support_actions": [],
            "administered": True,
            "graded_by": "agent",
            "surface": "cli",
        }
        proposal = {
            "proposal_id": "proposal-x",
            "source_event_id": base["event_id"],
            "item_id": "item-b",
            "algorithm": learner.proposal.algorithm,
            "algorithm_version": learner.proposal.algorithm_version,
            "learning_context": learner.proposal.learning_context,
            "configuration": learner.proposal.configuration,
            "previous_state_json": None,
            "previous_source_event_id": None,
            "due_at": learner.proposal.due_at.isoformat(),
            "rationale": "r",
            "created_at": self.now.isoformat(),
        }
        before = self.workspace.db_path.read_bytes()
        cases = [
            ("agent with proposal", base, proposal, learner.proposal.proposed_state_json),
            ("learner without proposal", {**base, "graded_by": "learner"}, None, None),
            ("unknown grader", {**base, "graded_by": "model"}, None, None),
            ("measured agent grade", {**base, "administered": False}, None, None),
            ("bad surface", {**base, "surface": ""}, None, None),
        ]
        for label, attempt, prop, state in cases:
            with self.subTest(label=label):
                with self.assertRaises(WorkspaceError):
                    self.workspace.record_attempt(
                        attempt=attempt, proposal=prop, state_json=state
                    )
                self.assertEqual(self.workspace.db_path.read_bytes(), before)
        self.assertEqual(stored["graded_by"], "learner")

    def test_agent_grade_rejects_unknown_grader_value(self) -> None:
        with self.assertRaises(Exception):
            PracticeService(self.workspace).run_administered(
                item_id="item-a", response="r", result="partial", confidence=3,
                graded_by="model",
            )
        self.assertEqual(self.workspace.list_attempts(), [])


def _review_due(workspace: WorkspaceService, now: datetime):
    from virtuoso.review import ReviewService

    return ReviewService(workspace).due(now + timedelta(days=400))


class SelectionIgnoresAgentGradesTests(_Fixture):
    def test_next_and_compose_identical_before_and_after_agent_grade(self) -> None:
        self._learner("item-a", "demonstrated", 5)
        # Seven minutes later item-a (due again at +10 min) is not due, so
        # `next` offers the new item-b. The agent grade below is at +5 min;
        # if it fed FSRS, item-a would be due at +6 min and move to the front.
        later = self.now + timedelta(minutes=7)
        composer = SessionComposer(self.workspace)
        next_before = self.workspace.select_next(later)
        self.assertEqual(next_before.item.item_id, "item-b")
        compose_before = composer.compose(now=later).to_dict()
        compose_ml_before = composer.compose(now=later, focus="ml").to_dict()

        # A not-demonstrated grade would become the composition gap target if
        # composition read it, and it would reschedule item-b if it fed FSRS.
        self._agent("item-b", result="not-demonstrated", confidence=1)
        self._agent("item-a", result="not-demonstrated", confidence=1)

        next_after = self.workspace.select_next(later)
        self.assertEqual(next_after.item.item_id, next_before.item.item_id)
        self.assertEqual(next_after.action, next_before.action)
        self.assertEqual(next_after.rationale, next_before.rationale)
        self.assertEqual(next_after.alternatives, next_before.alternatives)
        self.assertEqual(composer.compose(now=later).to_dict(), compose_before)
        self.assertEqual(composer.compose(now=later, focus="ml").to_dict(), compose_ml_before)


class _CliFixture(_Fixture):
    def cli(self, *args: str, body: str | None = None, expected: int = 0):
        process = subprocess.run(
            [sys.executable, "-m", "virtuoso.cli", "--workspace", str(self.root), *args],
            input=body if body is not None else "",
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(process.returncode, expected, process.stdout + process.stderr)
        return process


class PracticeAdministerGradedByCliTests(_CliFixture):
    def _administer(self, *extra: str, expected: int = 0):
        return self.cli(
            "practice", "--item", "item-a", "--administer", "--response", "r",
            "--result", "not-demonstrated", "--confidence", "2", *extra, "--json",
            expected=expected,
        )

    def test_default_records_learner_grade_and_feeds_scheduler(self) -> None:
        payload = json.loads(self._administer().stdout)
        self.assertEqual(payload["graded_by"], "learner")
        self.assertIsNotNone(payload["proposal_due_at"])
        self.assertEqual(len(_scheduler_rows(self.workspace.db_path)), 1)

    def test_agent_grade_leaves_scheduler_state_and_due_date_unchanged(self) -> None:
        json.loads(self._administer("--graded-by", "learner").stdout)
        state_before = _scheduler_rows(self.workspace.db_path)
        proposals_before = _proposal_rows(self.workspace.db_path)
        due_before = self.cli("review", "due", "--json").stdout
        workload_before = self.cli("queries", "workload", "--json").stdout

        payload = json.loads(self._administer("--graded-by", "agent").stdout)

        self.assertEqual(payload["graded_by"], "agent")
        self.assertIsNone(payload["proposal_due_at"])
        self.assertIsNone(payload["proposal_algorithm"])
        self.assertTrue(payload["administered"])
        self.assertEqual(_scheduler_rows(self.workspace.db_path), state_before)
        self.assertEqual(_proposal_rows(self.workspace.db_path), proposals_before)
        self.assertEqual(self.cli("review", "due", "--json").stdout, due_before)
        self.assertEqual(self.cli("queries", "workload", "--json").stdout, workload_before)
        attempts = json.loads(self.cli("attempts", "--json").stdout)["attempts"]
        self.assertEqual(
            sorted((a["graded_by"], a["surface"]) for a in attempts),
            [("agent", "cli"), ("learner", "cli")],
        )

    def test_graded_by_requires_administer_and_a_known_value(self) -> None:
        before = self.workspace.db_path.read_bytes()
        failed = self.cli(
            "practice", "--item", "item-a", "--graded-by", "agent", expected=2
        )
        self.assertIn("--graded-by", failed.stderr)
        self._administer("--graded-by", "model", expected=2)
        self.assertEqual(self.workspace.db_path.read_bytes(), before)


class SurfaceAllowListTests(_CliFixture):
    def setUp(self) -> None:
        super().setUp()
        self.workspace.add_item(
            item_id="learn-item", title="L", focus="ml", prompt="How?", answer="Thus.",
            entry_mode="learn-first", learning_unit="Read this first.",
        )

    def _study(self, surface: str) -> dict:
        item = self.workspace.load_item("learn-item")
        return {
            "schema": "virtuoso/study-completion@0.1",
            "item_id": item.item_id,
            "item_content_hash": item.content_hash,
            "learning_unit_hash": item.learning_unit_hash,
            "completed": True,
            "surface": surface,
        }

    def _skip(self, surface: str, submission: str) -> dict:
        return {
            "schema": "virtuoso/review-skip@0.1",
            "submission_id": submission * 32,
            "item_id": "item-a",
            "item_content_hash": self.workspace.load_item("item-a").content_hash,
            "occurred_at": "2026-09-11T08:00:00+00:00",
            "surface": surface,
        }

    def test_study_record_accepts_mcp_and_http_and_rejects_unknown(self) -> None:
        before = self.workspace.db_path.read_bytes()
        failed = self.cli(
            "review", "study-record", "--json",
            body=json.dumps(self._study("telegram")), expected=2,
        )
        self.assertEqual(json.loads(failed.stderr)["error"]["code"], "invalid-request")
        self.assertEqual(self.workspace.db_path.read_bytes(), before)
        for surface in ("mcp", "http"):
            with self.subTest(surface=surface):
                # The second surface replays the same item version, which
                # returns the stored completion rather than a new event.
                payload = json.loads(self.cli(
                    "review", "study-record", "--json", body=json.dumps(self._study(surface)),
                ).stdout)
                self.assertEqual(payload["study"]["surface"], "mcp")
        self.assertEqual(len(self.workspace.list_study_events()), 1)

    def test_skip_accepts_mcp_and_http_and_rejects_unknown(self) -> None:
        before = self.workspace.db_path.read_bytes()
        for surface in ("telegram", "cli"):
            with self.subTest(surface=surface):
                failed = self.cli(
                    "review", "skip", "--json",
                    body=json.dumps(self._skip(surface, "e")), expected=2,
                )
                self.assertEqual(json.loads(failed.stderr)["error"]["code"], "invalid-request")
        self.assertEqual(self.workspace.db_path.read_bytes(), before)
        for surface, submission in (("mcp", "a"), ("http", "b")):
            with self.subTest(surface=surface):
                payload = json.loads(self.cli(
                    "review", "skip", "--json", body=json.dumps(self._skip(surface, submission)),
                ).stdout)
                self.assertEqual(payload["skip"]["surface"], surface)
        self.assertEqual(
            sorted(skip["surface"] for skip in self.workspace.list_review_skips()),
            ["http", "mcp"],
        )


class ReviewRecordStaysLearnerGradedTests(_CliFixture):
    def test_review_record_is_learner_graded_with_unknown_surface(self) -> None:
        item = self.workspace.load_item("item-a")
        request = {
            "schema": "virtuoso/review-attempt@0.1",
            "submission_id": "f" * 32,
            "item_id": item.item_id,
            "item_content_hash": item.content_hash,
            "started_at": "2026-09-03T12:00:00+00:00",
            "initial_answered_at": "2026-09-03T12:00:04+00:00",
            "completed_at": "2026-09-03T12:00:09+00:00",
            "initial_response": "typed",
            "retry": None,
            "hint_used": False,
            "answer_revealed": True,
            "result": "partial",
            "confidence": 3,
            "open_notes": False,
        }
        payload = json.loads(
            self.cli("review", "record", "--json", body=json.dumps(request)).stdout
        )
        # The v0.1 result contract is unchanged.
        self.assertEqual(payload["schema"], "virtuoso/review-attempt-result@0.1")
        self.assertEqual(set(payload["attempt"]), {
            "event_id", "item_id", "item_content_hash", "result", "confidence",
            "initial_latency_ms", "administered", "occurred_at",
        })
        attempts = json.loads(self.cli("attempts", "--json").stdout)["attempts"]
        self.assertEqual(
            [(a["graded_by"], a["surface"]) for a in attempts], [("learner", None)]
        )
        self.assertEqual(len(_scheduler_rows(self.workspace.db_path)), 1)


class QuerySchemaTests(_CliFixture):
    def test_history_reports_graded_by_per_attempt_at_0_2(self) -> None:
        self._learner("item-a", "demonstrated", 4)
        self._agent("item-a")
        payload = json.loads(self.cli("queries", "history", "--item", "item-a", "--json").stdout)
        self.assertEqual(payload["schema"], "virtuoso/item-history@0.2")
        self.assertEqual(
            [(a["graded_by"], a["result"]) for a in payload["attempts"]],
            [("agent", "not-demonstrated"), ("learner", "demonstrated")],
        )
        self.assertEqual(
            [entry.graded_by for entry in item_history(self.workspace.db_path, "item-a")],
            ["agent", "learner"],
        )
        text = json.dumps(payload)
        for secret in ("Prompt item-a?", "Answer item-a.", "a real answer"):
            self.assertNotIn(secret, text)

    def test_focus_counts_learner_grades_and_reports_agent_graded(self) -> None:
        self._learner("item-a", "demonstrated", 4)
        PracticeService(self.workspace).run_administered(
            item_id="item-b", response="r", result="partial", confidence=2, now=self.now,
        )
        self._agent("item-a", result="not-demonstrated", confidence=1)
        self._agent("item-b", result="demonstrated", confidence=5)
        payload = json.loads(self.cli("queries", "focus", "--json").stdout)
        self.assertEqual(payload["schema"], "virtuoso/focus-performance@0.2")
        by_focus = {entry["focus"]: entry for entry in payload["focuses"]}
        self.assertEqual(by_focus["ml"], {
            "focus": "ml",
            "items": 2,
            "attempts": 2,
            "demonstrated": 1,
            "partial": 1,
            "not_demonstrated": 0,
            "administered": 1,
            "agent_graded": 2,
            "mean_confidence": 3.0,
            "mean_latency_ms": 0.0,
        })
        self.assertEqual(by_focus["db"]["agent_graded"], 0)
        self.assertEqual(by_focus["db"]["attempts"], 0)
        summaries = {s.focus: s for s in focus_performance(self.workspace.db_path)}
        self.assertEqual(summaries["ml"].agent_graded, 2)


if __name__ == "__main__":
    unittest.main()
