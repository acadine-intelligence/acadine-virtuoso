"""The phase 1 operation registry and the split recall contract (docs/22).

Every operation is declared once here: name, kind (read or write), input
schema and output schema. The MCP binding and the HTTP binding in
``virtuoso.server.transports`` are generated from ``REGISTRY``, so the two
surfaces cannot drift. ``ServerCore.call`` validates each request and each
response against the registry and applies the request and response size
limits, so both bindings share one set of checks.

Boundaries the registry keeps:

- No operation accepts a workspace path, a module id, ``allow_trusted`` or
  a time. Item ids are slugs, so they cannot carry a path. The server clock
  writes every time.
- ``next_action``, ``study_load`` and ``recall_start`` never return the
  reference answer. ``recall_reveal`` returns it only after
  ``recall_respond`` stored a response for the same attempt token.
  ``history`` never returns a prompt, an answer or a response.
- ``recall_record`` requires ``graded_by`` and ``agent_help`` with no
  default. A learner grade feeds the scheduler; an agent grade is evidence
  only. A workspace with an external scheduler refuses remote recall
  writes and names the CLI path instead.
"""

from __future__ import annotations

import json
import logging
import secrets
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from ..errors import VirtuosoError
from ..practice import PracticeError, PracticeService, SupportAction
from ..queries import QueryError, item_history, workload_by_focus
from ..review import ReviewError
from ..study import StudyReviewService
from ..workspace import WorkspaceError, WorkspaceService
from .schema import SchemaError, validate

logger = logging.getLogger("virtuoso.server")

# Limits shared with the Hermes Desktop adapter (docs/20).
MAX_REQUEST_BYTES = 65_536
MAX_RESPONSE_BYTES = 1_048_576
OPERATION_TIMEOUT_SECONDS = 30.0
# How long an open recall attempt stays usable. Expiry compares times the
# server wrote (created_at, expires_at and the server clock), never a time a
# client supplied.
ATTEMPT_TTL = timedelta(minutes=60)
SURFACES = ("mcp", "http")
ERROR_SCHEMA = "virtuoso/review-error@0.1"


class OperationError(VirtuosoError):
    """A domain or contract error with a stable code and a recovery action."""

    def __init__(
        self, message: str, *, code: str, recovery: str, status: int = 409
    ) -> None:
        super().__init__(message)
        self.code = code
        self.recovery = recovery
        self.status = status

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema": ERROR_SCHEMA,
            "error": {"code": self.code, "message": str(self), "recovery": self.recovery},
        }


def _error(code: str, message: str) -> OperationError:
    status, recovery = _ERRORS[code]
    return OperationError(message, code=code, recovery=recovery, status=status)


# code: (HTTP status, recovery action). MCP reports every one as isError.
_ERRORS: dict[str, tuple[int, str]] = {
    "invalid-request": (400, "check-contract"),
    "unknown-operation": (404, "check-contract"),
    "not-found": (404, "check-contract"),
    "request-too-large": (413, "shorten-request"),
    "item-not-found": (404, "reload-item"),
    "learning-required": (409, "study-first"),
    "study-not-required": (409, "start-recall"),
    "stale-content": (409, "reload-item"),
    "attempt-not-found": (404, "start-new-attempt"),
    "surface-mismatch": (409, "start-new-attempt"),
    "attempt-expired": (410, "start-new-attempt"),
    "attempt-closed": (409, "start-new-attempt"),
    "attempt-awaiting-record": (409, "record-result"),
    "response-required": (409, "respond-first"),
    "already-responded": (409, "reveal-answer"),
    "hint-unavailable": (409, "reveal-answer"),
    "no-hint": (409, "respond-without-hint"),
    "already-recorded": (409, "advance-card"),
    "submission-conflict": (409, "use-new-submission-id"),
    "blank-demonstrated": (422, "choose-another-result"),
    "cli-required": (409, "use-cli"),
    "scheduler-changed": (409, "retry-submit"),
    "clock-order": (409, "retry-submit"),
    "workspace-busy": (503, "retry-submit"),
    "workspace-error": (500, "check-settings"),
    "response-too-large": (500, "narrow-request"),
    "operation-timeout": (504, "retry-submit"),
    "internal-error": (500, "retry-submit"),
}


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

_SLUG = "^[a-z0-9]+(?:-[a-z0-9]+)*$"
_ITEM_ID = {"type": "string", "pattern": _SLUG, "maxLength": 128,
            "description": "Item id from next_action, for example `hash-lookup`."}
_HASH = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
_NULLABLE_HASH = {"type": ["string", "null"], "pattern": "^[0-9a-f]{64}$"}
_TOKEN = {"type": "string", "pattern": "^[0-9a-f]{32}$",
          "description": "The attempt_token recall_start returned."}
_SUBMISSION = {"type": "string", "pattern": "^[0-9a-f]{32}$",
               "description": "32 lowercase hex characters chosen once per write and reused on retry."}
