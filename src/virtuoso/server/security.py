"""HTTP authentication and request checks for ``virtuoso serve --transport http``.

Plain ASGI with no third-party import, so the base test job proves it.
Every HTTP request passes these checks before any route runs, in order:

1. A CORS preflight (``OPTIONS``) gets 403. The server sends no CORS headers.
2. ``Host`` must name the bind address or a configured host name, else 403.
   This blocks DNS rebinding to a loopback port.
3. ``Origin``, when present, must be on the configured allow list, else 403.
   Non-browser MCP clients send no ``Origin``, which is allowed.
4. ``Authorization: Bearer <token>`` must match the configured token,
   compared in constant time, else 401. The token is never logged or echoed.
5. The request body must fit in 64 KiB, else 413.

A refused request never reaches an operation, so it writes nothing.
"""

from __future__ import annotations

import hmac
import ipaddress
import json
import os
import stat
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable, Mapping

from .operations import ERROR_SCHEMA, MAX_REQUEST_BYTES

TOKEN_ENV = "VIRTUOSO_SERVER_TOKEN"
MIN_TOKEN_LENGTH = 32
MAX_TOKEN_LENGTH = 512

Scope = dict[str, Any]
Message = dict[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


class ServerConfigError(ValueError):
    """The server cannot start safely with this configuration."""


def load_token(*, environ: Mapping[str, str], token_file: Path | None) -> str:
    """Return the bearer token from ``token_file`` or ``VIRTUOSO_SERVER_TOKEN``.

    A token file wins over the environment. The file must be a regular,
    non-symlinked file readable by its owner only. Error messages never
    include the token.
    """
    if token_file is not None:
        path = Path(token_file)
        try:
            info = path.lstat()
        except OSError as exc:
            raise ServerConfigError(f"cannot read the token file: {exc.strerror}") from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise ServerConfigError("the token file must be a regular file, not a link")
        if os.name != "nt" and info.st_mode & 0o077:
            raise ServerConfigError(
                "the token file must be readable by its owner only (chmod 600)"
            )
        try:
            token = path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError) as exc:
            raise ServerConfigError("cannot read the token file as UTF-8 text") from exc
        source = "the token file"
    else:
        token = environ.get(TOKEN_ENV, "").strip()
        source = TOKEN_ENV
        if not token:
            raise ServerConfigError(
                "HTTP serving needs a bearer token on every address, loopback "
                f"included: set {TOKEN_ENV} or pass --token-file"
            )
    if not MIN_TOKEN_LENGTH <= len(token) <= MAX_TOKEN_LENGTH:
        raise ServerConfigError(
            f"the token from {source} must be {MIN_TOKEN_LENGTH} to "
            f"{MAX_TOKEN_LENGTH} characters"
        )
    if any(character.isspace() or not character.isprintable() for character in token):
        raise ServerConfigError(f"the token from {source} must be printable with no spaces")
    return token


def _host_literal(host: str) -> str:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host.lower()
    return f"[{address}]" if address.version == 6 else str(address)


def allowed_host_names(
    host: str, port: int, extra_hosts: Iterable[str]
) -> frozenset[str]:
    """``Host`` header values this server answers to.

    The bind address with its port, ``localhost`` with the port for a
    loopback bind, and each configured name exactly and with the port.
    """
    literal = _host_literal(host)
    names = {f"{literal}:{port}"}
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host.lower() == "localhost"
    if loopback:
        names.add(f"localhost:{port}")
    for name in extra_hosts:
        cleaned = name.strip().lower()
        if cleaned:
            names.add(cleaned)
            if not cleaned.endswith("]") and ":" not in cleaned:
                names.add(f"{cleaned}:{port}")
    return frozenset(names)


def _error_body(code: str, message: str, recovery: str) -> bytes:
    return json.dumps(
        {
            "schema": ERROR_SCHEMA,
            "error": {"code": code, "message": message, "recovery": recovery},
        },
        sort_keys=True,
    ).encode("utf-8")


async def send_json(
    send: Send, status: int, body: bytes, extra_headers: Iterable[tuple[bytes, bytes]] = ()
) -> None:
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [
            (b"content-type", b"application/json"),
            (b"cache-control", b"no-store"),
            (b"content-length", str(len(body)).encode("ascii")),
            *extra_headers,
        ],
    })
    await send({"type": "http.response.body", "body": body})


class HttpGuard:
    """ASGI middleware enforcing the checks in the module docstring."""

    def __init__(
        self,
        app: ASGIApp | None,
        *,
        token: str,
        allowed_hosts: frozenset[str],
        allowed_origins: Iterable[str],
        max_body_bytes: int = MAX_REQUEST_BYTES,
    ) -> None:
        self.app = app
        self._token = token.encode("utf-8")
        self._hosts = frozenset(name.lower() for name in allowed_hosts)
        self._origins = frozenset(origin.rstrip("/") for origin in allowed_origins)
        self._max_body = max_body_bytes

    def __repr__(self) -> str:  # never show the token
        return f"HttpGuard(hosts={sorted(self._hosts)}, origins={sorted(self._origins)})"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            assert self.app is not None
            await self.app(scope, receive, send)
            return
        headers: dict[str, list[str]] = {}
        for key, value in scope.get("headers", []):
            headers.setdefault(key.decode("latin-1").lower(), []).append(
                value.decode("latin-1")
            )

        def single(name: str) -> str | None:
            values = headers.get(name)
            if not values:
                return None
            return values[0] if len(values) == 1 else "\x00"

        if scope.get("method") == "OPTIONS":
            await send_json(send, 403, _error_body(
                "forbidden", "cross-origin requests are not served", "check-origin"))
            return
        host = single("host")
        if host is None or host.lower() not in self._hosts:
            await send_json(send, 403, _error_body(
                "forbidden", "this Host is not served", "check-host"))
            return
        origin = single("origin")
        if origin is not None and origin.rstrip("/") not in self._origins:
            await send_json(send, 403, _error_body(
                "forbidden", "this Origin is not allowed", "check-origin"))
            return
        if not self._authorized(single("authorization")):
            await send_json(
                send, 401,
                _error_body("unauthorized", "a valid bearer token is required", "check-token"),
                [(b"www-authenticate", b"Bearer")],
            )
            return
        declared = single("content-length")
        if declared is not None:
            try:
                too_large = int(declared) > self._max_body
            except ValueError:
                too_large = True
            if too_large:
                await self._too_large(send)
                return
        body = bytearray()
        trailing: Message | None = None
        while True:
            message = await receive()
            if message["type"] != "http.request":
                trailing = message
                break
            body.extend(message.get("body", b""))
            if len(body) > self._max_body:
                await self._too_large(send)
                return
            if not message.get("more_body", False):
                break
        replay: list[Message] = [{"type": "http.request", "body": bytes(body), "more_body": False}]
        if trailing is not None:
            replay.append(trailing)

        async def buffered() -> Message:
            if replay:
                return replay.pop(0)
            return await receive()

        assert self.app is not None
        await self.app(scope, buffered, send)

    def _authorized(self, header: str | None) -> bool:
        if header is None or not header.startswith("Bearer "):
            # Compare anyway so a missing header costs the same time.
            hmac.compare_digest(self._token, b"")
            return False
        supplied = header[len("Bearer "):].strip().encode("utf-8", "replace")
        return hmac.compare_digest(self._token, supplied)

    @staticmethod
    async def _too_large(send: Send) -> None:
        await send_json(send, 413, _error_body(
            "request-too-large", "request exceeds 64 KiB", "shorten-request"))
