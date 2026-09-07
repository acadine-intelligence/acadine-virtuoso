"""Real-process regression tests for module evidence validation."""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from virtuoso.workspace import WorkspaceError, WorkspaceService


class ExternalSchedulerValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve() / "workspace"
        self.assertEqual(self.cli("init").returncode, 0)
        self.assertEqual(self.cli(
            "add", "--id", "synthetic", "--title", "Synthetic", "--focus", "testing",
            "--prompt", "Synthetic question?", "--answer", "Synthetic answer.",
        ).returncode, 0)
        self.module = self.root / "modules" / "controlled"
        self.module.mkdir(parents=True)
        self.module.parent.chmod(0o700)
        self.module.chmod(0o700)
        self.script = self.module / "scheduler.py"
        manifest = {
            "schema": "virtuoso/module@0.1", "id": "controlled", "version": "1.0.0",
            "category": "scheduler", "trust": "local-executable",
            "command": {"argv": [sys.executable, str(self.script)], "timeout_seconds": 2},
            "capabilities": {"reads": ["scheduler.request"], "returns": "scheduler-proposal"},
        }
        manifest_path = self.module / "virtuoso.module.json"
        manifest_path.write_text(json.dumps(manifest))
        manifest_path.chmod(0o600)
        self.config_path = self.root / "virtuoso.json"
        config = json.loads(self.config_path.read_text())
        config["scheduler"] = {
            "algorithm": "module:controlled", "context": "atomic-recall",
            "configuration": {"weight": 1, "flag": False},
        }
        self.config_path.write_text(json.dumps(config))
        self.database = self.root / ".virtuoso" / "state.sqlite3"
        self.write_module()

    def cli(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "virtuoso.cli", "--workspace", str(self.root), *arguments],
            text=True, capture_output=True, timeout=20,
        )

    def practice(self) -> subprocess.CompletedProcess[str]:
        return self.cli(
            "practice", "--item", "synthetic", "--administer", "--response", "Synthetic answer.",
            "--result", "demonstrated", "--confidence", "3", "--allow-trusted-scheduler", "--json",
        )

    def write_module(self, mutation: str = "") -> None:
        self.script.write_text(
            "import json, sys, time\n"
            "from datetime import datetime, timedelta, timezone\n"
            "from pathlib import Path\n"
            "started = datetime.now(timezone.utc).isoformat()\n"
            "request = json.load(sys.stdin)['projections']['scheduler.request']\n"
            "due = (datetime.fromisoformat(request['attempt']['occurred_at']) + timedelta(days=1)).isoformat()\n"
            "payload = {'algorithm': 'controlled', 'algorithm_version': '1.0.0', "
            "'learning_context': request['learning_context'], 'configuration': request['configuration'], "
            "'proposed_state': {'due': due, 'execution_started_at': started}, 'due_at': due, "
            "'rationale': 'Synthetic scheduling control.'}\n"
            + mutation + "\n"
            "payload['proposed_state']['execution_completed_at'] = datetime.now(timezone.utc).isoformat()\n"
            "print(json.dumps({'schema': 'virtuoso/module-result@0.1', 'module_id': 'controlled', "
            "'kind': 'scheduler-proposal', 'payload': payload}))\n"
        )

    def assert_rejected_without_write(self, message: str) -> None:
        before = self.database.read_bytes()
        result = self.practice()
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn(message, result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(self.database.read_bytes(), before)

    def test_returned_configuration_preserves_json_types(self) -> None:
        for mutation in (
            "payload['configuration']['weight'] = True",
            "payload['configuration']['flag'] = 0",
        ):
            with self.subTest(mutation=mutation):
                self.write_module(mutation)
                self.assert_rejected_without_write("configuration must match the request")

    def test_stored_configuration_type_change_is_rejected_before_execution(self) -> None:
        first = self.practice()
        self.assertEqual(first.returncode, 0, first.stderr)
        config = json.loads(self.config_path.read_text())
        config["scheduler"]["configuration"]["weight"] = True
        self.config_path.write_text(json.dumps(config))
        self.write_module("Path('unexpected-execution').touch()")
        self.assert_rejected_without_write("configuration")
        self.assertFalse((self.module / "unexpected-execution").exists())

    def test_configuration_type_change_during_execution_is_rejected(self) -> None:
        self.write_module(
            "config_path = Path(__file__).parents[2] / 'virtuoso.json'\n"
            "config = json.loads(config_path.read_text())\n"
            "config['scheduler']['configuration']['weight'] = True\n"
            "config_path.write_text(json.dumps(config))"
        )
        self.assert_rejected_without_write("configuration changed during practice")

    def test_algorithm_change_during_execution_is_rejected(self) -> None:
        self.write_module(
            "config_path = Path(__file__).parents[2] / 'virtuoso.json'\n"
            "config = json.loads(config_path.read_text())\n"
            "config['scheduler'] = {'algorithm': 'sm2', 'context': 'atomic-recall'}\n"
            "config_path.write_text(json.dumps(config))"
        )
        self.assert_rejected_without_write("scheduler changed during practice")

    def test_due_timezone_overflow_is_an_actionable_error(self) -> None:
        self.write_module(
            "payload['due_at'] = '0001-01-01T00:00:00+14:00'\n"
            "payload['proposed_state']['due'] = payload['due_at']"
        )
        self.assert_rejected_without_write("due_at is outside the supported UTC range")

    def test_state_due_timezone_overflow_is_an_actionable_error(self) -> None:
        self.write_module("payload['proposed_state']['due'] = '9999-12-31T23:59:59-14:00'")
        self.assert_rejected_without_write("proposed_state.due is outside the supported UTC range")

    def test_storage_timestamp_parser_rejects_timezone_overflow(self) -> None:
        for value in ("0001-01-01T00:00:00+14:00", "9999-12-31T23:59:59-14:00"):
            with self.subTest(value=value), self.assertRaises(WorkspaceError):
                WorkspaceService._parse_aware_datetime(value, label="scheduler due time")

    def test_receipt_timestamps_cover_actual_module_execution(self) -> None:
        self.write_module("time.sleep(0.05)")
        result = self.practice()
        self.assertEqual(result.returncode, 0, result.stderr)
        with sqlite3.connect(f"file:{self.database}?mode=ro", uri=True) as db:
            receipt = db.execute("SELECT started_at, completed_at FROM module_run_receipts").fetchone()
            state = json.loads(db.execute("SELECT state_json FROM scheduler_state").fetchone()[0])
        self.assertLessEqual(datetime.fromisoformat(receipt[0]), datetime.fromisoformat(state["execution_started_at"]))
        self.assertGreaterEqual(datetime.fromisoformat(receipt[1]), datetime.fromisoformat(state["execution_completed_at"]))


if __name__ == "__main__":
    unittest.main()
