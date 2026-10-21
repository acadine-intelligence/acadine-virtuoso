"""Packaging, security and binding contracts for the optional server.

Nothing here needs the [server] extra. The HTTP guard is plain ASGI and
runs against a stub application, so its token, Host, Origin, preflight
and size checks are proven in the base job too.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from virtuoso.server.security import (
    HttpGuard,
    ServerConfigError,
    allowed_host_names,
    load_token,
)

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "s" * 40


class BaseInstallContractTests(unittest.TestCase):
    def test_base_dependencies_are_unchanged_and_the_extra_is_pinned(self) -> None:
        project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
        self.assertEqual(
            project["dependencies"], ["fsrs==6.3.2", "typing-extensions==4.16.0"]
        )
        self.assertEqual(
            project["optional-dependencies"],
            {"server": ["mcp==2.3.0", "uvicorn==0.41.0"]},
        )
        self.assertEqual(
            (ROOT / "requirements.lock").read_text().split(),
            ["fsrs==6.3.2", "typing-extensions==4.16.0"],
        )

    def test_lock_keeps_server_packages_out_of_the_base_dependency_set(self) -> None:
        lock = tomllib.loads((ROOT / "uv.lock").read_text())
        project = next(
            package for package in lock["package"]
            if package["name"] == "acadine-virtuoso"
        )
        self.assertEqual(
            sorted(dependency["name"] for dependency in project["dependencies"]),
            ["fsrs", "typing-extensions"],
        )
        self.assertEqual(
            sorted(
                dependency["name"]
                for dependency in project["optional-dependencies"]["server"]
            ),
            ["mcp", "uvicorn"],
        )
        versions = {package["name"]: package["version"] for package in lock["package"]}
        self.assertEqual(versions["mcp"], "2.3.0")
        self.assertEqual(versions["uvicorn"], "0.41.0")

    def test_cli_and_core_server_modules_import_no_server_dependency(self) -> None:
        probe = (
            "import json, sys\n"
            "import virtuoso.cli, virtuoso.server, virtuoso.server.operations,"
            " virtuoso.server.security\n"
            "print(json.dumps(sorted(name for name in ('mcp', 'uvicorn', 'starlette')"
            " if name in sys.modules)))\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", probe], capture_output=True, text=True, timeout=60
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [])

    def test_serve_without_the_extra_exits_2_and_names_the_install_command(self) -> None:
        probe = (
            "import sys\n"
            "for name in ('mcp', 'uvicorn', 'starlette'):\n"
            "    sys.modules[name] = None\n"
            "from virtuoso.cli import main\n"
            "sys.exit(main(['--workspace', sys.argv[1], 'serve', '--transport', sys.argv[2]]))\n"
        )
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary).resolve() / "learner"
            from virtuoso.workspace import WorkspaceService

            WorkspaceService.init(workspace)
            for transport in ("stdio", "http"):
                with self.subTest(transport=transport):
                    result = subprocess.run(
                        [sys.executable, "-c", probe, str(workspace), transport],
                        capture_output=True, text=True, timeout=60,
                        env={**os.environ, "VIRTUOSO_SERVER_TOKEN": TOKEN},
                    )
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertIn('pip install "acadine-virtuoso[server]"', result.stderr)
                    self.assertNotIn("Traceback", result.stderr)
                    self.assertEqual(result.stdout, "")


class ServerDocumentationTests(unittest.TestCase):
    def test_hint_protection_contract_and_recovery_are_documented(self) -> None:
        from virtuoso.server.operations import OPERATIONS, _ERRORS

        self.assertEqual(
            _ERRORS.get("attempt-hinted"),
            (409, "respond-and-record-or-wait-for-expiry"),
        )
        for name in ("recall_start", "recall_hint", "recall_skip"):
            with self.subTest(operation=name):
                self.assertIn("attempt-hinted", OPERATIONS[name].description)
        for name in ("12-cli-reference.md", "22-server-and-mcp-design.md"):
            with self.subTest(document=name):
                document = (ROOT / "docs" / name).read_text(encoding="utf-8")
                self.assertIn("attempt-hinted", document)
                self.assertIn("pending_attempt_warnings", document)
                self.assertNotIn("holds nothing to lose", document)
                self.assertNotIn("Nothing is lost, because the closed attempt holds no response", document)

    def test_reference_documents_the_command_and_every_operation(self) -> None:
        from virtuoso.server.operations import OPERATIONS

        reference = (ROOT / "docs" / "12-cli-reference.md").read_text(encoding="utf-8")
        server = reference.split("## Server (optional)", 1)[1].split("## Retrieval", 1)[0]
        self.assertIn("### `serve`", server)
        self.assertIn('pip install "acadine-virtuoso[server]"', server)
        self.assertIn("VIRTUOSO_SERVER_TOKEN", server)
        for name in OPERATIONS:
            with self.subTest(operation=name):
                self.assertIn(f"| `{name}` |", server)
        from virtuoso.server.operations import _ERRORS

        errors = server.split("#### Limits and errors", 1)[1]
        for code in _ERRORS:
            with self.subTest(code=code):
                self.assertIn(f"`{code}`", errors)
        self.assertIn("same `submission_id`", errors)
        self.assertIn("writes nothing", errors)
        design = (ROOT / "docs" / "22-server-and-mcp-design.md").read_text(encoding="utf-8")
        self.assertIn("Phase 1 server core is built", design)
        self.assertIn("Expiry compares only times the server wrote", design)
        agents = (ROOT / "docs" / "13-agent-usage.md").read_text(encoding="utf-8")
        self.assertIn("## MCP server or CLI", agents)


class TokenLoadingTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def test_missing_token_refuses_to_start(self) -> None:
        with self.assertRaises(ServerConfigError) as caught:
            load_token(environ={}, token_file=None)
        self.assertIn("VIRTUOSO_SERVER_TOKEN", str(caught.exception))
        self.assertIn("--token-file", str(caught.exception))

    def test_short_token_refuses_to_start_without_echoing_it(self) -> None:
        with self.assertRaises(ServerConfigError) as caught:
            load_token(environ={"VIRTUOSO_SERVER_TOKEN": "short-secret"}, token_file=None)
        self.assertNotIn("short-secret", str(caught.exception))

    def test_environment_token_loads(self) -> None:
        self.assertEqual(
            load_token(environ={"VIRTUOSO_SERVER_TOKEN": TOKEN}, token_file=None), TOKEN
        )

    def test_private_token_file_loads_and_wins_over_the_environment(self) -> None:
        path = self.directory / "token"
        path.write_text("f" * 40 + "\n")
        path.chmod(0o600)
        self.assertEqual(
            load_token(environ={"VIRTUOSO_SERVER_TOKEN": TOKEN}, token_file=path),
            "f" * 40,
        )

    @unittest.skipIf(os.name == "nt", "POSIX permission bits")
    def test_group_or_world_readable_token_file_is_refused(self) -> None:
        path = self.directory / "token"
        path.write_text("f" * 40 + "\n")
        path.chmod(0o644)
        with self.assertRaises(ServerConfigError) as caught:
            load_token(environ={}, token_file=path)
        self.assertNotIn("f" * 40, str(caught.exception))

    def test_symlinked_or_missing_token_file_is_refused(self) -> None:
        target = self.directory / "target"
        target.write_text("f" * 40)
        target.chmod(0o600)
        link = self.directory / "link"
        link.symlink_to(target)
        for path in (link, self.directory / "absent"):
            with self.subTest(path=path.name):
                with self.assertRaises(ServerConfigError):
                    load_token(environ={}, token_file=path)

    def test_non_ascii_token_refuses_to_start(self) -> None:
        for token in ("\u00e9" * 40, "a" * 39 + "\u0430", "b" * 40 + "\u200b"):
            with self.subTest(token=token):
                with self.assertRaises(ServerConfigError) as caught:
                    load_token(environ={"VIRTUOSO_SERVER_TOKEN": token}, token_file=None)
                self.assertIn("ASCII", str(caught.exception))
                self.assertNotIn(token, str(caught.exception))
        path = self.directory / "token"
        path.write_text("\u00e9" * 40, encoding="utf-8")
        path.chmod(0o600)
        with self.assertRaises(ServerConfigError):
            load_token(environ={}, token_file=path)

    @unittest.skipIf(os.name == "nt", "POSIX ownership")
    def test_token_file_owned_by_another_user_is_refused(self) -> None:
        path = self.directory / "token"
        path.write_text("f" * 40)
        path.chmod(0o600)
        other = os.geteuid() + 1
        with mock.patch("virtuoso.server.security.os.geteuid", return_value=other):
            with self.assertRaises(ServerConfigError) as caught:
                load_token(environ={}, token_file=path)
        self.assertIn("owned by the user running the server", str(caught.exception))

    @unittest.skipIf(os.name == "nt", "POSIX file types")
    def test_token_file_is_checked_and_read_through_one_descriptor(self) -> None:
        path = self.directory / "token"
        path.write_text("f" * 40 + "\n")
        path.chmod(0o600)
        checked: list[int] = []
        real_fstat = os.fstat

        def fstat(descriptor: int):
            checked.append(descriptor)
            return real_fstat(descriptor)

        # Reading by path again after the checks would let a swapped file
        # through; the loader must use only the descriptor it checked.
        with mock.patch.object(Path, "read_text", side_effect=AssertionError("path read")), \
                mock.patch.object(Path, "read_bytes", side_effect=AssertionError("path read")), \
                mock.patch.object(Path, "lstat", side_effect=AssertionError("path stat")), \
                mock.patch("virtuoso.server.security.os.fstat", side_effect=fstat):
            self.assertEqual(load_token(environ={}, token_file=path), "f" * 40)
        self.assertEqual(len(checked), 1)
        fifo = self.directory / "fifo"
        os.mkfifo(fifo, 0o600)
        for special in (fifo, self.directory):
            with self.subTest(path=special.name):
                with self.assertRaises(ServerConfigError):
                    load_token(environ={}, token_file=special)


def _run_guard(guard: HttpGuard, *, method: str = "POST", path: str = "/v1/ops/workload",
               headers: dict[str, str] | None = None, body: bytes = b"{}") -> dict:
    calls: list[str] = []

    async def app(scope, receive, send):
        received = b""
        while True:
            message = await receive()
            received += message.get("body", b"")
            if not message.get("more_body"):
                break
        calls.append(received.decode())
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    guard.app = app
    raw_headers = [
        (key.lower().encode("latin-1"), value.encode("latin-1"))
        for key, value in (headers or {}).items()
    ]
    scope = {
        "type": "http", "method": method, "path": path, "headers": raw_headers,
        "query_string": b"", "http_version": "1.1", "scheme": "http",
    }
    chunks = [body[index:index + 4096] for index in range(0, len(body), 4096)] or [b""]
    messages = [
        {"type": "http.request", "body": chunk, "more_body": index < len(chunks) - 1}
        for index, chunk in enumerate(chunks)
    ]
    sent: list[dict] = []

    async def receive():
        return messages.pop(0) if messages else {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    asyncio.run(guard(scope, receive, send))
    start = next(message for message in sent if message["type"] == "http.response.start")
    payload = b"".join(
        message.get("body", b"") for message in sent if message["type"] == "http.response.body"
    )
    return {
        "status": start["status"],
        "headers": {key.decode(): value.decode() for key, value in start["headers"]},
        "body": payload.decode(),
        "app_calls": calls,
    }


class HttpGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.guard = HttpGuard(
            None,
            token=TOKEN,
            allowed_hosts=allowed_host_names("127.0.0.1", 8765, ()),
            allowed_origins=("https://chat.example",),
        )
        self.good = {
            "Host": "127.0.0.1:8765",
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json",
        }

    def test_valid_request_reaches_the_application(self) -> None:
        result = _run_guard(self.guard, headers=self.good)
        self.assertEqual(result["status"], 200)
        self.assertEqual(result["app_calls"], ["{}"])

    def test_missing_or_wrong_token_gets_401_and_never_reaches_the_app(self) -> None:
        for authorization in (None, "Bearer wrong-token", f"Basic {TOKEN}", f"Bearer {TOKEN}x"):
            headers = dict(self.good)
            if authorization is None:
                del headers["Authorization"]
            else:
                headers["Authorization"] = authorization
            with self.subTest(authorization=authorization):
                result = _run_guard(self.guard, headers=headers)
                self.assertEqual(result["status"], 401)
                self.assertEqual(result["app_calls"], [])
                self.assertEqual(result["headers"]["www-authenticate"], "Bearer")
                self.assertNotIn(TOKEN, result["body"])
                self.assertEqual(
                    json.loads(result["body"])["error"]["code"], "unauthorized"
                )

    def test_foreign_host_gets_403(self) -> None:
        for host in ("evil.example:8765", "127.0.0.1:9999", None):
            headers = dict(self.good)
            if host is None:
                del headers["Host"]
            else:
                headers["Host"] = host
            with self.subTest(host=host):
                result = _run_guard(self.guard, headers=headers)
                self.assertEqual(result["status"], 403)
                self.assertEqual(result["app_calls"], [])

    def test_present_unlisted_origin_gets_403_and_absent_origin_is_allowed(self) -> None:
        for origin in ("https://evil.example", "null", "https://chat.example.evil"):
            with self.subTest(origin=origin):
                foreign = _run_guard(self.guard, headers={**self.good, "Origin": origin})
                self.assertEqual(foreign["status"], 403)
                self.assertEqual(foreign["app_calls"], [])
        listed = _run_guard(self.guard, headers={**self.good, "Origin": "https://chat.example"})
        self.assertEqual(listed["status"], 200)
        self.assertNotIn("access-control-allow-origin", listed["headers"])

    def test_websocket_and_unknown_scopes_never_reach_the_app(self) -> None:
        calls: list[str] = []

        async def app(scope, receive, send):
            calls.append(scope["type"])

        self.guard.app = app
        for scope_type in ("websocket", "webtransport", "custom"):
            with self.subTest(scope=scope_type):
                sent: list[dict] = []
                inbox = [{"type": "websocket.connect"}]

                async def receive():
                    return inbox.pop(0) if inbox else {"type": "websocket.disconnect"}

                async def send(message):
                    sent.append(message)

                scope = {
                    "type": scope_type, "path": "/mcp",
                    "headers": [
                        (b"host", b"127.0.0.1:8765"),
                        (b"authorization", f"Bearer {TOKEN}".encode()),
                    ],
                }
                asyncio.run(self.guard(scope, receive, send))
                self.assertEqual(calls, [])
                if scope_type == "websocket":
                    self.assertEqual(sent, [{"type": "websocket.close", "code": 1008}])

    def test_lifespan_scope_still_reaches_the_app(self) -> None:
        calls: list[str] = []

        async def app(scope, receive, send):
            calls.append(scope["type"])

        self.guard.app = app

        async def receive():
            return {"type": "lifespan.shutdown"}

        async def send(message):
            pass

        asyncio.run(self.guard({"type": "lifespan"}, receive, send))
        self.assertEqual(calls, ["lifespan"])

    def test_cors_preflight_gets_403(self) -> None:
        result = _run_guard(self.guard, method="OPTIONS", headers={
            "Host": "127.0.0.1:8765",
            "Origin": "https://chat.example",
            "Access-Control-Request-Method": "POST",
        })
        self.assertEqual(result["status"], 403)
        self.assertEqual(result["app_calls"], [])
        self.assertFalse(any(key.startswith("access-control") for key in result["headers"]))

    def test_body_over_64_kib_gets_413_before_the_app(self) -> None:
        body = b'{"x": "' + b"a" * 70_000 + b'"}'
        result = _run_guard(self.guard, headers=self.good, body=body)
        self.assertEqual(result["status"], 413)
        self.assertEqual(result["app_calls"], [])
        declared = _run_guard(
            self.guard, headers={**self.good, "Content-Length": str(len(body))}, body=body
        )
        self.assertEqual(declared["status"], 413)

    def test_loopback_bind_accepts_localhost_names_only_for_its_port(self) -> None:
        names = allowed_host_names("127.0.0.1", 8765, ())
        self.assertEqual(names, frozenset({"127.0.0.1:8765", "localhost:8765"}))
        extra = allowed_host_names("0.0.0.0", 443, ("learn.example",))
        self.assertEqual(
            extra, frozenset({"0.0.0.0:443", "learn.example", "learn.example:443"})
        )


if __name__ == "__main__":
    unittest.main()