_TIME = {"type": "string", "minLength": 1}
_NULLABLE_TIME = {"type": ["string", "null"]}
_TEXT = {"type": "string"}
_NULLABLE_TEXT = {"type": ["string", "null"]}
_RESULT = {"type": "string", "enum": ["demonstrated", "partial", "not-demonstrated"]}
_AGENT_HELP = {"type": "string", "enum": ["none", "light", "substantial", "unknown"]}
_GRADED_BY = {"type": "string", "enum": ["learner", "agent"]}
_SURFACE = {"type": "string", "enum": list(SURFACES)}
# Printable ASCII from a fixed set: no control characters, terminal escapes
# or bidi marks can reach stored evidence or a log line.
_CLIENT = {"type": ["string", "null"], "pattern": "^[A-Za-z0-9 ._:/@+-]{1,128}$",
           "minLength": 1, "maxLength": 128,
           "description": "Your application name: 1 to 128 letters, digits, spaces or . _ : / @ + -"}


def _object(properties: dict[str, Any], required: tuple[str, ...] = ()) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


def _closed(properties: dict[str, Any]) -> dict[str, Any]:
    """An output object whose every field is always present."""
    return _object(properties, tuple(properties))


_ATTEMPT_VIEW = _closed({
    "attempt_token": _TOKEN,
    "item_id": _ITEM_ID,
    "title": _TEXT,
    "focus": _TEXT,
    "item_content_hash": _HASH,
    "prompt": _TEXT,
    "hint_available": {"type": "boolean"},
    "state": {"type": "string", "enum": ["open"]},
    "surface": _SURFACE,
    "client": _CLIENT,
    "created_at": _TIME,
    "expires_at": _TIME,
})
_RESPONSE_VIEW = _closed({
    "attempt_token": _TOKEN,
    "item_id": _ITEM_ID,
    "state": {"type": "string", "enum": ["responded"]},
    "response": _TEXT,
    "responded_at": _TIME,
    "hint_used": {"type": "boolean"},
    "expires_at": _TIME,
})
_RECORDED_ATTEMPT = _closed({
    "event_id": {"type": "string", "pattern": "^attempt-[0-9a-f]{32}$"},
    "attempt_token": _TOKEN,
    "item_id": _ITEM_ID,
    "item_content_hash": _HASH,
    "response": _TEXT,
    "result": _RESULT,
    "confidence": {"type": "integer", "minimum": 1, "maximum": 5},
    "agent_help": _AGENT_HELP,
    "graded_by": _GRADED_BY,
    "surface": _SURFACE,
    "client": _CLIENT,
    "administered": {"type": "boolean", "const": True},
    "latency_ms": {"type": "null"},
    "hint_used": {"type": "boolean"},
    "support": {"type": "array", "items": {"type": "string", "enum": ["hint"]}},
    "occurred_at": _TIME,
})
_PROPOSAL = {
    "type": ["object", "null"],
    "properties": {
        "proposal_id": _TEXT,
        "algorithm": _TEXT,
        "algorithm_version": _TEXT,
        "learning_context": _TEXT,
        "due_at": _TIME,
    },
    "required": ["proposal_id", "algorithm", "algorithm_version", "learning_context", "due_at"],
    "additionalProperties": False,
}
_STUDY_EVENT = _closed({
    "event_id": _TEXT,
    "item_id": _ITEM_ID,
    "item_content_hash": _HASH,
    "learning_unit_hash": _HASH,
    "occurred_at": _TIME,
    # A retry can return a completion an earlier client recorded.
    "surface": _TEXT,
    "claims_mastery": {"type": "boolean", "const": False},
})
_SKIP = _closed({
    "event_id": {"type": "string", "pattern": "^skip-[0-9a-f]{32}$"},
    "item_id": _ITEM_ID,
    "item_content_hash": _HASH,
    "occurred_at": _TIME,
    "surface": _SURFACE,
})
_HISTORY_ATTEMPT = _closed({
    "event_id": _TEXT,
    "item_id": _ITEM_ID,
    "result": _RESULT,
    "confidence": {"type": "integer"},
    "administered": {"type": "boolean"},
    "latency_ms": {"type": ["integer", "null"]},
    "agent_help": _AGENT_HELP,
    "occurred_at": _TIME,
    "graded_by": _GRADED_BY,
})
_ANNOTATION = _closed({
    "annotation_id": _TEXT,
    "subject_kind": _TEXT,
    "subject_id": _TEXT,
    "subject_hash": _HASH,
    "question_set_id": _TEXT,
    "question_set_hash": _HASH,
    "module_id": _TEXT,
    "module_version": _TEXT,
    "module_network": _TEXT,
    "module_receipt_id": _TEXT,
    "occurred_at": _TIME,
    "surface": _TEXT,
    "answers": {"type": "object"},
    "stale": {"type": "boolean"},
    "claims_mastery": {"type": "boolean", "const": False},
})


def _schema_const(schema_id: str) -> dict[str, Any]:
    return {"type": "string", "const": schema_id}


@dataclass(frozen=True)
class Operation:
    name: str
    kind: str  # "read" or "write"
    title: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    idempotent: bool


