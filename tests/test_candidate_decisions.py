from __future__ import annotations

import importlib
import sqlite3
import tempfile
import unittest
from pathlib import Path

from virtuoso.workspace import WorkspaceService, WorkspaceError


class CandidateDecisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name).resolve()
        self.vault = root / "vault"
        self.vault.mkdir()
        self.workspace = WorkspaceService.init(root / "learner")
        self.workspace.add_source(source_id="vault", kind="obsidian", root=self.vault)
        note = self.vault / "Origin.md"
        note.write_text("# Origin\n\n[[Missing]].\n", encoding="utf-8")
        self.workspace.scan_source("vault")
        module = importlib.import_module("virtuoso.candidates")
        run = module.CandidateService(self.workspace).generate(
            source_id="vault", relative_path="Origin.md"
        )
        self.candidate_id = run.candidates[0].candidate_id
        self.service = module.CandidateService(self.workspace)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_accept_is_recorded(self) -> None:
        result = self.service.decide(
            candidate_id=self.candidate_id,
            decision="accept",
            note="Will draft tonight.",
        )
        self.assertEqual(result.review_state, "accepted")
        self.assertFalse(result.claims_mastery)
        with sqlite3.connect(self.workspace.db_path) as db:
            rows = db.execute(
                "SELECT decision, note FROM candidate_decisions"
            ).fetchall()
        self.assertEqual(rows, [("accept", "Will draft tonight.")])

    def test_reject_is_recorded_not_deleted(self) -> None:
        self.service.decide(
            candidate_id=self.candidate_id,
            decision="reject",
            note="Alias resolves elsewhere.",
        )
        with sqlite3.connect(self.workspace.db_path) as db:
            proposals = db.execute(
                "SELECT COUNT(*) FROM review_candidates"
            ).fetchone()[0]
            decisions = db.execute(
                "SELECT COUNT(*) FROM candidate_decisions"
            ).fetchone()[0]
        self.assertEqual(proposals, 1)
        self.assertEqual(decisions, 1)

    def test_decisions_are_append_only(self) -> None:
        self.service.decide(
            candidate_id=self.candidate_id, decision="accept", note=None
        )
        with sqlite3.connect(self.workspace.db_path) as db:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                db.execute("DELETE FROM candidate_decisions")

    def test_second_decision_is_rejected(self) -> None:
        self.service.decide(
            candidate_id=self.candidate_id, decision="accept", note=None
        )
        with self.assertRaisesRegex(WorkspaceError, "already"):
            self.service.decide(
                candidate_id=self.candidate_id, decision="reject", note=None
            )

    def test_unknown_candidate_fails_closed(self) -> None:
        with self.assertRaisesRegex(WorkspaceError, "no review candidate"):
            self.service.decide(
                candidate_id="candidate-nope", decision="accept", note=None
            )

    def test_invalid_decision_fails_closed(self) -> None:
        with self.assertRaisesRegex(WorkspaceError, "decision"):
            self.service.decide(
                candidate_id=self.candidate_id, decision="maybe", note=None
            )

    def test_decision_never_touches_vault_or_items(self) -> None:
        before = (self.vault / "Origin.md").read_bytes()
        self.service.decide(
            candidate_id=self.candidate_id, decision="accept", note=None
        )
        self.assertEqual((self.vault / "Origin.md").read_bytes(), before)
        with sqlite3.connect(self.workspace.db_path) as db:
            items = db.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        self.assertEqual(items, 0)

    def test_list_shows_decision_state(self) -> None:
        self.service.decide(
            candidate_id=self.candidate_id, decision="accept", note="ok"
        )
        listed = self.service.list(source_id="vault")
        self.assertEqual(listed[0].review_state, "accepted")


if __name__ == "__main__":
    unittest.main()
