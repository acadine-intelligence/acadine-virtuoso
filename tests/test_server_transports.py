"""Real MCP and HTTP clients against `virtuoso serve` (issue #69, docs/22).

Needs the [server] extra. CI runs this file in the server job with
VIRTUOSO_REQUIRE_SERVER_TESTS=1, which turns a missing extra into a failure
instead of a skip.
"""

from __future__ import annotations

import asyncio
import contextlib
import http.client
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from server_support import (
    RECALL_ANSWER,
    RECALL_PROMPT,
    FixedClock,
    database_digest,
    make_workspace,
    require_server_extra,
    rows,
    server_extra_available,
)

TOKEN = secrets.token_hex(24)

if server_extra_available():
    import httpx2
    import mcp_types
    import uvicorn
    from mcp import Client
    from mcp.client.stdio import StdioServerParameters
    from mcp.client.streamable_http import streamable_http_client

    from virtuoso.server import transports
    from virtuoso.server.operations import OPERATIONS, ServerCore
    from virtuoso.server.transports import build_http_app, build_mcp_server


def _cli_command(*arguments: str) -> list[str]:
    return [sys.executable, "-m", "virtuoso.cli", *arguments]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _tool_payload(result) -> dict:
    assert len(result.content) == 1
    return json.loads(result.content[0].text)


class _UvicornThread:
    def __init__(self, app, port: int) -> None:
        config = uvicorn.Config(
            app, host="127.0.0.1", port=port, log_level="warning",
            access_log=False, lifespan="on",
        )
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self):
        self.thread.start()
        deadline = time.monotonic() + 10
        while not self.server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("uvicorn did not start")
            time.sleep(0.02)
        return self

    def __exit__(self, *exc_info) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)


def _post(port: int, path: str, body: object | bytes, *, headers: dict[str, str] | None = None,
          method: str = "POST") -> tuple[int, dict, dict]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    data = body if isinstance(body, bytes) else json.dumps(body).encode()
    final = {"Host": f"127.0.0.1:{port}", "Content-Type": "application/json"}
    final.update(headers or {})
    connection.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
    for key, value in final.items():
        if value is not None:
            connection.putheader(key, value)
    connection.putheader("Content-Length", str(len(data)))
    connection.endheaders(data)
    response = connection.getresponse()
    raw = response.read()
    connection.close()
    try:
        payload = json.loads(raw) if raw else {}
    except ValueError:
        payload = {"raw": raw.decode(errors="replace")}
    return response.status, {k.lower(): v for k, v in response.getheaders()}, payload


@require_server_extra
class StdioHeroWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = make_workspace(Path(temporary.name).resolve())

    def test_mcp_client_completes_the_hero_workflow_over_stdio(self) -> None:
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "virtuoso.cli", "--workspace", str(self.workspace.root),
                  "serve", "--transport", "stdio"],
            env={"PYTHONNOUSERSITE": "1"},
        )
        client_info = mcp_types.Implementation(name="synthetic-test-client", version="0.0.1")

        async def journey() -> dict:
            out: dict = {}
            with open(os.devnull, "w") as quiet:
                async with Client(params, client_info=client_info) as client:
                    tools = await client.list_tools()
                    out["tools"] = {tool.name: tool for tool in tools.tools}
                    out["next"] = await client.call_tool("next_action", {})
                    loaded = await client.call_tool("study_load", {"item_id": "stack-order"})
                    out["study_load"] = loaded
                    item = _tool_payload(loaded)["item"]
                    out["study"] = await client.call_tool("study_record", {
                        "item_id": "stack-order",
                        "item_content_hash": item["content_hash"],
                        "learning_unit_hash": item["learning_unit_hash"],
                        "completed": True,
                    })
                    started = await client.call_tool(
                        "recall_start", {"item_id": "hash-lookup", "client": "synthetic-test-client"}
                    )
                    out["start"] = started
                    token = _tool_payload(started)["attempt"]["attempt_token"]
                    out["early_reveal"] = await client.call_tool(
                        "recall_reveal", {"attempt_token": token}
                    )
                    out["respond"] = await client.call_tool(
                        "recall_respond", {"attempt_token": token, "response": "a hash table"}
                    )
                    out["reveal"] = await client.call_tool("recall_reveal", {"attempt_token": token})
                    out["record"] = await client.call_tool("recall_record", {
                        "attempt_token": token,
                        "submission_id": secrets.token_hex(16),
                        "result": "demonstrated",
                        "confidence": 4,
                        "agent_help": "none",
                        "graded_by": "learner",
                    })
                    out["history"] = await client.call_tool("history", {"item_id": "hash-lookup"})
            del quiet
            return out

        out = asyncio.run(asyncio.wait_for(journey(), timeout=60))

        self.assertEqual(set(out["tools"]), set(OPERATIONS))
        for name, tool in out["tools"].items():
            self.assertEqual(tool.input_schema, OPERATIONS[name].input_schema)
            self.assertEqual(tool.output_schema, OPERATIONS[name].output_schema)
            self.assertEqual(
                tool.annotations.read_only_hint, OPERATIONS[name].kind == "read"
            )
        selected = _tool_payload(out["next"])
        self.assertEqual((selected["action"], selected["item_id"]), ("practice", "hash-lookup"))
        self.assertNotIn(RECALL_ANSWER, json.dumps(selected))
        self.assertEqual(_tool_payload(out["study"])["study"]["surface"], "mcp")
        started = _tool_payload(out["start"])
        self.assertEqual(started["attempt"]["prompt"], RECALL_PROMPT)
        self.assertNotIn(RECALL_ANSWER, json.dumps(started))
        self.assertTrue(out["early_reveal"].is_error)
        early = _tool_payload(out["early_reveal"])
        self.assertEqual(early["error"]["code"], "response-required")
        self.assertNotIn(RECALL_ANSWER, json.dumps(early))
        self.assertFalse(out["reveal"].is_error)
        self.assertEqual(_tool_payload(out["reveal"])["answer"], RECALL_ANSWER)
        record = _tool_payload(out["record"])
        self.assertEqual(out["record"].structured_content, record)
        self.assertEqual(record["attempt"]["surface"], "mcp")
        self.assertEqual(record["attempt"]["client"], "synthetic-test-client")
        self.assertIsNone(record["attempt"]["latency_ms"])
        self.assertEqual(record["proposal"]["algorithm"], "fsrs")
        history = _tool_payload(out["history"])
        self.assertEqual([entry["event_id"] for entry in history["attempts"]],
                         [record["attempt"]["event_id"]])
        self.assertNotIn("a hash table", json.dumps(history))
        stored = rows(self.workspace.db_path,
                      "SELECT surface, client, graded_by FROM attempts")
        self.assertEqual(stored, [{"surface": "mcp", "client": "synthetic-test-client",
                                   "graded_by": "learner"}])


