"""Phase 1 server operations over the in-process core (issue #69, docs/22).

These tests need no optional dependency: the operation registry and the
split recall contract are plain Python over the workspace services. The
transport tests in test_server_transports.py drive the same registry
through real MCP and HTTP clients.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
import sys
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from server_support import (
    LEARN_ANSWER,
    LEARN_PROMPT,
    LEARN_UNIT,
    RECALL_ANSWER,
    RECALL_HINT,
    RECALL_PROMPT,
    TemporaryWorkspaceCase,
    database_digest,
    make_workspace,
    rows,
)
from virtuoso.workspace import WorkspaceError, WorkspaceService
from virtuoso.server.schema import SchemaError, validate
from virtuoso.server.operations import (
    MAX_REQUEST_BYTES,
    OPERATIONS,
    OperationError,
    ServerCore,
)


def _submission() -> str:
    return secrets.token_hex(16)


class _CoreCase(TemporaryWorkspaceCase):
    surface = "mcp"

    def setUp(self) -> None:
        super().setUp()
        self.core = ServerCore(self.workspace, clock=self.clock)

    def call(self, name: str, surface: str | None = None, **arguments):
        return self.core.call(name, arguments, surface=surface or self.surface)

    def error(self, name: str, **arguments) -> OperationError:
        with self.assertRaises(OperationError) as caught:
            self.call(name, **arguments)
        return caught.exception

    def start(self, item_id: str = "hash-lookup", **extra) -> str:
        started = self.call("recall_start", item_id=item_id, **extra)
        return started["attempt"]["attempt_token"]

    def record(self, token: str, **overrides):
        arguments = {
            "attempt_token": token,
            "submission_id": _submission(),
            "result": "demonstrated",
            "confidence": 4,
            "agent_help": "none",
            "graded_by": "learner",
        }
        arguments.update(overrides)
        return self.call("recall_record", **arguments)

    def scheduler_rows(self) -> list[dict]:
        return rows(
            self.workspace.db_path,
            "SELECT item_id, state_json, source_event_id FROM scheduler_state ORDER BY item_id",
        )


class RegistryContractTests(unittest.TestCase):
    def test_registry_lists_exactly_the_phase_one_operations(self) -> None:
        self.assertEqual(
            sorted(OPERATIONS),
            sorted([
                "next_action", "study_load", "study_record", "recall_start",
                "recall_respond", "recall_hint", "recall_reveal", "recall_record",
                "recall_skip", "workload", "history",
            ]),
        )
        kinds = {name: operation.kind for name, operation in OPERATIONS.items()}
        self.assertEqual(
            {name for name, kind in kinds.items() if kind == "read"},
            {"next_action", "study_load", "recall_reveal", "workload", "history"},
        )

    def test_no_operation_accepts_a_path_module_or_trusted_flag(self) -> None:
        forbidden = ("path", "workspace", "module", "trusted", "root", "file", "dir")
        for name, operation in OPERATIONS.items():
            schema = operation.input_schema
            with self.subTest(operation=name):
                self.assertEqual(schema["type"], "object")
                self.assertIs(schema["additionalProperties"], False)
                for field in schema["properties"]:
                    self.assertFalse(
                        any(marker in field for marker in forbidden), field
                    )
                self.assertIs(operation.output_schema["additionalProperties"], False)

    def test_item_id_inputs_cannot_carry_a_path(self) -> None:
        for name, operation in OPERATIONS.items():
            item = operation.input_schema["properties"].get("item_id")
            if item is None:
                continue
            with self.subTest(operation=name):
                self.assertEqual(item["pattern"], "^[a-z0-9]+(?:-[a-z0-9]+)*$")


class InputContractTests(_CoreCase):
    def test_unknown_fields_and_trusted_flags_are_rejected(self) -> None:
        for arguments in (
            {"allow_trusted": True},
            {"workspace": "/tmp/other"},
            {"module_id": "scheduler"},
        ):
            with self.subTest(arguments=arguments):
                before = database_digest(self.workspace.db_path)
                with self.assertRaises(OperationError) as caught:
                    self.core.call(
                        "recall_start",
                        {"item_id": "hash-lookup", **arguments},
                        surface="mcp",
                    )
                self.assertEqual(caught.exception.code, "invalid-request")
                self.assertEqual(database_digest(self.workspace.db_path), before)

    def test_path_shaped_item_id_is_rejected(self) -> None:
        error = self.error("history", item_id="../hash-lookup")
        self.assertEqual(error.code, "invalid-request")

    def test_unknown_operation_has_its_own_code(self) -> None:
        with self.assertRaises(OperationError) as caught:
            self.core.call("retire_item", {}, surface="http")
        self.assertEqual(caught.exception.code, "unknown-operation")
        self.assertEqual(caught.exception.status, 404)

    def test_oversized_request_is_rejected_before_any_write(self) -> None:
        before = database_digest(self.workspace.db_path)
        token = self.start()
        after_start = database_digest(self.workspace.db_path)
        self.assertNotEqual(before, after_start)
        error = self.error(
            "recall_respond",
            attempt_token=token,
            response="x" * (MAX_REQUEST_BYTES + 1),
        )
        self.assertEqual(error.code, "request-too-large")
        self.assertEqual(error.status, 413)
        self.assertEqual(database_digest(self.workspace.db_path), after_start)

    def test_error_envelope_uses_the_review_error_schema(self) -> None:
        error = self.error("history", item_id="BAD")
        payload = error.to_payload()
        self.assertEqual(payload["schema"], "virtuoso/review-error@0.1")
        self.assertEqual(set(payload["error"]), {"code", "message", "recovery"})


class AnswerHidingTests(_CoreCase):
    def test_next_action_and_recall_start_never_return_the_answer(self) -> None:
        selected = self.call("next_action")
        self.assertEqual(selected["item_id"], "hash-lookup")
        self.assertEqual(selected["action"], "practice")
        text = json.dumps(selected)
        self.assertNotIn(RECALL_ANSWER, text)
        self.assertNotIn(RECALL_PROMPT, text)
        started = self.call("recall_start", item_id="hash-lookup")
        text = json.dumps(started)
        self.assertIn(RECALL_PROMPT, text)
        self.assertNotIn(RECALL_ANSWER, text)
        self.assertNotIn(RECALL_HINT, text)
        self.assertTrue(started["attempt"]["hint_available"])

    def test_study_load_returns_the_unit_without_prompt_or_answer(self) -> None:
        loaded = self.call("study_load", item_id="stack-order")
        text = json.dumps(loaded)
        self.assertIn(LEARN_UNIT, text)
        self.assertNotIn(LEARN_PROMPT, text)
        self.assertNotIn(LEARN_ANSWER, text)

    def test_reveal_before_response_fails_and_writes_nothing(self) -> None:
        token = self.start()
        before = database_digest(self.workspace.db_path)
        error = self.error("recall_reveal", attempt_token=token)
        self.assertEqual(error.code, "response-required")
        self.assertNotIn(RECALL_ANSWER, json.dumps(error.to_payload()))
        self.assertEqual(database_digest(self.workspace.db_path), before)

    def test_reveal_after_response_returns_the_answer(self) -> None:
        token = self.start()
        self.call("recall_respond", attempt_token=token, response="a hash table")
        revealed = self.call("recall_reveal", attempt_token=token)
        self.assertEqual(revealed["answer"], RECALL_ANSWER)

    def test_history_for_a_due_item_has_no_prompt_answer_or_response(self) -> None:
        token = self.start()
        response = "my distinctive synthetic response text"
        self.call("recall_respond", attempt_token=token, response=response)
        self.record(token, result="not-demonstrated", confidence=2)
        self.clock.advance(days=30)
        self.assertEqual(self.call("next_action")["item_id"], "hash-lookup")
        history = self.call("history", item_id="hash-lookup")
        self.assertEqual(history["schema"], "virtuoso/item-history@0.2")
        self.assertEqual(len(history["attempts"]), 1)
        text = json.dumps(history)
        for secret in (RECALL_PROMPT, RECALL_ANSWER, RECALL_HINT, response):
            self.assertNotIn(secret, text)


class SplitRecallTests(_CoreCase):
    def test_hint_between_start_and_response_sets_hint_shown_at_once(self) -> None:
        token = self.start()
        first = self.call("recall_hint", attempt_token=token)
        self.assertEqual(first["hint"], RECALL_HINT)
        self.clock.advance(seconds=5)
        again = self.call("recall_hint", attempt_token=token)
        self.assertEqual(again["hint_shown_at"], first["hint_shown_at"])
        stored = rows(
            self.workspace.db_path,
            "SELECT hint_shown_at FROM pending_attempts WHERE attempt_token = ?",
            (token,),
        )
        self.assertEqual(stored[0]["hint_shown_at"], first["hint_shown_at"])
        self.call("recall_respond", attempt_token=token, response="buckets")
        recorded = self.record(token, result="partial", confidence=3)
        self.assertTrue(recorded["attempt"]["hint_used"])
        self.assertEqual(recorded["attempt"]["support"], ["hint"])
        stored = rows(
            self.workspace.db_path,
            "SELECT support_json FROM attempts WHERE event_id = ?",
            (recorded["attempt"]["event_id"],),
        )[0]
        self.assertEqual(
            [action["kind"] for action in json.loads(stored["support_json"])], ["hint"]
        )

    def test_hint_after_response_is_refused(self) -> None:
        token = self.start()
        self.call("recall_respond", attempt_token=token, response="a guess")
        before = database_digest(self.workspace.db_path)
        error = self.error("recall_hint", attempt_token=token)
        self.assertEqual(error.code, "hint-unavailable")
        self.assertEqual(database_digest(self.workspace.db_path), before)

    def test_item_without_a_hint_reports_no_hint(self) -> None:
        self.workspace.add_item(
            item_id="plain-item", title="Plain", focus="misc",
            prompt="Plain prompt?", answer="Plain answer.",
        )
        token = self.start("plain-item")
        error = self.error("recall_hint", attempt_token=token)
        self.assertEqual(error.code, "no-hint")

    def test_response_is_stored_once_and_a_retry_returns_it(self) -> None:
        token = self.start()
        first = self.call("recall_respond", attempt_token=token, response="answer one")
        again = self.call("recall_respond", attempt_token=token, response="answer one")
        self.assertEqual(first, again)
        error = self.error("recall_respond", attempt_token=token, response="answer two")
        self.assertEqual(error.code, "already-responded")

    def test_record_before_response_is_refused(self) -> None:
        token = self.start()
        before = database_digest(self.workspace.db_path)
        with self.assertRaises(OperationError) as caught:
            self.record(token)
        self.assertEqual(caught.exception.code, "response-required")
        self.assertEqual(database_digest(self.workspace.db_path), before)

    def test_blank_response_cannot_be_demonstrated(self) -> None:
        token = self.start()
        self.call("recall_respond", attempt_token=token, response="   ")
        before = database_digest(self.workspace.db_path)
        with self.assertRaises(OperationError) as caught:
            self.record(token, result="demonstrated")
        self.assertEqual(caught.exception.code, "blank-demonstrated")
        self.assertEqual(database_digest(self.workspace.db_path), before)
        recorded = self.record(token, result="not-demonstrated", confidence=1)
        self.assertEqual(recorded["attempt"]["result"], "not-demonstrated")

    def test_unknown_token_has_its_own_code(self) -> None:
        error = self.error("recall_reveal", attempt_token="0" * 32)
        self.assertEqual(error.code, "attempt-not-found")

    def test_learn_first_item_must_be_studied_before_recall(self) -> None:
        error = self.error("recall_start", item_id="stack-order")
        self.assertEqual(error.code, "learning-required")

    def test_changed_item_returns_stale_content(self) -> None:
        token = self.start()
        self.call("recall_respond", attempt_token=token, response="a hash table")
        path = self.workspace.root / "items" / "hash-lookup.md"
        path.write_text(path.read_text(encoding="utf-8") + "\nEdited.\n", encoding="utf-8")
        before = database_digest(self.workspace.db_path)
        with self.assertRaises(OperationError) as caught:
            self.record(token)
        self.assertEqual(caught.exception.code, "stale-content")
        self.assertEqual(database_digest(self.workspace.db_path), before)


class IdempotencyTests(_CoreCase):
    def test_replayed_record_returns_the_original_and_writes_nothing(self) -> None:
        token = self.start()
        self.call("recall_respond", attempt_token=token, response="a hash table")
        submission = _submission()
        first = self.record(token, submission_id=submission)
        self.assertFalse(first["replayed"])
        before = database_digest(self.workspace.db_path)
        self.clock.advance(minutes=1)
        again = self.record(token, submission_id=submission)
        self.assertTrue(again["replayed"])
        self.assertEqual(again["attempt"], first["attempt"])
        self.assertEqual(again["proposal"], first["proposal"])
        self.assertEqual(database_digest(self.workspace.db_path), before)

    def test_replay_with_different_grade_is_a_conflict(self) -> None:
        token = self.start()
        self.call("recall_respond", attempt_token=token, response="a hash table")
        submission = _submission()
        self.record(token, submission_id=submission)
        before = database_digest(self.workspace.db_path)
        with self.assertRaises(OperationError) as caught:
            self.record(token, submission_id=submission, result="partial")
        self.assertEqual(caught.exception.code, "submission-conflict")
        self.assertEqual(database_digest(self.workspace.db_path), before)

    def test_second_submission_for_a_recorded_attempt_is_refused(self) -> None:
        token = self.start()
        self.call("recall_respond", attempt_token=token, response="a hash table")
        self.record(token)
        before = database_digest(self.workspace.db_path)
        with self.assertRaises(OperationError) as caught:
            self.record(token)
        self.assertEqual(caught.exception.code, "already-recorded")
        self.assertEqual(database_digest(self.workspace.db_path), before)

    def test_study_record_retry_returns_the_stored_completion(self) -> None:
        loaded = self.call("study_load", item_id="stack-order")["item"]
        arguments = {
            "item_id": "stack-order",
            "item_content_hash": loaded["content_hash"],
            "learning_unit_hash": loaded["learning_unit_hash"],
            "completed": True,
        }
        first = self.call("study_record", **arguments)
        self.assertEqual(first["study"]["surface"], "mcp")
        before = database_digest(self.workspace.db_path)
        again = self.call("study_record", **arguments)
        self.assertEqual(again["study"], first["study"])
        self.assertEqual(database_digest(self.workspace.db_path), before)
        self.assertEqual(self.call("next_action")["action"], "practice")


class SkipTests(_CoreCase):
    def test_skip_keeps_the_due_date_and_closes_the_attempt(self) -> None:
        token = self.start()
        self.call("recall_respond", attempt_token=token, response="a hash table")
        self.record(token)
        before_state = self.scheduler_rows()
        self.clock.advance(days=30)
        skipped_token = self.start()
        submission = _submission()
        skipped = self.call(
            "recall_skip", attempt_token=skipped_token, submission_id=submission
        )
        self.assertEqual(skipped["skip"]["surface"], "mcp")
        self.assertEqual(self.scheduler_rows(), before_state)
        before = database_digest(self.workspace.db_path)
        again = self.call(
            "recall_skip", attempt_token=skipped_token, submission_id=submission
        )
        self.assertTrue(again["replayed"])
        self.assertEqual(again["skip"], skipped["skip"])
        self.assertEqual(database_digest(self.workspace.db_path), before)
        error = self.error("recall_respond", attempt_token=skipped_token, response="late")
        self.assertEqual(error.code, "attempt-closed")


class GraderIsolationTests(_CoreCase):
    def test_agent_graded_record_leaves_scheduler_and_next_unchanged(self) -> None:
        token = self.start()
        self.call("recall_respond", attempt_token=token, response="a hash table")
        self.record(token)
        self.clock.advance(days=30)
        before_next = self.call("next_action")
        before_state = self.scheduler_rows()
        before_proposals = rows(self.workspace.db_path, "SELECT * FROM scheduler_proposals")
        before_workload = self.call("workload")

        token = self.start()
        self.call("recall_respond", attempt_token=token, response="no idea")
        recorded = self.record(
            token, result="not-demonstrated", confidence=1,
            graded_by="agent", agent_help="light",
        )
        self.assertIsNone(recorded["proposal"])
        self.assertEqual(recorded["attempt"]["graded_by"], "agent")
        self.assertEqual(self.scheduler_rows(), before_state)
        self.assertEqual(
            rows(self.workspace.db_path, "SELECT * FROM scheduler_proposals"),
            before_proposals,
        )
        self.assertEqual(self.call("next_action"), before_next)
        self.assertEqual(self.call("workload"), before_workload)

    def test_learner_graded_record_feeds_the_scheduler(self) -> None:
        token = self.start()
        self.call("recall_respond", attempt_token=token, response="a hash table")
        recorded = self.record(token)
        self.assertIsNotNone(recorded["proposal"])
        self.assertEqual(recorded["proposal"]["algorithm"], "fsrs")
        self.assertEqual(
            self.scheduler_rows()[0]["source_event_id"],
            recorded["attempt"]["event_id"],
        )

    def test_graded_by_and_agent_help_are_required_without_defaults(self) -> None:
        token = self.start()
        self.call("recall_respond", attempt_token=token, response="a hash table")
        for missing in ("graded_by", "agent_help"):
            arguments = {
                "attempt_token": token,
                "submission_id": _submission(),
                "result": "demonstrated",
                "confidence": 4,
                "agent_help": "none",
                "graded_by": "learner",
            }
            del arguments[missing]
            with self.subTest(missing=missing):
                with self.assertRaises(OperationError) as caught:
                    self.core.call("recall_record", arguments, surface="mcp")
                self.assertEqual(caught.exception.code, "invalid-request")
                self.assertIn(missing, str(caught.exception))
        required = OPERATIONS["recall_record"].input_schema["required"]
        self.assertIn("graded_by", required)
        self.assertIn("agent_help", required)
        properties = OPERATIONS["recall_record"].input_schema["properties"]
        self.assertNotIn("default", properties["agent_help"])
        self.assertNotIn("default", properties["graded_by"])


class EvidenceTests(_CoreCase):
    def test_stored_evidence_shows_surface_client_latency_help_and_grader(self) -> None:
        for surface, client, graded_by, help_level in (
            ("mcp", "Synthetic Chat Client", "learner", "light"),
            ("http", "synthetic-voice", "agent", "substantial"),
        ):
            with self.subTest(surface=surface):
                started = self.call(
                    "recall_start", surface=surface, item_id="hash-lookup", client=client
                )
                token = started["attempt"]["attempt_token"]
                self.assertEqual(started["attempt"]["client"], client)
                self.call("recall_respond", surface=surface, attempt_token=token,
                          response="a hash table")
                recorded = self.core.call(
                    "recall_record",
                    {
                        "attempt_token": token,
                        "submission_id": _submission(),
                        "result": "partial",
                        "confidence": 3,
                        "agent_help": help_level,
                        "graded_by": graded_by,
                    },
                    surface=surface,
                )
                stored = rows(
                    self.workspace.db_path,
                    "SELECT surface, client, initial_latency_ms, agent_help, graded_by,"
                    " administered FROM attempts WHERE event_id = ?",
                    (recorded["attempt"]["event_id"],),
                )[0]
                self.assertEqual(stored, {
                    "surface": surface,
                    "client": client,
                    "initial_latency_ms": None,
                    "agent_help": help_level,
                    "graded_by": graded_by,
                    "administered": 1,
                })
                self.assertEqual(recorded["attempt"]["surface"], surface)
                self.assertEqual(recorded["attempt"]["client"], client)
                self.assertIsNone(recorded["attempt"]["latency_ms"])
                self.clock.advance(days=30)

    def test_pending_row_with_null_client_records(self) -> None:
        token = self.start()
        pending = rows(
            self.workspace.db_path,
            "SELECT client FROM pending_attempts WHERE attempt_token = ?",
            (token,),
        )[0]
        self.assertIsNone(pending["client"])
        self.call("recall_respond", attempt_token=token, response="a hash table")
        recorded = self.record(token)
        self.assertIsNone(recorded["attempt"]["client"])
        closed = rows(
            self.workspace.db_path,
            "SELECT state, attempt_event_id, client FROM pending_attempts"
            " WHERE attempt_token = ?",
            (token,),
        )[0]
        self.assertEqual(closed, {
            "state": "recorded",
            "attempt_event_id": recorded["attempt"]["event_id"],
            "client": None,
        })

    def test_study_and_skip_writes_carry_the_binding_surface(self) -> None:
        loaded = self.call("study_load", surface="http", item_id="stack-order")["item"]
        studied = self.call(
            "study_record", surface="http", item_id="stack-order",
            item_content_hash=loaded["content_hash"],
            learning_unit_hash=loaded["learning_unit_hash"], completed=True,
        )
        self.assertEqual(studied["study"]["surface"], "http")


class OrderingAndExpiryTests(_CoreCase):
    def test_server_clock_before_creation_is_refused(self) -> None:
        token = self.start()
        self.clock.advance(seconds=-1)
        before = database_digest(self.workspace.db_path)
        for name, arguments in (
            ("recall_respond", {"attempt_token": token, "response": "x"}),
            ("recall_hint", {"attempt_token": token}),
        ):
            with self.subTest(operation=name):
                with self.assertRaises(OperationError) as caught:
                    self.core.call(name, arguments, surface="mcp")
                self.assertEqual(caught.exception.code, "clock-order")
        self.assertEqual(database_digest(self.workspace.db_path), before)

    def test_record_before_response_time_is_refused(self) -> None:
        token = self.start()
        self.clock.advance(seconds=10)
        self.call("recall_respond", attempt_token=token, response="a hash table")
        self.clock.advance(seconds=-5)
        before = database_digest(self.workspace.db_path)
        with self.assertRaises(OperationError) as caught:
            self.record(token)
        self.assertEqual(caught.exception.code, "clock-order")
        self.assertEqual(database_digest(self.workspace.db_path), before)

    def test_response_before_hint_time_is_refused(self) -> None:
        token = self.start()
        self.clock.advance(seconds=10)
        self.call("recall_hint", attempt_token=token)
        self.clock.advance(seconds=-5)
        with self.assertRaises(OperationError) as caught:
            self.call("recall_respond", attempt_token=token, response="x")
        self.assertEqual(caught.exception.code, "clock-order")

    def test_no_operation_accepts_a_caller_time(self) -> None:
        for name, operation in OPERATIONS.items():
            with self.subTest(operation=name):
                for field in operation.input_schema["properties"]:
                    self.assertFalse(field.endswith("_at"), field)

    def test_expired_attempt_records_nothing(self) -> None:
        token = self.start()
        self.call("recall_respond", attempt_token=token, response="a hash table")
        self.clock.advance(minutes=61)
        before = database_digest(self.workspace.db_path)
        for name, arguments in (
            ("recall_reveal", {"attempt_token": token}),
            ("recall_record", {
                "attempt_token": token, "submission_id": _submission(),
                "result": "demonstrated", "confidence": 4,
                "agent_help": "none", "graded_by": "learner",
            }),
        ):
            with self.subTest(operation=name):
                with self.assertRaises(OperationError) as caught:
                    self.core.call(name, arguments, surface="mcp")
                self.assertEqual(caught.exception.code, "attempt-expired")
        self.assertEqual(database_digest(self.workspace.db_path), before)

    def test_database_rejects_an_expired_record_even_if_the_server_misses_it(self) -> None:
        token = self.start()
        self.call("recall_respond", attempt_token=token, response="a hash table")
        self.clock.advance(minutes=61)
        before = database_digest(self.workspace.db_path)
        # Disable the server's own expiry check so the database trigger is
        # the only guard left.
        with mock.patch.object(ServerCore, "_require_unexpired", lambda *args: None):
            with self.assertRaises(OperationError) as caught:
                self.record(token)
        self.assertEqual(caught.exception.code, "attempt-expired")
        self.assertEqual(database_digest(self.workspace.db_path), before)


class BindingIdentityTests(_CoreCase):
    def test_attempt_must_finish_on_the_binding_that_started_it(self) -> None:
        token = self.start()
        before = database_digest(self.workspace.db_path)
        with self.assertRaises(OperationError) as caught:
            self.core.call(
                "recall_respond", {"attempt_token": token, "response": "x"}, surface="http"
            )
        self.assertEqual(caught.exception.code, "surface-mismatch")
        self.assertEqual(database_digest(self.workspace.db_path), before)

    def test_nothing_due_is_a_typed_none_action(self) -> None:
        empty = WorkspaceService.init(self.parent / "empty")
        selected = ServerCore(empty, clock=self.clock).call("next_action", {}, surface="mcp")
        self.assertEqual(selected["action"], "none")
        self.assertIsNone(selected["item_id"])


class ExternalSchedulerTests(_CoreCase):
    def _configure_external_scheduler(self) -> None:
        module_dir = self.workspace.root / "modules" / "fixed-ladder"
        module_dir.mkdir(parents=True, mode=0o700)
        module_dir.parent.chmod(0o700)
        script = module_dir / "scheduler.py"
        script.write_text("raise SystemExit('the server must not run the module')\n")
        script.chmod(0o700)
        manifest = module_dir / "virtuoso.module.json"
        manifest.write_text(json.dumps({
            "schema": "virtuoso/module@0.1",
            "id": "fixed-ladder",
            "version": "1.0.0",
            "category": "scheduler",
            "command": {"argv": [sys.executable, str(script)], "timeout_seconds": 2},
            "capabilities": {"reads": ["scheduler.request"], "returns": "scheduler-proposal"},
            "trust": "local-executable",
        }, indent=2, sort_keys=True) + "\n")
        manifest.chmod(0o600)
        config = self.workspace.configuration()
        config["scheduler"] = {
            "algorithm": "module:fixed-ladder",
            "context": "atomic-recall",
            "configuration": {"intervals": [1, 3, 7, 14, 30]},
        }
        self.workspace.config_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n")
        self.workspace.config_path.chmod(0o600)

    def test_external_scheduler_workspace_rejects_remote_recall_writes(self) -> None:
        token = self.start()
        self.call("recall_respond", attempt_token=token, response="a hash table")
        self._configure_external_scheduler()
        before = database_digest(self.workspace.db_path)
        for name, arguments in (
            ("recall_start", {"item_id": "hash-lookup"}),
            ("recall_record", {
                "attempt_token": token, "submission_id": _submission(),
                "result": "demonstrated", "confidence": 4,
                "agent_help": "none", "graded_by": "learner",
            }),
            ("recall_record", {
                "attempt_token": token, "submission_id": _submission(),
                "result": "demonstrated", "confidence": 4,
                "agent_help": "none", "graded_by": "agent",
            }),
        ):
            with self.subTest(operation=name, graded_by=arguments.get("graded_by")):
                with self.assertRaises(OperationError) as caught:
                    self.core.call(name, arguments, surface="mcp")
                self.assertEqual(caught.exception.code, "cli-required")
                self.assertIn("--allow-trusted-scheduler", str(caught.exception))
                self.assertIn("virtuoso", str(caught.exception))
        self.assertEqual(database_digest(self.workspace.db_path), before)


class LimitTests(_CoreCase):
    def test_response_over_the_limit_is_refused(self) -> None:
        self.core.max_response_bytes = 64
        with self.assertRaises(OperationError) as caught:
            self.call("workload")
        self.assertEqual(caught.exception.code, "response-too-large")
        self.assertGreaterEqual(caught.exception.status, 500)

    def test_outputs_match_their_declared_schemas(self) -> None:
        # The core validates every output against the registry before
        # returning it; a full hero flow exercises each schema once.
        selected = self.call("next_action")
        self.assertEqual(selected["schema"], "virtuoso/server-next-action@0.1")
        loaded = self.call("study_load", item_id="stack-order")
        self.call(
            "study_record", item_id="stack-order",
            item_content_hash=loaded["item"]["content_hash"],
            learning_unit_hash=loaded["item"]["learning_unit_hash"], completed=True,
        )
        token = self.start("stack-order")
        self.call("recall_respond", attempt_token=token, response="last in first out")
        self.call("recall_reveal", attempt_token=token)
        self.record(token)
        self.call("workload")
        self.call("history", item_id="stack-order")
        self.assertEqual(
            self.core.ttl, timedelta(minutes=60)
        )


class AwaitingRecordTests(_CoreCase):
    """A stored response is finished only by recall_record (review P1)."""

    def pending_states(self) -> list[tuple[str, str]]:
        return [
            (row["attempt_token"], row["state"])
            for row in rows(
                self.workspace.db_path,
                "SELECT attempt_token, state FROM pending_attempts ORDER BY rowid",
            )
        ]

    def test_skip_after_response_is_refused_and_writes_nothing(self) -> None:
        token = self.start()
        self.call("recall_respond", attempt_token=token, response="no idea")
        before = database_digest(self.workspace.db_path)
        # The service refuses before it reaches the workspace write.
        with mock.patch.object(
            self.workspace, "record_review_skip", side_effect=AssertionError("write")
        ):
            error = self.error(
                "recall_skip", attempt_token=token, submission_id=_submission()
            )
        self.assertEqual(error.code, "attempt-awaiting-record")
        self.assertEqual(error.status, 409)
        self.assertEqual(error.recovery, "record-result")
        self.assertIn("recall_record", str(error))
        self.assertEqual(database_digest(self.workspace.db_path), before)
        self.assertEqual(self.pending_states(), [(token, "responded")])
        recorded = self.record(token, result="not-demonstrated", confidence=1)
        self.assertEqual(recorded["attempt"]["result"], "not-demonstrated")

    def test_database_refuses_a_skip_after_response_without_the_service_check(self) -> None:
        token = self.start()
        self.call("recall_respond", attempt_token=token, response="no idea")
        before = database_digest(self.workspace.db_path)
        with mock.patch.object(ServerCore, "_require_skippable", lambda *args: None):
            error = self.error(
                "recall_skip", attempt_token=token, submission_id=_submission()
            )
        self.assertEqual(error.code, "attempt-awaiting-record")
        self.assertEqual(database_digest(self.workspace.db_path), before)

    def test_database_trigger_refuses_a_skip_linked_to_a_responded_attempt(self) -> None:
        token = self.start()
        self.call("recall_respond", attempt_token=token, response="no idea")
        row = self.workspace.pending_attempt(token)
        before = database_digest(self.workspace.db_path)
        with sqlite3.connect(self.workspace.db_path) as db:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "awaits recall_record"):
                db.execute(
                    """INSERT INTO review_skips(
                           event_id, item_id, item_content_hash, occurred_at,
                           surface, pending_attempt_token
                       ) VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        "skip-" + _submission(), row["item_id"],
                        row["item_content_hash"], self.clock().isoformat(),
                        row["surface"], token,
                    ),
                )
        self.assertEqual(database_digest(self.workspace.db_path), before)

    def test_start_is_refused_while_an_earlier_attempt_awaits_a_record(self) -> None:
        token = self.start()
        self.call("recall_respond", attempt_token=token, response="a hash table")
        before = database_digest(self.workspace.db_path)
        for surface in ("mcp", "http"):
            with self.subTest(surface=surface):
                # The service refuses before it reaches the workspace write.
                with self.assertRaises(OperationError) as caught, mock.patch.object(
                    self.workspace, "create_pending_attempt",
                    side_effect=AssertionError("write"),
                ):
                    self.core.call(
                        "recall_start", {"item_id": "hash-lookup"}, surface=surface
                    )
                self.assertEqual(caught.exception.code, "attempt-awaiting-record")
                self.assertEqual(caught.exception.status, 409)
                self.assertIn("recall_record", str(caught.exception))
                self.assertEqual(database_digest(self.workspace.db_path), before)
        self.record(token)
        self.clock.advance(days=30)
        self.assertNotEqual(self.start(), token)

    def test_open_attempt_without_a_response_is_superseded_by_a_start(self) -> None:
        first = self.start()
        second = self.start()
        self.assertNotEqual(first, second)
        # The unanswered attempt closes; it had no response, so nothing is lost.
        self.assertEqual(self.pending_states(), [(first, "expired"), (second, "open")])

    def test_database_refuses_the_start_without_the_service_check(self) -> None:
        token = self.start()
        self.call("recall_respond", attempt_token=token, response="a hash table")
        before = database_digest(self.workspace.db_path)
        with mock.patch.object(
            ServerCore, "_require_no_attempt_awaiting_record", lambda *args: None
        ):
            error = self.error("recall_start", item_id="hash-lookup")
        self.assertEqual(error.code, "attempt-awaiting-record")
        self.assertEqual(database_digest(self.workspace.db_path), before)

    def test_unrecorded_response_expires_without_evidence_then_a_start_is_allowed(self) -> None:
        token = self.start()
        self.call("recall_respond", attempt_token=token, response="no idea")
        state_before = self.scheduler_rows()
        self.clock.advance(minutes=61)
        fresh = self.start()
        self.assertNotEqual(fresh, token)
        # The expired attempt records nothing: no attempt evidence, no
        # scheduler change. The start closed it (state expired) in the same
        # transaction, and its row keeps the response it stored.
        self.assertEqual(self.call("history", item_id="hash-lookup")["attempts"], [])
        self.assertEqual(self.scheduler_rows(), state_before)
        self.assertEqual(self.pending_states(), [(token, "expired"), (fresh, "open")])
        old = self.workspace.pending_attempt(token)
        assert old is not None
        self.assertEqual(old["response"], "no idea")
        self.assertIsNone(old["attempt_event_id"])
        self.assertIsNone(old["recorded_at"])
        self.assertEqual(rows(self.workspace.db_path, "SELECT event_id FROM attempts"), [])
        error = self.error("recall_skip", attempt_token=token, submission_id=_submission())
        self.assertEqual(error.code, "attempt-closed")
        error = self.error("recall_record", attempt_token=token, submission_id=_submission(),
                           result="demonstrated", confidence=4, agent_help="none",
                           graded_by="learner")
        self.assertEqual(error.code, "attempt-closed")

    def test_reviewer_erase_sequence_cannot_hide_a_failed_recall(self) -> None:
        # Wrong response, reveal, skip, start again, answer, record
        # demonstrated: the failed attempt must not disappear.
        token = self.start()
        self.call("recall_respond", attempt_token=token, response="a linked list")
        self.assertEqual(
            self.call("recall_reveal", attempt_token=token)["answer"], RECALL_ANSWER
        )
        skip = self.error("recall_skip", attempt_token=token, submission_id=_submission())
        self.assertEqual(skip.code, "attempt-awaiting-record")
        start = self.error("recall_start", item_id="hash-lookup")
        self.assertEqual(start.code, "attempt-awaiting-record")
        self.assertEqual(rows(self.workspace.db_path, "SELECT * FROM review_skips"), [])
        self.assertEqual(self.scheduler_rows(), [])
        self.record(token, result="not-demonstrated", confidence=1)
        history = self.call("history", item_id="hash-lookup")["attempts"]
        self.assertEqual([entry["result"] for entry in history], ["not-demonstrated"])

    def test_skip_description_says_open_attempts_only(self) -> None:
        description = OPERATIONS["recall_skip"].description
        self.assertIn("before the learner responds", description)
        self.assertIn("attempt-awaiting-record", description)
        self.assertIn("recall_record", description)
        self.assertIn("attempt-awaiting-record", OPERATIONS["recall_start"].description)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _real_now() -> datetime:
    """The wall clock the database reads through julianday('now')."""
    return datetime.now(timezone.utc)


