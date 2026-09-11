"""Hermes REST adapter. Only the installed Virtuoso CLI writes learning state."""

from __future__ import annotations

import functools
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import selectors
import shutil
import signal
import subprocess
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool


router = APIRouter()
_INSTANCE = secrets.token_hex(32)
TIMEOUT = 30
MAX_BYTES = 1_000_000


class PluginError(Exception):
    def __init__(self, message: str, code: str = "setup-required", recovery: str = "check-settings"):
        super().__init__(message)
        self.code = code
        self.recovery = recovery


def configuration() -> tuple[Path, dict]:
    # Resolve on each request, after Hermes has selected the backend/profile.
    from hermes_cli.config import load_config
    from hermes_constants import get_hermes_home

    config = load_config()
    settings = config.get("plugins", {}).get("entries", {}).get("virtuoso", {}).get("settings", {})
    return get_hermes_home(), settings


def response(value: dict) -> JSONResponse:
    return JSONResponse(value, headers={"Cache-Control": "no-store"})


def error_response(error: PluginError) -> JSONResponse:
    return response({
        "schema": "virtuoso/desktop-error@0.1",
        "error": {"code": error.code, "message": str(error), "recovery": error.recovery},
    })


def handled(function):
    @functools.wraps(function)
    def call(*args, **kwargs):
        try:
            return response(function(*args, **kwargs))
        except PluginError as exc:
            return error_response(exc)
    return call


def configured() -> dict:
    home, settings = configuration()
    if not isinstance(settings, dict):
        raise PluginError("Set Virtuoso executable and workspace settings on the Hermes backend.")
    workspace = settings.get("workspace")
    executable = settings.get("executable", "virtuoso")
    if not isinstance(workspace, str) or not workspace.strip():
        raise PluginError("Set plugins.entries.virtuoso.settings.workspace on the Hermes backend.")
    if not isinstance(executable, str) or not executable.strip():
        raise PluginError("Set plugins.entries.virtuoso.settings.executable on the Hermes backend.")
    root = Path(workspace).expanduser()
    candidate = str(Path(executable).expanduser())
    cli = shutil.which(candidate)
    if cli is None:
        raise PluginError("Virtuoso CLI is unavailable on the backend. Set its installed executable path.")
    if not root.is_absolute() or not root.is_dir():
        raise PluginError("The backend workspace must be an existing absolute directory.")
    root = root.resolve()
    cli = str(Path(cli).absolute())
    try:
        database = (root / ".virtuoso" / "state.sqlite3").stat()
    except OSError as exc:
        raise PluginError("The configured directory has no readable Virtuoso database. Check the backend workspace.") from exc
    identity = json.dumps(
        [_INSTANCE, str(home.resolve()), str(root), cli, database.st_dev, database.st_ino],
        separators=(",", ":"),
    )
    return {
        "root": root, "cli": cli,
        "context_id": hashlib.sha256(identity.encode()).hexdigest(),
    }


def require_context(context_id: str) -> dict:
    config = configured()
    if context_id != config["context_id"]:
        raise PluginError(
            "The backend connection or workspace changed. Reconnect before continuing.",
            "context-changed", "reconnect",
        )
    return config


def run_cli(config: dict, args: list[str], body: dict | None = None) -> tuple[int, str, str]:
    """Bound both streams and stdin without blocking on an unread input pipe."""
    argv = [config["cli"], f"--workspace={config['root']}", *args, "--json"]
    data = json.dumps(body, allow_nan=False).encode() if body is not None else b""
    env = {k: v for k, v in os.environ.items() if k not in {"PYTHONPATH", "PYTHONHOME"}}
    try:
        process = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            shell=False, start_new_session=True, env=env,
        )
    except OSError as exc:
        raise PluginError("The configured Virtuoso executable could not start.") from exc
    streams = {"stdout": bytearray(), "stderr": bytearray()}
    started = time.monotonic()
    try:
        with selectors.DefaultSelector() as selector:
            for stream, name in ((process.stdout, "stdout"), (process.stderr, "stderr")):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, name)
            if data:
                os.set_blocking(process.stdin.fileno(), False)
                selector.register(process.stdin, selectors.EVENT_WRITE, "stdin")
            else:
                process.stdin.close()
            while selector.get_map():
                remaining = TIMEOUT - (time.monotonic() - started)
                if remaining <= 0:
                    raise PluginError("Virtuoso timed out. Retry the same submission.", "timeout", "retry-submit")
                for key, _mask in selector.select(min(remaining, 0.1)):
                    if key.data == "stdin":
                        try:
                            data = data[os.write(key.fd, data[:8192]):]
                        except BrokenPipeError:
                            data = b""
                        if not data:
                            selector.unregister(key.fileobj)
                            key.fileobj.close()
                    else:
                        chunk = os.read(key.fd, 65_536)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        streams[key.data].extend(chunk)
                        if sum(map(len, streams.values())) > MAX_BYTES:
                            raise PluginError("Virtuoso returned too much output.", "output-limit", "check-settings")
            remaining = TIMEOUT - (time.monotonic() - started)
            try:
                code = process.wait(timeout=max(remaining, 0.001))
            except subprocess.TimeoutExpired as exc:
                raise PluginError("Virtuoso timed out. Retry the same submission.", "timeout", "retry-submit") from exc
        return code, streams["stdout"].decode("utf-8"), streams["stderr"].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PluginError("Virtuoso returned invalid text.", "schema-failure") from exc
    finally:
        # Also close descendants holding pipes after the parent exits.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()


