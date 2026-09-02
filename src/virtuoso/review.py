from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .errors import VirtuosoError
from .practice import PracticeError, PracticeResult, PracticeService, SupportAction
from .workspace import WorkspaceError, WorkspaceService


REVIEW_QUEUE_SCHEMA = "virtuoso/review-queue@0.1"
REVIEW_ITEM_SCHEMA = "virtuoso/review-item@0.1"
REVIEW_ATTEMPT_SCHEMA = "virtuoso/review-attempt@0.1"
REVIEW_ATTEMPT_RESULT_SCHEMA = "virtuoso/review-attempt-result@0.1"
REVIEW_SKIP_SCHEMA = "virtuoso/review-skip@0.1"
REVIEW_SKIP_RESULT_SCHEMA = "virtuoso/review-skip-result@0.1"
_SUBMISSION_ID = re.compile(r"^[0-9a-f]{32}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RESULTS = {"demonstrated", "partial", "not-demonstrated"}


class ReviewError(VirtuosoError):
    def __init__(self, message: str, *, code: str, recovery: str) -> None:
        super().__init__(message)
        self.code = code
        self.recovery = recovery

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema": "virtuoso/review-error@0.1",
            "error": {
                "code": self.code,
                "message": str(self),
                "recovery": self.recovery,
            },
        }


class ReviewContractError(ReviewError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="invalid-request", recovery="check-contract")


@dataclass(frozen=True)
class ReviewQueueItem:
    item_id: str
    content_hash: str
    focus: str
    project_ids: tuple[str, ...]
    selection_reason: str
    status: str
    due_at: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "content_hash": self.content_hash,
            "focus": self.focus,
            "project_ids": list(self.project_ids),
            "selection_reason": self.selection_reason,
            "status": self.status,
            "due_at": self.due_at,
        }


@dataclass(frozen=True)
class ReviewItemSnapshot:
    item_id: str
    title: str
    focus: str
    content_hash: str
    prompt: str
    answer: str
    hint: str | None
    follow_up: str | None
    learning_context: str

    def to_dict(self) -> dict[str, str | None]:
        return {
            "item_id": self.item_id,
            "title": self.title,
            "focus": self.focus,
            "content_hash": self.content_hash,
            "prompt": self.prompt,
            "answer": self.answer,
            "hint": self.hint,
            "follow_up": self.follow_up,
            "learning_context": self.learning_context,
        }


