from __future__ import annotations

import importlib.util
import inspect
import json
import subprocess
import unittest
from collections.abc import Callable
from pathlib import Path
from typing import cast
from unittest.mock import ANY, call, patch


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_PATH = ROOT / "plugins" / "hermes" / "__init__.py"
SPEC = importlib.util.spec_from_file_location("virtuoso_hermes_plugin", PLUGIN_PATH)
assert SPEC is not None and SPEC.loader is not None
plugin = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(plugin)


class FakeContext:
    def __init__(self, workspace: str = "/workspace") -> None:
        self.workspace = workspace
        self.tools: dict[str, dict[str, object]] = {}

    def get_config(self, key: str) -> str | None:
        return self.workspace if key == "workspace" else None

    def register_tool(self, **kwargs: object) -> None:
        self.tools[str(kwargs["name"])] = kwargs


def result(raw: str) -> dict[str, object]:
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise AssertionError("plugin result must be an object")
    return parsed


class DueAggregationTests(unittest.TestCase):
    def test_first_child_failure_stops_due_aggregation(self) -> None:
        failure = json.dumps(
            {"success": False, "error": "no learning item is due"}
        )
        transfer_success = json.dumps({"success": True, "data": {"checks": []}})
        with patch.object(
            plugin, "_run", side_effect=[failure, transfer_success]
        ) as run:
            payload = result(plugin.virtuoso_due(FakeContext()))

        self.assertIs(payload["success"], False)
        self.assertEqual(payload["error"], "no learning item is due")
        self.assertEqual(payload["component"], "recommended_next")
        run.assert_called_once_with(ANY, "next")

    def test_second_child_failure_fails_due_aggregation(self) -> None:
        next_success = json.dumps({"success": True, "data": {"item_id": "one"}})
        failure = json.dumps(
            {"success": False, "error": "delayed checks unavailable"}
        )
        with patch.object(plugin, "_run", side_effect=[next_success, failure]):
            payload = result(plugin.virtuoso_due(FakeContext()))

        self.assertIs(payload["success"], False)
        self.assertEqual(payload["error"], "delayed checks unavailable")
        self.assertEqual(payload["component"], "transfer_checks_due")

    def test_successful_children_are_aggregated(self) -> None:
        next_data = {"item_id": "one"}
        transfer_data = {"checks": []}
        with patch.object(
            plugin,
            "_run",
            side_effect=[
                json.dumps({"success": True, "data": next_data}),
                json.dumps({"success": True, "data": transfer_data}),
            ],
        ) as run:
            payload = result(
                plugin.virtuoso_due(FakeContext(), focus="-urgent learning")
            )

        self.assertEqual(
            payload,
            {
                "success": True,
                "recommended_next": next_data,
                "transfer_checks_due": transfer_data,
            },
        )
        self.assertEqual(
            run.call_args_list,
            [
                call(ANY, "next", "--focus=-urgent learning"),
                call(ANY, "transfer", "check", "due"),
            ],
        )


