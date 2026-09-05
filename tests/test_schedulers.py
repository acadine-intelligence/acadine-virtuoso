"""Scheduler portfolio: backend contract, SM-2, fail-closed switching (issue #47)."""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from conftest import downgrade_scheduler_switches_to_v15
from virtuoso.composition import CompositionError, SessionComposer
from virtuoso.practice import PracticeError, PracticeService
from virtuoso.review import ReviewService
from virtuoso.schedulers import (
    AttemptFacts,
    FsrsBackend,
    SchedulerConfigurationError,
    SchedulerStateError,
    Sm2Backend,
    builtin_algorithms,
    resolve_backend,
)
from virtuoso.workspace import WorkspaceError, WorkspaceService

NOW = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)


def _attempt(result: str, *, at: datetime = NOW, latency: int | None = 1200) -> AttemptFacts:
    return AttemptFacts(
        result=result,
        confidence=3,
        occurred_at=at,
        latency_ms=latency,
        administered=latency is None,
    )


class BackendContractTests(unittest.TestCase):
    """Every built-in backend satisfies the same contract."""

    def setUp(self) -> None:
        self.backends = [resolve_backend(name) for name in builtin_algorithms()]

    def test_registry_exposes_fsrs_and_sm2_only(self) -> None:
        self.assertEqual(builtin_algorithms(), ("fsrs", "sm2"))
        self.assertIsInstance(resolve_backend("fsrs"), FsrsBackend)
        self.assertIsInstance(resolve_backend("sm2"), Sm2Backend)
        for bad in ("", "  ", None, 3, "supermemo", "FSRS"):
            with self.subTest(bad=bad):
                with self.assertRaises(SchedulerConfigurationError):
                    resolve_backend(bad)

    def test_identical_input_gives_identical_output(self) -> None:
        for backend in self.backends:
            with self.subTest(backend=backend.name):
                configuration = backend.validate_configuration({})
                first = backend.propose(
                    previous_state_json=None,
                    attempt=_attempt("demonstrated"),
                    configuration=configuration,
                )
                second = backend.propose(
                    previous_state_json=None,
                    attempt=_attempt("demonstrated"),
                    configuration=configuration,
                )
                self.assertEqual(first, second)

    def test_due_never_precedes_attempt_and_state_round_trips(self) -> None:
        for backend in self.backends:
            with self.subTest(backend=backend.name):
                configuration = backend.validate_configuration({})
                state: str | None = None
                at = NOW
                for result in ("not-demonstrated", "partial", "demonstrated", "demonstrated"):
                    outcome = backend.propose(
                        previous_state_json=state,
                        attempt=_attempt(result, at=at),
                        configuration=configuration,
                    )
                    self.assertGreater(outcome.due_at, at)
                    self.assertEqual(backend.due_from_state(outcome.proposed_state_json), outcome.due_at)
                    self.assertIsInstance(json.loads(outcome.proposed_state_json), dict)
                    self.assertIn("do not assert competence", outcome.rationale)
                    state = outcome.proposed_state_json
                    at = outcome.due_at

    def test_administered_attempt_never_claims_a_latency(self) -> None:
        for backend in self.backends:
            with self.subTest(backend=backend.name):
                outcome = backend.propose(
                    previous_state_json=None,
                    attempt=_attempt("partial", latency=None),
                    configuration=backend.validate_configuration({}),
                )
                self.assertIn("unmeasured", outcome.rationale)
                self.assertNotIn("0 ms", outcome.rationale)

    def test_invalid_stored_state_fails_closed(self) -> None:
        for backend, bad in (
            (resolve_backend("fsrs"), "{}"),
            (resolve_backend("fsrs"), "not json"),
            (resolve_backend("sm2"), '{"easiness": "high"}'),
            (resolve_backend("sm2"), '{"easiness": 2.5, "repetitions": -1, "interval_days": 1}'),
            (resolve_backend("sm2"), "[]"),
        ):
            with self.subTest(backend=backend.name, bad=bad):
                with self.assertRaises(SchedulerStateError):
                    backend.propose(
                        previous_state_json=bad,
                        attempt=_attempt("demonstrated"),
                        configuration=backend.validate_configuration({}),
                    )

    def test_naive_attempt_timestamp_is_rejected(self) -> None:
        for backend in self.backends:
            with self.subTest(backend=backend.name):
                with self.assertRaises(SchedulerStateError):
                    backend.propose(
                        previous_state_json=None,
                        attempt=_attempt("demonstrated", at=datetime(2026, 8, 19, 12, 0)),
                        configuration=backend.validate_configuration({}),
                    )

    def test_unknown_configuration_keys_name_the_switch_command(self) -> None:
        with self.assertRaisesRegex(
            SchedulerConfigurationError, "unknown scheduler configuration fields for sm2"
        ) as caught:
            resolve_backend("sm2").validate_configuration({"desired_retention": 0.9})
        self.assertIn("virtuoso scheduler switch --to sm2", str(caught.exception))
        with self.assertRaisesRegex(
            SchedulerConfigurationError, "unknown scheduler configuration fields for fsrs"
        ):
            resolve_backend("fsrs").validate_configuration({"first_interval_days": 1})