class OneLiveAttemptTests(_CoreCase):
    """At most one live (open or responded) pending attempt per item.

    The service closes an unanswered open attempt when a new one starts, and
    refuses the start while an unexpired response awaits its record. The
    database holds the same line with a partial unique index, so raw SQL
    cannot open a second live row either (independent review of #69).
    """

    def live(self, item_id: str = "hash-lookup") -> list[str]:
        return [
            row["attempt_token"]
            for row in rows(
                self.workspace.db_path,
                "SELECT attempt_token FROM pending_attempts WHERE item_id = ?"
                " AND state IN ('open', 'responded') ORDER BY created_at",
                (item_id,),
            )
        ]

    def states(self) -> list[tuple[str, str, str | None]]:
        return [
            (row["attempt_token"], row["state"], row["response"])
            for row in rows(
                self.workspace.db_path,
                "SELECT attempt_token, state, response FROM pending_attempts"
                " ORDER BY rowid",
            )
        ]

    def raw_open(
        self,
        db: sqlite3.Connection,
        token: str,
        *,
        created_at: datetime,
        expires_at: datetime,
        surface: str = "mcp",
        item_id: str = "hash-lookup",
        content_hash: str | None = None,
    ) -> None:
        item_hash = content_hash or self.workspace.load_item(item_id).content_hash
        db.execute(
            """INSERT INTO pending_attempts(
                   attempt_token, item_id, item_content_hash, prompt_shown,
                   surface, created_at, expires_at)
               VALUES (?, ?, ?, 'prompt', ?, ?, ?)""",
            (token, item_id, item_hash, surface, _iso(created_at), _iso(expires_at)),
        )

    @staticmethod
    def raw_respond(db: sqlite3.Connection, token: str, at: datetime, text: str) -> None:
        db.execute(
            "UPDATE pending_attempts SET state = 'responded', response = ?,"
            " responded_at = ? WHERE attempt_token = ?",
            (text, _iso(at), token),
        )

    # -- the reviewer's parallel sequence ------------------------------------

    def test_reviewer_parallel_sequence_cannot_hide_a_failed_recall(self) -> None:
        # Start A (mcp) and B (http) while both are open, respond A wrong,
        # reveal, respond B with the answer, record B, let A expire.
        a = self.start()
        b = self.core.call(
            "recall_start", {"item_id": "hash-lookup"}, surface="http"
        )["attempt"]["attempt_token"]
        self.assertEqual(self.live(), [b])
        error = self.error("recall_respond", attempt_token=a, response="a linked list")
        self.assertEqual(error.code, "attempt-closed")
        error = self.error("recall_reveal", attempt_token=a)
        self.assertEqual(error.code, "attempt-closed")
        self.core.call(
            "recall_respond", {"attempt_token": b, "response": RECALL_ANSWER},
            surface="http",
        )
        self.core.call("recall_reveal", {"attempt_token": b}, surface="http")
        self.core.call("recall_record", {
            "attempt_token": b, "submission_id": _submission(),
            "result": "demonstrated", "confidence": 4,
            "agent_help": "none", "graded_by": "learner",
        }, surface="http")
        self.clock.advance(minutes=61)
        # A never stored a response, so no failed response was dropped.
        self.assertEqual(
            sorted(self.states()),
            sorted([(a, "expired", None), (b, "recorded", RECALL_ANSWER)]),
        )

    def test_reviewer_sequence_with_a_stored_wrong_response_keeps_it(self) -> None:
        a = self.start()
        self.call("recall_respond", attempt_token=a, response="a linked list")
        self.call("recall_reveal", attempt_token=a)
        error = self.core_error("recall_start", {"item_id": "hash-lookup"}, "http")
        self.assertEqual(error.code, "attempt-awaiting-record")
        self.assertEqual(self.live(), [a])
        self.record(a, result="not-demonstrated", confidence=1)
        history = self.call("history", item_id="hash-lookup")["attempts"]
        self.assertEqual([entry["result"] for entry in history], ["not-demonstrated"])

    def core_error(self, name: str, arguments: dict, surface: str) -> OperationError:
        with self.assertRaises(OperationError) as caught:
            self.core.call(name, arguments, surface=surface)
        return caught.exception

    def test_reviewer_parallel_sequence_is_refused_by_raw_sql(self) -> None:
        now = _real_now()
        a, b = "a" * 32, "b" * 32
        with sqlite3.connect(self.workspace.db_path) as db:
            self.raw_open(db, a, created_at=now, expires_at=now + timedelta(hours=1))
            with self.assertRaisesRegex(
                sqlite3.IntegrityError, "UNIQUE constraint failed: pending_attempts.item_id"
            ):
                self.raw_open(
                    db, b, created_at=now, expires_at=now + timedelta(hours=1),
                    surface="http",
                )
            self.raw_respond(db, a, now, "a linked list")
            with self.assertRaisesRegex(sqlite3.IntegrityError, "awaits recall_record"):
                self.raw_open(
                    db, b, created_at=now, expires_at=now + timedelta(hours=1),
                    surface="http",
                )
            # Closing A's response early to make room is refused too.
            with self.assertRaisesRegex(sqlite3.IntegrityError, "awaits recall_record"):
                db.execute(
                    "UPDATE pending_attempts SET state = 'expired' WHERE attempt_token = ?",
                    (a,),
                )
        self.assertEqual(self.states(), [(a, "responded", "a linked list")])

    # -- concurrency -----------------------------------------------------------

    def test_concurrent_starts_on_both_bindings_leave_one_live_attempt(self) -> None:
        for round_number in range(6):
            with self.subTest(round=round_number):
                # Separate workspace handles and cores, so no shared server
                # lock orders the starts: only the database does.
                cores = {
                    surface: ServerCore(
                        WorkspaceService.open(self.workspace.root), clock=self.clock
                    )
                    for surface in ("mcp", "http")
                }
                barrier = threading.Barrier(len(cores))
                results: dict[str, str] = {}

                def run(surface: str) -> None:
                    barrier.wait()
                    try:
                        results[surface] = cores[surface].call(
                            "recall_start", {"item_id": "hash-lookup"}, surface=surface
                        )["attempt"]["attempt_token"]
                    except OperationError as exc:
                        results[surface] = exc.code

                threads = [threading.Thread(target=run, args=(s,)) for s in cores]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join()
                live = self.live()
                self.assertEqual(len(live), 1, results)
                started = [value for value in results.values() if len(value) == 32]
                self.assertIn(live[0], started)
                for value in results.values():
                    if value not in started:
                        self.assertEqual(value, "workspace-busy")

    def test_starts_through_one_core_on_both_bindings_leave_one_live_attempt(self) -> None:
        results: list[str] = []
        barrier = threading.Barrier(2)

        def run(surface: str) -> None:
            barrier.wait()
            results.append(self.core.call(
                "recall_start", {"item_id": "hash-lookup"}, surface=surface
            )["attempt"]["attempt_token"])

        threads = [threading.Thread(target=run, args=(s,)) for s in ("mcp", "http")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(results), 2)
        self.assertEqual(self.live(), [results[-1]])

    # -- supersede ---------------------------------------------------------------

    def test_start_supersedes_an_unanswered_open_attempt(self) -> None:
        a = self.start()
        self.call("recall_hint", attempt_token=a)
        b = self.core.call(
            "recall_start", {"item_id": "hash-lookup"}, surface="http"
        )["attempt"]["attempt_token"]
        self.assertEqual(self.states(), [(a, "expired", None), (b, "open", None)])
        # The superseded attempt records nothing and accepts nothing more.
        for name, arguments in (
            ("recall_respond", {"attempt_token": a, "response": "late"}),
            ("recall_skip", {"attempt_token": a, "submission_id": _submission()}),
        ):
            with self.subTest(operation=name):
                before = database_digest(self.workspace.db_path)
                error = self.core_error(name, arguments, "mcp")
                self.assertEqual(error.code, "attempt-closed")
                self.assertEqual(database_digest(self.workspace.db_path), before)
        self.assertEqual(rows(self.workspace.db_path, "SELECT * FROM attempts"), [])
        self.assertEqual(rows(self.workspace.db_path, "SELECT * FROM review_skips"), [])

    def test_supersede_and_start_share_one_transaction(self) -> None:
        a = self.start()
        before = database_digest(self.workspace.db_path)
        # Reusing A's token makes the insert fail after the supersede ran.
        with mock.patch(
            "virtuoso.server.operations.secrets.token_hex", return_value=a
        ):
            with self.assertRaises(OperationError):
                self.call("recall_start", item_id="hash-lookup")
        self.assertEqual(database_digest(self.workspace.db_path), before)
        self.assertEqual(self.states(), [(a, "open", None)])

    def test_responded_attempt_for_an_earlier_item_version_blocks_a_start(self) -> None:
        # The rule is per item, not per item version: an earlier version's
        # response still waits for its record or its expiry.
        old = "c" * 32
        with sqlite3.connect(self.workspace.db_path) as db:
            self.raw_open(
                db, old, created_at=self.clock(),
                expires_at=self.clock() + timedelta(minutes=60), content_hash="f" * 64,
            )
            self.raw_respond(db, old, self.clock(), "a linked list")
        before = database_digest(self.workspace.db_path)
        error = self.error("recall_start", item_id="hash-lookup")
        self.assertEqual(error.code, "attempt-awaiting-record")
        self.assertEqual(database_digest(self.workspace.db_path), before)
        self.clock.advance(minutes=61)
        fresh = self.start()
        self.assertEqual(
            self.states(), [(old, "expired", "a linked list"), (fresh, "open", None)]
        )

    # -- respond must target the live attempt ------------------------------------

    def test_respond_refuses_an_attempt_that_is_not_the_live_one(self) -> None:
        a = self.start()
        with sqlite3.connect(self.workspace.db_path) as db:
            # A damaged database without the index holds two live rows.
            db.execute("DROP INDEX IF EXISTS pending_attempts_one_live_per_item")
            self.raw_open(
                db, "d" * 32, created_at=self.clock() + timedelta(seconds=1),
                expires_at=self.clock() + timedelta(minutes=60),
            )
        self.clock.advance(seconds=2)
        before = database_digest(self.workspace.db_path)
        error = self.error("recall_respond", attempt_token=a, response="a linked list")
        self.assertEqual(error.code, "attempt-closed")
        self.assertEqual(database_digest(self.workspace.db_path), before)

    # -- raw SQL hardening (migration 20) ----------------------------------------

    def test_raw_sql_cannot_expire_a_response_before_its_expiry(self) -> None:
        now = _real_now()
        with sqlite3.connect(self.workspace.db_path) as db:
            self.raw_open(
                db, "a" * 32, created_at=now - timedelta(minutes=1),
                expires_at=now + timedelta(hours=1),
            )
            self.raw_respond(db, "a" * 32, now, "a linked list")
            with self.assertRaisesRegex(sqlite3.IntegrityError, "awaits recall_record"):
                db.execute(
                    "UPDATE pending_attempts SET state = 'expired'"
                    " WHERE attempt_token = ?", ("a" * 32,),
                )
            # Control: an open row may close before its expiry; it holds no
            # response, so a supersede loses nothing.
            self.raw_open(
                db, "b" * 32, item_id="stack-order", created_at=now - timedelta(minutes=1),
                expires_at=now + timedelta(hours=1),
            )
            db.execute(
                "UPDATE pending_attempts SET state = 'expired' WHERE attempt_token = ?",
                ("b" * 32,),
            )
            # Control: a response past its expiry by the database clock closes.
            self.raw_open(
                db, "c" * 32, item_id="stack-order", created_at=now - timedelta(hours=2),
                expires_at=now - timedelta(hours=1),
            )
            self.raw_respond(db, "c" * 32, now - timedelta(minutes=90), "no idea")
            db.execute(
                "UPDATE pending_attempts SET state = 'expired' WHERE attempt_token = ?",
                ("c" * 32,),
            )
        self.assertEqual(
            {token[0]: state for token, state, _ in self.states()},
            {"a": "responded", "b": "expired", "c": "expired"},
        )

    def test_raw_sql_refuses_a_forged_future_creation_time(self) -> None:
        now = _real_now()
        with sqlite3.connect(self.workspace.db_path) as db:
            for label, created in (
                ("a day ahead", now + timedelta(days=1)),
                ("ten minutes ahead", now + timedelta(minutes=10)),
            ):
                with self.subTest(label=label):
                    with self.assertRaisesRegex(
                        sqlite3.IntegrityError, "creation time is in the future"
                    ):
                        self.raw_open(
                            db, "a" * 32, created_at=created,
                            expires_at=created + timedelta(hours=1),
                        )
            # Control: a creation time at the database clock is accepted.
            self.raw_open(db, "a" * 32, created_at=now, expires_at=now + timedelta(hours=1))
            self.raw_respond(db, "a" * 32, now, "a linked list")
            # A future creation time after A's expiry no longer slips past A.
            late = now + timedelta(hours=2)
            with self.assertRaises(sqlite3.IntegrityError):
                self.raw_open(
                    db, "b" * 32, created_at=late, expires_at=late + timedelta(hours=1)
                )
        self.assertEqual(self.live(), ["a" * 32])

    def test_unique_index_refuses_a_second_live_row_for_any_version(self) -> None:
        now = _real_now()
        hour = timedelta(hours=1)
        with sqlite3.connect(self.workspace.db_path) as db:
            self.raw_open(db, "a" * 32, created_at=now, expires_at=now + hour)
            for label, content_hash in (("same version", None), ("other version", "f" * 64)):
                with self.subTest(label=label):
                    with self.assertRaisesRegex(
                        sqlite3.IntegrityError,
                        "UNIQUE constraint failed: pending_attempts.item_id",
                    ):
                        self.raw_open(
                            db, "b" * 32, created_at=now, expires_at=now + hour,
                            content_hash=content_hash,
                        )
            # Controls: another item, and the same item once A closed.
            self.raw_open(
                db, "c" * 32, item_id="stack-order", created_at=now, expires_at=now + hour
            )
            db.execute(
                "UPDATE pending_attempts SET state = 'expired' WHERE attempt_token = ?",
                ("a" * 32,),
            )
            self.raw_open(db, "b" * 32, created_at=now, expires_at=now + hour)
        self.assertEqual(self.live(), ["b" * 32])

    def test_schema_validation_compares_the_index_and_trigger_sql(self) -> None:
        tampered = {
            "narrower index": (
                "DROP INDEX pending_attempts_one_live_per_item",
                "CREATE UNIQUE INDEX pending_attempts_one_live_per_item"
                " ON pending_attempts(item_id) WHERE state = 'open'",
            ),
            "missing index": ("DROP INDEX pending_attempts_one_live_per_item",),
            "expiry trigger without the database clock": (
                "DROP TRIGGER pending_attempts_expire_response_only_after_expiry",
                """CREATE TRIGGER pending_attempts_expire_response_only_after_expiry
                    BEFORE UPDATE ON pending_attempts
                    WHEN 0
                    BEGIN
                        SELECT RAISE(ABORT, 'pending attempt has a response and awaits recall_record');
                    END""",
            ),
            "creation trigger without the database clock": (
                "DROP TRIGGER pending_attempts_reject_future_creation",
                """CREATE TRIGGER pending_attempts_reject_future_creation
                    BEFORE INSERT ON pending_attempts
                    WHEN 0
                    BEGIN
                        SELECT RAISE(ABORT, 'pending attempt creation time is in the future');
                    END""",
            ),
        }
        for index, (label, statements) in enumerate(tampered.items()):
            with self.subTest(label=label):
                workspace = make_workspace(self.parent, f"tampered-{index}")
                with sqlite3.connect(workspace.db_path) as db:
                    for statement in statements:
                        db.execute(statement)
                with self.assertRaisesRegex(WorkspaceError, "incompatible database schema"):
                    WorkspaceService.open(workspace.root)
        # Control: an untouched workspace opens.
        WorkspaceService.open(make_workspace(self.parent, "untouched").root).doctor()

    def test_upgrade_from_v19_keeps_one_live_attempt_per_item(self) -> None:
        from conftest import downgrade_skip_links_to_v19

        base = self.clock()
        hour = timedelta(hours=1)
        with sqlite3.connect(self.workspace.db_path) as db:
            downgrade_skip_links_to_v19(db)
            # Two open attempts for one item: the newer one stays live.
            self.raw_open(db, "a" * 32, created_at=base, expires_at=base + hour)
            self.raw_open(
                db, "b" * 32, created_at=base + timedelta(minutes=1),
                expires_at=base + hour, surface="http",
            )
            # A response and a newer open attempt: the response stays live,
            # because closing it would drop a stored recall.
            self.raw_open(
                db, "c" * 32, item_id="stack-order", created_at=base, expires_at=base + hour
            )
            self.raw_respond(db, "c" * 32, base + timedelta(minutes=1), "no idea")
            self.raw_open(
                db, "d" * 32, item_id="stack-order",
                created_at=base + timedelta(minutes=2), expires_at=base + hour,
            )
        reopened = WorkspaceService.open(self.workspace.root)
        reopened.doctor()
        self.assertEqual(
            rows(reopened.db_path, "SELECT MAX(version) AS v FROM schema_migrations"),
            [{"v": 20}],
        )
        self.assertEqual(
            {token[0]: state for token, state, _ in self.states()},
            {"a": "expired", "b": "open", "c": "responded", "d": "expired"},
        )
        self.assertEqual(self.live(), ["b" * 32])
        self.assertEqual(self.live("stack-order"), ["c" * 32])
        self.assertEqual(
            rows(
                reopened.db_path,
                "SELECT name FROM sqlite_master WHERE type = 'index'"
                " AND name = 'pending_attempts_one_live_per_item'",
            ),
            [{"name": "pending_attempts_one_live_per_item"}],
        )