class TransferArgumentTests(unittest.TestCase):
    def test_invalid_item_id_fails_before_process_spawn(self) -> None:
        with patch.object(
            plugin,
            "_run",
            return_value=json.dumps({"success": True, "data": {}}),
        ) as run:
            payload = result(
                plugin.virtuoso_transfer_record(
                    FakeContext(),
                    item_id="Bad Item",
                    project="project-one",
                    use_case="Applied the concept",
                    outcome="successful",
                )
            )

        self.assertIs(payload["success"], False)
        self.assertEqual(
            payload["error"],
            "item id must be lowercase words or numbers separated by single dashes",
        )
        run.assert_not_called()

    def test_invalid_project_id_fails_before_process_spawn(self) -> None:
        with patch.object(
            plugin,
            "_run",
            return_value=json.dumps({"success": True, "data": {}}),
        ) as run:
            payload = result(
                plugin.virtuoso_transfer_record(
                    FakeContext(),
                    item_id="item-one",
                    project="-project",
                    use_case="Applied the concept",
                    outcome="successful",
                )
            )

        self.assertIs(payload["success"], False)
        self.assertEqual(
            payload["error"],
            "project id must be lowercase words or numbers separated by single dashes",
        )
        run.assert_not_called()

    def test_transfer_values_use_safe_option_arguments(self) -> None:
        self.assertIn(
            "artifact",
            inspect.signature(plugin.virtuoso_transfer_record).parameters,
        )
        with patch.object(
            plugin,
            "_run",
            return_value=json.dumps({"success": True, "data": {}}),
        ) as run:
            payload = result(
                plugin.virtuoso_transfer_record(
                    FakeContext(),
                    item_id="item-one",
                    project="project-one",
                    use_case="-applied in a live workflow",
                    outcome="successful",
                    independence="guided",
                    reflection="-reflection text",
                    artifact="-artifact reference",
                )
            )

        self.assertIs(payload["success"], True)
        run.assert_called_once_with(
            ANY,
            "transfer",
            "record",
            "--item=item-one",
            "--project=project-one",
            "--use-case=-applied in a live workflow",
            "--outcome=successful",
            "--independence=guided",
            "--artifact=-artifact reference",
            "--reflection=-reflection text",
        )

    def test_optional_transfer_values_can_be_omitted(self) -> None:
        with patch.object(
            plugin,
            "_run",
            return_value=json.dumps({"success": True, "data": {}}),
        ) as run:
            payload = result(
                plugin.virtuoso_transfer_record(
                    FakeContext(),
                    item_id="item-one",
                    project="project-one",
                    use_case="Applied it",
                    outcome="partial",
                )
            )

        self.assertIs(payload["success"], True)
        argv = " ".join(run.call_args.args[1:])
        self.assertNotIn("--artifact=", argv)
        self.assertNotIn("--reflection=", argv)


class FocusArgumentTests(unittest.TestCase):
    def test_focus_is_free_text_and_uses_safe_option_argument(self) -> None:
        with patch.object(
            plugin,
            "_run",
            return_value=json.dumps({"success": True, "data": {}}),
        ) as run:
            payload = result(
                plugin.virtuoso_next(FakeContext(), focus="-urgent learning")
            )

        self.assertIs(payload["success"], True)
        run.assert_called_once_with(ANY, "next", "--focus=-urgent learning")

    def test_blank_focus_fails_before_process_spawn(self) -> None:
        for focus in ("", "   "):
            with self.subTest(focus=repr(focus)):
                with patch.object(
                    plugin,
                    "_run",
                    return_value=json.dumps({"success": True, "data": {}}),
                ) as run:
                    payload = result(plugin.virtuoso_next(FakeContext(), focus=focus))

                self.assertIs(payload["success"], False)
                self.assertEqual(payload["error"], "focus must be a non-empty string")
                run.assert_not_called()


