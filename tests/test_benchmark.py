"""Tests for issue 10: benchmark-directed focus."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from virtuoso.benchmark import BenchmarkError, BenchmarkService
from virtuoso.composition import CompositionError, SessionComposer
from virtuoso.practice import PracticeService
from virtuoso.workspace import WorkspaceService


def _artifact(
    *,
    run_id: str = "bench-001",
    criterion: str = "regression-test-writing",
    status: str = "fail",
    value: float = 0.4,
    **overrides: object,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema": "virtuoso/benchmark-run@0.1",
        "run_id": run_id,
        "source_reference": "ci://synthetic-project/nightly",
        "tested_commit": "0" * 40,
        "harness": "synthetic-harness",
        "harness_version": "1.0.0",
        "model_id": "synthetic-model",
        "prompt_hash": "a" * 64,
        "tool_permissions": ["read", "test"],
        "environment": "linux-ci",
        "operating_level_map_version": "opmap@1",
        "occurred_at": "2026-09-03T08:00:00+00:00",
        "observations": [
            {
                "criterion": criterion,
                "level": "execution",
                "status": status,
                "metric": "pass_rate",
                "value": value,
            }
        ],
    }
    payload.update(overrides)
    return payload


def _write_artifact(root: Path, payload: dict[str, object]) -> Path:
    path = root / f"{payload['run_id']}.json"
    path.write_text(json.dumps(payload))
    return path


class _RecordingIO:
    def __init__(self, answers: list[str]) -> None:
        self.answers = iter(answers)
        self.output: list[str] = []

    def write(self, text: str) -> None:
        self.output.append(text)

    def ask(self, prompt: str) -> str:
        return next(self.answers)


class _ZeroClock:
    def monotonic(self) -> float:
        return 0.0


class BenchmarkImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve() / "learner"
        self.workspace = WorkspaceService.init(self.root)
        self.service = BenchmarkService(self.workspace)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_import_stores_append_only_run(self) -> None:
        path = _write_artifact(Path(self.tmp.name), _artifact())
        run = self.service.import_run(path)
        self.assertEqual(run.run_id, "bench-001")
        self.assertEqual(run.observations[0]["criterion"], "regression-test-writing")
        with sqlite3.connect(self.workspace.db_path) as db:
            stored = db.execute(
                "SELECT payload_json FROM benchmark_runs WHERE run_id = ?",
                ("bench-001",),
            ).fetchone()
            self.assertIsNotNone(stored)
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute(
                    "UPDATE benchmark_runs SET payload_json = '{}' "
                    "WHERE run_id = ?",
                    ("bench-001",),
                )

    def test_malformed_json_fails_without_state_change(self) -> None:
        bad = Path(self.tmp.name) / "bad.json"
        bad.write_text("{not json")
        with self.assertRaises(BenchmarkError):
            self.service.import_run(bad)
        self._assert_no_runs()

    def test_unknown_level_fails_closed(self) -> None:
        artifact = _artifact(
            observations=[
                {
                    "criterion": "c",
                    "level": "astral-plane",
                    "status": "fail",
                    "metric": "m",
                    "value": 1,
                }
            ]
        )
        path = _write_artifact(Path(self.tmp.name), artifact)
        with self.assertRaises(BenchmarkError):
            self.service.import_run(path)
        self._assert_no_runs()

    def test_duplicate_run_id_fails(self) -> None:
        path = _write_artifact(Path(self.tmp.name), _artifact())
        self.service.import_run(path)
        with self.assertRaises(BenchmarkError):
            self.service.import_run(path)
        with sqlite3.connect(self.workspace.db_path) as db:
            count = db.execute(
                "SELECT COUNT(*) FROM benchmark_runs"
            ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_stale_source_hash_fails_for_same_reference(self) -> None:
        first = _write_artifact(Path(self.tmp.name), _artifact())
        self.service.import_run(first)
        changed = _artifact(run_id="bench-002")
        second = _write_artifact(Path(self.tmp.name), changed)
        with self.assertRaises(BenchmarkError):
            self.service.import_run(second)
        with sqlite3.connect(self.workspace.db_path) as db:
            ids = [
                row[0]
                for row in db.execute("SELECT run_id FROM benchmark_runs").fetchall()
            ]
        self.assertEqual(ids, ["bench-001"])

    def test_private_source_reference_fails(self) -> None:
        artifact = _artifact(source_reference="~/secret-project/ci")
        path = _write_artifact(Path(self.tmp.name), artifact)
        with self.assertRaises(BenchmarkError):
            self.service.import_run(path)
        self._assert_no_runs()

    def _assert_no_runs(self) -> None:
        with sqlite3.connect(self.workspace.db_path) as db:
            count = db.execute("SELECT COUNT(*) FROM benchmark_runs").fetchone()[0]
        self.assertEqual(count, 0)


class BenchmarkProposeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve() / "learner"
        self.workspace = WorkspaceService.init(self.root)
        self.benchmarks = BenchmarkService(self.workspace)
        self.composer = SessionComposer(self.workspace)
        self.now = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _add_item(self, item_id: str = "regression-drill") -> None:
        self.workspace.add_item(
            item_id=item_id,
            title="Regression drill",
            focus="regression-test-writing",
            prompt="Write one regression test for a failing criterion.",
            answer="A test that reproduces the failure first.",
        )

    def test_failed_criterion_proposes_benchmark_focus(self) -> None:
        self._add_item()
        path = _write_artifact(Path(self.tmp.name), _artifact())
        self.benchmarks.import_run(path)

        proposal = self.composer.compose(now=self.now)
        self.assertEqual(proposal.primary_item_id, "regression-drill")
        benchmark = proposal.payload["benchmark"]
        self.assertEqual(benchmark["run_id"], "bench-001")
        self.assertEqual(benchmark["failed_criterion"], "regression-test-writing")
        self.assertEqual(benchmark["operating_level"], "execution")
        self.assertIn("rerun_condition", benchmark)
        self.assertIn("benchmark:bench-001", proposal.payload["source_event_ids"])
        self.assertTrue(proposal.payload["rationale"])

    def test_propose_is_deterministic_for_ties(self) -> None:
        artifact = _artifact(
            observations=[
                {
                    "criterion": "zeta",
                    "level": "execution",
                    "status": "fail",
                    "metric": "pass_rate",
                    "value": 0.2,
                },
                {
                    "criterion": "alpha",
                    "level": "execution",
                    "status": "fail",
                    "metric": "pass_rate",
                    "value": 0.1,
                },
            ]
        )
        path = _write_artifact(Path(self.tmp.name), artifact)

        picks: list[str] = []
        for item_id in ("item-a", "item-b"):
            workspace = WorkspaceService.init(
                Path(self.tmp.name).resolve() / f"learner-{item_id}"
            )
            workspace.add_item(
                item_id=item_id,
                title=f"Drill {item_id}",
                focus="alpha",
                prompt="Prompt",
                answer="Answer",
            )
            BenchmarkService(workspace).import_run(path)
            proposal = SessionComposer(workspace).compose(now=self.now)
            picks.append(proposal.payload["benchmark"]["failed_criterion"])

        self.assertEqual(picks, ["alpha", "alpha"])

    def test_missing_compatible_item_fails_with_guidance(self) -> None:
        path = _write_artifact(Path(self.tmp.name), _artifact())
        self.benchmarks.import_run(path)
        with self.assertRaises(CompositionError) as caught:
            self.composer.compose(now=self.now)
        self.assertIn("regression-test-writing", str(caught.exception))

    def test_simple_mode_selection_without_benchmark(self) -> None:
        self._add_item()
        proposal = self.composer.compose(now=self.now)
        self.assertNotIn("benchmark", proposal.payload)
        self.assertIn(
            "deterministic item-id order", proposal.payload["rationale"]
        )

    def test_decision_appends_without_scheduler_change(self) -> None:
        self._add_item()
        path = _write_artifact(Path(self.tmp.name), _artifact())
        self.benchmarks.import_run(path)
        proposal = self.composer.compose(now=self.now)
        with sqlite3.connect(self.workspace.db_path) as db:
            proposals_before = db.execute(
                "SELECT COUNT(*) FROM scheduler_proposals"
            ).fetchone()[0]
        decision = self.composer.decide(
            proposal_id=proposal.proposal_id,
            decision="accept",
            now=self.now,
            surface="test",
        )
        self.assertEqual(decision.decision, "accept")
        with sqlite3.connect(self.workspace.db_path) as db:
            proposals_after = db.execute(
                "SELECT COUNT(*) FROM scheduler_proposals"
            ).fetchone()[0]
        self.assertEqual(proposals_before, proposals_after)


class BenchmarkRerunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve() / "learner"
        self.workspace = WorkspaceService.init(self.root)
        self.service = BenchmarkService(self.workspace)
        baseline_path = _write_artifact(Path(self.tmp.name), _artifact())
        self.service.import_run(baseline_path)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_compatible_rerun_reports_metric_change(self) -> None:
        rerun = _artifact(run_id="bench-002", value=0.9, status="pass")
        path = _write_artifact(Path(self.tmp.name), rerun)
        report = self.service.import_rerun(path, baseline_run_id="bench-001")
        self.assertEqual(report["baseline_run_id"], "bench-001")
        self.assertEqual(report["changes"][0]["delta"], 0.5)
        self.assertEqual(report["warnings"], [])
        with sqlite3.connect(self.workspace.db_path) as db:
            link = db.execute(
                "SELECT baseline_run_id FROM benchmark_reruns WHERE run_id = ?",
                ("bench-002",),
            ).fetchone()
            runs = db.execute("SELECT COUNT(*) FROM benchmark_runs").fetchone()[0]
        self.assertEqual(link, ("bench-001",))
        self.assertEqual(runs, 2)

    def test_changed_fields_produce_specific_warnings(self) -> None:
        rerun = _artifact(
            run_id="bench-002",
            tested_commit="1" * 40,
            harness="other-harness",
            harness_version="2.0.0",
            model_id="other-model",
            prompt_hash="b" * 64,
            tool_permissions=["read", "write", "test"],
            environment="macos-local",
            value=0.8,
        )
        path = _write_artifact(Path(self.tmp.name), rerun)
        report = self.service.import_rerun(path, baseline_run_id="bench-001")
        warnings = " ".join(report["warnings"])
        for field in (
            "tested_commit",
            "harness",
            "harness_version",
            "model_id",
            "prompt_hash",
            "tool_permissions",
            "environment",
        ):
            self.assertIn(field, warnings)

    def test_missing_metric_reports_metric_missing(self) -> None:
        rerun = _artifact(
            run_id="bench-002",
            observations=[
                {
                    "criterion": "regression-test-writing",
                    "level": "execution",
                    "status": "fail",
                    "metric": "different_metric",
                    "value": 0.5,
                }
            ],
        )
        path = _write_artifact(Path(self.tmp.name), rerun)
        report = self.service.import_rerun(path, baseline_run_id="bench-001")
        self.assertEqual(report["changes"][0]["comparison"], "metric-missing")

    def test_rerun_never_promotes_capability_or_mastery(self) -> None:
        rerun = _artifact(run_id="bench-002", value=1.0, status="pass")
        path = _write_artifact(Path(self.tmp.name), rerun)
        self.service.import_rerun(path, baseline_run_id="bench-001")
        with sqlite3.connect(self.workspace.db_path) as db:
            attempts = db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0]
            study = db.execute("SELECT COUNT(*) FROM study_events").fetchone()[0]
            tables = {
                row[0]
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
        self.assertEqual(attempts, 0)
        self.assertEqual(study, 0)
        self.assertNotIn("capability_events", tables)


class BenchmarkExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve() / "learner"
        self.workspace = WorkspaceService.init(self.root)
        self.service = BenchmarkService(self.workspace)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_export_emits_normalized_fields_only(self) -> None:
        path = _write_artifact(Path(self.tmp.name), _artifact())
        self.service.import_run(path)
        exported = self.service.export("bench-001")
        self.assertTrue(exported["redacted"])
        self.assertEqual(exported["run_id"], "bench-001")
        self.assertNotIn("raw_artifact", exported)
        self.assertNotIn("learner_content", exported)
        self.assertEqual(
            set(exported["observations"][0]),
            {"criterion", "level", "status", "metric", "value"},
        )


if __name__ == "__main__":
    unittest.main()