class SkipReplayOwnershipTests(_CoreCase):
    def test_upgrade_from_v19_adds_the_skip_link_and_keeps_skips(self) -> None:
        from conftest import downgrade_skip_links_to_v19

        token = self.start()
        submission = _submission()
        self.call("recall_skip", attempt_token=token, submission_id=submission)
        with sqlite3.connect(self.workspace.db_path) as db:
            downgrade_skip_links_to_v19(db)
        legacy = rows(
            self.workspace.db_path,
            "SELECT event_id, item_id, item_content_hash, occurred_at, surface"
            " FROM review_skips",
        )
        reopened = WorkspaceService.open(self.workspace.root)
        reopened.doctor()
        self.assertEqual(
            rows(reopened.db_path, "SELECT MAX(version) AS v FROM schema_migrations"),
            [{"v": 20}],
        )
        upgraded = rows(reopened.db_path, "SELECT * FROM review_skips")
        self.assertEqual(
            [{key: row[key] for key in legacy[0]} for row in upgraded], legacy
        )
        # Skips from before migration 20 name no attempt.
        self.assertEqual([row["pending_attempt_token"] for row in upgraded], [None])
        # So a replay of such a skip cannot prove which attempt it closed and
        # is refused as a conflict, writing nothing (docs/12).
        core = ServerCore(reopened, clock=self.clock)
        before = database_digest(reopened.db_path)
        with self.assertRaises(OperationError) as caught:
            core.call(
                "recall_skip", {"attempt_token": token, "submission_id": submission},
                surface="mcp",
            )
        self.assertEqual(caught.exception.code, "submission-conflict")
        self.assertEqual(database_digest(reopened.db_path), before)

    def test_skip_replay_with_another_attempts_submission_is_a_conflict(self) -> None:
        first = self.start()
        submission = _submission()
        self.call("recall_skip", attempt_token=first, submission_id=submission)
        second = self.start()
        self.call("recall_skip", attempt_token=second, submission_id=_submission())
        before = database_digest(self.workspace.db_path)
        error = self.error("recall_skip", attempt_token=second, submission_id=submission)
        self.assertEqual(error.code, "submission-conflict")
        self.assertEqual(database_digest(self.workspace.db_path), before)
        replay = self.call("recall_skip", attempt_token=first, submission_id=submission)
        self.assertTrue(replay["replayed"])
        self.assertEqual(database_digest(self.workspace.db_path), before)

    def test_skip_links_its_event_to_the_attempt(self) -> None:
        token = self.start()
        submission = _submission()
        self.call("recall_skip", attempt_token=token, submission_id=submission)
        self.assertEqual(
            rows(
                self.workspace.db_path,
                "SELECT event_id, pending_attempt_token FROM review_skips",
            ),
            [{"event_id": f"skip-{submission}", "pending_attempt_token": token}],
        )


