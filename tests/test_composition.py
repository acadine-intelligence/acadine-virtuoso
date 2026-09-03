from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from virtuoso.composition import CompositionError, SessionComposer
from virtuoso.practice import PracticeService
from virtuoso.workspace import WorkspaceService


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


class SessionCompositionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve() / "learner"
        self.workspace = WorkspaceService.init(self.root)
        self.now = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
        self.composer = SessionComposer(self.workspace)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _add(self, item_id: str, focus: str = "ml", **kwargs: str) -> None:
        defaults = {
            "title": f"Title {item_id}",
            "prompt": f"Prompt {item_id}?",
            "answer": f"Answer {item_id}.",
        }
        defaults.update(kwargs)
        self.workspace.add_item(item_id=item_id, focus=focus, **defaults)

    def _practice(self, item_id: str, result: str, confidence: int) -> None:
        PracticeService(self.workspace, clock=_ZeroClock()).run(
            item_id=item_id,
            io=_IO(["n", "a real answer", "reveal", result, str(confidence)]),
            now=self.now,
        )

    def test_gap_attempt_targets_gap_and_cites_source_events(self) -> None:
        self._add("item-a")
        self._add("item-b")
        self._practice("item-a", "partial", 3)

        proposal = self.composer.compose(now=self.now)

        self.assertEqual(proposal.primary_item_id, "item-a")
        self.assertEqual(proposal.action, "practice")
        self.assertTrue(proposal.source_event_ids)
        self.assertIn("partial", proposal.rationale.lower())
        self.assertIn("gap", proposal.rationale.lower())
        payload = proposal.to_dict()
        self.assertEqual(payload["schema"], "virtuoso/focus-proposal@0.1")
        self.assertIn("item_content_hash", payload["primary"])
        self.assertNotIn("answer", json.dumps(payload["primary"]).lower())

    def test_determinism_same_snapshot_clock_request(self) -> None:
        self._add("item-a")
        self._add("item-b")
        self._practice("item-a", "partial", 3)

        first = self.composer.compose(now=self.now)
        second = self.composer.compose(now=self.now)

        self.assertEqual(first.to_dict(), second.to_dict())

    def test_fallback_missing_evidence_uses_next_selection(self) -> None:
        self._add("item-b")
        self._add("item-a")

        proposal = self.composer.compose(now=self.now)
        next_selection = self.workspace.select_next(self.now)

        self.assertEqual(proposal.primary_item_id, next_selection.item.item_id)
        self.assertIsNotNone(proposal.uncertainty)

    def test_demonstrated_skipped_with_traceable_reason(self) -> None:
        self._add("item-a")
        self._add("item-b")
        self._practice("item-a", "demonstrated", 5)
        self._practice("item-b", "partial", 3)

        for _ in range(5):
            proposal = self.composer.compose(now=self.now)
            self.assertEqual(proposal.primary_item_id, "item-b")
            skipped = {entry["item_id"]: entry for entry in proposal.skipped}
            self.assertIn("item-a", skipped)
            self.assertTrue(skipped["item-a"]["reason"])
            self.assertTrue(skipped["item-a"]["source_event_ids"])

    def test_decide_accept_records_one_decision_no_other_evidence(self) -> None:
        self._add("item-a")
        proposal = self.composer.compose(now=self.now)

        decision = self.composer.decide(
            proposal_id=proposal.proposal_id,
            decision="accept",
            now=self.now,
            surface="cli",
        )

        self.assertEqual(decision.decision, "accept")
        self.assertEqual(decision.chosen_item_id, "item-a")
        self.assertEqual(self._table_count("composition_decisions"), 1)
        for table in ("attempts", "scheduler_proposals", "scheduler_state", "transfer_events", "review_skips"):
            self.assertEqual(self._table_count(table), 0, table)

    def test_decide_second_fails(self) -> None:
        self._add("item-a")
        proposal = self.composer.compose(now=self.now)
        self.composer.decide(proposal_id=proposal.proposal_id, decision="accept", now=self.now, surface="cli")

        with self.assertRaisesRegex(CompositionError, "already decided"):
            self.composer.decide(proposal_id=proposal.proposal_id, decision="reject", now=self.now, surface="cli")

    def test_decide_change_requires_same_focus_active_item(self) -> None:
        self._add("item-a", focus="ml")
        self._add("item-b", focus="go")
        proposal = self.composer.compose(now=self.now, focus="ml")

        with self.assertRaisesRegex(CompositionError, "focus"):
            self.composer.decide(
                proposal_id=proposal.proposal_id,
                decision="change",
                chosen_item_id="item-b",
                now=self.now,
                surface="cli",
            )
        self.assertEqual(self._table_count("composition_decisions"), 0)

    def test_decide_stale_content_fails_closed(self) -> None:
        self._add("item-a")
        proposal = self.composer.compose(now=self.now)
        import sqlite3

        with sqlite3.connect(self.workspace.db_path) as db:
            db.execute("UPDATE items SET content_hash = 'x' WHERE item_id = 'item-a'")

        with self.assertRaisesRegex(CompositionError, "stale"):
            self.composer.decide(proposal_id=proposal.proposal_id, decision="accept", now=self.now, surface="cli")
        self.assertEqual(self._table_count("composition_decisions"), 0)

    def test_learn_first_pending_produces_learn_proposal(self) -> None:
        self.workspace.add_item(
            item_id="learn-item",
            title="Learn item",
            focus="ml",
            prompt="Recall something?",
            answer="An answer.",
            entry_mode="learn-first",
            learning_unit="A learning unit.",
        )

        proposal = self.composer.compose(now=self.now)

        self.assertEqual(proposal.action, "learn")
        self.assertIsNone(proposal.to_dict()["primary"]["prompt"])

    def test_never_exposes_answer_or_capability(self) -> None:
        self._add("item-a", answer="SECRET-ANSWER-MARKER", hint="SECRET-HINT-MARKER")
        proposal = self.composer.compose(now=self.now)

        payload = json.dumps(proposal.to_dict())
        self.assertNotIn("SECRET-ANSWER-MARKER", payload)
        self.assertNotIn("SECRET-HINT-MARKER", payload)
        self.assertNotIn("mastery", payload.lower())
        self.assertNotIn("capability", payload.lower())

    def _table_count(self, table: str) -> int:
        import sqlite3

        with sqlite3.connect(self.workspace.db_path) as db:
            return int(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


if __name__ == "__main__":
    unittest.main()
