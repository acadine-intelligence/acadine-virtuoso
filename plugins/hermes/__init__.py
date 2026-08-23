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
import logging
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_WORKSPACE = str(Path.home() / ".virtuoso" / "workspace")
TIMEOUT_S = 30


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


def _run(ctx, *argv: str) -> str:
    cli = _cli()
    if not cli:
        return json.dumps({"success": False, "error": "virtuoso CLI not found on PATH"})
    ws = _workspace(ctx)
    cmd = [cli, "--workspace", ws, *argv, "--json"]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return json.dumps({"success": False, "error": f"virtuoso timed out after {TIMEOUT_S}s", "command": cmd})
    if proc.returncode != 0:
        return json.dumps({
            "success": False,
            "error": proc.stderr.strip() or proc.stdout.strip() or f"exit {proc.returncode}",
            "command": cmd,
        })
    out = proc.stdout.strip()
    try:
        payload = json.loads(out)
        return json.dumps({"success": True, "data": payload})
    except (json.JSONDecodeError, ValueError):
        return json.dumps({"success": True, "data": out})


# ---------- tool entry points ----------

def virtuoso_due(ctx, focus: str = "") -> str:
    """What is due right now: FSRS recall items plus delayed transfer checks."""
    args = ["next"]
    if focus:
        args += ["--focus", focus]
    nxt = json.loads(_run(ctx, *args))
    transfer = json.loads(_run(ctx, "transfer", "check", "due"))
    return json.dumps({"success": True, "recommended_next": nxt.get("data"), "transfer_checks_due": transfer.get("data")})


def virtuoso_next(ctx, focus: str = "") -> str:
    """The single recommended practice item (Virtuoso recommendation policy)."""
    args = ["next"]
    if focus:
        args += ["--focus", focus]
    return _run(ctx, *args)


def virtuoso_transfer_record(ctx, item_id: str, project: str, use_case: str,
                             outcome: str, independence: str = "guided",
                             reflection: str = "") -> str:
    """Record a project-transfer event: learner applied a practiced concept in real work."""
    if outcome not in ("successful", "partial", "unsuccessful"):
        return json.dumps({"success": False, "error": "outcome must be successful|partial|unsuccessful"})
    if independence not in ("independent", "guided", "agent-produced", "unknown"):
        return json.dumps({"success": False, "error": "independence must be independent|guided|agent-produced|unknown"})
    args = ["transfer", "record", "--item", item_id, "--project", project,
            "--use-case", use_case, "--outcome", outcome, "--independence", independence]
    if reflection:
        args += ["--reflection", reflection]
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
            "description": "Show Virtuoso work due now: recommended next recall item plus delayed transfer checks. Read-only.",
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
            "description": "Get the single recommended Virtuoso practice item per the recommendation policy (due retention first, then transfer, then new material). Read-only.",
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
                    "item_id": {"type": "string", "description": "Virtuoso item id that was practiced."},
                    "project": {"type": "string", "description": "The real project where the concept was applied."},
                    "use_case": {"type": "string", "description": "What the learner did with the concept in that project."},
                    "outcome": {"type": "string", "enum": ["successful", "partial", "unsuccessful"]},
                    "independence": {"type": "string", "enum": ["independent", "guided", "agent-produced", "unknown"], "default": "guided"},
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