class AnchoredPatternTests(_CoreCase):
    """Patterns match the whole value; a trailing newline is refused (P2a)."""

    def test_schema_patterns_refuse_a_trailing_newline(self) -> None:
        for pattern, value in (
            ("^[0-9a-f]{32}$", "a" * 32 + "\n"),
            ("^[a-z0-9]+(?:-[a-z0-9]+)*$", "hash-lookup\n"),
        ):
            with self.subTest(value=value):
                with self.assertRaises(SchemaError):
                    validate(value, {"type": "string", "pattern": pattern})

    def test_trailing_newline_submission_id_is_refused_and_writes_nothing(self) -> None:
        token = self.start()
        self.call("recall_respond", attempt_token=token, response="a hash table")
        before = database_digest(self.workspace.db_path)
        for name in ("recall_record", "recall_skip"):
            with self.subTest(operation=name):
                arguments = {"attempt_token": token, "submission_id": _submission() + "\n"}
                if name == "recall_record":
                    arguments.update(
                        result="demonstrated", confidence=4,
                        agent_help="none", graded_by="learner",
                    )
                with self.assertRaises(OperationError) as caught:
                    self.core.call(name, arguments, surface="mcp")
                self.assertEqual(caught.exception.code, "invalid-request")
        self.assertEqual(database_digest(self.workspace.db_path), before)
        self.assertEqual(
            rows(self.workspace.db_path, "SELECT event_id FROM attempts"), []
        )

    def test_trailing_newline_item_id_token_and_client_are_refused(self) -> None:
        token = self.start()
        before = database_digest(self.workspace.db_path)
        for name, arguments in (
            ("recall_start", {"item_id": "hash-lookup\n"}),
            ("history", {"item_id": "hash-lookup\n"}),
            ("recall_start", {"item_id": "hash-lookup", "client": "claude-desktop\n"}),
            ("recall_respond", {"attempt_token": token + "\n", "response": "x"}),
            ("recall_hint", {"attempt_token": token + "\n"}),
        ):
            with self.subTest(operation=name, arguments=arguments):
                with self.assertRaises(OperationError) as caught:
                    self.core.call(name, arguments, surface="mcp")
                self.assertEqual(caught.exception.code, "invalid-request")
        self.assertEqual(database_digest(self.workspace.db_path), before)


