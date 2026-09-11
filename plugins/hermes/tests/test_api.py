from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from virtuoso.workspace import WorkspaceService


ROOT = Path(__file__).resolve().parents[3]
API = ROOT / "plugins/hermes/dashboard/plugin_api.py"


class DesktopApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(API.exists(), "The plugin must ship real backend routes")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name).resolve()
        self.workspace = WorkspaceService.init(self.home / "learning")
        self.workspace.add_item(
            item_id="retrieval", title="Study retrieval", focus="learning",
            entry_mode="learn-first", learning_unit="Practice retrieving memories.",
            prompt="What should you practice?", answer="Retrieving memories.",
            hint="Remember the lesson.", follow_up="Give an example.",
        )
        spec = importlib.util.spec_from_file_location("test_virtuoso_api", API)
        self.api = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = self.api
        spec.loader.exec_module(self.api)
        self.addCleanup(sys.modules.pop, spec.name, None)
        self.settings = {
            "executable": str(Path(sys.executable).parent / "virtuoso"),
            "workspace": str(self.workspace.root),
        }
        patcher = patch.object(self.api, "configuration", lambda: (self.home, self.settings.copy()))
        patcher.start()
        self.addCleanup(patcher.stop)
        app = FastAPI()
        app.include_router(self.api.router, prefix="/api/plugins/virtuoso")
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def get(self, path: str, **params) -> dict:
        response = self.client.get("/api/plugins/virtuoso" + path, params=params)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers.get("cache-control"), "no-store")
        return response.json()

    def test_context_uses_backend_settings_without_changing_workspace(self) -> None:
        before = self.workspace.db_path.read_bytes()
        context = self.get("/context")
        self.assertEqual(context["schema"], "virtuoso/desktop-context@0.1")
        self.assertRegex(context["context_id"], r"^[0-9a-f]{64}$")
        self.assertEqual(context["workspace_name"], "learning")
        self.assertNotIn(str(self.home), str(context))
        self.assertEqual(self.workspace.db_path.read_bytes(), before)

    def post(self, path: str, context_id: str, request: dict) -> dict:
        result = self.client.post("/api/plugins/virtuoso" + path, json={
            "context_id": context_id, "request": request,
        })
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.headers.get("cache-control"), "no-store")
        return result.json()

    def test_remote_study_then_direct_review_uses_the_installed_cli(self) -> None:
        context_id = self.get("/context")["context_id"]
        loaded = self.get("/next", context_id=context_id)
        self.assertEqual(loaded["action"], "learn")
        item = loaded["item"]
        self.assertNotIn("answer", item)
        study_request = {
            "schema": "virtuoso/study-completion@0.1", "item_id": item["item_id"],
            "item_content_hash": item["content_hash"],
            "learning_unit_hash": item["learning_unit_hash"],
            "completed": True, "surface": "hermes-desktop",
        }
        studied = self.post("/study", context_id, study_request)
        self.assertEqual(studied["schema"], "virtuoso/study-result@0.1")
        self.assertEqual(self.post("/study", context_id, study_request), studied)
        loaded = self.get("/next", context_id=context_id)
        self.assertEqual(loaded["action"], "practice")
        item = loaded["item"]
        request = {
            "schema": "virtuoso/review-attempt@0.1", "submission_id": "a" * 32,
            "item_id": item["item_id"], "item_content_hash": item["content_hash"],
            "started_at": "2026-09-11T08:00:00+00:00",
            "initial_answered_at": "2026-09-11T08:00:02+00:00",
            "completed_at": "2026-09-11T08:00:05+00:00",
            "initial_response": "Retrieve the memory.", "retry": None,
            "hint_used": True, "answer_revealed": True, "result": "partial",
            "confidence": 3, "open_notes": True,
        }
        recorded = self.post("/record", context_id, request)
        self.assertEqual(recorded["attempt"]["initial_latency_ms"], 2000)
        self.assertIs(recorded["attempt"]["administered"], False)
        again = self.post("/record", context_id, request)
        self.assertEqual(again["error"]["code"], "already-recorded")
        self.assertEqual(len(self.workspace.list_study_events()), 1)
        self.assertEqual(len(self.workspace.list_attempts()), 1)
        self.assertEqual(len(self.workspace.list_proposals()), 1)

    def test_configuration_change_rejects_old_context_before_running_cli(self) -> None:
        context_id = self.get("/context")["context_id"]
        other = WorkspaceService.init(self.home / "other")
        self.settings["workspace"] = str(other.root)
        with patch.object(self.api, "run_cli") as run:
            result = self.post("/record", context_id, {})
            self.assertEqual(result["error"]["code"], "context-changed")
            run.assert_not_called()
        self.assertEqual(other.list_attempts(), [])

    def test_non_finite_json_and_oversize_body_fail_without_launching_cli(self) -> None:
        context_id = self.get("/context")["context_id"]
        bodies = [
            '{"context_id":"' + context_id + '","request":{"confidence":NaN}}',
            " " * 65_537,
        ]
        with patch.object(self.api, "run_cli") as run:
            for body in bodies:
                result = self.client.post(
                    "/api/plugins/virtuoso/record", content=body,
                    headers={"Content-Type": "application/json"},
                ).json()
                self.assertEqual(result["error"]["code"], "invalid-request")
            run.assert_not_called()

    def test_replaced_workspace_database_invalidates_context(self) -> None:
        context_id = self.get("/context")["context_id"]
        database = self.workspace.db_path
        replacement = database.with_suffix(".replacement")
        replacement.write_bytes(database.read_bytes())
        replacement.replace(database)
        self.assertNotEqual(self.get("/context")["context_id"], context_id)

    def test_empty_queue_and_missing_cli_have_distinct_states(self) -> None:
        self.workspace.retire_item("retrieval")
        context_id = self.get("/context")["context_id"]
        self.assertIsNone(self.get("/next", context_id=context_id)["action"])
        self.settings["executable"] = str(self.home / "absent")
        self.assertEqual(self.get("/context")["error"]["code"], "setup-required")

    def test_runner_caps_output_and_time(self) -> None:
        for source, code in [
            ("import sys; sys.stdout.write('x' * 1100000)", "output-limit"),
            ("import time; time.sleep(20)", "timeout"),
        ]:
            executable = self.home / "test-cli"
            executable.write_text(f"#!{sys.executable}\n{source}\n")
            executable.chmod(0o700)
            self.settings["executable"] = str(executable)
            config = self.api.configured()
            with patch.object(self.api, "TIMEOUT", 2):
                with self.assertRaises(self.api.PluginError) as caught:
                    self.api.run_cli(config, ["next"])
            self.assertEqual(caught.exception.code, code)


if __name__ == "__main__":
    unittest.main()