class FsrsBackendTests(unittest.TestCase):
    def test_fsrs_configuration_and_version_are_unchanged(self) -> None:
        backend = resolve_backend("fsrs")
        self.assertEqual(backend.version, "6.3.2")
        self.assertEqual(
            backend.validate_configuration({}),
            {"desired_retention": 0.9, "enable_fuzzing": False},
        )
        self.assertEqual(
            backend.validate_configuration({"desired_retention": 0.8, "enable_fuzzing": True}),
            {"desired_retention": 0.8, "enable_fuzzing": True},
        )
        for bad in (
            {"desired_retention": "very high"},
            {"desired_retention": 1.0},
            {"desired_retention": 0},
            {"desired_retention": True},
            {"enable_fuzzing": "yes"},
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(SchedulerConfigurationError):
                    backend.validate_configuration(bad)

    def test_fsrs_rationale_and_rating_map_are_unchanged(self) -> None:
        backend = resolve_backend("fsrs")
        configuration = backend.validate_configuration({})
        for result, rating in (
            ("demonstrated", "Good"),
            ("partial", "Hard"),
            ("not-demonstrated", "Again"),
        ):
            outcome = backend.propose(
                previous_state_json=None,
                attempt=_attempt(result),
                configuration=configuration,
            )
            self.assertTrue(
                outcome.rationale.startswith(f"FSRS rating {rating} from result {result}; "),
                outcome.rationale,
            )
            self.assertIn("latency 1200 ms", outcome.rationale)


class Sm2BackendTests(unittest.TestCase):
    """SM-2 as published (Wozniak, 1990), from the description alone."""

    def setUp(self) -> None:
        self.backend = Sm2Backend()
        self.configuration = self.backend.validate_configuration({})

    def _run(self, results: list[str]) -> list[dict]:
        states: list[dict] = []
        state: str | None = None
        at = NOW
        for result in results:
            outcome = self.backend.propose(
                previous_state_json=state,
                attempt=_attempt(result, at=at),
                configuration=self.configuration,
            )
            states.append(json.loads(outcome.proposed_state_json))
            state = outcome.proposed_state_json
            at = outcome.due_at
        return states

    def test_defaults_and_version(self) -> None:
        self.assertEqual(self.backend.name, "sm2")
        self.assertEqual(self.backend.version, "sm2-1990/1")
        self.assertEqual(
            self.configuration,
            {"first_interval_days": 1, "second_interval_days": 6, "minimum_easiness": 1.3},
        )

    def test_interval_ladder_one_six_then_easiness_product(self) -> None:
        states = self._run(["demonstrated", "demonstrated", "demonstrated", "demonstrated"])
        # 15 * 2.5 = 37.5 rounds half up to 38 (not banker's rounding).
        self.assertEqual([s["interval_days"] for s in states], [1, 6, 15, 38])
        self.assertEqual([s["repetitions"] for s in states], [1, 2, 3, 4])
        # quality 4: EF += 0.1 - 1 * (0.08 + 0.02) = 0, so easiness stays 2.5
        self.assertTrue(all(abs(s["easiness"] - 2.5) < 1e-9 for s in states))
        self.assertEqual(states[0]["due"], (NOW + timedelta(days=1)).isoformat())
        self.assertEqual(states[0]["last_review"], NOW.isoformat())

    def test_partial_lowers_easiness_and_still_advances(self) -> None:
        states = self._run(["partial", "partial", "partial"])
        # quality 3: EF += 0.1 - 2 * (0.08 + 2 * 0.02) = -0.14 per response
        self.assertAlmostEqual(states[0]["easiness"], 2.36, places=9)
        self.assertAlmostEqual(states[1]["easiness"], 2.22, places=9)
        self.assertAlmostEqual(states[2]["easiness"], 2.08, places=9)
        self.assertEqual([s["interval_days"] for s in states], [1, 6, 12])

    def test_failure_restarts_repetitions_without_touching_easiness(self) -> None:
        states = self._run(["partial", "demonstrated", "not-demonstrated", "demonstrated"])
        self.assertAlmostEqual(states[1]["easiness"], 2.36, places=9)
        failed = states[2]
        self.assertEqual(failed["repetitions"], 0)
        self.assertEqual(failed["interval_days"], 1)
        self.assertAlmostEqual(failed["easiness"], 2.36, places=9)
        self.assertEqual(states[3]["repetitions"], 1)
        self.assertEqual(states[3]["interval_days"], 1)

    def test_easiness_never_drops_below_the_floor(self) -> None:
        states = self._run(["partial"] * 12)
        self.assertTrue(all(s["easiness"] >= 1.3 - 1e-9 for s in states))
        self.assertAlmostEqual(states[-1]["easiness"], 1.3, places=9)

    def test_rationale_names_quality_repetition_and_interval(self) -> None:
        outcome = self.backend.propose(
            previous_state_json=None,
            attempt=_attempt("partial"),
            configuration=self.configuration,
        )
        self.assertIn("SM-2 quality 3 from result partial", outcome.rationale)
        self.assertIn("repetition 1, next interval 1 day(s)", outcome.rationale)
        self.assertIn("latency 1200 ms", outcome.rationale)

    def test_configuration_validation(self) -> None:
        ok = self.backend.validate_configuration(
            {"first_interval_days": 2, "second_interval_days": 5, "minimum_easiness": 1.5}
        )
        self.assertEqual(
            ok, {"first_interval_days": 2, "second_interval_days": 5, "minimum_easiness": 1.5}
        )
        for bad in (
            {"first_interval_days": 0},
            {"first_interval_days": 1.5},
            {"first_interval_days": True},
            {"second_interval_days": 0},
            {"first_interval_days": 7, "second_interval_days": 3},
            {"minimum_easiness": 0.9},
            {"minimum_easiness": 2.6},
            {"minimum_easiness": "low"},
            {"desired_retention": 0.9},
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(SchedulerConfigurationError):
                    self.backend.validate_configuration(bad)

    def test_custom_ladder_is_honoured(self) -> None:
        self.configuration = self.backend.validate_configuration(
            {"first_interval_days": 2, "second_interval_days": 4}
        )
        states = self._run(["demonstrated", "demonstrated", "demonstrated"])
        self.assertEqual([s["interval_days"] for s in states], [2, 4, 10])


class WorkspaceSchedulerTests(unittest.TestCase):
    """Configuration, guard, and switch through the workspace."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve() / "learner"
        self.workspace = WorkspaceService.init(self.root)
        for item_id in ("alpha", "beta"):
            self.workspace.add_item(
                item_id=item_id,
                title=f"Item {item_id}",
                focus="scheduling",
                prompt=f"Prompt {item_id}?",
                answer=f"Answer {item_id}.",
            )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write_scheduler(self, scheduler: dict) -> None:
        config = json.loads(self.workspace.config_path.read_text())
        config["scheduler"] = scheduler
        self.workspace.config_path.write_text(json.dumps(config, indent=2))

    def _practise(self, item_id: str, result: str = "demonstrated", *, at: datetime = NOW):
        return PracticeService(self.workspace).run_administered(
            item_id=item_id,
            response="an answer",
            result=result,
            confidence=4,
            now=at,
        )

    def _state_rows(self) -> list[tuple]:
        with sqlite3.connect(self.workspace.db_path) as db:
            return db.execute(
                "SELECT item_id, algorithm, algorithm_version FROM scheduler_state "
                "ORDER BY item_id, algorithm"
            ).fetchall()

    def test_init_still_produces_an_fsrs_workspace(self) -> None:
        settings = self.workspace.scheduler_settings()
        self.assertEqual(settings.algorithm, "fsrs")
        self.assertEqual(settings.algorithm_version, "6.3.2")
        self.assertEqual(settings.learning_context, "atomic-recall")
        self.assertEqual(
            settings.configuration, {"desired_retention": 0.9, "enable_fuzzing": False}
        )
        self.assertEqual(self.workspace.list_scheduler_switches(), [])

    def test_sm2_workspace_runs_attempt_to_proposal_to_due_to_workload(self) -> None:
        self._write_scheduler({"algorithm": "sm2", "context": "atomic-recall"})
        settings = self.workspace.scheduler_settings()
        self.assertEqual(settings.algorithm, "sm2")
        self.assertEqual(settings.algorithm_version, "sm2-1990/1")

        result = self._practise("alpha", "partial")
        self.assertEqual(result.proposal.algorithm, "sm2")
        self.assertEqual(result.proposal.algorithm_version, "sm2-1990/1")
        self.assertEqual(
            result.proposal.configuration,
            {"first_interval_days": 1, "second_interval_days": 6, "minimum_easiness": 1.3},
        )
        self.assertEqual(result.proposal.due_at, NOW + timedelta(days=1))
        self.assertIn("SM-2 quality 3", result.proposal.rationale)
        self.assertEqual(self._state_rows(), [("alpha", "sm2", "sm2-1990/1")])

        stored = self.workspace.list_proposals()[0]
        self.assertEqual(stored["algorithm"], "sm2")
        self.assertEqual(stored["configuration"]["second_interval_days"], 6)

        second = self._practise("alpha", "demonstrated", at=NOW + timedelta(days=1))
        self.assertEqual(second.proposal.due_at, NOW + timedelta(days=7))
        self.assertEqual(second.proposal.previous_source_event_id, result.attempt.event_id)

        selection = self.workspace.select_next(NOW + timedelta(days=1, hours=1))
        self.assertEqual(selection.item.item_id, "beta")
        queue = ReviewService(self.workspace).due(now=NOW + timedelta(days=8))
        self.assertEqual([entry.item_id for entry in queue], ["alpha", "beta"])
        self.assertEqual(
            self.workspace.doctor(now=NOW + timedelta(days=8))["workload"],
            {"due_now": 1, "scheduled_total": 1, "new_items": 1},
        )
        report = self.workspace.doctor(now=NOW)
        self.assertEqual(report["status"], "healthy")
        self.assertEqual(report["scheduler"]["algorithm"], "sm2")
        self.assertIsNone(report["scheduler"]["unrecorded_switch_from"])

    def test_sm2_configuration_mismatch_fails_closed_like_fsrs(self) -> None:
        self._write_scheduler({"algorithm": "sm2", "context": "atomic-recall"})
        self._practise("alpha")
        self._write_scheduler(
            {"algorithm": "sm2", "context": "atomic-recall", "second_interval_days": 4}
        )
        with self.assertRaisesRegex(PracticeError, "SM-2 state has an incompatible scheduler configuration"):
            self._practise("alpha", at=NOW + timedelta(days=1))
        self.assertEqual(len(self.workspace.list_attempts()), 1)

    def test_configuration_with_wrong_algorithm_keys_fails_before_any_write(self) -> None:
        self._write_scheduler(
            {
                "algorithm": "sm2",
                "context": "atomic-recall",
                "desired_retention": 0.9,
                "enable_fuzzing": False,
            }
        )
        with self.assertRaisesRegex(WorkspaceError, "unknown scheduler configuration fields for sm2"):
            self.workspace.scheduler_settings()
        with self.assertRaisesRegex(PracticeError, "virtuoso scheduler switch --to sm2"):
            self._practise("alpha")
        self.assertEqual(self.workspace.list_attempts(), [])
        with self.assertRaisesRegex(WorkspaceError, "unsupported built-in scheduler: 'sm3'"):
            self._write_scheduler({"algorithm": "sm3", "context": "atomic-recall"})
            self.workspace.scheduler_settings()

    def test_editing_the_algorithm_by_hand_fails_closed_everywhere(self) -> None:
        self._practise("alpha")
        self._write_scheduler({"algorithm": "sm2", "context": "atomic-recall"})
        expected = (
            "scheduler algorithm changed from fsrs to sm2 without a recorded switch; "
            "run: virtuoso scheduler switch --to sm2"
        )
        with self.assertRaisesRegex(PracticeError, expected):
            self._practise("beta")
        with self.assertRaisesRegex(WorkspaceError, expected):
            self.workspace.select_next(NOW)
        with self.assertRaisesRegex(WorkspaceError, expected):
            ReviewService(self.workspace).due(now=NOW)
        with self.assertRaisesRegex(CompositionError, expected):
            SessionComposer(self.workspace).compose(now=NOW)
        with self.assertRaisesRegex(WorkspaceError, expected):
            self.workspace.scheduler_settings()
        report = self.workspace.doctor(now=NOW)
        self.assertEqual(report["status"], "needs-attention")
        self.assertEqual(report["scheduler"]["unrecorded_switch_from"], "fsrs")
        self.assertEqual(len(self.workspace.list_attempts()), 1)
        self.assertEqual(self._state_rows(), [("alpha", "fsrs", "6.3.2")])

    def test_switch_records_row_rewrites_configuration_and_clears_the_guard(self) -> None:
        self._practise("alpha")
        before = self.workspace.config_path.stat()
        switched = self.workspace.switch_scheduler(to_algorithm="sm2", now=NOW + timedelta(hours=1))

        self.assertEqual(switched["schema"], "virtuoso/scheduler-switch@0.1")
        self.assertEqual(switched["from_algorithm"], "fsrs")
        self.assertEqual(switched["to_algorithm"], "sm2")
        self.assertEqual(switched["learning_context"], "atomic-recall")
        self.assertEqual(switched["mode"], "fresh")
        self.assertEqual(switched["items_with_prior_state"], 1)
        self.assertEqual(switched["algorithm_version"], "sm2-1990/1")
        self.assertEqual(
            switched["configuration"],
            {"first_interval_days": 1, "second_interval_days": 6, "minimum_easiness": 1.3},
        )
        config = json.loads(self.workspace.config_path.read_text())
        self.assertEqual(
            config["scheduler"],
            {
                "algorithm": "sm2",
                "context": "atomic-recall",
                "first_interval_days": 1,
                "second_interval_days": 6,
                "minimum_easiness": 1.3,
            },
        )
        self.assertEqual(config["schema"], "virtuoso/workspace@0.1")
        self.assertEqual(oct(self.workspace.config_path.stat().st_mode & 0o777), oct(0o600))
        self.assertNotEqual(self.workspace.config_path.stat().st_ino, before.st_ino)
        self.assertEqual([p.name for p in self.root.iterdir() if p.name.endswith(".tmp")], [])

        history = self.workspace.list_scheduler_switches()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["switch_id"], switched["switch_id"])
        self.assertEqual(self.workspace.scheduler_settings().algorithm, "sm2")

        # The target sees every item as new; FSRS history stays queryable.
        selection = self.workspace.select_next(NOW + timedelta(hours=2))
        self.assertEqual(selection.action, "practice")
        self.assertEqual(selection.item.item_id, "alpha")
        self.assertEqual(
            self.workspace.doctor(now=NOW + timedelta(hours=2))["workload"],
            {"due_now": 0, "scheduled_total": 0, "new_items": 2},
        )
        fresh = self._practise("alpha", at=NOW + timedelta(hours=2))
        self.assertEqual(fresh.proposal.algorithm, "sm2")
        self.assertIsNone(fresh.proposal.previous_state_json)
        self.assertEqual(
            self._state_rows(), [("alpha", "fsrs", "6.3.2"), ("alpha", "sm2", "sm2-1990/1")]
        )
        self.assertEqual(
            [p["algorithm"] for p in self.workspace.list_proposals()], ["fsrs", "sm2"]
        )
        report = self.workspace.doctor(now=NOW + timedelta(hours=2))
        self.assertEqual(report["status"], "healthy")
        self.assertEqual(report["scheduler"]["last_switch"]["to_algorithm"], "sm2")

    def test_switch_after_a_hand_edit_names_the_algorithm_that_holds_state(self) -> None:
        self._practise("alpha")
        self._write_scheduler({"algorithm": "sm2", "context": "atomic-recall"})
        switched = self.workspace.switch_scheduler(to_algorithm="sm2", now=NOW + timedelta(hours=1))
        self.assertEqual(switched["from_algorithm"], "fsrs")
        self.assertEqual(switched["items_with_prior_state"], 1)
        self.assertEqual(self.workspace.scheduler_settings().algorithm, "sm2")

    def test_switch_back_and_forth_keeps_both_histories(self) -> None:
        self._practise("alpha")
        self.workspace.switch_scheduler(to_algorithm="sm2", now=NOW + timedelta(hours=1))
        self._practise("beta", at=NOW + timedelta(hours=2))
        back = self.workspace.switch_scheduler(to_algorithm="fsrs", now=NOW + timedelta(hours=3))
        self.assertEqual(back["from_algorithm"], "sm2")
        self.assertEqual(back["items_with_prior_state"], 1)
        settings = self.workspace.scheduler_settings()
        self.assertEqual(settings.algorithm, "fsrs")
        self.assertEqual(settings.configuration, {"desired_retention": 0.9, "enable_fuzzing": False})
        # alpha's FSRS state is still there, so it is scheduled, not new.
        self.assertEqual(
            self.workspace.doctor(now=NOW + timedelta(hours=3))["workload"],
            {"due_now": 1, "scheduled_total": 1, "new_items": 1},
        )
        self.assertEqual(
            [(s["from_algorithm"], s["to_algorithm"]) for s in self.workspace.list_scheduler_switches()],
            [("fsrs", "sm2"), ("sm2", "fsrs")],
        )

    def test_switch_rejects_same_algorithm_unknown_algorithm_and_naive_time(self) -> None:
        with self.assertRaisesRegex(WorkspaceError, "already fsrs; nothing to switch"):
            self.workspace.switch_scheduler(to_algorithm="fsrs")
        with self.assertRaisesRegex(WorkspaceError, "unsupported built-in scheduler: 'leitner'"):
            self.workspace.switch_scheduler(to_algorithm="leitner")
        with self.assertRaisesRegex(WorkspaceError, "timezone-aware"):
            self.workspace.switch_scheduler(to_algorithm="sm2", now=datetime(2026, 8, 19))
        self.assertEqual(self.workspace.list_scheduler_switches(), [])
        self.assertEqual(json.loads(self.workspace.config_path.read_text())["scheduler"]["algorithm"], "fsrs")

    def test_switch_rows_are_append_only(self) -> None:
        self.workspace.switch_scheduler(to_algorithm="sm2", now=NOW)
        with sqlite3.connect(self.workspace.db_path) as db:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                db.execute("UPDATE scheduler_switches SET mode = 'fresh'")
            with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                db.execute("DELETE FROM scheduler_switches")
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute(
                    """INSERT INTO scheduler_switches(
                           switch_id, from_algorithm, to_algorithm, learning_context,
                           mode, items_with_prior_state, occurred_at
                       ) VALUES ('x', 'sm2', 'sm2', 'atomic-recall', 'fresh', 0, '2026-01-01T00:00:00+00:00')"""
                )

    def test_switch_writes_nothing_when_configuration_replacement_fails(self) -> None:
        self._practise("alpha")
        original = self.workspace.config_path.read_bytes()
        # A read-only workspace root refuses the temporary file, so the
        # switch row inside the same transaction must roll back.
        self.root.chmod(0o500)
        try:
            with self.assertRaisesRegex(WorkspaceError, "cannot write workspace configuration"):
                self.workspace.switch_scheduler(to_algorithm="sm2", now=NOW)
        finally:
            self.root.chmod(0o700)
        self.assertEqual(self.workspace.list_scheduler_switches(), [])
        self.assertEqual(self.workspace.config_path.read_bytes(), original)
        self.assertEqual([p.name for p in self.root.iterdir() if p.name.endswith(".tmp")], [])
        self.assertEqual(self.workspace.scheduler_settings().algorithm, "fsrs")

    def test_v15_workspace_migrates_without_inventing_switch_history(self) -> None:
        self._practise("alpha")
        with sqlite3.connect(self.workspace.db_path) as db:
            before = db.execute("SELECT * FROM scheduler_state").fetchall()
            downgrade_scheduler_switches_to_v15(db)
            self.assertEqual(
                db.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0], 15
            )
        reopened = WorkspaceService.open(self.root)
        with sqlite3.connect(reopened.db_path) as db:
            self.assertEqual(db.execute("SELECT * FROM scheduler_state").fetchall(), before)
            self.assertEqual(
                db.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0], 16
            )
            self.assertEqual(db.execute("SELECT COUNT(*) FROM scheduler_switches").fetchone()[0], 0)
        self.assertEqual(reopened.scheduler_settings().algorithm, "fsrs")

    def test_open_does_not_recreate_a_missing_switch_table(self) -> None:
        with sqlite3.connect(self.workspace.db_path) as db:
            db.execute("DROP TABLE scheduler_switches")
        with self.assertRaisesRegex(WorkspaceError, "missing objects"):
            WorkspaceService.open(self.root)

    def test_record_attempt_validates_every_algorithm_state_due(self) -> None:
        item = self.workspace.load_item("alpha")
        attempt = {
            "event_id": "attempt-" + "a" * 32,
            "item_id": item.item_id,
            "item_content_hash": item.content_hash,
            "occurred_at": NOW.isoformat(),
            "started_at": (NOW - timedelta(seconds=1)).isoformat(),
            "completed_at": NOW.isoformat(),
            "initial_response": "x",
            "initial_latency_ms": 10,
            "result": "partial",
            "confidence": 3,
            "open_notes": False,
            "agent_help": "none",
            "support_actions": [],
            "administered": False,
        }
        proposal = {
            "proposal_id": "proposal-" + "b" * 32,
            "source_event_id": attempt["event_id"],
            "item_id": item.item_id,
            "algorithm": "sm2",
            "algorithm_version": "sm2-1990/1",
            "learning_context": "atomic-recall",
            "configuration": {},
            "previous_state_json": None,
            "previous_source_event_id": None,
            "due_at": (NOW + timedelta(days=1)).isoformat(),
            "rationale": "fixture",
            "created_at": NOW.isoformat(),
        }
        with self.assertRaisesRegex(WorkspaceError, "does not match the proposed scheduler state"):
            self.workspace.record_attempt(
                attempt=attempt,
                proposal=proposal,
                state_json=json.dumps({"due": (NOW + timedelta(days=2)).isoformat()}),
            )
        with self.assertRaisesRegex(WorkspaceError, "proposed scheduler state due timestamp"):
            self.workspace.record_attempt(
                attempt=attempt, proposal=proposal, state_json=json.dumps({"easiness": 2.5})
            )
        self.assertEqual(self.workspace.list_attempts(), [])


class SchedulerCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name).resolve() / "learner"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _run(self, *args: str, expected: int = 0) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(
            [sys.executable, "-m", "virtuoso.cli", "--workspace", str(self.workspace), *args],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            expected,
            msg=f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}",
        )
        return completed

    def test_scheduler_show_switch_history_and_guard_through_the_cli(self) -> None:
        self._run("init", "--json")
        self._run(
            "add", "--id", "alpha", "--title", "Alpha", "--focus", "scheduling",
            "--prompt", "Alpha?", "--answer", "Alpha.", "--json",
        )
        shown = json.loads(self._run("scheduler", "show", "--json").stdout)
        self.assertEqual(shown["schema"], "virtuoso/scheduler-settings@0.1")
        self.assertEqual(shown["algorithm"], "fsrs")
        self.assertEqual(shown["algorithm_version"], "6.3.2")
        self.assertEqual(shown["built_in_algorithms"], ["fsrs", "sm2"])

        self._run(
            "practice", "--item", "alpha", "--administer", "--response", "a",
            "--result", "demonstrated", "--confidence", "4", "--json",
        )

        config_path = self.workspace / "virtuoso.json"
        config = json.loads(config_path.read_text())
        config["scheduler"] = {"algorithm": "sm2", "context": "atomic-recall"}
        config_path.write_text(json.dumps(config))
        failed = self._run("next", "--json", expected=2)
        self.assertIn("run: virtuoso scheduler switch --to sm2", failed.stderr)
        failed = self._run("review", "due", "--json", expected=2)
        self.assertIn("virtuoso scheduler switch --to sm2", failed.stderr)
        doctor = json.loads(self._run("doctor", "--json").stdout)
        self.assertEqual(doctor["status"], "needs-attention")
        self.assertEqual(doctor["scheduler"]["unrecorded_switch_from"], "fsrs")

        switched = json.loads(self._run("scheduler", "switch", "--to", "sm2", "--json").stdout)
        self.assertEqual(switched["from_algorithm"], "fsrs")
        self.assertEqual(switched["to_algorithm"], "sm2")
        self.assertEqual(switched["items_with_prior_state"], 1)
        history = json.loads(self._run("scheduler", "history", "--json").stdout)
        self.assertEqual(history["schema"], "virtuoso/scheduler-history@0.1")
        self.assertEqual(len(history["switches"]), 1)

        nxt = json.loads(self._run("next", "--json").stdout)
        self.assertEqual(nxt["item_id"], "alpha")
        practised = json.loads(
            self._run(
                "practice", "--item", "alpha", "--administer", "--response", "a",
                "--result", "partial", "--confidence", "3", "--json",
            ).stdout
        )
        self.assertEqual(practised["proposal_algorithm"], "sm2")
        attempts = json.loads(self._run("attempts", "--json").stdout)
        self.assertEqual([p["algorithm"] for p in attempts["proposals"]], ["fsrs", "sm2"])
        self.assertEqual(attempts["proposals"][1]["algorithm_version"], "sm2-1990/1")
        doctor = json.loads(self._run("doctor", "--json").stdout)
        self.assertEqual(doctor["status"], "healthy")
        self.assertEqual(doctor["scheduler"]["algorithm"], "sm2")
        workload = json.loads(self._run("queries", "workload", "--json").stdout)
        self.assertEqual(workload["focuses"][0]["scheduled"], 1)

        again = self._run("scheduler", "switch", "--to", "sm2", "--json", expected=2)
        self.assertIn("already sm2", again.stderr)
        unknown = self._run("scheduler", "switch", "--to", "leitner", expected=2)
        self.assertIn("unsupported built-in scheduler", unknown.stderr)


if __name__ == "__main__":
    unittest.main()
