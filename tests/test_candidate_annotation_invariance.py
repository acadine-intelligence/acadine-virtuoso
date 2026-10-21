from __future__ import annotations

import contextlib
import io
import json
import shutil
import sqlite3
import tempfile
import unittest
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

from virtuoso.annotations import AnnotationService
from virtuoso.candidates import CandidateService
from virtuoso.cli import main
from virtuoso.practice import PracticeService
from virtuoso.schedulers import builtin_algorithms
from virtuoso.workspace import WorkspaceService


_NOW = datetime(2030, 1, 1, 12, tzinfo=timezone.utc)


class _FixedDatetime(datetime):
    observed_at = _NOW

    @classmethod
    def now(cls, tz=None):
        return cls.observed_at.replace(tzinfo=None) if tz is None else cls.observed_at.astimezone(tz)


class CandidateAnnotationInvarianceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.workspace = WorkspaceService.init(self.root / "base")
        self.vault = self.root / "source"
        self.vault.mkdir()
        note = "---\nschema: virtuoso/curriculum@0.1\ntitle: Synthetic intake\n---\n"
        for decision in ("accept", "edit", "skip", "reject"):
            item = {
                "schema": "virtuoso/practice-item@0.1",
                "id": f"candidate-{decision}",
                "title": f"Candidate for {decision}",
                "focus": "testing",
                "prompt": "What makes repeated requests safe?",
                "answer": "An idempotent operation preserves its intended effect.",
                "hint": "Consider repeating an operation.",
                "follow_up": None,
                "state": "active",
                "historical_due_at": None,
            }
            note += "\n```virtuoso-practice\n" + json.dumps(item) + "\n```\n"
        (self.vault / "Intake.md").write_text(note, encoding="utf-8")
        self.workspace.add_source(source_id="synthetic", kind="markdown", root=self.vault)
        self.workspace.scan_source("synthetic")
        run = CandidateService(self.workspace).generate(
            source_id="synthetic", relative_path="Intake.md", adapter="curriculum"
        )
        self.candidate_ids = {
            candidate.proposal["item"]["item_id"].removeprefix("candidate-"): candidate.candidate_id
            for candidate in run.candidates
        }
        self.assertEqual(set(self.candidate_ids), {"accept", "edit", "skip", "reject"})
        self.workspace.add_item(
            item_id="existing-recall", title="Existing recall", focus="testing",
            prompt="What makes repeated requests safe?",
            answer="An idempotent operation preserves its intended effect.",
        )
        self.workspace.add_item(
            item_id="existing-study", title="Existing study", focus="study",
            prompt="Why separate a proposal from a decision?",
            answer="The learner owns the decision.", entry_mode="learn-first",
            learning_unit="A proposal records advice; a learner decision grants authority.",
        )
        for module_id in ("rule-annotator", "fixed-ladder"):
            source = Path(__file__).resolve().parents[1] / "examples/modules" / module_id
            target = self.workspace.root / "modules" / module_id
            target.parent.mkdir(exist_ok=True, mode=0o700)
            shutil.copytree(source, target)
            target.chmod(0o700)
            (target / "virtuoso.module.json").chmod(0o600)

    @staticmethod
    def _state(workspace: WorkspaceService) -> dict[str, list[tuple]]:
        """Compare all stored state except the two authorized annotation writes."""
        with sqlite3.connect(workspace.db_path) as db:
            tables = [row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
            )]
            return {
                table: sorted(db.execute('SELECT * FROM "' + table.replace('"', '""') + '"').fetchall(), key=repr)
                for table in tables
                if table not in {"annotations", "module_run_receipts"}
            }

    @staticmethod
    def _items(workspace: WorkspaceService) -> dict[str, bytes]:
        return {path.name: path.read_bytes() for path in workspace.items_dir.glob("*.md")}

    def _clone(self, name: str) -> WorkspaceService:
        target = self.root / name
        shutil.copytree(self.workspace.root, target)
        return WorkspaceService.open(target)

    def _annotate(self, workspace: WorkspaceService) -> None:
        for candidate_id in self.candidate_ids.values():
            record = AnnotationService(workspace).annotate_candidate(
                candidate_id=candidate_id, module_id="rule-annotator"
            )
            self.assertEqual(record["subject_kind"], "candidate")
            self.assertFalse(record["claims_mastery"])

    @contextlib.contextmanager
    def _clock(self, now: datetime):
        connect = WorkspaceService._connect

        def connect_at_fixed_time(workspace: WorkspaceService) -> sqlite3.Connection:
            db = connect(workspace)
            # SQLite's insertion timestamp is independent of Python's clock.
            # Fix it as well so complete rows remain comparable across clones.
            db.create_function("current_timestamp", 0, lambda: now.strftime("%Y-%m-%d %H:%M:%S"))
            return db

        with (
            patch.object(WorkspaceService, "_connect", connect_at_fixed_time),
            patch.object(_FixedDatetime, "observed_at", now),
            patch("virtuoso.cli.datetime", _FixedDatetime),
            patch("virtuoso.review.datetime", _FixedDatetime),
            patch("virtuoso.candidates.datetime", _FixedDatetime),
            patch("virtuoso.practice.datetime", _FixedDatetime),
        ):
            yield

    def _cli(self, workspace: WorkspaceService, *args: str, now: datetime = _NOW) -> dict:
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr), self._clock(now):
            code = main(["--workspace", str(workspace.root), *args, "--json"])
        self.assertEqual(code, 0, stderr.getvalue())
        return json.loads(stdout.getvalue())

    def test_annotation_creates_no_item_or_decision_or_learning_evidence(self) -> None:
        before = self._state(self.workspace)
        items_before = self._items(self.workspace)
        source_before = (self.vault / "Intake.md").read_bytes()

        self._annotate(self.workspace)

        self.assertEqual(self._state(self.workspace), before)
        self.assertEqual(self._items(self.workspace), items_before)
        self.assertEqual((self.vault / "Intake.md").read_bytes(), source_before)
        with sqlite3.connect(self.workspace.db_path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM annotations").fetchone()[0], 4)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM module_run_receipts").fetchone()[0], 4)

    def test_human_accept_edit_skip_and_reject_ignore_candidate_annotations(self) -> None:
        baseline = self._clone("decisions-without-annotations")
        annotated = self._clone("decisions-with-annotations")
        self._annotate(annotated)

        for decision, candidate_id in self.candidate_ids.items():
            with self.subTest(decision=decision):
                arguments = [
                    "candidate", "decide", "--id", candidate_id,
                    "--decision", decision, "--note", "The learner chose this action.",
                ]
                if decision == "edit":
                    arguments += [
                        "--focus", "human-choice", "--prompt", "Who owns this choice?",
                        "--answer", "The learner owns this choice.",
                    ]
                before = self._cli(baseline, *arguments)
                after = self._cli(annotated, *arguments)
                self.assertEqual(after, before)
                self.assertEqual(after["decision"], decision)
                self.assertEqual(self._state(annotated), self._state(baseline))
                self.assertEqual(self._items(annotated), self._items(baseline))

        self.assertEqual(set(self._items(annotated)), {
            "existing-recall.md", "existing-study.md", "candidate-accept.md", "candidate-edit.md",
        })
        edited = annotated.load_item("candidate-edit")
        self.assertEqual(edited.focus, "human-choice")
        self.assertEqual(edited.prompt, "Who owns this choice?")
        self.assertEqual(edited.answer, "The learner owns this choice.")
        with sqlite3.connect(annotated.db_path) as db:
            for table in ("attempts", "scheduler_state", "scheduler_proposals", "study_events"):
                self.assertEqual(db.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0], 0)

    def _views(self, workspace: WorkspaceService, now: datetime = _NOW) -> list[dict]:
        # Exercise both learn-first selection and recall selection, plus due ordering.
        return [self._cli(workspace, *arguments, now=now) for arguments in (
            ("next",), ("next", "--focus", "testing"),
            ("compose",), ("compose", "--focus", "testing"), ("review", "due"),
        )]

    def _attempt(self, workspace: WorkspaceService, index: int, now: datetime) -> dict:
        # Only nondeterministic identifiers and wall-clock reads are fixed. The
        # scheduler and external module run normally and persist their actual result.
        with (
            patch("virtuoso.practice.uuid.uuid4", side_effect=[UUID(int=index * 10 + n) for n in (1, 2)]),
            self._clock(now),
        ):
            result = PracticeService(workspace).run_administered(
                item_id="existing-recall", event_id=f"attempt-invariance-{index}",
                response="An idempotent operation can be repeated safely." if index < 2 else "",
                result=("demonstrated", "partial", "not-demonstrated")[index],
                confidence=4 - index, agent_help="none", now=now, allow_trusted=True,
            )
        self.assertIsNotNone(result.proposal)
        payload = asdict(result)
        receipt = payload["proposal"]["module_receipt"]
        if receipt is not None:
            self.assertGreaterEqual(receipt["duration_ms"], 0)
            # Actual subprocess duration is observation metadata, not a decision.
            del receipt["duration_ms"]
        return payload

    def test_next_compose_due_and_every_scheduler_ignore_candidate_annotations(self) -> None:
        for algorithm in (*builtin_algorithms(), "module:fixed-ladder"):
            with self.subTest(algorithm=algorithm):
                if self.workspace.scheduler_settings().algorithm != algorithm:
                    self.workspace.switch_scheduler(to_algorithm=algorithm, now=_NOW)
                name = algorithm.replace(":", "-")
                baseline = self._clone(f"{name}-without-annotations")
                annotated = self._clone(f"{name}-with-annotations")
                self._annotate(annotated)
                self.assertEqual(self._views(annotated), self._views(baseline))
                now = _NOW
                for index in range(3):
                    with self.subTest(algorithm=algorithm, attempt=index):
                        before = self._attempt(baseline, index, now)
                        after = self._attempt(annotated, index, now)
                        self.assertEqual(after, before)
                        self.assertEqual(after["proposal"]["algorithm"], algorithm)
                        now = before["proposal"]["due_at"]
                        self.assertEqual(self._views(annotated, now), self._views(baseline, now))
                        self.assertEqual(self._state(annotated), self._state(baseline))
                self.assertEqual(self._items(annotated), self._items(baseline))


if __name__ == "__main__":
    unittest.main()