class ReviewService:
    """Stable local review contracts over the core workspace."""

    def __init__(self, workspace: WorkspaceService) -> None:
        self.workspace = workspace

    def load(self, item_id: str) -> ReviewItemSnapshot:
        item = self.workspace.load_item(item_id)
        return ReviewItemSnapshot(
            item_id=item.item_id,
            title=item.title,
            focus=item.focus,
            content_hash=item.content_hash,
            prompt=item.prompt,
            answer=item.answer,
            hint=item.hint,
            follow_up=item.follow_up,
            learning_context=item.learning_context,
        )

    def record(self, raw_request: str) -> PracticeResult:
        request = self._request_object(raw_request)
        expected = {
            "schema",
            "submission_id",
            "item_id",
            "item_content_hash",
            "started_at",
            "initial_answered_at",
            "completed_at",
            "initial_response",
            "retry",
            "hint_used",
            "answer_revealed",
            "result",
            "confidence",
            "open_notes",
        }
        if set(request) != expected:
            raise ReviewContractError("review attempt request fields do not match the contract")
        if request["schema"] != REVIEW_ATTEMPT_SCHEMA:
            raise ReviewContractError(
                f"unsupported review attempt schema: {request['schema']!r}"
            )
        submission_id = request["submission_id"]
        if not isinstance(submission_id, str) or not _SUBMISSION_ID.fullmatch(
            submission_id
        ):
            raise ReviewContractError("submission_id must be 32 lowercase hexadecimal characters")
        for name in ("item_id", "item_content_hash", "initial_response", "result"):
            if not isinstance(request[name], str):
                raise ReviewContractError(f"{name} must be a string")
        if not request["item_id"]:
            raise ReviewContractError("item_id must be non-empty")
        if not _SHA256.fullmatch(request["item_content_hash"]):
            raise ReviewContractError(
                "item_content_hash must be 64 lowercase hexadecimal characters"
            )
        if request["result"] not in _RESULTS:
            raise ReviewContractError(
                "result must be demonstrated, partial, or not-demonstrated"
            )
        confidence = request["confidence"]
        if (
            not isinstance(confidence, int)
            or isinstance(confidence, bool)
            or not 1 <= confidence <= 5
        ):
            raise ReviewContractError("confidence must be an integer from 1 to 5")
        if not isinstance(request["open_notes"], bool):
            raise ReviewContractError("open_notes must be true or false")
        if not isinstance(request["hint_used"], bool):
            raise ReviewContractError("hint_used must be true or false")
        if request["answer_revealed"] is not True:
            raise ReviewContractError("answer_revealed must be true before grading")

        support: list[SupportAction] = []
        retry = request["retry"]
        if retry is not None:
            if not isinstance(retry, dict) or set(retry) != {"response", "latency_ms"}:
                raise ReviewContractError("retry must be null or contain response and latency_ms")
            response = retry["response"]
            latency_ms = retry["latency_ms"]
            if not isinstance(response, str):
                raise ReviewContractError("retry response must be a string")
            if (
                not isinstance(latency_ms, int)
                or isinstance(latency_ms, bool)
                or latency_ms < 0
            ):
                raise ReviewContractError("retry latency_ms must be a non-negative integer")
            support.append(
                SupportAction(
                    kind="retry-unaided", response=response, latency_ms=latency_ms
                )
            )
        if request["hint_used"]:
            support.append(SupportAction(kind="hint"))
        support.append(SupportAction(kind="worked-feedback"))

        started_at = self._timestamp(request["started_at"], "started_at")
        initial_answered_at = self._timestamp(
            request["initial_answered_at"], "initial_answered_at"
        )
        completed_at = self._timestamp(request["completed_at"], "completed_at")
        if initial_answered_at < started_at:
            raise ReviewContractError("initial_answered_at must not precede started_at")
        if completed_at < initial_answered_at:
            raise ReviewContractError(
                "completed_at must not precede initial_answered_at"
            )

        try:
            return PracticeService(self.workspace).run_direct(
                event_id=f"attempt-{submission_id}",
                item_id=request["item_id"],
                item_content_hash=request["item_content_hash"],
                started_at=started_at,
                initial_answered_at=initial_answered_at,
                completed_at=completed_at,
                initial_response=request["initial_response"],
                result=request["result"],
                confidence=confidence,
                open_notes=request["open_notes"],
                support_actions=tuple(support),
            )
        except PracticeError as exc:
            if "stale" in str(exc).lower():
                raise ReviewError(
                    str(exc), code="stale-content", recovery="reload-item"
                ) from exc
            raise ReviewError(
                str(exc), code="record-failed", recovery="retry-submit"
            ) from exc
        except sqlite3.IntegrityError as exc:
            if "attempts.event_id" in str(exc):
                raise ReviewError(
                    "this review submission was already recorded",
                    code="already-recorded",
                    recovery="advance-card",
                ) from exc
            raise ReviewError(
                f"review attempt could not be recorded: {exc}",
                code="record-failed",
                recovery="retry-submit",
            ) from exc

    def skip(self, raw_request: str) -> dict[str, str]:
        request = self._request_object(raw_request)
        expected = {
            "schema",
            "submission_id",
            "item_id",
            "item_content_hash",
            "occurred_at",
            "surface",
        }
        if set(request) != expected:
            raise ReviewContractError("review skip request fields do not match the contract")
        if request["schema"] != REVIEW_SKIP_SCHEMA:
            raise ReviewContractError(f"unsupported review skip schema: {request['schema']!r}")
        submission_id = request["submission_id"]
        if not isinstance(submission_id, str) or not _SUBMISSION_ID.fullmatch(
            submission_id
        ):
            raise ReviewContractError("submission_id must be 32 lowercase hexadecimal characters")
        for name in ("item_id", "item_content_hash", "surface"):
            if not isinstance(request[name], str):
                raise ReviewContractError(f"{name} must be a string")
        if not request["item_id"]:
            raise ReviewContractError("item_id must be non-empty")
        if not _SHA256.fullmatch(request["item_content_hash"]):
            raise ReviewContractError(
                "item_content_hash must be 64 lowercase hexadecimal characters"
            )
        if request["surface"] != "obsidian-plugin":
            raise ReviewContractError("surface must be obsidian-plugin")
        occurred_at = self._timestamp(request["occurred_at"], "occurred_at")
        try:
            return self.workspace.record_review_skip(
                event_id=f"skip-{submission_id}",
                item_id=request["item_id"],
                item_content_hash=request["item_content_hash"],
                occurred_at=occurred_at.isoformat(),
                surface=request["surface"],
            )
        except WorkspaceError as exc:
            message = str(exc)
            if "review_skips.event_id" in message:
                raise ReviewError(
                    "this review skip was already recorded",
                    code="already-recorded",
                    recovery="advance-card",
                ) from exc
            if "stale" in message.lower():
                raise ReviewError(
                    message, code="stale-content", recovery="reload-item"
                ) from exc
            raise ReviewError(
                message, code="skip-failed", recovery="retry-submit"
            ) from exc

    @staticmethod
    def attempt_result_payload(result: PracticeResult) -> dict[str, Any]:
        return {
            "schema": REVIEW_ATTEMPT_RESULT_SCHEMA,
            "attempt": {
                "event_id": result.attempt.event_id,
                "item_id": result.attempt.item_id,
                "item_content_hash": result.attempt.item_content_hash,
                "result": result.attempt.result,
                "confidence": result.attempt.confidence,
                "initial_latency_ms": result.attempt.initial_latency_ms,
                "administered": result.attempt.administered,
                "occurred_at": result.attempt.occurred_at.isoformat(),
            },
            "proposal": {
                "proposal_id": result.proposal.proposal_id,
                "algorithm": result.proposal.algorithm,
                "algorithm_version": result.proposal.algorithm_version,
                "due_at": result.proposal.due_at.isoformat(),
            },
        }

    @staticmethod
    def skip_result_payload(skip: dict[str, str]) -> dict[str, Any]:
        return {"schema": REVIEW_SKIP_RESULT_SCHEMA, "skip": skip}

    @staticmethod
    def _request_object(raw_request: str) -> dict[str, Any]:
        try:
            value = json.loads(raw_request)
        except json.JSONDecodeError as exc:
            raise ReviewContractError(f"review request must be valid JSON: {exc.msg}") from exc
        if not isinstance(value, dict):
            raise ReviewContractError("review request must be a JSON object")
        return value

    @staticmethod
    def _timestamp(value: object, name: str) -> datetime:
        if not isinstance(value, str):
            raise ReviewContractError(f"{name} must be a timestamp string")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ReviewContractError(f"{name} must be a valid timestamp") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ReviewContractError(f"{name} must include a timezone")
        return parsed.astimezone(timezone.utc)

    def due(self, now: datetime | None = None) -> list[ReviewQueueItem]:
        observed_at = now or datetime.now(timezone.utc)
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise WorkspaceError("review queue timestamp must be timezone-aware")
        observed_at = observed_at.astimezone(timezone.utc)

        scheduler = self.workspace.configuration().get("scheduler")
        if not isinstance(scheduler, dict):
            raise WorkspaceError("workspace scheduler configuration is missing")
        algorithm = scheduler.get("algorithm")
        context = scheduler.get("context")
        if not isinstance(algorithm, str) or not algorithm:
            raise WorkspaceError("scheduler algorithm must be a non-empty string")
        if not isinstance(context, str) or not context:
            raise WorkspaceError("scheduler context must be a non-empty string")

        with sqlite3.connect(self.workspace.db_path) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                """
                SELECT i.item_id, i.content_hash, i.focus, p.due_at
                FROM items AS i
                LEFT JOIN scheduler_state AS s
                  ON s.item_id = i.item_id
                 AND s.algorithm = ?
                 AND s.learning_context = ?
                LEFT JOIN scheduler_proposals AS p
                  ON p.source_event_id = s.source_event_id
                WHERE i.retired_at IS NULL
                ORDER BY i.item_id
                """,
                (algorithm, context),
            ).fetchall()

        projects_by_item: dict[str, list[str]] = {}
        for event in self.workspace.list_transfer_events():
            projects = projects_by_item.setdefault(event.item_id, [])
            if event.project_id not in projects:
                projects.append(event.project_id)

        due: list[tuple[datetime, ReviewQueueItem]] = []
        new: list[ReviewQueueItem] = []
        for row in rows:
            if row["due_at"] is None:
                new.append(
                    ReviewQueueItem(
                        item_id=row["item_id"],
                        content_hash=row["content_hash"],
                        focus=row["focus"],
                        project_ids=tuple(projects_by_item.get(row["item_id"], [])),
                        selection_reason=(
                            "Selected a new item in deterministic item-id order."
                        ),
                        status="new",
                        due_at=None,
                    )
                )
                continue
            try:
                due_at = datetime.fromisoformat(row["due_at"].replace("Z", "+00:00"))
            except (AttributeError, ValueError) as exc:
                raise WorkspaceError(
                    f"invalid scheduler due timestamp for item {row['item_id']}: {row['due_at']!r}"
                ) from exc
            if due_at.tzinfo is None or due_at.utcoffset() is None:
                raise WorkspaceError(
                    f"invalid scheduler due timestamp for item {row['item_id']}: {row['due_at']!r}"
                )
            due_at = due_at.astimezone(timezone.utc)
            if due_at <= observed_at:
                due.append(
                    (
                        due_at,
                        ReviewQueueItem(
                            item_id=row["item_id"],
                            content_hash=row["content_hash"],
                            focus=row["focus"],
                            project_ids=tuple(
                                projects_by_item.get(row["item_id"], [])
                            ),
                            selection_reason=(
                                "Selected the earliest due item; ties use item id."
                            ),
                            status="due",
                            due_at=due_at.isoformat(),
                        ),
                    )
                )

        due.sort(key=lambda entry: (entry[0], entry[1].item_id))
        new.sort(key=lambda entry: entry.item_id)
        return [entry for _, entry in due] + new