REGISTRY: tuple[Operation, ...] = (
    Operation(
        name="next_action",
        kind="read",
        title="Next learning action",
        description=(
            "Choose the learner's next action: `learn` (study a learning unit first) or "
            "`practice` (recall), with the item id, title and focus. It never returns the "
            "prompt or the answer. `none` means nothing is due."
        ),
        input_schema=_object({
            "focus": {"type": ["string", "null"], "minLength": 1, "maxLength": 128,
                      "description": "Optional focus track to restrict selection."},
        }),
        output_schema=_closed({
            "schema": _schema_const("virtuoso/server-next-action@0.1"),
            "action": {"type": "string", "enum": ["learn", "practice", "none"]},
            "item_id": {"type": ["string", "null"], "pattern": _SLUG},
            "title": _NULLABLE_TEXT,
            "focus": _NULLABLE_TEXT,
            "item_content_hash": _NULLABLE_HASH,
            "learning_unit_hash": _NULLABLE_HASH,
            "rationale": _TEXT,
            "alternatives": {"type": "array", "items": {"type": "string", "pattern": _SLUG}},
            "uncertainty": _NULLABLE_TEXT,
        }),
        idempotent=True,
    ),
    Operation(
        name="study_load",
        kind="read",
        title="Load a learning unit",
        description=(
            "Load the learning unit for a learn-first item so the learner can study it. "
            "It returns no recall prompt and no answer."
        ),
        input_schema=_object({"item_id": _ITEM_ID}, ("item_id",)),
        output_schema=_closed({
            "schema": _schema_const("virtuoso/study-item@0.1"),
            "item": _closed({
                "item_id": _ITEM_ID,
                "title": _TEXT,
                "focus": _TEXT,
                "content_hash": _HASH,
                "learning_unit_hash": _HASH,
                "learning_unit": _TEXT,
            }),
        }),
        idempotent=True,
    ),
    Operation(
        name="study_record",
        kind="write",
        title="Record finished study",
        description=(
            "Record that the learner finished studying the learning unit. Call it only "
            "after the learner says they finished. It records exposure only, never recall "
            "or mastery. A retry with the same hashes returns the stored completion."
        ),
        input_schema=_object({
            "item_id": _ITEM_ID,
            "item_content_hash": _HASH,
            "learning_unit_hash": _HASH,
            "completed": {"type": "boolean", "const": True},
        }, ("item_id", "item_content_hash", "learning_unit_hash", "completed")),
        output_schema=_closed({
            "schema": _schema_const("virtuoso/study-result@0.1"),
            "study": _STUDY_EVENT,
        }),
        idempotent=True,
    ),
    Operation(
        name="recall_start",
        kind="write",
        title="Start a recall attempt",
        description=(
            "Open a recall attempt and return the prompt and an attempt token. Show the "
            "prompt to the learner and do not answer it for them. The answer stays hidden "
            "until recall_respond stores the learner's own response. `client` names your "
            "application in the stored evidence. While an earlier attempt for the same item "
            "has a stored response that is not recorded yet, this fails with "
            "attempt-awaiting-record: finish that attempt with recall_record."
        ),
        input_schema=_object({"item_id": _ITEM_ID, "client": _CLIENT}, ("item_id",)),
        output_schema=_closed({
            "schema": _schema_const("virtuoso/recall-attempt@0.1"),
            "attempt": _ATTEMPT_VIEW,
        }),
        idempotent=False,
    ),
    Operation(
        name="recall_hint",
        kind="write",
        title="Show the hint",
        description=(
            "Show the item's hint, only if the learner asks for it, before they respond. "
            "The hint is recorded as support used. Repeating the call returns the same hint."
        ),
        input_schema=_object({"attempt_token": _TOKEN}, ("attempt_token",)),
        output_schema=_closed({
            "schema": _schema_const("virtuoso/recall-hint@0.1"),
            "attempt_token": _TOKEN,
            "item_id": _ITEM_ID,
            "hint": _TEXT,
            "hint_shown_at": _TIME,
        }),
        idempotent=True,
    ),
    Operation(
        name="recall_respond",
        kind="write",
        title="Store the learner's response",
        description=(
            "Store the learner's own response, transcribed as they gave it, before the "
            "answer is revealed. Do not improve or complete it. An empty response is "
            "allowed when the learner could not recall."
        ),
        input_schema=_object({
            "attempt_token": _TOKEN,
            "response": {"type": "string", "maxLength": 16_000},
        }, ("attempt_token", "response")),
        output_schema=_closed({
            "schema": _schema_const("virtuoso/recall-response@0.1"),
            "attempt": _RESPONSE_VIEW,
        }),
        idempotent=True,
    ),
    Operation(
        name="recall_reveal",
        kind="read",
        title="Reveal the answer",
        description=(
            "Return the reference answer after recall_respond stored a response for this "
            "attempt. Before that it fails with response-required."
        ),
        input_schema=_object({"attempt_token": _TOKEN}, ("attempt_token",)),
        output_schema=_closed({
            "schema": _schema_const("virtuoso/recall-reveal@0.1"),
            "attempt_token": _TOKEN,
            "item_id": _ITEM_ID,
            "answer": _TEXT,
        }),
        idempotent=True,
    ),
    Operation(
        name="recall_record",
        kind="write",
        title="Record the result",
        description=(
            "Record the result and confidence for a responded attempt. `graded_by` is "
            "`learner` when the learner chose the grade (even if you relayed it) and "
            "`agent` when you chose it; an agent grade is stored as evidence only and "
            "never changes the schedule. Report your own help honestly in `agent_help`: "
            "`none`, `light`, `substantial` or `unknown`. Reuse the same submission_id "
            "on retry; a replay returns the original record."
        ),
        input_schema=_object({
            "attempt_token": _TOKEN,
            "submission_id": _SUBMISSION,
            "result": _RESULT,
            "confidence": {"type": "integer", "minimum": 1, "maximum": 5},
            "agent_help": _AGENT_HELP,
            "graded_by": _GRADED_BY,
        }, ("attempt_token", "submission_id", "result", "confidence", "agent_help", "graded_by")),
        output_schema=_closed({
            "schema": _schema_const("virtuoso/recall-record@0.1"),
            "replayed": {"type": "boolean"},
            "attempt": _RECORDED_ATTEMPT,
            "proposal": _PROPOSAL,
        }),
        idempotent=True,
    ),
    Operation(
        name="recall_skip",
        kind="write",
        title="Skip this item",
        description=(
            "Skip an open attempt before the learner responds, when they choose not to "
            "answer. It appends a skip event, closes the attempt and leaves the due date "
            "unchanged. After recall_respond stored a response, skipping fails with "
            "attempt-awaiting-record: finish the attempt with recall_record instead."
        ),
        input_schema=_object({
            "attempt_token": _TOKEN,
            "submission_id": _SUBMISSION,
        }, ("attempt_token", "submission_id")),
        output_schema=_closed({
            "schema": _schema_const("virtuoso/recall-skip@0.1"),
            "replayed": {"type": "boolean"},
            "attempt_token": _TOKEN,
            "state": {"type": "string", "enum": ["expired"]},
            "skip": _SKIP,
        }),
        idempotent=True,
    ),
    Operation(
        name="workload",
        kind="read",
        title="Due workload",
        description="Due-now and scheduled item counts per focus.",
        input_schema=_object({}),
        output_schema=_closed({
            "schema": _schema_const("virtuoso/workload-by-focus@0.1"),
            "focuses": {"type": "array", "items": _closed({
                "focus": _TEXT,
                "items": {"type": "integer"},
                "due_now": {"type": "integer"},
                "scheduled": {"type": "integer"},
            })},
        }),
        idempotent=True,
    ),
    Operation(
        name="history",
        kind="read",
        title="Item evidence history",
        description=(
            "Recorded evidence for one item: result, confidence, help, grader and timing, "
            "plus annotations. It never contains the prompt, the answer or a response."
        ),
        input_schema=_object({"item_id": _ITEM_ID}, ("item_id",)),
        output_schema=_closed({
            "schema": _schema_const("virtuoso/item-history@0.2"),
            "item_id": _ITEM_ID,
            "attempts": {"type": "array", "items": _HISTORY_ATTEMPT},
            "annotations": {"type": "array", "items": _ANNOTATION},
        }),
        idempotent=True,
    ),
)
OPERATIONS: dict[str, Operation] = {operation.name: operation for operation in REGISTRY}