class RunnerTests(unittest.TestCase):
    def test_workspace_uses_safe_option_argument(self) -> None:
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout='{"status": "healthy"}',
            stderr="",
        )
        with (
            patch.object(plugin, "_cli", return_value="/usr/bin/virtuoso"),
            patch.object(plugin.subprocess, "run", return_value=completed) as run,
        ):
            payload = result(plugin._run(FakeContext("-private path"), "doctor"))

        self.assertIs(payload["success"], True)
        self.assertEqual(payload["data"], {"status": "healthy"})
        self.assertEqual(
            run.call_args.args[0],
            ["/usr/bin/virtuoso", "--workspace=-private path", "doctor", "--json"],
        )
        self.assertEqual(
            run.call_args.kwargs,
            {"capture_output": True, "text": True, "timeout": plugin.TIMEOUT_S},
        )

    def test_missing_cli_returns_failed_envelope(self) -> None:
        with patch.object(plugin, "_cli", return_value=None):
            payload = result(plugin._run(FakeContext(), "doctor"))

        self.assertEqual(
            payload,
            {"success": False, "error": "virtuoso CLI not found on PATH"},
        )

    def test_zero_exit_malformed_json_is_failure(self) -> None:
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="not json", stderr=""
        )
        with (
            patch.object(plugin, "_cli", return_value="/usr/bin/virtuoso"),
            patch.object(plugin.subprocess, "run", return_value=completed),
        ):
            payload = result(plugin._run(FakeContext(), "doctor"))

        self.assertIs(payload["success"], False)
        self.assertEqual(payload["error"], "virtuoso returned invalid JSON")

    def test_zero_exit_empty_output_is_failure(self) -> None:
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="   ", stderr=""
        )
        with (
            patch.object(plugin, "_cli", return_value="/usr/bin/virtuoso"),
            patch.object(plugin.subprocess, "run", return_value=completed),
        ):
            payload = result(plugin._run(FakeContext(), "doctor"))

        self.assertIs(payload["success"], False)
        self.assertEqual(payload["error"], "virtuoso returned empty JSON output")

    def test_zero_exit_non_object_json_is_failure(self) -> None:
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="[]", stderr=""
        )
        with (
            patch.object(plugin, "_cli", return_value="/usr/bin/virtuoso"),
            patch.object(plugin.subprocess, "run", return_value=completed),
        ):
            payload = result(plugin._run(FakeContext(), "doctor"))

        self.assertIs(payload["success"], False)
        self.assertEqual(payload["error"], "virtuoso returned non-object JSON")

    def test_timeout_error_does_not_echo_private_arguments(self) -> None:
        private_value = "private-workspace-marker"
        timeout = subprocess.TimeoutExpired(
            cmd=["virtuoso", private_value], timeout=plugin.TIMEOUT_S
        )
        with (
            patch.object(plugin, "_cli", return_value="/usr/bin/virtuoso"),
            patch.object(plugin.subprocess, "run", side_effect=timeout),
        ):
            raw = plugin._run(FakeContext(private_value), "doctor")
            payload = result(raw)

        self.assertIs(payload["success"], False)
        self.assertEqual(
            payload["error"], f"virtuoso timed out after {plugin.TIMEOUT_S}s"
        )
        self.assertNotIn("command", payload)
        self.assertNotIn(private_value, raw)

    def test_nonzero_error_does_not_echo_private_arguments(self) -> None:
        private_value = "private-transfer-marker"
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=2,
            stdout="",
            stderr="Error: invalid transfer",
        )
        with (
            patch.object(plugin, "_cli", return_value="/usr/bin/virtuoso"),
            patch.object(plugin.subprocess, "run", return_value=completed),
        ):
            raw = plugin._run(
                FakeContext("private-workspace-marker"),
                "transfer",
                "record",
                f"--use-case={private_value}",
            )
            payload = result(raw)

        self.assertIs(payload["success"], False)
        self.assertEqual(payload["error"], "Error: invalid transfer")
        self.assertNotIn("command", payload)
        self.assertNotIn(private_value, raw)

    def test_spawn_failure_returns_failed_envelope(self) -> None:
        private_value = "private-executable-marker"
        failure = FileNotFoundError(2, "No such file or directory", private_value)
        with (
            patch.object(plugin, "_cli", return_value=private_value),
            patch.object(plugin.subprocess, "run", side_effect=failure),
        ):
            try:
                raw = plugin._run(FakeContext(), "doctor")
            except OSError as exc:
                self.fail(f"spawn error escaped the plugin boundary: {exc}")
            payload = result(raw)

        self.assertIs(payload["success"], False)
        self.assertEqual(
            payload["error"], "virtuoso could not start: No such file or directory"
        )
        self.assertNotIn(private_value, raw)


class RegistrationTests(unittest.TestCase):
    def test_transfer_tool_schema_and_handler_forward_artifact(self) -> None:
        ctx = FakeContext()
        plugin.register(ctx)
        registration = ctx.tools["virtuoso_transfer_record"]
        schema = registration["schema"]
        assert isinstance(schema, dict)
        parameters = schema["parameters"]
        assert isinstance(parameters, dict)
        properties = parameters["properties"]
        assert isinstance(properties, dict)
        self.assertIn("artifact", properties)
        for field in ("item_id", "project"):
            with self.subTest(field=field):
                value = properties[field]
                assert isinstance(value, dict)
                self.assertEqual(
                    value.get("pattern"), r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
                )

        handler = cast(Callable[[dict[str, object]], str], registration["handler"])
        with patch.object(
            plugin,
            "_run",
            return_value=json.dumps({"success": True, "data": {}}),
        ) as run:
            raw = handler(
                {
                    "item_id": "item-one",
                    "project": "project-one",
                    "use_case": "Applied it",
                    "outcome": "successful",
                    "artifact": "report.md",
                }
            )

        self.assertIs(result(raw)["success"], True)
        self.assertIn("--artifact=report.md", run.call_args.args)


if __name__ == "__main__":
    unittest.main()
