from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

from virtuoso.modules import ModuleError, ModuleManifest, ModuleRunner
from virtuoso.workspace import WorkspaceService


class ModuleBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _script(self, name: str, source: str) -> Path:
        path = self.root / name
        path.write_text(source)
        return path

    def _manifest(self, script: Path, **overrides: object) -> Path:
        value: dict[str, object] = {
            "schema": "virtuoso/module@0.1",
            "id": "example-score",
            "version": "0.1.0",
            "category": "scoring-signal",
            "command": {
                "argv": [sys.executable, str(script)],
                "timeout_seconds": 2,
            },
            "capabilities": {
                "reads": ["challenge.summary"],
                "returns": "score-proposal",
            },
            "trust": "local-executable",
        }
        value.update(overrides)
        path = self.root / "virtuoso.module.json"
        path.write_text(json.dumps(value))
        return path

    def _run(self, manifest: ModuleManifest, request: dict[str, object], **kwargs: object):
        return ModuleRunner(**kwargs).run(manifest, request, allow_trusted=True)

    def test_valid_external_command_receives_bounded_projection_and_returns_typed_result(self) -> None:
        script = self._script(
            "valid.py",
            """import json, sys
request = json.load(sys.stdin)
print(json.dumps({
    'schema': 'virtuoso/module-result@0.1',
    'module_id': 'example-score',
    'kind': 'score-proposal',
    'payload': {'score': len(request['projections']['challenge.summary']['title'])}
}))
""",
        )
        manifest = ModuleManifest.load(self._manifest(script))
        result = self._run(
            manifest,
            {
                "schema": "virtuoso/module-request@0.1",
                "projections": {"challenge.summary": {"title": "Testing effect"}},
            },
            max_output_bytes=4096,
        )

        self.assertEqual(result.module_id, "example-score")
        self.assertEqual(result.kind, "score-proposal")
        self.assertEqual(result.payload, {"score": 14})
        self.assertEqual(len(result.stdout_sha256), 64)
        self.assertGreaterEqual(result.duration_ms, 0)

        workspace = WorkspaceService.init(self.root / "learner")
        receipt = workspace.record_module_receipt(manifest=manifest, result=result)
        self.assertEqual(receipt["module_id"], "example-score")
        self.assertEqual(receipt["kind"], "score-proposal")
        self.assertEqual(len(workspace.list_module_receipts()), 1)

    def test_unknown_manifest_schema_fails_closed(self) -> None:
        script = self._script("unused.py", "")
        path = self._manifest(script, schema="virtuoso/module@9.9")
        with self.assertRaisesRegex(ModuleError, "unsupported module schema"):
            ModuleManifest.load(path)

    def test_unknown_manifest_fields_and_unapproved_execution_fail_closed(self) -> None:
        script = self._script("unused.py", "")
        path = self._manifest(script, shell="python unused.py")
        with self.assertRaisesRegex(ModuleError, "unknown manifest fields"):
            ModuleManifest.load(path)

        manifest = ModuleManifest.load(self._manifest(script))
        with self.assertRaisesRegex(ModuleError, "trusted local executable"):
            ModuleRunner().run(
                manifest,
                {"schema": "virtuoso/module-request@0.1", "projections": {}},
            )

    def test_shell_command_and_direct_write_capability_are_rejected(self) -> None:
        script = self._script("unused.py", "")
        shell_path = self._manifest(
            script,
            command={"shell": "python unused.py", "timeout_seconds": 2},
        )
        with self.assertRaisesRegex(ModuleError, "unknown command fields|argv"):
            ModuleManifest.load(shell_path)

        write_path = self._manifest(script)
        value = json.loads(write_path.read_text())
        value["capabilities"]["writes"] = ["core.database"]
        write_path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ModuleError, "unknown capability fields|must not declare writes"):
            ModuleManifest.load(write_path)

    def test_direct_state_path_in_request_is_rejected(self) -> None:
        script = self._script("unused.py", "")
        manifest = ModuleManifest.load(self._manifest(script))
        with self.assertRaisesRegex(ModuleError, "private state path"):
            self._run(
                manifest,
                {
                    "schema": "virtuoso/module-request@0.1",
                    "projections": {"challenge.summary": {"database_path": "/tmp/state.sqlite3"}},
                },
            )

    def test_undeclared_projection_is_rejected(self) -> None:
        script = self._script("unused.py", "")
        manifest = ModuleManifest.load(self._manifest(script))
        with self.assertRaisesRegex(ModuleError, "undeclared projection"):
            self._run(
                manifest,
                {
                    "schema": "virtuoso/module-request@0.1",
                    "projections": {"project.summary": {"title": "Private project"}},
                },
            )

    def test_request_projection_bodies_use_exact_typed_schemas(self) -> None:
        script = self._script("unused.py", "")
        manifest = ModuleManifest.load(self._manifest(script))
        invalid_projections = (
            ({"challenge.summary": "not-an-object"}, "must be a JSON object"),
            (
                {"challenge.summary": {"title": "Safe", "unknown": True}},
                "unknown challenge.summary fields",
            ),
            (
                {"challenge.summary": {"title": 42}},
                "challenge.summary.title must be a string",
            ),
        )
        for projections, message in invalid_projections:
            with self.subTest(message=message), self.assertRaisesRegex(ModuleError, message):
                self._run(
                    manifest,
                    {
                        "schema": "virtuoso/module-request@0.1",
                        "projections": projections,
                    },
                )

    def test_request_rejects_private_aliases_paths_and_nonfinite_values(self) -> None:
        script = self._script("unused.py", "")
        manifest = ModuleManifest.load(self._manifest(script))
        invalid_projections = (
            (
                {
                    "challenge.summary": {
                        "title": "Safe",
                        "path": "/tmp/learner/.virtuoso/state.sqlite3",
                        "token": "private-token",
                    }
                },
                "private state path or secret",
            ),
            (
                {"challenge.summary": {"title": "/tmp/private/answer.md"}},
                "private state path or secret",
            ),
            (
                {"challenge.summary": {"title": "Safe", "priority": float("nan")}},
                "finite JSON",
            ),
        )
        for projections, message in invalid_projections:
            with self.subTest(message=message), self.assertRaisesRegex(ModuleError, message):
                self._run(
                    manifest,
                    {
                        "schema": "virtuoso/module-request@0.1",
                        "projections": projections,
                    },
                )

    def test_result_payloads_reject_unknown_untyped_and_nonfinite_fields(self) -> None:
        invalid_scripts = (
            (
                "unknown-payload.py",
                "import json; print(json.dumps({'schema':'virtuoso/module-result@0.1','module_id':'example-score','kind':'score-proposal','payload':{'score':1,'extra':True}}))\n",
                {},
                "unknown score-proposal fields",
            ),
            (
                "nonfinite.py",
                "print('{\"schema\":\"virtuoso/module-result@0.1\",\"module_id\":\"example-score\",\"kind\":\"score-proposal\",\"payload\":{\"score\":NaN}}')\n",
                {},
                "finite JSON",
            ),
            (
                "scheduler.py",
                "import json; print(json.dumps({'schema':'virtuoso/module-result@0.1','module_id':'example-score','kind':'scheduler-proposal','payload':{'nonsense':True}}))\n",
                {
                    "category": "scheduler",
                    "capabilities": {"reads": [], "returns": "scheduler-proposal"},
                },
                "scheduler-proposal fields",
            ),
        )
        for name, source, overrides, message in invalid_scripts:
            with self.subTest(name=name):
                script = self._script(name, source)
                manifest = ModuleManifest.load(self._manifest(script, **overrides))
                with self.assertRaisesRegex(ModuleError, message):
                    self._run(
                        manifest,
                        {"schema": "virtuoso/module-request@0.1", "projections": {}},
                    )

    def test_manifest_rejects_shell_executable_in_argv(self) -> None:
        script = self._script("unused.py", "")
        path = self._manifest(
            script,
            command={"argv": ["/bin/sh", "-c", "exit 0"], "timeout_seconds": 2},
        )
        with self.assertRaisesRegex(ModuleError, "shell executables"):
            ModuleManifest.load(path)

    def test_malformed_or_oversized_output_fails_closed(self) -> None:
        bad = self._script("bad.py", "print('not json')\n")
        with self.assertRaisesRegex(ModuleError, "valid JSON"):
            self._run(
                ModuleManifest.load(self._manifest(bad)),
                {"schema": "virtuoso/module-request@0.1", "projections": {}},
            )

        huge = self._script("huge.py", "print('x' * 2048)\n")
        with self.assertRaisesRegex(ModuleError, "output limit"):
            self._run(
                ModuleManifest.load(self._manifest(huge)),
                {"schema": "virtuoso/module-request@0.1", "projections": {}},
                max_output_bytes=512,
            )

    def test_result_unknown_fields_and_empty_payload_fail_closed(self) -> None:
        unknown = self._script(
            "unknown.py",
            "import json; print(json.dumps({'schema':'virtuoso/module-result@0.1','module_id':'example-score','kind':'score-proposal','payload':{'score':1},'extra':True}))\n",
        )
        with self.assertRaisesRegex(ModuleError, "unknown result fields"):
            self._run(
                ModuleManifest.load(self._manifest(unknown)),
                {"schema": "virtuoso/module-request@0.1", "projections": {}},
            )

        empty = self._script(
            "empty.py",
            "import json; print(json.dumps({'schema':'virtuoso/module-result@0.1','module_id':'example-score','kind':'score-proposal','payload':{}}))\n",
        )
        with self.assertRaisesRegex(ModuleError, "non-empty"):
            self._run(
                ModuleManifest.load(self._manifest(empty)),
                {"schema": "virtuoso/module-request@0.1", "projections": {}},
            )

    def test_timeout_fails_closed(self) -> None:
        slow = self._script("slow.py", "import time; time.sleep(1)\n")
        path = self._manifest(slow)
        value = json.loads(path.read_text())
        value["command"]["timeout_seconds"] = 0.05
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ModuleError, "timed out"):
            self._run(
                ModuleManifest.load(path),
                {"schema": "virtuoso/module-request@0.1", "projections": {}},
            )

    def test_timeout_kills_descendant_processes(self) -> None:
        marker = self.root / "descendant-survived"
        child = (
            "import pathlib,time; time.sleep(0.5); "
            f"pathlib.Path({str(marker)!r}).write_text('escaped')"
        )
        parent = self._script(
            "parent.py",
            "import subprocess,sys,time\n"
            f"subprocess.Popen([sys.executable, '-c', {child!r}])\n"
            "time.sleep(5)\n",
        )
        path = self._manifest(parent)
        value = json.loads(path.read_text())
        value["command"]["timeout_seconds"] = 0.15
        path.write_text(json.dumps(value))

        with self.assertRaisesRegex(ModuleError, "timed out"):
            self._run(
                ModuleManifest.load(path),
                {"schema": "virtuoso/module-request@0.1", "projections": {}},
            )
        time.sleep(0.65)
        self.assertFalse(marker.exists())

    def test_timeout_kills_detached_descendant_processes(self) -> None:
        marker = self.root / "detached-descendant-survived"
        child = (
            "import pathlib,time; time.sleep(0.5); "
            f"pathlib.Path({str(marker)!r}).write_text('escaped')"
        )
        parent = self._script(
            "detached-parent.py",
            "import subprocess,sys,time\n"
            f"subprocess.Popen([sys.executable, '-c', {child!r}], start_new_session=True)\n"
            "time.sleep(5)\n",
        )
        path = self._manifest(parent)
        value = json.loads(path.read_text())
        value["command"]["timeout_seconds"] = 0.15
        path.write_text(json.dumps(value))

        with self.assertRaisesRegex(ModuleError, "timed out"):
            self._run(
                ModuleManifest.load(path),
                {"schema": "virtuoso/module-request@0.1", "projections": {}},
            )
        time.sleep(0.65)
        self.assertFalse(marker.exists())

    def test_successful_parent_exit_kills_remaining_descendants(self) -> None:
        marker = self.root / "successful-descendant-survived"
        child = (
            "import pathlib,time; time.sleep(0.5); "
            f"pathlib.Path({str(marker)!r}).write_text('escaped')"
        )
        parent = self._script(
            "successful-parent.py",
            "import json,subprocess,sys\n"
            f"subprocess.Popen([sys.executable, '-c', {child!r}])\n"
            "print(json.dumps({'schema':'virtuoso/module-result@0.1','module_id':'example-score','kind':'score-proposal','payload':{'score':1}}))\n",
        )

        result = self._run(
            ModuleManifest.load(self._manifest(parent)),
            {"schema": "virtuoso/module-request@0.1", "projections": {}},
        )
        self.assertEqual(result.payload, {"score": 1})
        time.sleep(0.65)
        self.assertFalse(marker.exists())

    def test_workspace_records_success_and_failed_run_receipts_with_completion_times(self) -> None:
        workspace = WorkspaceService.init(self.root / "receipt-learner")
        valid = self._script(
            "receipt-valid.py",
            "import json; print(json.dumps({'schema':'virtuoso/module-result@0.1','module_id':'example-score','kind':'score-proposal','payload':{'score':1}}))\n",
        )
        valid_manifest = ModuleManifest.load(self._manifest(valid))
        workspace.run_module(
            runner=ModuleRunner(),
            manifest=valid_manifest,
            request={"schema": "virtuoso/module-request@0.1", "projections": {}},
            allow_trusted=True,
        )

        bad = self._script("receipt-bad.py", "print('not json')\n")
        bad_manifest = ModuleManifest.load(self._manifest(bad))
        with self.assertRaisesRegex(ModuleError, "valid JSON"):
            workspace.run_module(
                runner=ModuleRunner(),
                manifest=bad_manifest,
                request={"schema": "virtuoso/module-request@0.1", "projections": {}},
                allow_trusted=True,
            )

        receipts = workspace.list_module_receipts()
        self.assertEqual([receipt["status"] for receipt in receipts], ["succeeded", "failed"])
        self.assertIsNone(receipts[0]["error"])
        self.assertIn("valid JSON", receipts[1]["error"])
        for receipt in receipts:
            self.assertLessEqual(receipt["started_at"], receipt["completed_at"])
            self.assertGreaterEqual(receipt["duration_ms"], 0)

    def test_receipt_uses_manifest_hash_captured_at_load(self) -> None:
        script = self._script(
            "valid.py",
            "import json; print(json.dumps({'schema':'virtuoso/module-result@0.1','module_id':'example-score','kind':'score-proposal','payload':{'score':1}}))\n",
        )
        path = self._manifest(script)
        original_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest = ModuleManifest.load(path)
        result = self._run(
            manifest,
            {"schema": "virtuoso/module-request@0.1", "projections": {}},
        )
        path.write_text("{}")
        workspace = WorkspaceService.init(self.root / "hash-learner")
        receipt = workspace.record_module_receipt(manifest=manifest, result=result)
        self.assertEqual(receipt["manifest_sha256"], original_hash)


if __name__ == "__main__":
    unittest.main()