class ClientNameTests(_CoreCase):
    def test_client_names_are_limited_to_printable_ascii(self) -> None:
        before = database_digest(self.workspace.db_path)
        for client in (
            "bell\x07", "\x1b[31mred", "abc\u202edef", "zero\u200bwidth",
            "tab\tname", "caf\u00e9", "", "x" * 129, "semi;colon",
        ):
            with self.subTest(client=client):
                error = self.error("recall_start", item_id="hash-lookup", client=client)
                self.assertEqual(error.code, "invalid-request")
        self.assertEqual(database_digest(self.workspace.db_path), before)
        for client in ("claude-desktop/1.2.3", "Hermes Agent@host:8765", "a+b_c.d", "x" * 128):
            with self.subTest(client=client):
                started = self.call("recall_start", item_id="hash-lookup", client=client)
                self.assertEqual(started["attempt"]["client"], client)

    def test_workspace_refuses_a_control_character_client(self) -> None:
        item = self.workspace.load_item("hash-lookup")
        with self.assertRaises(WorkspaceError):
            self.workspace.create_pending_attempt(
                attempt_token=secrets.token_hex(16), item_id=item.item_id,
                item_content_hash=item.content_hash, prompt_shown=item.prompt,
                surface="mcp", client="evil\x1b[2J", created_at=self.clock(),
                expires_at=self.clock() + timedelta(minutes=60),
            )