SERVER_INSTRUCTIONS = (
    "Virtuoso is the learner's practice workspace. Start with next_action. For "
    "`learn`, call study_load, let the learner study, and call study_record only "
    "after they say they finished. For `practice`, call recall_start, show the "
    "prompt, wait for the learner's own answer, store it with recall_respond, then "
    "recall_reveal, then recall_record with the learner's grade. Never answer a "
    "prompt for the learner and never reveal before they respond."
)


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise WorkspaceError(f"stored time is not timezone-aware: {value!r}")
    return parsed.astimezone(timezone.utc)


class ServerCore:
    """Runs registry operations against one workspace, for one learner.

    Calls are serialized: the server is single-learner, and serializing
    keeps each check-then-write sequence atomic with respect to other
    calls. The database transactions and triggers remain the final guard.
    """

    def __init__(
        self,
        workspace: WorkspaceService,
        *,
        clock: Callable[[], datetime] | None = None,
        attempt_ttl: timedelta = ATTEMPT_TTL,
    ) -> None:
        self.workspace = workspace
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.ttl = attempt_ttl
        self.max_response_bytes = MAX_RESPONSE_BYTES
        self._lock = threading.Lock()
        # The running call's deadline, set only while it holds the lock.
        self._deadline: float | None = None

    # -- dispatch ----------------------------------------------------------

    def call(
        self,
        name: str,
        arguments: object,
        *,
        surface: str,
        deadline: float | None = None,
    ) -> dict[str, Any]:
        """Run one operation.

        ``deadline`` is a ``time.monotonic()`` value. A binding that stops
        waiting at its operation limit passes the same instant here. The
        call checks it after taking the lock and again just before its
        write, and refuses with ``operation-timeout`` without writing once
        it has passed. So a call that waited behind a slow one, or ran out
        of time while reading, writes nothing after its client was told
        it timed out. A call already inside its write transaction when the
        deadline passes still commits; a retry with the same
        ``submission_id`` then returns the stored record.
        """
        if surface not in SURFACES:
            raise AssertionError(f"unknown binding surface: {surface!r}")
        operation = OPERATIONS.get(name)
        if operation is None:
            raise _error("unknown-operation", f"unknown operation: {name!r}"[:200])
        if arguments is None:
            arguments = {}
        try:
            size = len(json.dumps(arguments, ensure_ascii=False).encode("utf-8"))
        except (TypeError, ValueError) as exc:
            raise _error("invalid-request", "request must be JSON") from exc
        if size > MAX_REQUEST_BYTES:
            raise _error("request-too-large", "request exceeds 64 KiB")
        try:
            validate(arguments, operation.input_schema, where="request")
        except SchemaError as exc:
            raise _error("invalid-request", str(exc)) from exc
        assert isinstance(arguments, dict)
        with self._lock:
            self._deadline = deadline
            try:
                self._check_deadline()
                result = getattr(self, f"_op_{name}")(arguments, surface)
            except OperationError:
                raise
            except ReviewError as exc:
                code = exc.code if exc.code in _ERRORS else "workspace-error"
                raise _error(code, str(exc)) from exc
            except (WorkspaceError, PracticeError, QueryError) as exc:
                raise self._domain_error(str(exc)) from exc
            except sqlite3.Error as exc:
                raise self._database_error(exc) from exc
            except Exception as exc:  # pragma: no cover - defensive
                logger.exception("operation %s failed", name)
                raise _error("internal-error", "the server failed; nothing was recorded") from exc
            finally:
                self._deadline = None
        try:
            validate(result, operation.output_schema, where="response")
        except SchemaError as exc:
            logger.error("operation %s produced an invalid response: %s", name, exc)
            raise _error("internal-error", "the server produced an invalid response") from exc
        encoded = json.dumps(result, ensure_ascii=False, sort_keys=True).encode("utf-8")
        if len(encoded) > self.max_response_bytes:
            raise _error("response-too-large", "response exceeds the one-megabyte limit")
        return result

    def _check_deadline(self) -> None:
        """Refuse, writing nothing, once the caller's deadline has passed."""
        if self._deadline is not None and time.monotonic() >= self._deadline:
            raise _error(
                "operation-timeout",
                "the operation ran past its time limit and wrote nothing; "
                "retry the same request",
            )

    @staticmethod
    def _domain_error(message: str) -> OperationError:
        lowered = message.lower()
        if "awaits recall_record" in lowered:
            return _awaiting_record()
        if "has expired" in lowered:
            return _error("attempt-expired", "this recall attempt has expired")
        if "stale" in lowered or "changed after this attempt started" in lowered:
            return _error("stale-content", message)
        if "requires learning before practice" in lowered:
            return _error("learning-required", message)
        if "no learning item with id" in lowered or "learning item is retired" in lowered:
            return _error("item-not-found", message)
        if "blank recall" in lowered:
            return _error("blank-demonstrated", message)
        if "not awaiting a record" in lowered:
            return _error("already-recorded", "this attempt was already recorded")
        if "pending attempt is not open" in lowered:
            return _error("attempt-closed", "this recall attempt is closed")
        if "scheduler" in lowered and "changed" in lowered:
            return _error("scheduler-changed", message)
        if "trusted scheduler" in lowered:
            return _cli_required()
        if "locked" in lowered or "busy" in lowered:
            return _error("workspace-busy", "the workspace is busy; retry the same request")
        return _error("workspace-error", message)

    def _database_error(self, exc: sqlite3.Error) -> OperationError:
        message = str(exc)
        if isinstance(exc, sqlite3.IntegrityError):
            if "has expired" in message:
                return _error("attempt-expired", "this recall attempt has expired")
            if "transition is not allowed" in message:
                return _error("attempt-closed", "this recall attempt is closed")
        return self._domain_error(message)

    # -- shared checks -----------------------------------------------------

    def now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise AssertionError("server clock must be timezone-aware")
        return value.astimezone(timezone.utc)

    def _require_builtin_scheduler(self) -> None:
        settings = self.workspace.scheduler_settings()
        if settings.module_manifest is not None or settings.algorithm.startswith("module:"):
            raise _cli_required()

    def _load_pending(self, token: str, surface: str) -> dict[str, Any]:
        row = self.workspace.pending_attempt(token)
        if row is None:
            raise _error("attempt-not-found", "no recall attempt with this token")
        if row["surface"] != surface:
            raise _error(
                "surface-mismatch",
                "finish an attempt on the binding that started it",
            )
        return row

    def _require_unexpired(self, row: dict[str, Any], now: datetime) -> None:
        if now > _parse_time(row["expires_at"]):
            raise _error("attempt-expired", "this recall attempt has expired")

    @staticmethod
    def _require_after(now: datetime, *stored: str | None) -> None:
        for value in stored:
            if value is not None and now < _parse_time(value):
                raise _error(
                    "clock-order",
                    "the server clock is earlier than a time already recorded for "
                    "this attempt; retry when the clock is correct",
                )

    def _current_item(self, row: dict[str, Any]):
        item = self.workspace.load_item(row["item_id"])
        if item.content_hash != row["item_content_hash"]:
            raise _error(
                "stale-content",
                "the item changed after this attempt started; start a new attempt",
            )
        return item

    def _require_skippable(self, row: dict[str, Any], now: datetime) -> None:
        """Only an open attempt, with no stored response, can be skipped.

        Once the learner responded (and may have seen the answer), the
        attempt is finished by recording it, so a failed recall cannot be
        erased by skipping it and starting again.
        """
        if row["state"] == "responded":
            self._require_unexpired(row, now)
            raise _awaiting_record()
        if row["state"] != "open":
            raise self._closed_state_error(row)
        self._require_unexpired(row, now)

    def _require_no_attempt_awaiting_record(
        self, item_id: str, content_hash: str, now: datetime
    ) -> None:
        """Refuse a new attempt while an earlier one awaits its record.

        An attempt that expires unrecorded records nothing (docs/22) and
        stops blocking a new start once ``expires_at`` passes. An attempt
        for an earlier item version cannot be recorded, so it does not block.
        """
        waiting = self.workspace.attempt_awaiting_record(item_id, content_hash, now=now)
        if waiting is not None:
            raise _awaiting_record(waiting["expires_at"])

    @staticmethod
    def _closed_state_error(row: dict[str, Any]) -> OperationError:
        if row["state"] == "recorded":
            return _error("already-recorded", "this attempt was already recorded")
        return _error("attempt-closed", "this recall attempt is closed")

    # -- operations --------------------------------------------------------

    def _op_next_action(self, arguments: dict[str, Any], surface: str) -> dict[str, Any]:
        focus = arguments.get("focus")
        try:
            selection = self.workspace.select_next(self.now(), focus=focus)
        except WorkspaceError as exc:
            if not str(exc).startswith("no learning item is due"):
                raise
            return {
                "schema": "virtuoso/server-next-action@0.1",
                "action": "none",
                "item_id": None,
                "title": None,
                "focus": focus,
                "item_content_hash": None,
                "learning_unit_hash": None,
                "rationale": str(exc),
                "alternatives": [],
                "uncertainty": None,
            }
        return {
            "schema": "virtuoso/server-next-action@0.1",
            "action": selection.action,
            "item_id": selection.item.item_id,
            "title": selection.item.title,
            "focus": selection.item.focus,
            "item_content_hash": selection.item.content_hash,
            "learning_unit_hash": selection.item.learning_unit_hash,
            "rationale": selection.rationale,
            "alternatives": list(selection.alternatives),
            "uncertainty": selection.uncertainty,
        }

    def _op_study_load(self, arguments: dict[str, Any], surface: str) -> dict[str, Any]:
        try:
            return StudyReviewService(self.workspace).load(arguments["item_id"])
        except ReviewError as exc:
            if exc.code == "invalid-request":
                raise _error("study-not-required", str(exc)) from exc
            raise

    def _op_study_record(self, arguments: dict[str, Any], surface: str) -> dict[str, Any]:
        keys = ("item_id", "item_content_hash", "learning_unit_hash")
        now = self.now()
        self._check_deadline()
        try:
            event = self.workspace.record_study_completion(
                item_id=arguments["item_id"],
                item_content_hash=arguments["item_content_hash"],
                learning_unit_hash=arguments["learning_unit_hash"],
                occurred_at=now,
                surface=surface,
            )
        except WorkspaceError as exc:
            # The core checks current hashes before uniqueness, so this is a
            # retry of a completion that already landed: return it.
            if "already completed" not in str(exc):
                raise
            event = next(
                (
                    row for row in self.workspace.list_study_events()
                    if all(row[key] == arguments[key] for key in keys)
                ),
                None,
            )
            if event is None:
                raise
        return {"schema": "virtuoso/study-result@0.1", "study": event}

    def _op_recall_start(self, arguments: dict[str, Any], surface: str) -> dict[str, Any]:
        self._require_builtin_scheduler()
        item = self.workspace.load_item(arguments["item_id"])
        self.workspace.require_practice_ready(item.item_id)
        now = self.now()
        self._require_no_attempt_awaiting_record(item.item_id, item.content_hash, now)
        self._check_deadline()
        row = self.workspace.create_pending_attempt(
            attempt_token=secrets.token_hex(16),
            item_id=item.item_id,
            item_content_hash=item.content_hash,
            prompt_shown=item.prompt,
            surface=surface,
            client=arguments.get("client"),
            created_at=now,
            expires_at=now + self.ttl,
        )
        return {
            "schema": "virtuoso/recall-attempt@0.1",
            "attempt": {
                "attempt_token": row["attempt_token"],
                "item_id": row["item_id"],
                "title": item.title,
                "focus": item.focus,
                "item_content_hash": row["item_content_hash"],
                "prompt": row["prompt_shown"],
                "hint_available": bool(item.hint),
                "state": row["state"],
                "surface": row["surface"],
                "client": row["client"],
                "created_at": row["created_at"],
                "expires_at": row["expires_at"],
            },
        }

    def _op_recall_hint(self, arguments: dict[str, Any], surface: str) -> dict[str, Any]:
        token = arguments["attempt_token"]
        row = self._load_pending(token, surface)
        if row["hint_shown_at"] is None:
            if row["state"] == "responded":
                raise _error(
                    "hint-unavailable",
                    "a hint is available only before the response; reveal the answer instead",
                )
            if row["state"] != "open":
                raise self._closed_state_error(row)
            now = self.now()
            self._require_unexpired(row, now)
            self._require_after(now, row["created_at"])
            item = self._current_item(row)
            if not item.hint:
                raise _error("no-hint", "this item has no hint")
            self._check_deadline()
            row = self.workspace.record_pending_hint(
                attempt_token=token, hint_shown_at=now
            )
        else:
            item = self._current_item(row)
        return {
            "schema": "virtuoso/recall-hint@0.1",
            "attempt_token": token,
            "item_id": row["item_id"],
            "hint": item.hint or "",
            "hint_shown_at": row["hint_shown_at"],
        }

    def _response_view(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "schema": "virtuoso/recall-response@0.1",
            "attempt": {
                "attempt_token": row["attempt_token"],
                "item_id": row["item_id"],
                "state": "responded",
                "response": row["response"],
                "responded_at": row["responded_at"],
                "hint_used": row["hint_shown_at"] is not None,
                "expires_at": row["expires_at"],
            },
        }

    def _op_recall_respond(self, arguments: dict[str, Any], surface: str) -> dict[str, Any]:
        token = arguments["attempt_token"]
        response = arguments["response"]
        row = self._load_pending(token, surface)
        if row["state"] == "responded":
            if row["response"] == response:
                return self._response_view(row)
            raise _error("already-responded", "a response is already stored for this attempt")
        if row["state"] != "open":
            raise self._closed_state_error(row)
        now = self.now()
        self._require_unexpired(row, now)
        self._require_after(now, row["created_at"], row["hint_shown_at"])
        self._current_item(row)
        self._check_deadline()
        row = self.workspace.record_pending_response(
            attempt_token=token, response=response, responded_at=now
        )
        return self._response_view(row)

    def _op_recall_reveal(self, arguments: dict[str, Any], surface: str) -> dict[str, Any]:
        token = arguments["attempt_token"]
        row = self._load_pending(token, surface)
        if row["state"] == "open":
            raise _error(
                "response-required",
                "store the learner's response with recall_respond before revealing the answer",
            )
        if row["state"] == "expired":
            raise _error("attempt-closed", "this recall attempt is closed")
        if row["state"] == "responded":
            self._require_unexpired(row, self.now())
        item = self._current_item(row)
        return {
            "schema": "virtuoso/recall-reveal@0.1",
            "attempt_token": token,
            "item_id": row["item_id"],
            "answer": item.answer,
        }

    def _record_view(
        self, evidence: dict[str, Any], row: dict[str, Any], *, replayed: bool
    ) -> dict[str, Any]:
        attempt = evidence["attempt"]
        support = [
            action["kind"] for action in attempt["support_actions"]
            if action.get("kind") == "hint"
        ]
        proposal = evidence["proposal"]
        return {
            "schema": "virtuoso/recall-record@0.1",
            "replayed": replayed,
            "attempt": {
                "event_id": attempt["event_id"],
                "attempt_token": row["attempt_token"],
                "item_id": attempt["item_id"],
                "item_content_hash": attempt["item_content_hash"],
                "response": attempt["initial_response"],
                "result": attempt["result"],
                "confidence": attempt["confidence"],
                "agent_help": attempt["agent_help"],
                "graded_by": attempt["graded_by"],
                "surface": attempt["surface"],
                "client": attempt["client"],
                "administered": attempt["administered"],
                "latency_ms": attempt["initial_latency_ms"],
                "hint_used": bool(support),
                "support": support,
                "occurred_at": attempt["occurred_at"],
            },
            "proposal": None if proposal is None else {
                "proposal_id": proposal["proposal_id"],
                "algorithm": proposal["algorithm"],
                "algorithm_version": proposal["algorithm_version"],
                "learning_context": proposal["learning_context"],
                "due_at": proposal["due_at"],
            },
        }

    def _replayed_record(
        self, arguments: dict[str, Any], event_id: str, token: str
    ) -> dict[str, Any] | None:
        evidence = self.workspace.attempt_evidence(event_id)
        if evidence is None:
            return None
        row = self.workspace.pending_attempt(token)
        attempt = evidence["attempt"]
        same = row is not None and row["attempt_event_id"] == event_id and all(
            attempt[key] == arguments[key]
            for key in ("result", "confidence", "agent_help", "graded_by")
        )
        if not same:
            raise _error(
                "submission-conflict",
                "this submission_id was already used with different values",
            )
        assert row is not None
        return self._record_view(evidence, row, replayed=True)

    def _op_recall_record(self, arguments: dict[str, Any], surface: str) -> dict[str, Any]:
        token = arguments["attempt_token"]
        event_id = f"attempt-{arguments['submission_id']}"
        row = self._load_pending(token, surface)
        replayed = self._replayed_record(arguments, event_id, token)
        if replayed is not None:
            return replayed
        if row["state"] == "open":
            raise _error(
                "response-required",
                "store the learner's response with recall_respond before recording",
            )
        if row["state"] != "responded":
            raise self._closed_state_error(row)
        self._require_builtin_scheduler()
        now = self.now()
        self._require_unexpired(row, now)
        self._require_after(now, row["created_at"], row["responded_at"], row["hint_shown_at"])
        if arguments["result"] == "demonstrated" and not row["response"].strip():
            raise _error(
                "blank-demonstrated",
                "a blank response cannot be recorded as demonstrated; "
                "record partial or not-demonstrated",
            )
        support = (SupportAction(kind="hint"),) if row["hint_shown_at"] else ()
        self._check_deadline()
        try:
            PracticeService(self.workspace).run_administered(
                item_id=row["item_id"],
                response=row["response"],
                result=arguments["result"],
                confidence=arguments["confidence"],
                agent_help=arguments["agent_help"],
                now=now,
                allow_trusted=False,
                graded_by=arguments["graded_by"],
                surface=row["surface"],
                client=row["client"],
                event_id=event_id,
                support_actions=support,
                expected_content_hash=row["item_content_hash"],
                pending_attempt={
                    "attempt_token": token,
                    "recorded_at": now.isoformat(),
                },
            )
        except sqlite3.IntegrityError as exc:
            if "attempts.event_id" in str(exc):
                replayed = self._replayed_record(arguments, event_id, token)
                if replayed is not None:
                    return replayed
            raise
        evidence = self.workspace.attempt_evidence(event_id)
        stored = self.workspace.pending_attempt(token)
        assert evidence is not None and stored is not None
        return self._record_view(evidence, stored, replayed=False)

    def _op_recall_skip(self, arguments: dict[str, Any], surface: str) -> dict[str, Any]:
        token = arguments["attempt_token"]
        event_id = f"skip-{arguments['submission_id']}"
        row = self._load_pending(token, surface)
        existing = self.workspace.review_skip(event_id)
        if existing is not None:
            # A replay must name the attempt this skip closed.
            owner = existing.pop("pending_attempt_token")
            if (
                owner == token
                and row["state"] == "expired"
                and existing["item_id"] == row["item_id"]
                and existing["item_content_hash"] == row["item_content_hash"]
            ):
                return {
                    "schema": "virtuoso/recall-skip@0.1",
                    "replayed": True,
                    "attempt_token": token,
                    "state": "expired",
                    "skip": existing,
                }
            raise _error(
                "submission-conflict",
                "this submission_id was already used for another skip",
            )
        now = self.now()
        self._require_skippable(row, now)
        self._require_after(now, row["created_at"], row["responded_at"], row["hint_shown_at"])
        self._current_item(row)
        self._check_deadline()
        skip = self.workspace.record_review_skip(
            event_id=event_id,
            item_id=row["item_id"],
            item_content_hash=row["item_content_hash"],
            occurred_at=now.isoformat(),
            surface=row["surface"],
            pending_attempt_token=token,
        )
        return {
            "schema": "virtuoso/recall-skip@0.1",
            "replayed": False,
            "attempt_token": token,
            "state": "expired",
            "skip": skip,
        }

    def _op_workload(self, arguments: dict[str, Any], surface: str) -> dict[str, Any]:
        settings = self.workspace.scheduler_settings()
        return {
            "schema": "virtuoso/workload-by-focus@0.1",
            "focuses": workload_by_focus(
                self.workspace.db_path,
                now=self.now(),
                algorithm=settings.algorithm,
                learning_context=settings.learning_context,
            ),
        }

    def _op_history(self, arguments: dict[str, Any], surface: str) -> dict[str, Any]:
        item_id = arguments["item_id"]
        return {
            "schema": "virtuoso/item-history@0.2",
            "item_id": item_id,
            "attempts": [
                asdict(entry) for entry in item_history(self.workspace.db_path, item_id)
            ],
            "annotations": self.workspace.list_annotations(
                subject_kind="item", subject_id=item_id
            ),
        }


def _awaiting_record(expires_at: str | None = None) -> OperationError:
    message = (
        "an attempt for this item has a stored response that is not recorded; "
        "finish it with recall_record"
    )
    if expires_at is not None:
        message += f" (it expires at {expires_at})"
    return _error("attempt-awaiting-record", message)


def _cli_required() -> OperationError:
    return _error(
        "cli-required",
        "this workspace uses an external scheduler, which only the CLI may run; "
        "record recall with `virtuoso --workspace PATH practice --item ITEM "
        "--allow-trusted-scheduler` on the workspace machine",
    )