@require_server_extra
class HttpTransportTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = make_workspace(Path(temporary.name).resolve())
        self.port = _free_port()
        core = ServerCore(self.workspace)
        app = build_http_app(
            core, token=TOKEN, host="127.0.0.1", port=self.port,
            allowed_hosts=(), allowed_origins=("https://chat.example",),
        )
        self.server = _UvicornThread(app, self.port)
        self.server.__enter__()
        self.addCleanup(self.server.__exit__, None, None, None)
        self.auth = {"Authorization": f"Bearer {TOKEN}"}

    def test_ops_binding_runs_with_a_valid_token(self) -> None:
        status, headers, payload = _post(
            self.port, "/v1/ops/recall_start", {"item_id": "hash-lookup"}, headers=self.auth
        )
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["attempt"]["surface"], "http")
        self.assertEqual(headers["cache-control"], "no-store")

    def test_wrong_or_missing_token_gets_401_and_writes_nothing(self) -> None:
        before = database_digest(self.workspace.db_path)
        for headers in ({}, {"Authorization": "Bearer " + "0" * 48}):
            for path in ("/v1/ops/recall_start", "/mcp"):
                with self.subTest(path=path, headers=headers):
                    status, response_headers, payload = _post(
                        self.port, path, {"item_id": "hash-lookup"}, headers=headers
                    )
                    self.assertEqual(status, 401)
                    self.assertEqual(payload["error"]["code"], "unauthorized")
                    self.assertNotIn(TOKEN, json.dumps(payload))
        self.assertEqual(database_digest(self.workspace.db_path), before)

    def test_foreign_host_origin_and_preflight_get_403_and_write_nothing(self) -> None:
        before = database_digest(self.workspace.db_path)
        cases = (
            ("POST", {"Host": "rebound.example:%d" % self.port}),
            ("POST", {"Origin": "https://evil.example"}),
            ("OPTIONS", {"Origin": "https://chat.example",
                         "Access-Control-Request-Method": "POST"}),
        )
        for method, extra in cases:
            for path in ("/v1/ops/recall_start", "/mcp"):
                with self.subTest(method=method, extra=extra, path=path):
                    status, headers, _payload = _post(
                        self.port, path, {"item_id": "hash-lookup"},
                        headers={**self.auth, **extra}, method=method,
                    )
                    self.assertEqual(status, 403)
                    self.assertFalse(any(k.startswith("access-control") for k in headers))
        self.assertEqual(database_digest(self.workspace.db_path), before)

    def test_listed_origin_and_absent_origin_are_allowed(self) -> None:
        for extra in ({}, {"Origin": "https://chat.example"}):
            with self.subTest(extra=extra):
                status, _headers, payload = _post(
                    self.port, "/v1/ops/workload", {}, headers={**self.auth, **extra}
                )
                self.assertEqual(status, 200, payload)

    def test_domain_errors_map_to_4xx_with_the_envelope(self) -> None:
        _status, _h, started = _post(
            self.port, "/v1/ops/recall_start", {"item_id": "hash-lookup"}, headers=self.auth
        )
        token = started["attempt"]["attempt_token"]
        before = database_digest(self.workspace.db_path)
        status, _h, payload = _post(
            self.port, "/v1/ops/recall_reveal", {"attempt_token": token}, headers=self.auth
        )
        self.assertEqual(status, 409)
        self.assertEqual(payload["schema"], "virtuoso/review-error@0.1")
        self.assertEqual(payload["error"]["code"], "response-required")
        self.assertEqual(database_digest(self.workspace.db_path), before)
        status, _h, payload = _post(
            self.port, "/v1/ops/not_an_operation", {}, headers=self.auth
        )
        self.assertEqual(status, 404)
        status, _h, payload = _post(
            self.port, "/v1/ops/workload", b"[not json", headers=self.auth
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "invalid-request")

    def test_mcp_streamable_http_with_token(self) -> None:
        async def call() -> tuple:
            http_client = httpx2.AsyncClient(headers=self.auth)
            transport = streamable_http_client(
                f"http://127.0.0.1:{self.port}/mcp", http_client=http_client
            )
            async with Client(transport) as client:
                tools = await client.list_tools()
                result = await client.call_tool("next_action", {})
            await http_client.aclose()
            return tools, result

        tools, result = asyncio.run(asyncio.wait_for(call(), timeout=30))
        self.assertEqual({tool.name for tool in tools.tools}, set(OPERATIONS))
        self.assertFalse(result.is_error)
        self.assertEqual(_tool_payload(result)["item_id"], "hash-lookup")


