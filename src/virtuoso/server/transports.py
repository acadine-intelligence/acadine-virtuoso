"""MCP and HTTP bindings generated from the operation registry (docs/22).

Needs the ``[server]`` extra. Both bindings call ``ServerCore.call`` with
the registry's operation name and the caller's JSON arguments, so the MCP
tool list and the ``POST /v1/ops/<name>`` routes cannot drift apart:

- MCP (stdio, or streamable HTTP at ``/mcp``): one tool per operation, with
  the registry's input and output schemas. Success returns the record as
  text and structured content. A domain error returns the error envelope
  with ``isError: true``.
- HTTP JSON (``POST /v1/ops/<name>``): the arguments object as the body,
  the record as a 200 response, or the error envelope with its 4xx or 5xx
  status.

Each call runs in a worker thread with the 30-second operation limit.
"""

from __future__ import annotations

import contextlib
import json
import logging
import sys
from collections.abc import AsyncIterator
from typing import Any

import anyio
import anyio.to_thread
import mcp_types as types
import uvicorn
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.server.streamable_http_manager import StreamableHTTPASGIApp, StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from .. import __version__
from .operations import (
    MAX_REQUEST_BYTES,
    OPERATION_TIMEOUT_SECONDS,
    REGISTRY,
    SERVER_INSTRUCTIONS,
    OperationError,
    ServerCore,
    _error,
)
from .security import HttpGuard, allowed_host_names

logger = logging.getLogger("virtuoso.server")


async def run_operation(
    core: ServerCore, name: str, arguments: object, *, surface: str
) -> tuple[int, dict[str, Any]]:
    """Run one registry operation and return (HTTP status, payload)."""
    try:
        with anyio.fail_after(OPERATION_TIMEOUT_SECONDS):
            payload = await anyio.to_thread.run_sync(
                lambda: core.call(name, arguments, surface=surface),
                abandon_on_cancel=True,
            )
        return 200, payload
    except OperationError as exc:
        return exc.status, exc.to_payload()
    except TimeoutError:
        error = _error(
            "operation-timeout",
            "the operation took longer than 30 seconds; retry the same request",
        )
        return error.status, error.to_payload()


def _tools() -> list[types.Tool]:
    return [
        types.Tool(
            name=operation.name,
            title=operation.title,
            description=operation.description,
            input_schema=operation.input_schema,
            output_schema=operation.output_schema,
            annotations=types.ToolAnnotations(
                title=operation.title,
                read_only_hint=operation.kind == "read",
                destructive_hint=False,
                idempotent_hint=operation.idempotent,
                open_world_hint=False,
            ),
        )
        for operation in REGISTRY
    ]


def build_mcp_server(core: ServerCore, *, surface: str = "mcp") -> Server:
    async def list_tools(ctx: Any, params: Any) -> types.ListToolsResult:
        return types.ListToolsResult(tools=_tools())

    async def call_tool(ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        status, payload = await run_operation(
            core, params.name, params.arguments or {}, surface=surface
        )
        text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        content = [types.TextContent(type="text", text=text)]
        if status == 200:
            return types.CallToolResult(content=content, structured_content=payload)
        return types.CallToolResult(content=content, is_error=True)

    return Server(
        "virtuoso",
        version=__version__,
        instructions=SERVER_INSTRUCTIONS,
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )


def build_http_app(
    core: ServerCore,
    *,
    token: str,
    host: str,
    port: int,
    allowed_hosts: tuple[str, ...] = (),
    allowed_origins: tuple[str, ...] = (),
) -> HttpGuard:
    """The HTTP application: MCP at ``/mcp`` and JSON at ``/v1/ops/<name>``."""
    hosts = allowed_host_names(host, port, allowed_hosts)
    session_manager = StreamableHTTPSessionManager(
        app=build_mcp_server(core, surface="mcp"),
        json_response=True,
        stateless=True,
        security_settings=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=sorted(hosts),
            allowed_origins=sorted(allowed_origins),
        ),
        max_request_body_size=MAX_REQUEST_BYTES,
    )

    async def operation_route(request: Request) -> Response:
        name = request.path_params["name"]
        if request.method != "POST":
            return _json_response(405, _error(
                "invalid-request", "use POST with a JSON object body").to_payload())
        content_type = request.headers.get("content-type", "")
        if not content_type.lower().startswith("application/json"):
            return _json_response(415, _error(
                "invalid-request", "the body must be application/json").to_payload())
        raw = await request.body()
        try:
            arguments = json.loads(raw.decode("utf-8")) if raw.strip() else {}
        except (UnicodeDecodeError, ValueError, RecursionError):
            error = _error("invalid-request", "the body must be a JSON object")
            return _json_response(error.status, error.to_payload())
        if not isinstance(arguments, dict):
            error = _error("invalid-request", "the body must be a JSON object")
            return _json_response(error.status, error.to_payload())
        status, payload = await run_operation(core, name, arguments, surface="http")
        return _json_response(status, payload)

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        async with session_manager.run():
            yield

    app = Starlette(
        routes=[
            Route("/mcp", endpoint=StreamableHTTPASGIApp(session_manager)),
            Route("/v1/ops/{name}", endpoint=operation_route, methods=["POST", "GET", "PUT", "DELETE"]),
        ],
        lifespan=lifespan,
    )
    return HttpGuard(
        app,
        token=token,
        allowed_hosts=hosts,
        allowed_origins=allowed_origins,
    )


def _json_response(status: int, payload: dict[str, Any]) -> Response:
    return Response(
        json.dumps(payload, ensure_ascii=False, sort_keys=True),
        status_code=status,
        media_type="application/json",
        headers={"Cache-Control": "no-store"},
    )


def serve_stdio(core: ServerCore) -> int:
    """Serve MCP on stdin and stdout until the client closes the stream."""
    server = build_mcp_server(core, surface="mcp")

    async def main() -> None:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())

    anyio.run(main)
    return 0


def serve_http(
    core: ServerCore,
    *,
    token: str,
    host: str,
    port: int,
    allowed_hosts: tuple[str, ...],
    allowed_origins: tuple[str, ...],
) -> int:
    app = build_http_app(
        core,
        token=token,
        host=host,
        port=port,
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
    )
    print(
        f"Virtuoso server on http://{host}:{port} (MCP at /mcp, JSON at /v1/ops/<name>)",
        file=sys.stderr,
        flush=True,
    )
    # Access logs are off: request lines can carry attempt tokens in a
    # client's mistaken query string, and nothing here needs them.
    uvicorn.run(app, host=host, port=port, log_level="warning", access_log=False,
                server_header=False, lifespan="on")
    return 0
