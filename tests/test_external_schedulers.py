from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from virtuoso.practice import PracticeError, PracticeService
from virtuoso.queries import workload_by_focus
from virtuoso.review import ReviewService
from virtuoso.workspace import WorkspaceService


class ExternalSchedulerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve() / "learner"
        self.workspace = WorkspaceService.init(self.root)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _configure_module(self, module_id: str = "fixed-ladder") -> Path:
        module_dir = self.root / "modules" / module_id
        module_dir.mkdir(parents=True, mode=0o700)
        module_dir.parent.chmod(0o700)
        script = module_dir / "scheduler.py"
        script.write_text("raise SystemExit('selection must not execute the module')\n")
        script.chmod(0o700)
        manifest = module_dir / "virtuoso.module.json"
        manifest.write_text(
            json.dumps(
                {
                    "schema": "virtuoso/module@0.1",
                    "id": module_id,
                    "version": "1.0.0",
                    "category": "scheduler",
                    "command": {
                        "argv": [sys.executable, str(script)],
                        "timeout_seconds": 2,
                    },
                    "capabilities": {
                        "reads": ["scheduler.request"],
                        "returns": "scheduler-proposal",
                    },
                    "trust": "local-executable",
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        manifest.chmod(0o600)
        config = self.workspace.configuration()
        config["scheduler"] = {
            "algorithm": f"module:{module_id}",
            "context": "atomic-recall",
            "configuration": {"intervals": [1, 3, 7, 14, 30]},
        }
        self.workspace.config_path.write_text(
            json.dumps(config, indent=2, sort_keys=True) + "\n"
        )
        self.workspace.config_path.chmod(0o600)
        return manifest

    def test_module_scheduler_selection_validates_manifest_without_execution(self) -> None:
        self._configure_module()

        settings = self.workspace.scheduler_settings()

        self.assertEqual(settings.algorithm, "module:fixed-ladder")
        self.assertEqual(settings.algorithm_version, "1.0.0")
        self.assertEqual(settings.learning_context, "atomic-recall")
        self.assertEqual(
            settings.configuration, {"intervals": [1, 3, 7, 14, 30]}
        )
        self._add_item()
        self.assertEqual(
            self.workspace.select_next(datetime.now(timezone.utc)).item.item_id,
            "testing-effect",
        )
        self.assertEqual(len(ReviewService(self.workspace).due()), 1)
        self.assertIn(self.workspace.doctor()["status"], {"healthy", "needs-attention"})

    def _install_example(self) -> None:
        source = Path(__file__).resolve().parents[1] / "examples/modules/fixed-ladder"
        target = self.root / "modules/fixed-ladder"
        target.parent.mkdir(mode=0o700)
        shutil.copytree(source, target)
        target.chmod(0o700)
        (target / "virtuoso.module.json").chmod(0o600)
        config = self.workspace.configuration()
        config["scheduler"] = {
            "algorithm": "module:fixed-ladder",
            "context": "atomic-recall",
            "configuration": {"intervals": [1, 3, 7, 14, 30]},
        }
        self.workspace.config_path.write_text(
            json.dumps(config, indent=2, sort_keys=True) + "\n"
        )
        self.workspace.config_path.chmod(0o600)

    def _add_item(self) -> None:
        self.workspace.add_item(
            item_id="testing-effect",
            title="Explain the testing effect",
            focus="learning-science",
            prompt="Why does retrieval improve recall?",
            answer="Retrieval strengthens later access.",
        )

    def test_api_requires_per_run_opt_in_without_database_mutation(self) -> None:
        self._install_example()
        self._add_item()
        before = self.workspace.db_path.read_bytes()

        with self.assertRaisesRegex(PracticeError, "allow-trusted-scheduler"):
            PracticeService(self.workspace).run_administered(
                item_id="testing-effect",
                response="retrieval strengthens access",
                result="demonstrated",
                confidence=4,
            )

        self.assertEqual(self.workspace.db_path.read_bytes(), before)
        self.assertEqual(self.workspace.list_attempts(), [])
        self.assertEqual(self.workspace.list_proposals(), [])
        self.assertEqual(self.workspace.list_module_receipts(), [])

    def test_actual_example_progresses_and_is_queryable(self) -> None:
        self._install_example()
        self._add_item()

        first = PracticeService(self.workspace).run_administered(
            item_id="testing-effect",
            response="retrieval strengthens access",
            result="demonstrated",
            confidence=4,
            allow_trusted=True,
        )
        second = PracticeService(self.workspace).run_administered(
            item_id="testing-effect",
            response="retrieval strengthens access",
            result="demonstrated",
            confidence=4,
            now=first.proposal.due_at,
            allow_trusted=True,
        )
        failed = PracticeService(self.workspace).run_administered(
            item_id="testing-effect",
            response="",
            result="not-demonstrated",
            confidence=2,
            now=second.proposal.due_at,
            allow_trusted=True,
        )

        self.assertEqual(first.proposal.algorithm, "module:fixed-ladder")
        self.assertEqual(
            (first.proposal.due_at - first.attempt.occurred_at).days, 1
        )
        self.assertEqual(
            (second.proposal.due_at - second.attempt.occurred_at).days, 3
        )
        state = json.loads(second.proposal.proposed_state_json)
        self.assertEqual(state["step"], 1)
        self.assertEqual(
            (failed.proposal.due_at - failed.attempt.occurred_at).days, 1
        )
        self.assertEqual(json.loads(failed.proposal.proposed_state_json)["step"], 0)
        due = ReviewService(self.workspace).due(now=failed.proposal.due_at)
        self.assertEqual([entry.item_id for entry in due], ["testing-effect"])
        workload = workload_by_focus(
            self.workspace.db_path,
            now=failed.proposal.due_at,
            algorithm="module:fixed-ladder",
            learning_context="atomic-recall",
        )
        self.assertEqual(workload[0]["due_now"], 1)
        self.assertEqual(len(self.workspace.list_attempts()), 3)
        self.assertEqual(len(self.workspace.list_module_receipts()), 3)

    def _write_result_script(self, payload_source: str) -> None:
        script = self.root / "modules/fixed-ladder/scheduler.py"
        script.write_text(
            "import json, sys\n"
            "request = json.load(sys.stdin)\n"
            "projection = request['projections']['scheduler.request']\n"
            + payload_source
        )
        script.chmod(0o700)

    def test_rejected_results_and_process_failures_leave_database_bytes_unchanged(self) -> None:
        self._configure_module()
        self._add_item()
        future = "2099-01-02T00:00:00+00:00"
        base = {
            "due_at": future,
            "algorithm": "fixed-ladder",
            "algorithm_version": "1.0.0",
            "learning_context": "atomic-recall",
            "configuration": {"intervals": [1, 3, 7, 14, 30]},
            "proposed_state": {"step": 0, "due": future},
            "rationale": "synthetic rejection fixture",
        }
        variants: list[tuple[str, str, str]] = []
        malformed = dict(base)
        malformed.pop("proposed_state")
        variants.append(("missing-state", repr(malformed), "proposed_state"))
        malformed = dict(base, proposed_state=[])
        variants.append(("malformed-state", repr(malformed), "proposed_state"))
        for name, field, value, message in (
            ("algorithm", "algorithm", "sm2", "algorithm"),
            ("version", "algorithm_version", "9.9.9", "algorithm_version"),
            ("context", "learning_context", "other", "learning_context"),
            ("configuration", "configuration", {}, "configuration"),
        ):
            variants.append((name, repr(dict(base, **{field: value})), message))
        variants.extend(
            [
                (
                    "naive-due",
                    repr(
                        dict(
                            base,
                            due_at="2099-01-02T00:00:00",
                            proposed_state={"step": 0, "due": "2099-01-02T00:00:00"},
                        )
                    ),
                    "timezone-aware",
                ),
                (
                    "past-due",
                    repr(
                        dict(
                            base,
                            due_at="2000-01-01T00:00:00+00:00",
                            proposed_state={"step": 0, "due": "2000-01-01T00:00:00+00:00"},
                        )
                    ),
                    "precede",
                ),
                (
                    "due-mismatch",
                    repr(
                        dict(
                            base,
                            proposed_state={"step": 0, "due": "2099-01-03T00:00:00+00:00"},
                        )
                    ),
                    "match due_at",
                ),
                ("empty-rationale", repr(dict(base, rationale="  ")), "nonempty"),
            ]
        )
        for name, payload, message in variants:
            with self.subTest(name=name):
                self._write_result_script(
                    "print(json.dumps({'schema':'virtuoso/module-result@0.1',"
                    "'module_id':'fixed-ladder','kind':'scheduler-proposal',"
                    f"'payload':{payload}}}))\n"
                )
                before = self.workspace.db_path.read_bytes()
                with self.assertRaisesRegex(PracticeError, message):
                    PracticeService(self.workspace).run_administered(
                        item_id="testing-effect",
                        response="synthetic response",
                        result="partial",
                        confidence=3,
                        allow_trusted=True,
                    )
                self.assertEqual(self.workspace.db_path.read_bytes(), before)

        process_sources = (
            ("failed-process", "raise SystemExit(3)\n", "status 3"),
            (
                "nonfinite",
                "print('{\"schema\":\"virtuoso/module-result@0.1\",\"module_id\":\"fixed-ladder\",\"kind\":\"scheduler-proposal\",\"payload\":{\"due_at\":\"2099-01-02T00:00:00+00:00\",\"algorithm\":\"fixed-ladder\",\"algorithm_version\":\"1.0.0\",\"learning_context\":\"atomic-recall\",\"configuration\":{\"intervals\":[1,3,7,14,30]},\"proposed_state\":{\"step\":NaN,\"due\":\"2099-01-02T00:00:00+00:00\"},\"rationale\":\"bad\"}}')\n",
                "finite JSON",
            ),
            ("timeout", "import time\ntime.sleep(3)\n", "timed out"),
        )
        for name, source, message in process_sources:
            with self.subTest(name=name):
                (self.root / "modules/fixed-ladder/scheduler.py").write_text(source)
                before = self.workspace.db_path.read_bytes()
                with self.assertRaisesRegex(PracticeError, message):
                    PracticeService(self.workspace).run_administered(
                        item_id="testing-effect",
                        response="synthetic response",
                        result="partial",
                        confidence=3,
                        allow_trusted=True,
                    )
                self.assertEqual(self.workspace.db_path.read_bytes(), before)
        self.assertEqual(self.workspace.list_module_receipts(), [])

    def test_manifest_or_configuration_change_before_persistence_is_atomic(self) -> None:
        self._install_example()
        self._add_item()
        original_record = self.workspace.record_attempt

        def change_configuration(**kwargs: object) -> None:
            config = self.workspace.configuration()
            config["scheduler"]["configuration"] = {"intervals": [2, 4]}
            self.workspace.config_path.write_text(json.dumps(config))
            original_record(**kwargs)

        before = self.workspace.db_path.read_bytes()
        with patch.object(self.workspace, "record_attempt", side_effect=change_configuration):
            with self.assertRaisesRegex(PracticeError, "configuration changed"):
                PracticeService(self.workspace).run_administered(
                    item_id="testing-effect",
                    response="synthetic response",
                    result="partial",
                    confidence=3,
                    allow_trusted=True,
                )
        self.assertEqual(self.workspace.db_path.read_bytes(), before)
        self.assertEqual(self.workspace.list_module_receipts(), [])

        config = self.workspace.configuration()
        config["scheduler"]["configuration"] = {"intervals": [1, 3, 7, 14, 30]}
        self.workspace.config_path.write_text(json.dumps(config))
        manifest_path = self.root / "modules/fixed-ladder/virtuoso.module.json"

        def change_manifest(**kwargs: object) -> None:
            manifest_path.write_text(manifest_path.read_text() + "\n")
            original_record(**kwargs)

        before = self.workspace.db_path.read_bytes()
        with patch.object(self.workspace, "record_attempt", side_effect=change_manifest):
            with self.assertRaisesRegex(PracticeError, "manifest changed"):
                PracticeService(self.workspace).run_administered(
                    item_id="testing-effect",
                    response="synthetic response",
                    result="partial",
                    confidence=3,
                    allow_trusted=True,
                )
        self.assertEqual(self.workspace.db_path.read_bytes(), before)
        self.assertEqual(self.workspace.list_module_receipts(), [])

    def test_switching_to_and_from_module_keeps_state_isolated(self) -> None:
        self._install_example()
        self._add_item()
        module_result = PracticeService(self.workspace).run_administered(
            item_id="testing-effect",
            response="synthetic response",
            result="partial",
            confidence=3,
            allow_trusted=True,
        )
        self.workspace.switch_scheduler(to_algorithm="sm2")
        built_in_result = PracticeService(self.workspace).run_administered(
            item_id="testing-effect",
            response="synthetic response",
            result="partial",
            confidence=3,
            now=module_result.proposal.due_at,
        )
        self.workspace.switch_scheduler(to_algorithm="module:fixed-ladder")
        resumed = PracticeService(self.workspace).run_administered(
            item_id="testing-effect",
            response="synthetic response",
            result="partial",
            confidence=3,
            now=built_in_result.proposal.due_at,
            allow_trusted=True,
        )

        self.assertEqual(built_in_result.proposal.algorithm, "sm2")
        self.assertEqual(
            json.loads(resumed.proposal.previous_state_json or "null"),
            json.loads(module_result.proposal.proposed_state_json),
        )
        with sqlite3.connect(self.workspace.db_path) as db:
            algorithms = {
                row[0] for row in db.execute("SELECT algorithm FROM scheduler_state")
            }
        self.assertEqual(algorithms, {"module:fixed-ladder", "sm2"})

    def test_stored_module_version_and_configuration_mismatches_are_actionable(self) -> None:
        self._install_example()
        self._add_item()
        PracticeService(self.workspace).run_administered(
            item_id="testing-effect",
            response="synthetic response",
            result="partial",
            confidence=3,
            allow_trusted=True,
        )
        before = self.workspace.db_path.read_bytes()
        config = self.workspace.configuration()
        config["scheduler"]["configuration"] = {"intervals": [2, 4, 8]}
        self.workspace.config_path.write_text(json.dumps(config))
        with self.assertRaisesRegex(PracticeError, "incompatible scheduler configuration"):
            PracticeService(self.workspace).run_administered(
                item_id="testing-effect",
                response="synthetic response",
                result="partial",
                confidence=3,
                allow_trusted=True,
            )
        self.assertEqual(self.workspace.db_path.read_bytes(), before)

        config["scheduler"]["configuration"] = {"intervals": [1, 3, 7, 14, 30]}
        self.workspace.config_path.write_text(json.dumps(config))
        manifest_path = self.root / "modules/fixed-ladder/virtuoso.module.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["version"] = "1.0.1"
        manifest_path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(PracticeError, "incompatible algorithm version"):
            PracticeService(self.workspace).run_administered(
                item_id="testing-effect",
                response="synthetic response",
                result="partial",
                confidence=3,
                allow_trusted=True,
            )
        self.assertEqual(self.workspace.db_path.read_bytes(), before)

    def test_cli_administer_passes_explicit_authorization(self) -> None:
        self._install_example()
        self._add_item()
        command = [
            sys.executable,
            "-m",
            "virtuoso.cli",
            "--workspace",
            str(self.root),
            "practice",
            "--item",
            "testing-effect",
            "--administer",
            "--response",
            "synthetic response",
            "--result",
            "partial",
            "--confidence",
            "3",
            "--allow-trusted-scheduler",
            "--json",
        ]

        completed = subprocess.run(command, text=True, capture_output=True, check=False)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            json.loads(completed.stdout)["proposal_algorithm"],
            "module:fixed-ladder",
        )

        request = {
            "schema": "virtuoso/review-attempt@0.1",
            "submission_id": "0123456789abcdef0123456789abcdef",
            "item_id": "testing-effect",
            "item_content_hash": self.workspace.load_item("testing-effect").content_hash,
            "started_at": "2099-01-01T00:00:00+00:00",
            "initial_answered_at": "2099-01-01T00:00:01+00:00",
            "completed_at": "2099-01-01T00:00:02+00:00",
            "initial_response": "synthetic response",
            "retry": None,
            "hint_used": False,
            "answer_revealed": True,
            "result": "partial",
            "confidence": 3,
            "open_notes": False,
        }
        review_command = [
            sys.executable, "-m", "virtuoso.cli", "--workspace", str(self.root),
            "review", "record", "--allow-trusted-scheduler", "--json",
        ]
        recorded = subprocess.run(
            review_command,
            input=json.dumps(request),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(recorded.returncode, 0, recorded.stderr)
        self.assertEqual(
            json.loads(recorded.stdout)["proposal"]["algorithm"],
            "module:fixed-ladder",
        )


if __name__ == "__main__":
    unittest.main()
