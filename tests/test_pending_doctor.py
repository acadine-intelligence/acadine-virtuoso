"""Doctor clock warnings use raw SQL and the actual database clock.

The server fixtures use a fixed 2025 clock, so they do not exercise triggers
that read SQLite's clock. These rows keep created_at within migration 20's
five-minute allowance while preserving future response and hint timestamps.
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

from virtuoso.practice import PracticeService
from virtuoso.workspace import WorkspaceService


class PendingAttemptDoctorTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = WorkspaceService.init(Path(temporary.name).resolve() / "learner")
        with sqlite3.connect(self.workspace.db_path) as db:
            self.now = datetime.fromisoformat(
                db.execute("SELECT strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')").fetchone()[0]
            )

    def _pending(
        self,
        item_id: str,
        *,
        created_at: datetime,
        responded_at: datetime | None = None,
        hint_shown_at: datetime | None = None,
        expires_at: datetime | None = None,
    ) -> dict:
        self.workspace.add_item(
            item_id=item_id, title=item_id, focus="clock-check",
            prompt="What does this clock show?", answer="A synthetic time.",
            hint="Check the hands.",
        )
        item = self.workspace.load_item(item_id)
        token = f"pending-{item_id}".ljust(32, "-")
        with sqlite3.connect(self.workspace.db_path) as db:
            db.execute("PRAGMA foreign_keys = ON")
            db.execute(
                """INSERT INTO pending_attempts(
                       attempt_token, item_id, item_content_hash, prompt_shown,
                       surface, created_at, expires_at)
                   VALUES (?, ?, ?, ?, 'http', ?, ?)""",
                (token, item_id, item.content_hash, item.prompt, created_at.isoformat(),
                 (expires_at or created_at + timedelta(minutes=60)).isoformat()),
            )
            if hint_shown_at is not None:
                db.execute(
                    "UPDATE pending_attempts SET hint_shown_at = ? WHERE attempt_token = ?",
                    (hint_shown_at.isoformat(), token),
                )
            if responded_at is not None:
                db.execute(
                    """UPDATE pending_attempts SET state = 'responded', response = ?,
                           responded_at = ? WHERE attempt_token = ?""",
                    ("A synthetic response.", responded_at.isoformat(), token),
                )
            db.row_factory = sqlite3.Row
            return dict(db.execute(
                "SELECT * FROM pending_attempts WHERE attempt_token = ?", (token,)
            ).fetchone())

    def _snapshot(self) -> str:
        with sqlite3.connect(self.workspace.db_path) as db:
            return "\n".join(db.iterdump())

    def test_doctor_warns_for_each_future_pending_timestamp(self) -> None:
        rows = [
            self._pending("future-start", created_at=self.now + timedelta(minutes=2)),
            self._pending(
                "future-response", created_at=self.now - timedelta(minutes=1),
                responded_at=self.now + timedelta(minutes=2),
            ),
            self._pending(
                "future-hint", created_at=self.now - timedelta(minutes=1),
                # Compare instants, including a timestamp with another UTC offset.
                hint_shown_at=(self.now + timedelta(minutes=2)).astimezone(
                    timezone(timedelta(hours=-4))
                ),
            ),
        ]
        before = self._snapshot()
        report = self.workspace.doctor(now=self.now)
        warnings = {row["item_id"]: row for row in report["pending_attempt_warnings"]}
        self.assertEqual(set(warnings), {row["item_id"] for row in rows})
        for row, future_field in zip(rows, ("created_at", "responded_at", "hint_shown_at")):
            with self.subTest(field=future_field):
                warning = warnings[row["item_id"]]
                self.assertEqual(warning["reason"], "future-timestamp")
                self.assertEqual(warning["future_fields"], [future_field])
                for field in (
                    "attempt_token", "created_at", "responded_at", "hint_shown_at", "expires_at"
                ):
                    self.assertEqual(warning[field], row[field])
        self.assertEqual(report["status"], "healthy")
        self.assertEqual(self._snapshot(), before)

    def test_doctor_json_reports_future_response_and_hint_as_warning(self) -> None:
        row = self._pending(
            "future-clock", created_at=self.now - timedelta(minutes=1),
            hint_shown_at=self.now + timedelta(minutes=1),
            responded_at=self.now + timedelta(minutes=2),
        )
        before = self._snapshot()
        completed = subprocess.run(
            [sys.executable, "-m", "virtuoso.cli", "--workspace",
             str(self.workspace.root), "doctor", "--json"],
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        report = json.loads(completed.stdout)
        self.assertEqual(report["status"], "healthy")
        self.assertEqual(len(report["pending_attempt_warnings"]), 1)
        warning = report["pending_attempt_warnings"][0]
        self.assertEqual(warning["item_id"], "future-clock")
        self.assertEqual(warning["future_fields"], ["responded_at", "hint_shown_at"])
        for field in ("created_at", "responded_at", "hint_shown_at", "expires_at"):
            self.assertEqual(warning[field], row[field])
        self.assertEqual(self._snapshot(), before)

    def test_doctor_ignores_live_pending_times_at_or_before_now(self) -> None:
        self._pending("current-start", created_at=self.now)
        self._pending(
            "current-response", created_at=self.now - timedelta(minutes=2),
            hint_shown_at=self.now - timedelta(minutes=1), responded_at=self.now,
        )
        report = self.workspace.doctor(now=self.now)
        self.assertEqual(report["pending_attempt_warnings"], [])
        self.assertEqual(report["status"], "healthy")

    def test_doctor_ignores_elapsed_and_closed_pending_rows(self) -> None:
        self._pending(
            "elapsed", created_at=self.now - timedelta(minutes=61),
            responded_at=self.now - timedelta(minutes=60),
        )
        closed = self._pending(
            "closed", created_at=self.now + timedelta(minutes=1),
            hint_shown_at=self.now + timedelta(minutes=2),
        )
        recorded = self._pending(
            "recorded", created_at=self.now,
            responded_at=self.now + timedelta(minutes=1),
        )
        attempt = PracticeService(self.workspace).run_administered(
            item_id="recorded", response=recorded["response"], result="partial",
            confidence=3, graded_by="agent", surface="http",
            now=self.now + timedelta(minutes=2),
        ).attempt
        with sqlite3.connect(self.workspace.db_path) as db:
            db.execute(
                "UPDATE pending_attempts SET state = 'expired' WHERE attempt_token = ?",
                (closed["attempt_token"],),
            )
            db.execute(
                """UPDATE pending_attempts SET state = 'recorded', attempt_event_id = ?,
                       recorded_at = ? WHERE attempt_token = ?""",
                (attempt.event_id, (self.now + timedelta(minutes=2)).isoformat(),
                 recorded["attempt_token"]),
            )
        before = self._snapshot()
        report = self.workspace.doctor(now=self.now)
        self.assertEqual(report["pending_attempt_warnings"], [])
        self.assertEqual(report["status"], "healthy")
        self.assertEqual(self._snapshot(), before)