class DeadlineTests(_CoreCase):
    """A call past its deadline writes nothing (review P2b)."""

    def test_call_that_reaches_the_lock_after_its_deadline_writes_nothing(self) -> None:
        token = self.start()
        self.call("recall_respond", attempt_token=token, response="a hash table")
        before = database_digest(self.workspace.db_path)
        expired = time.monotonic() - 1
        for name, arguments in (
            ("recall_start", {"item_id": "hash-lookup"}),
            ("recall_record", {
                "attempt_token": token, "submission_id": _submission(),
                "result": "demonstrated", "confidence": 4,
                "agent_help": "none", "graded_by": "learner",
            }),
            ("workload", {}),
        ):
            with self.subTest(operation=name):
                with self.assertRaises(OperationError) as caught:
                    self.core.call(name, arguments, surface="mcp", deadline=expired)
                self.assertEqual(caught.exception.code, "operation-timeout")
                self.assertEqual(caught.exception.status, 504)
        self.assertEqual(database_digest(self.workspace.db_path), before)

    def test_deadline_passing_inside_the_call_stops_before_the_write(self) -> None:
        before = database_digest(self.workspace.db_path)
        original = self.workspace.load_item

        def slow_load(item_id):
            time.sleep(0.2)
            return original(item_id)

        with mock.patch.object(self.workspace, "load_item", side_effect=slow_load):
            with self.assertRaises(OperationError) as caught:
                self.core.call(
                    "recall_start", {"item_id": "hash-lookup"}, surface="mcp",
                    deadline=time.monotonic() + 0.05,
                )
        self.assertEqual(caught.exception.code, "operation-timeout")
        self.assertEqual(database_digest(self.workspace.db_path), before)
        self.assertEqual(rows(self.workspace.db_path, "SELECT * FROM pending_attempts"), [])

    def _expire_deadline_on_clock_read(self):
        """Pass the call's deadline when it reads the server clock.

        The lock-entry check has already passed by then, so only the check
        just before the write can stop the call.
        """
        core = self.core
        clock_read = ServerCore.now

        def now():
            core._deadline = time.monotonic() - 1
            return clock_read(core)

        return mock.patch.object(core, "now", side_effect=now)

    def _assert_times_out_without_writing(self, name: str, arguments: dict) -> None:
        before = database_digest(self.workspace.db_path)
        with self._expire_deadline_on_clock_read():
            with self.assertRaises(OperationError) as caught:
                self.core.call(
                    name, arguments, surface="mcp", deadline=time.monotonic() + 30
                )
        self.assertEqual(caught.exception.code, "operation-timeout")
        self.assertEqual(database_digest(self.workspace.db_path), before)

    def test_each_write_checks_its_deadline_just_before_writing(self) -> None:
        loaded = self.call("study_load", item_id="stack-order")["item"]
        with self.subTest(operation="study_record"):
            self._assert_times_out_without_writing("study_record", {
                "item_id": "stack-order",
                "item_content_hash": loaded["content_hash"],
                "learning_unit_hash": loaded["learning_unit_hash"],
                "completed": True,
            })
        with self.subTest(operation="recall_start"):
            self._assert_times_out_without_writing(
                "recall_start", {"item_id": "hash-lookup"}
            )
        token = self.start()
        for name, arguments in (
            ("recall_hint", {"attempt_token": token}),
            ("recall_respond", {"attempt_token": token, "response": "a hash table"}),
            ("recall_skip", {"attempt_token": token, "submission_id": _submission()}),
        ):
            with self.subTest(operation=name):
                self._assert_times_out_without_writing(name, arguments)
        self.call("recall_respond", attempt_token=token, response="a hash table")
        with self.subTest(operation="recall_record"):
            self._assert_times_out_without_writing("recall_record", {
                "attempt_token": token, "submission_id": _submission(),
                "result": "demonstrated", "confidence": 4,
                "agent_help": "none", "graded_by": "learner",
            })
        self.assertEqual(rows(self.workspace.db_path, "SELECT * FROM attempts"), [])
        self.assertEqual(rows(self.workspace.db_path, "SELECT * FROM study_events"), [])
        self.assertEqual(rows(self.workspace.db_path, "SELECT * FROM review_skips"), [])

    def test_call_within_its_deadline_runs(self) -> None:
        started = self.core.call(
            "recall_start", {"item_id": "hash-lookup"}, surface="mcp",
            deadline=time.monotonic() + 30,
        )
        self.assertEqual(started["attempt"]["state"], "open")


if __name__ == "__main__":
    unittest.main()