def _normalize(value, tokens: dict[str, str]):
    volatile = {"attempt_token", "event_id", "proposal_id", "submission_id"}
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if key in volatile and isinstance(item, str):
                out[key] = tokens.setdefault(item, f"<{key}-{len(tokens)}>")
            elif key == "surface":
                out[key] = "<surface>"
            else:
                out[key] = _normalize(item, tokens)
        return out
    if isinstance(value, list):
        return [_normalize(item, tokens) for item in value]
    return value


@require_server_extra
class BindingParityTests(unittest.TestCase):
    """The same operation sequence gives identical records on both bindings.

    Each binding gets its own synthetic workspace with identical items and
    the same fixed server clock. Random identifiers and the surface name
    (`mcp` or `http`, by design) are normalized before comparison.
    """

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        parent = Path(temporary.name).resolve()
        self.mcp_core = ServerCore(make_workspace(parent, "over-mcp"), clock=FixedClock())
        self.http_core = ServerCore(make_workspace(parent, "over-http"), clock=FixedClock())

    def _sequence(self):
        submission = secrets.token_hex(16)
        skip_submission = secrets.token_hex(16)
        yield "next_action", {}
        yield "study_load", {"item_id": "stack-order"}
        yield "study_record", lambda prior: {
            "item_id": "stack-order",
            "item_content_hash": prior["study_load"]["item"]["content_hash"],
            "learning_unit_hash": prior["study_load"]["item"]["learning_unit_hash"],
            "completed": True,
        }
        yield "recall_start", {"item_id": "hash-lookup", "client": "parity-client"}
        yield "recall_hint", lambda prior: {"attempt_token": prior["recall_start"]["attempt"]["attempt_token"]}
        yield "recall_respond", lambda prior: {
            "attempt_token": prior["recall_start"]["attempt"]["attempt_token"],
            "response": "a hash table",
        }
        yield "recall_reveal", lambda prior: {"attempt_token": prior["recall_start"]["attempt"]["attempt_token"]}
        yield "recall_record", lambda prior: {
            "attempt_token": prior["recall_start"]["attempt"]["attempt_token"],
            "submission_id": submission, "result": "partial", "confidence": 3,
            "agent_help": "light", "graded_by": "learner",
        }
        yield "recall_record#replay", lambda prior: {
            "attempt_token": prior["recall_start"]["attempt"]["attempt_token"],
            "submission_id": submission, "result": "partial", "confidence": 3,
            "agent_help": "light", "graded_by": "learner",
        }
        yield "recall_start#2", {"item_id": "stack-order"}
        yield "recall_skip", lambda prior: {
            "attempt_token": prior["recall_start#2"]["attempt"]["attempt_token"],
            "submission_id": skip_submission,
        }
        yield "workload", {}
        yield "history", {"item_id": "hash-lookup"}

    def test_mcp_and_http_bindings_return_identical_records(self) -> None:
        mcp_records = asyncio.run(asyncio.wait_for(self._run_mcp(), timeout=60))
        http_records = self._run_http()
        self.assertEqual(len(mcp_records), 13)
        tokens_mcp: dict[str, str] = {}
        tokens_http: dict[str, str] = {}
        self.assertEqual(
            [_normalize(record, tokens_mcp) for record in mcp_records],
            [_normalize(record, tokens_http) for record in http_records],
        )
        self.assertEqual(mcp_records[7]["attempt"]["surface"], "mcp")
        self.assertEqual(http_records[7]["attempt"]["surface"], "http")

    async def _run_mcp(self) -> list[dict]:
        server = build_mcp_server(self.mcp_core, surface="mcp")
        prior: dict = {}
        records: list[dict] = []
        async with Client(server) as client:
            for label, arguments in self._sequence():
                if callable(arguments):
                    arguments = arguments(prior)
                result = await client.call_tool(label.split("#")[0], arguments)
                self.assertFalse(result.is_error, result.content)
                payload = _tool_payload(result)
                prior[label] = payload
                records.append(payload)
        return records

    def _run_http(self) -> list[dict]:
        port = _free_port()
        app = build_http_app(self.http_core, token=TOKEN, host="127.0.0.1", port=port,
                             allowed_hosts=(), allowed_origins=())
        prior: dict = {}
        records: list[dict] = []
        with _UvicornThread(app, port):
            for label, arguments in self._sequence():
                if callable(arguments):
                    arguments = arguments(prior)
                status, _h, payload = _post(
                    port, f"/v1/ops/{label.split('#')[0]}", arguments,
                    headers={"Authorization": f"Bearer {TOKEN}"},
                )
                self.assertEqual(status, 200, payload)
                prior[label] = payload
                records.append(payload)
        return records


