from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from virtuoso.modules import ModuleError, ModuleManifest, ModuleRunner
from virtuoso.workspace import WorkspaceService


class ModuleBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

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
        }
        value.update(overrides)
        path = self.root / "virtuoso.module.json"
        path.write_text(json.dumps(value))
        return path

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
        result = ModuleRunner(max_output_bytes=4096).run(
            manifest,
            {
                "schema": "virtuoso/module-request@0.1",
                "projections": {"challenge.summary": {"title": "Testing effect"}},
            },
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

    def test_shell_command_and_direct_write_capability_are_rejected(self) -> None:
        script = self._script("unused.py", "")
        shell_path = self._manifest(
            script,
            command={"shell": "python unused.py", "timeout_seconds": 2},
        )
        with self.assertRaisesRegex(ModuleError, "argv"):
            ModuleManifest.load(shell_path)

        write_path = self._manifest(script)
        value = json.loads(write_path.read_text())
        value["capabilities"]["writes"] = ["core.database"]
        write_path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ModuleError, "must not declare writes"):
            ModuleManifest.load(write_path)

    def test_direct_state_path_in_request_is_rejected(self) -> None:
        script = self._script("unused.py", "")
        manifest = ModuleManifest.load(self._manifest(script))
        with self.assertRaisesRegex(ModuleError, "private state path"):
            ModuleRunner().run(
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
            ModuleRunner().run(
                manifest,
                {
                    "schema": "virtuoso/module-request@0.1",
                    "projections": {"project.summary": {"title": "Private project"}},
                },
            )

    def test_malformed_or_oversized_output_fails_closed(self) -> None:
        bad = self._script("bad.py", "print('not json')\n")
        with self.assertRaisesRegex(ModuleError, "valid JSON"):
            ModuleRunner().run(
                ModuleManifest.load(self._manifest(bad)),
                {"schema": "virtuoso/module-request@0.1", "projections": {}},
            )

        huge = self._script("huge.py", "print('x' * 2048)\n")
        with self.assertRaisesRegex(ModuleError, "output limit"):
            ModuleRunner(max_output_bytes=512).run(
                ModuleManifest.load(self._manifest(huge)),
                {"schema": "virtuoso/module-request@0.1", "projections": {}},
            )

    def test_timeout_fails_closed(self) -> None:
        slow = self._script("slow.py", "import time; time.sleep(1)\n")
        path = self._manifest(slow)
        value = json.loads(path.read_text())
        value["command"]["timeout_seconds"] = 0.05
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ModuleError, "timed out"):
            ModuleRunner().run(
                ModuleManifest.load(path),
                {"schema": "virtuoso/module-request@0.1", "projections": {}},
            )


if __name__ == "__main__":
    unittest.main()
