"""Optional FSRS interval floor, using synthetic workspaces only."""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch as mock_patch
from datetime import datetime, timedelta, timezone

from virtuoso.schedulers import AttemptFacts, FsrsBackend, SchedulerConfigurationError, SchedulerStateError

from virtuoso.practice import PracticeError, PracticeService
from virtuoso.workspace import WorkspaceError, WorkspaceService

NOW = datetime(2026, 9, 5, 12, tzinfo=timezone.utc)


def facts(result: str, at: datetime = NOW) -> AttemptFacts:
    return AttemptFacts(
        result=result, confidence=3, occurred_at=at,
        latency_ms=None, administered=True,
    )


class MinimumIntervalBackendTests(unittest.TestCase):
    def test_unrepresentable_floor_fails_with_scheduler_error(self) -> None:
        with self.assertRaisesRegex(SchedulerStateError, "minimum interval.*timestamp"):
            FsrsBackend().propose(
                previous_state_json=None,
                attempt=facts("not-demonstrated", datetime(9999, 12, 30, tzinfo=timezone.utc)),
                configuration={"minimum_interval_days": 7},
            )

    def test_minimum_accepts_only_bounded_whole_days(self) -> None:
        backend = FsrsBackend()
        for value in (0, 1, 7, 36500):
            with self.subTest(value=value):
                config = backend.validate_configuration({"minimum_interval_days": value})
                self.assertEqual(config["minimum_interval_days"], value)
        for value in (-1, 0.5, 1.0, True, False, "1", None, [], {}, float("nan"), float("inf"), 36501, 10**100):
            with self.subTest(value=value):
                with self.assertRaisesRegex(SchedulerConfigurationError, "minimum_interval_days"):
                    backend.validate_configuration({"minimum_interval_days": value})

    def test_zero_and_omission_keep_identical_fsrs_behavior(self) -> None:
        backend = FsrsBackend()
        state = None
        at = NOW
        for result in ("partial", "demonstrated", "demonstrated", "not-demonstrated"):
            plain = backend.propose(
                previous_state_json=state, attempt=facts(result, at),
                configuration=backend.default_configuration(),
            )
            zero = backend.propose(
                previous_state_json=state, attempt=facts(result, at),
                configuration={**backend.default_configuration(), "minimum_interval_days": 0},
            )
            self.assertEqual(plain, zero)
            state, at = plain.proposed_state_json, plain.due_at

    def test_floor_preserves_memory_parameters_and_longer_intervals_through_lapse(self) -> None:
        backend = FsrsBackend()
        state = None
        at = NOW
        longer_seen = False
        for result in ["demonstrated"] * 5 + ["not-demonstrated", "partial", "demonstrated"]:
            raw = backend.propose(
                previous_state_json=state, attempt=facts(result, at), configuration={},
            )
            floored = backend.propose(
                previous_state_json=state, attempt=facts(result, at),
                configuration={"minimum_interval_days": 1},
            )
            self.assertEqual(floored.due_at, max(raw.due_at, at + timedelta(days=1)))
            raw_card, floor_card = json.loads(raw.proposed_state_json), json.loads(floored.proposed_state_json)
            raw_card.pop("due")
            floor_card.pop("due")
            self.assertEqual(raw_card, floor_card)
            if result == "not-demonstrated":
                self.assertLess(raw.due_at, floored.due_at)
                self.assertEqual(floored.due_at, at + timedelta(days=1))
            longer_seen |= raw.due_at > at + timedelta(days=1)
            state, at = floored.proposed_state_json, floored.due_at
        self.assertTrue(longer_seen)

    def test_custom_floor_is_elapsed_time_in_utc(self) -> None:
        at = NOW.astimezone(timezone(timedelta(hours=2)))
        outcome = FsrsBackend().propose(
            previous_state_json=None, attempt=facts("partial", at),
            configuration={"minimum_interval_days": 7},
        )
        self.assertEqual(outcome.due_at, NOW + timedelta(days=7))
        self.assertEqual(outcome.due_at.utcoffset(), timedelta(0))

    def test_one_day_floor_applies_to_each_result_and_serialized_due(self) -> None:
        backend = FsrsBackend()
        for result in ("demonstrated", "partial", "not-demonstrated"):
            with self.subTest(result=result):
                outcome = backend.propose(
                    previous_state_json=None,
                    attempt=facts(result),
                    configuration={**backend.default_configuration(), "minimum_interval_days": 1},
                )
                self.assertEqual(outcome.due_at, NOW + timedelta(days=1))
                self.assertEqual(backend.due_from_state(outcome.proposed_state_json), outcome.due_at)
                self.assertIn("minimum interval", outcome.rationale)
                self.assertIn("1 day", outcome.rationale)
                self.assertIn("original due", outcome.rationale)
                self.assertEqual(json.loads(outcome.proposed_state_json)["last_review"], NOW.isoformat())


class MinimumIntervalWorkspaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.workspace = WorkspaceService.init(Path(self.tmp.name).resolve() / "learner")
        self.workspace.add_item(
            item_id="alpha", title="Synthetic item", focus="scheduling",
            prompt="Explain retrieval.", answer="Retrieval strengthens later access.",
        )

    def practice(self, at: datetime = NOW, result: str = "not-demonstrated"):
        return PracticeService(self.workspace).run_administered(
            item_id="alpha", response="Synthetic response", result=result,
            confidence=3, now=at,
        )

    def cli(self, *args: str, expected: int = 0):
        proc = subprocess.run(
            [sys.executable, "-m", "virtuoso.cli", "--workspace", str(self.workspace.root), *args],
            capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(proc.returncode, expected, proc.stdout + proc.stderr)
        return proc

    def test_changed_floor_before_record_rejects_stale_proposal(self) -> None:
        record = self.workspace.record_attempt
        before = self.workspace.db_path.read_bytes()

        def change_floor(**kwargs):
            self.workspace.configure_scheduler(minimum_interval_days=1)
            return record(**kwargs)

        with mock_patch.object(self.workspace, "record_attempt", side_effect=change_floor):
            with self.assertRaisesRegex(PracticeError, "minimum interval changed"):
                self.practice()
        self.assertEqual(self.workspace.db_path.read_bytes(), before)

    def test_record_rejects_due_below_floor_even_when_state_due_matches(self) -> None:
        self.workspace.configure_scheduler(minimum_interval_days=1)
        record = self.workspace.record_attempt
        before = self.workspace.db_path.read_bytes()

        def shorten_due(**kwargs):
            due = (NOW + timedelta(minutes=5)).isoformat()
            kwargs["proposal"]["due_at"] = due
            state = json.loads(kwargs["state_json"])
            state["due"] = due
            kwargs["state_json"] = json.dumps(state)
            return record(**kwargs)

        with mock_patch.object(self.workspace, "record_attempt", side_effect=shorten_due):
            with self.assertRaisesRegex(PracticeError, "shorter than.*minimum interval"):
                self.practice()
        self.assertEqual(self.workspace.db_path.read_bytes(), before)

    def test_invalid_cli_setting_leaves_config_and_database_unchanged(self) -> None:
        before_config = self.workspace.config_path.read_bytes()
        before_db = self.workspace.db_path.read_bytes()
        for value in ("-1", "1.5", "true", "NaN", "36501"):
            with self.subTest(value=value):
                failed = self.cli("scheduler", "configure", "--minimum-interval-days", value, expected=2)
                self.assertNotIn("Traceback", failed.stderr)
                self.assertEqual(self.workspace.config_path.read_bytes(), before_config)
                self.assertEqual(self.workspace.db_path.read_bytes(), before_db)
        for value in (True, None, 1.0):
            with self.subTest(value=value):
                with self.assertRaisesRegex(WorkspaceError, "minimum_interval_days"):
                    self.workspace.configure_scheduler(minimum_interval_days=value)
                self.assertEqual(self.workspace.config_path.read_bytes(), before_config)
                self.assertEqual(self.workspace.db_path.read_bytes(), before_db)

    def test_sm2_configure_rejects_without_writes(self) -> None:
        self.workspace.switch_scheduler(to_algorithm="sm2", now=NOW)
        before = (self.workspace.config_path.read_bytes(), self.workspace.db_path.read_bytes())
        failed = self.cli("scheduler", "configure", "--minimum-interval-days", "1", expected=2)
        self.assertIn("only for FSRS", failed.stderr)
        self.assertEqual((self.workspace.config_path.read_bytes(), self.workspace.db_path.read_bytes()), before)

    def test_replace_failure_keeps_original_config_and_database(self) -> None:
        before = (self.workspace.config_path.read_bytes(), self.workspace.db_path.read_bytes())
        with mock_patch("virtuoso.workspace.os.replace", side_effect=OSError("synthetic failure")):
            with self.assertRaisesRegex(WorkspaceError, "cannot replace"):
                self.workspace.configure_scheduler(minimum_interval_days=1)
        self.assertEqual((self.workspace.config_path.read_bytes(), self.workspace.db_path.read_bytes()), before)
        self.assertEqual(list(self.workspace.root.glob(".*.tmp")), [])

    def test_other_configuration_changes_still_reject(self) -> None:
        first = self.practice()
        config = self.workspace.configuration()
        for key, value in (("desired_retention", 0.8), ("enable_fuzzing", True)):
            changed = json.loads(json.dumps(config))
            changed["scheduler"].update({"minimum_interval_days": 1, key: value})
            self.workspace.config_path.write_text(json.dumps(changed))
            with self.assertRaisesRegex(PracticeError, "incompatible scheduler configuration"):
                self.practice(first.proposal.due_at)
            self.assertEqual(len(self.workspace.list_attempts()), 1)

    def test_incomplete_stored_configuration_still_rejects(self) -> None:
        first = self.practice()
        with sqlite3.connect(self.workspace.db_path) as db:
            db.execute("UPDATE scheduler_state SET configuration_json = '{}' ")
        with self.assertRaisesRegex(PracticeError, "incompatible scheduler configuration"):
            self.practice(first.proposal.due_at)
        self.assertEqual(len(self.workspace.list_attempts()), 1)

    def test_direct_measured_attempt_uses_completion_time_for_floor(self) -> None:
        self.workspace.configure_scheduler(minimum_interval_days=1)
        item = self.workspace.load_item("alpha")
        complete = NOW + timedelta(seconds=20)
        result = PracticeService(self.workspace).run_direct(
            event_id="attempt-" + "a" * 32, item_id="alpha", item_content_hash=item.content_hash,
            started_at=NOW, initial_answered_at=NOW + timedelta(seconds=5),
            completed_at=complete, initial_response="Synthetic response", result="partial",
            confidence=3, open_notes=False, support_actions=(),
        )
        self.assertEqual(result.proposal.due_at, complete + timedelta(days=1))
        self.assertEqual(result.attempt.initial_latency_ms, 5000)
        self.assertFalse(result.attempt.administered)

    def test_cli_configures_floor_preserves_state_and_resumes_review(self) -> None:
        first = self.practice()
        old_config = self.workspace.configuration()
        before = self.workspace.db_path.read_bytes()
        configured = json.loads(self.cli(
            "scheduler", "configure", "--minimum-interval-days", "1", "--json",
        ).stdout)
        self.assertEqual(configured["schema"], "virtuoso/scheduler-settings@0.1")
        self.assertEqual(configured["configuration"]["minimum_interval_days"], 1)
        self.assertEqual(configured["built_in_algorithms"], ["fsrs", "sm2"])
        self.assertEqual(configured["existing_due_dates_changed"], False)
        self.assertEqual(self.workspace.db_path.read_bytes(), before)
        expected_config = old_config
        expected_config["scheduler"]["minimum_interval_days"] = 1
        self.assertEqual(self.workspace.configuration(), expected_config)
        self.assertEqual(self.workspace.config_path.stat().st_mode & 0o777, 0o600)
        shown = json.loads(self.cli("scheduler", "show", "--json").stdout)
        self.assertEqual(shown["configuration"], configured["configuration"])
        second = self.practice(first.proposal.due_at)
        self.assertEqual(second.proposal.due_at, first.proposal.due_at + timedelta(days=1))
        self.assertEqual(self.workspace.doctor(now=first.proposal.due_at)["status"], "healthy")
        self.cli("scheduler", "configure", "--minimum-interval-days", "0")
        third = self.practice(second.proposal.due_at)
        self.assertEqual(third.proposal.due_at, second.proposal.due_at + timedelta(minutes=1))

    def test_floor_change_continues_existing_state_without_rewriting_history(self) -> None:
        first = self.practice()
        history = self.workspace.list_proposals()
        config = self.workspace.configuration()
        config["scheduler"]["minimum_interval_days"] = 1
        self.workspace.config_path.write_text(json.dumps(config))
        self.assertEqual(self.workspace.list_proposals(), history)
        second = self.practice(first.proposal.due_at)
        self.assertEqual(second.proposal.previous_source_event_id, first.attempt.event_id)
        self.assertEqual(second.proposal.previous_state_json, first.proposal.proposed_state_json)
        self.assertEqual(second.proposal.due_at, first.proposal.due_at + timedelta(days=1))
        self.assertEqual(second.proposal.configuration["minimum_interval_days"], 1)
        self.assertEqual(self.workspace.list_proposals()[0], history[0])
        config["scheduler"]["minimum_interval_days"] = 0
        self.workspace.config_path.write_text(json.dumps(config))
        third = self.practice(second.proposal.due_at)
        self.assertEqual(third.proposal.previous_source_event_id, second.attempt.event_id)
        self.assertEqual(third.proposal.due_at, second.proposal.due_at + timedelta(minutes=1))
        self.assertEqual(len(self.workspace.list_attempts()), 3)


if __name__ == "__main__":
    unittest.main()