@require_server_extra
class OperationTimeoutTests(unittest.TestCase):
    def test_slow_operation_returns_operation_timeout(self) -> None:
        class SlowCore:
            def call(self, name, arguments, *, surface):
                time.sleep(0.5)
                return {}

        with mock.patch.object(transports, "OPERATION_TIMEOUT_SECONDS", 0.05):
            status, payload = asyncio.run(
                transports.run_operation(SlowCore(), "workload", {}, surface="http")
            )
        self.assertEqual(status, 504)
        self.assertEqual(payload["error"]["code"], "operation-timeout")


@require_server_extra
class ServeCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()
        self.workspace = make_workspace(self.directory)
        self.env = {
            key: value for key, value in os.environ.items()
            if key != "VIRTUOSO_SERVER_TOKEN"
        }

    def _serve(self, *arguments: str, env: dict | None = None):
        return subprocess.run(
            _cli_command("--workspace", str(self.workspace.root), "serve", *arguments),
            capture_output=True, text=True, timeout=30, env=env or self.env,
        )

    def test_http_without_a_token_refuses_to_start_on_every_bind(self) -> None:
        for host in ("127.0.0.1", "0.0.0.0", "::1"):
            with self.subTest(host=host):
                result = self._serve("--transport", "http", "--host", host, "--port", "0")
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("VIRTUOSO_SERVER_TOKEN", result.stderr)
                self.assertNotIn("Traceback", result.stderr)

    def test_http_refuses_a_readable_token_file(self) -> None:
        if os.name == "nt":
            self.skipTest("POSIX permission bits")
        path = self.directory / "token"
        path.write_text(TOKEN)
        path.chmod(0o644)
        result = self._serve("--transport", "http", "--token-file", str(path))
        self.assertEqual(result.returncode, 2)
        self.assertNotIn(TOKEN, result.stderr)

    def test_http_serves_and_never_logs_the_token(self) -> None:
        port = _free_port()
        process = subprocess.Popen(
            _cli_command("--workspace", str(self.workspace.root), "serve",
                         "--transport", "http", "--port", str(port)),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env={**self.env, "VIRTUOSO_SERVER_TOKEN": TOKEN},
        )
        try:
            deadline = time.monotonic() + 20
            status = None
            while time.monotonic() < deadline:
                with contextlib.suppress(OSError):
                    status, _h, payload = _post(
                        port, "/v1/ops/workload", {},
                        headers={"Authorization": f"Bearer {TOKEN}"},
                    )
                    break
                time.sleep(0.1)
            self.assertEqual(status, 200)
            unauthorized, _h, _p = _post(port, "/v1/ops/workload", {})
            self.assertEqual(unauthorized, 401)
        finally:
            process.terminate()
            stdout, stderr = process.communicate(timeout=20)
        self.assertNotIn(TOKEN, stdout + stderr)

    def test_stdio_and_http_reject_unknown_transports(self) -> None:
        result = self._serve("--transport", "websocket")
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
