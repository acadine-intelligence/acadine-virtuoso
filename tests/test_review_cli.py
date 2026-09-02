from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class ReviewCliJourneyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name).resolve() / "learner"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _run(
        self, *args: str, input_text: str | None = None, expected: int = 0
    ) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "virtuoso.cli",
                "--workspace",
                str(self.workspace),
                *args,
            ],
            input=input_text,
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

    def _init_with_item(self) -> None:
        self._run("init", "--json")
        self._run(
            "add",
            "--id",
            "testing-effect",
            "--title",
            "Explain the testing effect",
            "--focus",
            "learning-science",
            "--prompt",
            "Why does active recall improve memory?",
            "--answer",
            "Retrieval changes memory and strengthens later access.",
            "--hint",
            "Compare retrieval with rereading.",
            "--follow-up",
            "Give one coding example.",
            "--json",
        )

    def test_due_contract_lists_new_items_without_exposing_answers(self) -> None:
        self._init_with_item()

        payload = json.loads(self._run("review", "due", "--json").stdout)

        self.assertEqual(payload["schema"], "virtuoso/review-queue@0.1")
        self.assertEqual(
            payload["items"],
            [
                {
                    "content_hash": payload["items"][0]["content_hash"],
                    "due_at": None,
                    "item_id": "testing-effect",
                    "status": "new",
                }
            ],
        )
        self.assertEqual(len(payload["items"][0]["content_hash"]), 64)
        self.assertNotIn("answer", json.dumps(payload).lower())

    def test_due_contract_orders_due_items_before_new_items(self) -> None:
        self._init_with_item()
        self._run(
            "add",
            "--id",
            "new-item",
            "--title",
            "A new item",
            "--focus",
            "learning-science",
            "--prompt",
            "What remains new?",
            "--answer",
            "An item without scheduler state.",
            "--json",
        )
        snapshot = json.loads(
            self._run(
                "review", "load", "--item", "testing-effect", "--json"
            ).stdout
        )["item"]
        old_attempt = {
            "schema": "virtuoso/review-attempt@0.1",
            "submission_id": "88888888888888888888888888888888",
            "item_id": "testing-effect",
            "item_content_hash": snapshot["content_hash"],
            "started_at": "2020-01-01T12:00:00+00:00",
            "initial_answered_at": "2020-01-01T12:00:01+00:00",
            "completed_at": "2020-01-01T12:00:02+00:00",
            "initial_response": "A measured response.",
            "retry": None,
            "hint_used": False,
            "answer_revealed": True,
            "result": "partial",
            "confidence": 3,
            "open_notes": False,
        }
        self._run(
            "review", "record", "--json", input_text=json.dumps(old_attempt)
        )

        payload = json.loads(self._run("review", "due", "--json").stdout)

        self.assertEqual(
            [(item["item_id"], item["status"]) for item in payload["items"]],
            [("testing-effect", "due"), ("new-item", "new")],
        )
        self.assertIsNotNone(payload["items"][0]["due_at"])
        self.assertIsNone(payload["items"][1]["due_at"])

    def test_load_contract_returns_hash_bound_item_content_snapshot(self) -> None:
        self._init_with_item()
        queue = json.loads(self._run("review", "due", "--json").stdout)

        payload = json.loads(
            self._run(
                "review", "load", "--item", "testing-effect", "--json"
            ).stdout
        )

        self.assertEqual(payload["schema"], "virtuoso/review-item@0.1")
        self.assertEqual(
            payload["item"],
            {
                "answer": "Retrieval changes memory and strengthens later access.",
                "content_hash": queue["items"][0]["content_hash"],
                "focus": "learning-science",
                "follow_up": "Give one coding example.",
                "hint": "Compare retrieval with rereading.",
                "item_id": "testing-effect",
                "learning_context": "atomic-recall",
                "prompt": "Why does active recall improve memory?",
                "title": "Explain the testing effect",
            },
        )
        self.assertNotIn(str(self.workspace), json.dumps(payload))

    def test_record_contract_persists_measured_direct_attempt_and_proposal(self) -> None:
        self._init_with_item()
        snapshot = json.loads(
            self._run(
                "review", "load", "--item", "testing-effect", "--json"
            ).stdout
        )["item"]
        request = {
            "schema": "virtuoso/review-attempt@0.1",
            "submission_id": "0123456789abcdef0123456789abcdef",
            "item_id": "testing-effect",
            "item_content_hash": snapshot["content_hash"],
            "started_at": "2026-09-02T12:00:00+00:00",
            "initial_answered_at": "2026-09-02T12:00:01.250000+00:00",
            "completed_at": "2026-09-02T12:00:05+00:00",
            "initial_response": "Retrieval strengthens later access paths.",
            "retry": {
                "response": "It changes memory through retrieval.",
                "latency_ms": 750,
            },
            "hint_used": True,
            "answer_revealed": True,
            "result": "demonstrated",
            "confidence": 4,
            "open_notes": False,
        }

        payload = json.loads(
            self._run(
                "review", "record", "--json", input_text=json.dumps(request)
            ).stdout
        )

        self.assertEqual(
            payload["schema"], "virtuoso/review-attempt-result@0.1"
        )
        self.assertEqual(payload["attempt"]["event_id"], "attempt-" + request["submission_id"])
        self.assertEqual(payload["attempt"]["initial_latency_ms"], 1250)
        self.assertFalse(payload["attempt"]["administered"])
        self.assertEqual(payload["proposal"]["algorithm"], "fsrs")

        evidence = json.loads(self._run("attempts", "--json").stdout)
        self.assertEqual(len(evidence["attempts"]), 1)
        self.assertEqual(len(evidence["proposals"]), 1)
        stored = evidence["attempts"][0]
        self.assertFalse(stored["administered"])
        self.assertEqual(stored["initial_latency_ms"], 1250)
        self.assertEqual(
            [entry["kind"] for entry in stored["support_actions"]],
            ["retry-unaided", "hint", "worked-feedback"],
        )

    def test_skip_contract_appends_evidence_without_changing_scheduler(self) -> None:
        self._init_with_item()
        snapshot = json.loads(
            self._run(
                "review", "load", "--item", "testing-effect", "--json"
            ).stdout
        )["item"]
        attempt = {
            "schema": "virtuoso/review-attempt@0.1",
            "submission_id": "11111111111111111111111111111111",
            "item_id": "testing-effect",
            "item_content_hash": snapshot["content_hash"],
            "started_at": "2026-09-02T12:00:00+00:00",
            "initial_answered_at": "2026-09-02T12:00:01+00:00",
            "completed_at": "2026-09-02T12:00:02+00:00",
            "initial_response": "A measured response.",
            "retry": None,
            "hint_used": False,
            "answer_revealed": True,
            "result": "partial",
            "confidence": 3,
            "open_notes": False,
        }
        self._run("review", "record", "--json", input_text=json.dumps(attempt))
        db_path = self.workspace / ".virtuoso" / "state.sqlite3"
        with sqlite3.connect(db_path) as db:
            before_state = db.execute(
                "SELECT state_json, source_event_id FROM scheduler_state"
            ).fetchall()
            before_attempts = db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0]
            before_proposals = db.execute(
                "SELECT COUNT(*) FROM scheduler_proposals"
            ).fetchone()[0]

        request = {
            "schema": "virtuoso/review-skip@0.1",
            "submission_id": "22222222222222222222222222222222",
            "item_id": "testing-effect",
            "item_content_hash": snapshot["content_hash"],
            "occurred_at": "2026-09-02T12:01:00+00:00",
            "surface": "obsidian-plugin",
        }
        payload = json.loads(
            self._run(
                "review", "skip", "--json", input_text=json.dumps(request)
            ).stdout
        )

        self.assertEqual(payload["schema"], "virtuoso/review-skip-result@0.1")
        self.assertEqual(payload["skip"]["event_id"], "skip-" + request["submission_id"])
        with sqlite3.connect(db_path) as db:
            after_state = db.execute(
                "SELECT state_json, source_event_id FROM scheduler_state"
            ).fetchall()
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0],
                before_attempts,
            )
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM scheduler_proposals").fetchone()[0],
                before_proposals,
            )
            skip = db.execute(
                "SELECT item_id, item_content_hash, surface FROM review_skips"
            ).fetchone()
            with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                db.execute(
                    "UPDATE review_skips SET surface = 'changed' WHERE item_id = ?",
                    ("testing-effect",),
                )
            db.rollback()
            with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                db.execute(
                    "DELETE FROM review_skips WHERE item_id = ?", ("testing-effect",)
                )
            db.rollback()
        self.assertEqual(after_state, before_state)
        self.assertEqual(skip, ("testing-effect", snapshot["content_hash"], "obsidian-plugin"))
        evidence = json.loads(self._run("attempts", "--json").stdout)
        self.assertEqual(
            evidence["skips"],
            [
                {
                    "event_id": "skip-" + request["submission_id"],
                    "item_content_hash": snapshot["content_hash"],
                    "item_id": "testing-effect",
                    "occurred_at": request["occurred_at"],
                    "surface": "obsidian-plugin",
                }
            ],
        )

    def test_record_rejects_malformed_or_unknown_schema_with_typed_recovery(self) -> None:
        self._init_with_item()
        snapshot = json.loads(
            self._run(
                "review", "load", "--item", "testing-effect", "--json"
            ).stdout
        )["item"]
        valid = {
            "schema": "virtuoso/review-attempt@0.1",
            "submission_id": "33333333333333333333333333333333",
            "item_id": "testing-effect",
            "item_content_hash": snapshot["content_hash"],
            "started_at": "2026-09-02T12:00:00+00:00",
            "initial_answered_at": "2026-09-02T12:00:01+00:00",
            "completed_at": "2026-09-02T12:00:02+00:00",
            "initial_response": "A measured response.",
            "retry": None,
            "hint_used": False,
            "answer_revealed": True,
            "result": "partial",
            "confidence": 3,
            "open_notes": False,
        }
        unknown = dict(valid, schema="virtuoso/review-attempt@9.9")

        for raw in ("{", json.dumps(unknown)):
            with self.subTest(raw=raw):
                failed = self._run(
                    "review", "record", "--json", input_text=raw, expected=2
                )
                self.assertEqual(failed.stdout, "")
                error = json.loads(failed.stderr)
                self.assertEqual(error["schema"], "virtuoso/review-error@0.1")
                self.assertEqual(error["error"]["code"], "invalid-request")
                self.assertEqual(error["error"]["recovery"], "check-contract")
        evidence = json.loads(self._run("attempts", "--json").stdout)
        self.assertEqual(evidence["attempts"], [])
        self.assertEqual(evidence["proposals"], [])

    def test_write_contract_rejects_invalid_typed_values_before_any_write(self) -> None:
        self._init_with_item()
        snapshot = json.loads(
            self._run(
                "review", "load", "--item", "testing-effect", "--json"
            ).stdout
        )["item"]
        valid_attempt = {
            "schema": "virtuoso/review-attempt@0.1",
            "submission_id": "cccccccccccccccccccccccccccccccc",
            "item_id": "testing-effect",
            "item_content_hash": snapshot["content_hash"],
            "started_at": "2026-09-02T12:00:00+00:00",
            "initial_answered_at": "2026-09-02T12:00:01+00:00",
            "completed_at": "2026-09-02T12:00:02+00:00",
            "initial_response": "A measured response.",
            "retry": None,
            "hint_used": False,
            "answer_revealed": True,
            "result": "partial",
            "confidence": 3,
            "open_notes": False,
        }
        invalid_attempts = [
            dict(valid_attempt, result="correct"),
            dict(valid_attempt, confidence=True),
            dict(valid_attempt, open_notes="no"),
            dict(valid_attempt, item_content_hash="bad-hash"),
        ]
        invalid_skip = {
            "schema": "virtuoso/review-skip@0.1",
            "submission_id": "dddddddddddddddddddddddddddddddd",
            "item_id": "testing-effect",
            "item_content_hash": snapshot["content_hash"],
            "occurred_at": "2026-09-02T12:01:00+00:00",
            "surface": "external-writer",
        }

        for operation, request in [
            *(("record", request) for request in invalid_attempts),
            ("skip", invalid_skip),
        ]:
            with self.subTest(operation=operation, request=request):
                failed = self._run(
                    "review",
                    operation,
                    "--json",
                    input_text=json.dumps(request),
                    expected=2,
                )
                error = json.loads(failed.stderr)
                self.assertEqual(error["error"]["code"], "invalid-request")
                self.assertEqual(error["error"]["recovery"], "check-contract")

        db_path = self.workspace / ".virtuoso" / "state.sqlite3"
        with sqlite3.connect(db_path) as db:
            for table in (
                "attempts",
                "scheduler_proposals",
                "scheduler_state",
                "review_skips",
            ):
                self.assertEqual(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)

    def test_repeated_skip_submission_is_rejected_without_second_event(self) -> None:
        self._init_with_item()
        snapshot = json.loads(
            self._run(
                "review", "load", "--item", "testing-effect", "--json"
            ).stdout
        )["item"]
        request = {
            "schema": "virtuoso/review-skip@0.1",
            "submission_id": "99999999999999999999999999999999",
            "item_id": "testing-effect",
            "item_content_hash": snapshot["content_hash"],
            "occurred_at": "2026-09-02T12:01:00+00:00",
            "surface": "obsidian-plugin",
        }
        raw = json.dumps(request)
        self._run("review", "skip", "--json", input_text=raw)

        failed = self._run(
            "review", "skip", "--json", input_text=raw, expected=2
        )

        error = json.loads(failed.stderr)
        self.assertEqual(error["error"]["code"], "already-recorded")
        self.assertEqual(error["error"]["recovery"], "advance-card")
        db_path = self.workspace / ".virtuoso" / "state.sqlite3"
        with sqlite3.connect(db_path) as db:
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM review_skips").fetchone()[0], 1
            )
            self.assertEqual(db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0], 0)
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM scheduler_state").fetchone()[0], 0
            )

    def test_snapshot_hash_mismatch_rejects_every_review_write(self) -> None:
        self._init_with_item()
        attempt = {
            "schema": "virtuoso/review-attempt@0.1",
            "submission_id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "item_id": "testing-effect",
            "item_content_hash": "0" * 64,
            "started_at": "2026-09-02T12:00:00+00:00",
            "initial_answered_at": "2026-09-02T12:00:01+00:00",
            "completed_at": "2026-09-02T12:00:02+00:00",
            "initial_response": "A response to an old snapshot.",
            "retry": None,
            "hint_used": False,
            "answer_revealed": True,
            "result": "partial",
            "confidence": 3,
            "open_notes": False,
        }
        skip = {
            "schema": "virtuoso/review-skip@0.1",
            "submission_id": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "item_id": "testing-effect",
            "item_content_hash": "0" * 64,
            "occurred_at": "2026-09-02T12:01:00+00:00",
            "surface": "obsidian-plugin",
        }

        for operation, request in (("record", attempt), ("skip", skip)):
            with self.subTest(operation=operation):
                failed = self._run(
                    "review",
                    operation,
                    "--json",
                    input_text=json.dumps(request),
                    expected=2,
                )
                error = json.loads(failed.stderr)
                self.assertEqual(error["error"]["code"], "stale-content")
                self.assertEqual(error["error"]["recovery"], "reload-item")

        db_path = self.workspace / ".virtuoso" / "state.sqlite3"
        with sqlite3.connect(db_path) as db:
            for table in (
                "attempts",
                "scheduler_proposals",
                "scheduler_state",
                "review_skips",
            ):
                self.assertEqual(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)

    def test_stale_item_rejects_attempt_and_skip_without_any_state_change(self) -> None:
        self._init_with_item()
        snapshot = json.loads(
            self._run(
                "review", "load", "--item", "testing-effect", "--json"
            ).stdout
        )["item"]
        item_path = self.workspace / "items" / "testing-effect.md"
        item_path.write_text(
            item_path.read_text(encoding="utf-8") + "\nLearner changed the prompt.\n",
            encoding="utf-8",
        )
        attempt = {
            "schema": "virtuoso/review-attempt@0.1",
            "submission_id": "44444444444444444444444444444444",
            "item_id": "testing-effect",
            "item_content_hash": snapshot["content_hash"],
            "started_at": "2026-09-02T12:00:00+00:00",
            "initial_answered_at": "2026-09-02T12:00:01+00:00",
            "completed_at": "2026-09-02T12:00:02+00:00",
            "initial_response": "A response to the old prompt.",
            "retry": None,
            "hint_used": False,
            "answer_revealed": True,
            "result": "partial",
            "confidence": 3,
            "open_notes": False,
        }
        skip = {
            "schema": "virtuoso/review-skip@0.1",
            "submission_id": "55555555555555555555555555555555",
            "item_id": "testing-effect",
            "item_content_hash": snapshot["content_hash"],
            "occurred_at": "2026-09-02T12:01:00+00:00",
            "surface": "obsidian-plugin",
        }

        for operation, request in (("record", attempt), ("skip", skip)):
            with self.subTest(operation=operation):
                failed = self._run(
                    "review",
                    operation,
                    "--json",
                    input_text=json.dumps(request),
                    expected=2,
                )
                error = json.loads(failed.stderr)
                self.assertEqual(error["error"]["code"], "stale-content")
                self.assertEqual(error["error"]["recovery"], "reload-item")

        db_path = self.workspace / ".virtuoso" / "state.sqlite3"
        with sqlite3.connect(db_path) as db:
            counts = {
                table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in (
                    "attempts",
                    "scheduler_proposals",
                    "scheduler_state",
                    "review_skips",
                )
            }
        self.assertEqual(counts, {table: 0 for table in counts})

    def test_load_stale_item_returns_reload_recovery(self) -> None:
        self._init_with_item()
        item_path = self.workspace / "items" / "testing-effect.md"
        item_path.write_text(
            item_path.read_text(encoding="utf-8") + "\nChanged before load.\n",
            encoding="utf-8",
        )

        failed = self._run(
            "review",
            "load",
            "--item",
            "testing-effect",
            "--json",
            expected=2,
        )

        error = json.loads(failed.stderr)
        self.assertEqual(error["error"]["code"], "stale-content")
        self.assertEqual(error["error"]["recovery"], "reload-item")
        self.assertEqual(failed.stdout, "")

    def test_repeated_record_submission_is_rejected_without_second_transition(self) -> None:
        self._init_with_item()
        snapshot = json.loads(
            self._run(
                "review", "load", "--item", "testing-effect", "--json"
            ).stdout
        )["item"]
        request = {
            "schema": "virtuoso/review-attempt@0.1",
            "submission_id": "66666666666666666666666666666666",
            "item_id": "testing-effect",
            "item_content_hash": snapshot["content_hash"],
            "started_at": "2026-09-02T12:00:00+00:00",
            "initial_answered_at": "2026-09-02T12:00:01+00:00",
            "completed_at": "2026-09-02T12:00:02+00:00",
            "initial_response": "A measured response.",
            "retry": None,
            "hint_used": False,
            "answer_revealed": True,
            "result": "partial",
            "confidence": 3,
            "open_notes": False,
        }
        raw = json.dumps(request)
        self._run("review", "record", "--json", input_text=raw)
        db_path = self.workspace / ".virtuoso" / "state.sqlite3"
        with sqlite3.connect(db_path) as db:
            before_state = db.execute(
                "SELECT state_json, source_event_id FROM scheduler_state"
            ).fetchall()

        failed = self._run(
            "review", "record", "--json", input_text=raw, expected=2
        )

        error = json.loads(failed.stderr)
        self.assertEqual(error["error"]["code"], "already-recorded")
        self.assertEqual(error["error"]["recovery"], "advance-card")
        with sqlite3.connect(db_path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0], 1)
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM scheduler_proposals").fetchone()[0], 1
            )
            after_state = db.execute(
                "SELECT state_json, source_event_id FROM scheduler_state"
            ).fetchall()
        self.assertEqual(after_state, before_state)

    def test_database_write_failure_returns_recovery_and_leaves_state_atomic(self) -> None:
        self._init_with_item()
        snapshot = json.loads(
            self._run(
                "review", "load", "--item", "testing-effect", "--json"
            ).stdout
        )["item"]
        request = {
            "schema": "virtuoso/review-attempt@0.1",
            "submission_id": "77777777777777777777777777777777",
            "item_id": "testing-effect",
            "item_content_hash": snapshot["content_hash"],
            "started_at": "2026-09-02T12:00:00+00:00",
            "initial_answered_at": "2026-09-02T12:00:01+00:00",
            "completed_at": "2026-09-02T12:00:02+00:00",
            "initial_response": "A measured response.",
            "retry": None,
            "hint_used": False,
            "answer_revealed": True,
            "result": "partial",
            "confidence": 3,
            "open_notes": False,
        }
        db_path = self.workspace / ".virtuoso" / "state.sqlite3"
        lock = sqlite3.connect(db_path)
        lock.execute("BEGIN EXCLUSIVE")
        try:
            failed = self._run(
                "review",
                "record",
                "--json",
                input_text=json.dumps(request),
                expected=2,
            )
        finally:
            lock.rollback()
            lock.close()

        error = json.loads(failed.stderr)
        self.assertEqual(error["error"]["code"], "workspace-busy")
        self.assertEqual(error["error"]["recovery"], "retry-submit")
        with sqlite3.connect(db_path) as db:
            counts = {
                table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("attempts", "scheduler_proposals", "scheduler_state")
            }
        self.assertEqual(counts, {table: 0 for table in counts})


if __name__ == "__main__":
    unittest.main()