def invoke(config: dict, args: list[str], schema: str, body: dict | None = None) -> dict | None:
    code, stdout, stderr = run_cli(config, args, body)
    if code != 0:
        if args == ["next"] and stderr.strip() == "Error: no learning item is due; add an item or return later":
            return None
        try:
            failure = json.loads(stderr)
        except ValueError:
            failure = None
        if isinstance(failure, dict) and failure.get("schema") == "virtuoso/review-error@0.1":
            error = failure.get("error", {})
            if isinstance(error, dict) and all(isinstance(error.get(k), str) for k in ("message", "code", "recovery")):
                message = error["message"].replace(str(config["root"]), "<workspace>")
                raise PluginError(message[:500], error["code"], error["recovery"])
        raise PluginError("Virtuoso could not complete the command. Check the backend CLI version and workspace.", "cli-failed")
    try:
        payload = json.loads(stdout)
    except (ValueError, RecursionError) as exc:
        raise PluginError("Virtuoso returned invalid JSON.", "schema-failure") from exc
    if not isinstance(payload, dict) or payload.get("schema") != schema:
        raise PluginError("The backend CLI returned an unsupported response. Update Virtuoso.", "schema-failure")
    return payload


@router.get("/context")
@handled
def context():
    config = configured()
    return {
        "schema": "virtuoso/desktop-context@0.1",
        "context_id": config["context_id"],
        "workspace_name": config["root"].name,
    }


@router.get("/next")
@handled
def next_item(context_id: str = ""):
    config = require_context(context_id)
    selection = invoke(config, ["next"], "virtuoso/next-action@0.1")
    result = {"schema": "virtuoso/desktop-item@0.1", "context_id": context_id, "action": None, "item": None, "reason": "Nothing is due. Return later or add an item with the CLI."}
    if selection is None:
        return result
    item_id = selection.get("item_id")
    action = selection.get("action")
    if not isinstance(item_id, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", item_id) or action not in ("learn", "practice"):
        raise PluginError("Virtuoso returned an invalid next action.", "schema-failure")
    operation, schema = {
        "learn": ("study-load", "virtuoso/study-item@0.1"),
        "practice": ("load", "virtuoso/review-item@0.1"),
    }[action]
    loaded = invoke(require_context(context_id), ["review", operation, f"--item={item_id}"], schema)
    item = loaded.get("item")
    if not isinstance(item, dict) or item.get("item_id") != item_id or item.get("content_hash") != selection.get("item_content_hash"):
        raise PluginError("The selected item changed. Reload it before continuing.", "stale-content", "reload-item")
    require_context(context_id)
    return {**result, "action": action, "item": item, "reason": selection.get("rationale", "")}


_WRITES = {
    "study": ("study-record", "virtuoso/study-result@0.1"),
    "record": ("record", "virtuoso/review-attempt-result@0.1"),
    "skip": ("skip", "virtuoso/review-skip-result@0.1"),
}


def reject_constant(_value: str):
    raise ValueError("Non-finite JSON numbers are unsupported.")


def submit(operation: str, payload: dict) -> dict:
    if not isinstance(payload, dict) or set(payload) != {"context_id", "request"} or not isinstance(payload["request"], dict):
        raise PluginError("Invalid submission envelope.", "invalid-request", "check-contract")
    config = require_context(payload["context_id"])
    command, schema = _WRITES[operation]
    return invoke(config, ["review", command], schema, payload["request"])


@router.post("/study")
@router.post("/record")
@router.post("/skip")
async def write(request: Request):
    try:
        if request.headers.get("content-type", "").split(";", 1)[0].strip() != "application/json":
            raise PluginError("Send JSON for this action.", "invalid-request", "check-contract")
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 65_536:
                raise PluginError("Submission exceeds 64 KiB.", "invalid-request", "check-contract")
        try:
            payload = json.loads(raw, parse_constant=reject_constant)
        except (ValueError, RecursionError) as exc:
            raise PluginError("Submission must be valid JSON.", "invalid-request", "check-contract") from exc
        operation = request.url.path.rsplit("/", 1)[-1]
        return response(await run_in_threadpool(submit, operation, payload))
    except PluginError as exc:
        return error_response(exc)
