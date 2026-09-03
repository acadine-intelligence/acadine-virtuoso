"""Virtuoso Hermes plugin.

Thin wrappers around the installed ``virtuoso`` CLI. The plugin never
reimplements scheduling logic — it shells out with ``--json`` and passes the
CLI's structured output through, so behaviour always matches the canonical
workspace (default ``~/.virtuoso/workspace``, override with the
``workspace`` plugin setting).

Scheduling ownership reminder (2026-07-24 architecture decision): Virtuoso
says WHAT is due; the project system says which project matters; the Obsidian
SR plugin owns atomic flashcards. These tools only expose Virtuoso's view.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

DEFAULT_WORKSPACE = str(Path.home() / ".virtuoso" / "workspace")
TIMEOUT_S = 30
_PRODUCT_ID_PATTERN = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
_PRODUCT_ID = re.compile(_PRODUCT_ID_PATTERN)


def _workspace(ctx) -> str:
    try:
        value = ctx.get_config("workspace")
    except Exception:  # pragma: no cover — defensive against config shape drift
        value = None
    if isinstance(value, str) and value.strip():
        return str(Path(value.strip()).expanduser())
    return DEFAULT_WORKSPACE


def _cli() -> str | None:
    return shutil.which("virtuoso")


def check_virtuoso_available() -> bool:
    """Service gate: only advertise these tools when the CLI is installed."""
    return _cli() is not None


def _error(message: str) -> str:
    return json.dumps({"success": False, "error": message})


def _product_id_error(value: object, *, field: str) -> str | None:
    if isinstance(value, str) and _PRODUCT_ID.fullmatch(value):
        return None
    return _error(
        f"{field} must be lowercase words or numbers separated by single dashes"
    )


def _focus_option(focus: object) -> tuple[str | None, str | None]:
    if focus is None:
        return None, None
    if not isinstance(focus, str) or not focus.strip():
        return None, _error("focus must be a non-empty string")
    return f"--focus={focus.strip()}", None


def _run(ctx, *argv: str) -> str:
    cli = _cli()
    if not cli:
        return _error("virtuoso CLI not found on PATH")
    ws = _workspace(ctx)
    cmd = [cli, f"--workspace={ws}", *argv, "--json"]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return _error(f"virtuoso timed out after {TIMEOUT_S}s")
    except OSError as exc:
        reason = exc.strerror or exc.__class__.__name__
        return _error(f"virtuoso could not start: {reason}")
    if proc.returncode != 0:
        message = proc.stderr.strip() or proc.stdout.strip() or f"exit {proc.returncode}"
        return _error(message)
    out = proc.stdout.strip()
    if not out:
        return _error("virtuoso returned empty JSON output")
    try:
        payload = json.loads(out)
    except json.JSONDecodeError:
        return _error("virtuoso returned invalid JSON")
    if not isinstance(payload, dict):
        return _error("virtuoso returned non-object JSON")
    return json.dumps({"success": True, "data": payload})


# ---------- tool entry points ----------

def virtuoso_due(ctx, focus: str | None = None) -> str:
    """What is due right now: FSRS recall items plus delayed transfer checks."""
    focus_option, focus_error = _focus_option(focus)
    if focus_error:
        return focus_error
    args = ["next"]
    if focus_option:
        args.append(focus_option)
    nxt = json.loads(_run(ctx, *args))
    if not nxt.get("success"):
        nxt["component"] = "recommended_next"
        return json.dumps(nxt)
    transfer = json.loads(_run(ctx, "transfer", "check", "due"))
    if not transfer.get("success"):
        transfer["component"] = "transfer_checks_due"
        return json.dumps(transfer)
    return json.dumps(
        {
            "success": True,
            "recommended_next": nxt.get("data"),
            "transfer_checks_due": transfer.get("data"),
        }
    )


def virtuoso_next(ctx, focus: str | None = None) -> str:
    """The single recommended learning or practice action."""
    focus_option, focus_error = _focus_option(focus)
    if focus_error:
        return focus_error
    args = ["next"]
    if focus_option:
        args.append(focus_option)
    return _run(ctx, *args)


def virtuoso_transfer_record(ctx, item_id: str, project: str, use_case: str,
                             outcome: str, independence: str = "guided",
                             reflection: str = "", artifact: str = "") -> str:
    """Record a project-transfer event: learner applied a practiced concept in real work."""
    item_error = _product_id_error(item_id, field="item id")
    if item_error:
        return item_error
    project_error = _product_id_error(project, field="project id")
    if project_error:
        return project_error
    if outcome not in ("successful", "partial", "unsuccessful"):
        return _error("outcome must be successful|partial|unsuccessful")
    if independence not in ("independent", "guided", "agent-produced", "unknown"):
        return _error(
            "independence must be independent|guided|agent-produced|unknown"
        )
    args = [
        "transfer",
        "record",
        f"--item={item_id}",
        f"--project={project}",
        f"--use-case={use_case}",
        f"--outcome={outcome}",
        f"--independence={independence}",
    ]
    if artifact:
        args.append(f"--artifact={artifact}")
    if reflection:
        args.append(f"--reflection={reflection}")
    return _run(ctx, *args)


def virtuoso_status(ctx) -> str:
    """Workspace health: item counts, due-now workload, stale links."""
    return _run(ctx, "doctor")


# ---------- registration ----------

_TOOLS = (
    (
        "virtuoso_due",
        {
            "name": "virtuoso_due",
            "description": "Show Virtuoso work due now: typed next learning or practice action plus delayed transfer checks. Read-only.",
            "parameters": {
                "type": "object",
                "properties": {
                    "focus": {"type": "string", "description": "Optional focus track to restrict selection."},
                },
            },
        },
        virtuoso_due,
        "🎯",
    ),
    (
        "virtuoso_next",
        {
            "name": "virtuoso_next",
            "description": "Get the single typed Virtuoso learning or practice action with its selection reason. Read-only.",
            "parameters": {
                "type": "object",
                "properties": {
                    "focus": {"type": "string", "description": "Optional focus track to restrict selection."},
                },
            },
        },
        virtuoso_next,
        "⏭",
    ),
    (
        "virtuoso_transfer_record",
        {
            "name": "virtuoso_transfer_record",
            "description": "Record a project-transfer event after the learner applies a practiced concept in real project work. This is the evidence that advances capability state — schedule ratings alone do not.",
            "parameters": {
                "type": "object",
                "properties": {
                    "item_id": {
                        "type": "string",
                        "pattern": _PRODUCT_ID_PATTERN,
                        "description": "Virtuoso item id that was practiced.",
                    },
                    "project": {
                        "type": "string",
                        "pattern": _PRODUCT_ID_PATTERN,
                        "description": "The real project where the concept was applied.",
                    },
                    "use_case": {"type": "string", "description": "What the learner did with the concept in that project."},
                    "outcome": {"type": "string", "enum": ["successful", "partial", "unsuccessful"]},
                    "independence": {"type": "string", "enum": ["independent", "guided", "agent-produced", "unknown"], "default": "guided"},
                    "artifact": {
                        "type": "string",
                        "description": "Optional reference to evidence from the real project.",
                    },
                    "reflection": {"type": "string", "description": "Optional learner reflection note."},
                },
                "required": ["item_id", "project", "use_case", "outcome"],
            },
        },
        virtuoso_transfer_record,
        "📝",
    ),
    (
        "virtuoso_status",
        {
            "name": "virtuoso_status",
            "description": "Virtuoso workspace health: item counts, due-now workload, stale source links. Read-only.",
            "parameters": {"type": "object", "properties": {}},
        },
        virtuoso_status,
        "🩺",
    ),
)


def register(ctx) -> None:
    """Register Virtuoso tools. Called once when enabled via ``plugins.enabled``."""
    for name, schema, handler, emoji in _TOOLS:
        def _make(h):
            def _handler(args, **_kw):
                return h(ctx, **{k: v for k, v in args.items() if v is not None})
            return _handler
        ctx.register_tool(
            name=name,
            toolset="virtuoso",
            schema=schema,
            handler=_make(handler),
            check_fn=check_virtuoso_available,
            emoji=emoji,
        )
