from __future__ import annotations

import importlib.metadata
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Protocol

from fsrs import Card, Rating, Scheduler

from .workspace import LearningItem, WorkspaceError, WorkspaceService


_RESULTS = {"demonstrated", "partial", "not-demonstrated"}
_AGENT_HELP = {"none", "light", "substantial", "unknown"}


class PracticeError(RuntimeError):
    """A practice session cannot proceed without corrupting its evidence."""


class PracticeIO(Protocol):
    def write(self, text: str) -> None: ...

    def ask(self, prompt: str) -> str: ...


class Clock(Protocol):
    def monotonic(self) -> float: ...


class SystemClock:
    def monotonic(self) -> float:
        return time.monotonic()


@dataclass(frozen=True)
class SupportAction:
    kind: str
    response: str | None = None
    latency_ms: int | None = None


@dataclass(frozen=True)
class AttemptRecord:
    event_id: str
    item_id: str
    item_content_hash: str
    occurred_at: datetime
    initial_response: str
    initial_latency_ms: int
    result: str
    confidence: int
    open_notes: bool
    agent_help: str
    support_actions: tuple[SupportAction, ...]


@dataclass(frozen=True)
class SchedulerProposal:
    proposal_id: str
    source_event_id: str
    item_id: str
    algorithm: str
    algorithm_version: str
    learning_context: str
    configuration: dict[str, object]
    due_at: datetime
    rationale: str
    previous_state_json: str | None
    proposed_state_json: str
    previous_source_event_id: str | None
    created_at: datetime


@dataclass(frozen=True)
class PracticeResult:
    attempt: AttemptRecord
    proposal: SchedulerProposal


class PracticeService:
    def __init__(self, workspace: WorkspaceService, *, clock: Clock | None = None) -> None:
        self.workspace = workspace
        self.clock = clock or SystemClock()

    def run(
        self,
        *,
        item_id: str,
        io: PracticeIO,
        now: datetime | None = None,
        agent_help: str = "none",
    ) -> PracticeResult:
        if agent_help not in _AGENT_HELP:
            raise PracticeError(
                "agent_help must be one of none, light, substantial, or unknown"
            )
        observed_at = now or datetime.now(timezone.utc)
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise PracticeError("practice timestamps must be timezone-aware")
        observed_at = observed_at.astimezone(timezone.utc)

        try:
            item = self.workspace.load_item(item_id)
        except WorkspaceError as exc:
            raise PracticeError(str(exc)) from exc

        open_notes = self._ask_yes_no(io, "Notes open? [y/N]: ")
        io.write(f"Challenge: {item.title}")
        io.write(item.prompt)

        initial_response, initial_latency_ms = self._timed_answer(
            io, "Your recall: "
        )
        io.write(f"Initial recall time: {initial_latency_ms} ms")

        support: list[SupportAction] = []
        retried = False
        hinted = False
        while True:
            allowed = ["reveal"]
            if not retried:
                allowed.insert(0, "retry")
            if item.hint and not hinted:
                allowed.insert(-1 if allowed else 0, "hint")
            choice = self._ask_choice(
                io,
                f"Next [{' / '.join(allowed)}]: ",
                set(allowed),
            )
            if choice == "retry":
                response, latency = self._timed_answer(io, "Retry without help: ")
                support.append(
                    SupportAction(
                        kind="retry-unaided", response=response, latency_ms=latency
                    )
                )
                retried = True
                continue
            if choice == "hint":
                io.write(f"Hint\n{item.hint}")
                response, latency = self._timed_answer(io, "Response after hint: ")
                support.append(
                    SupportAction(kind="hint", response=response, latency_ms=latency)
                )
                hinted = True
                break
            break

        io.write(f"Answer\n{item.answer}")
        result = self._ask_choice(
            io,
            "Result [demonstrated / partial / not-demonstrated]: ",
            _RESULTS,
        )
        unaided_responses = [initial_response] + [
            action.response or ""
            for action in support
            if action.kind == "retry-unaided"
        ]
        if result == "demonstrated" and not any(
            response.strip() for response in unaided_responses
        ):
            raise PracticeError(
                "blank recall cannot be recorded as demonstrated; record partial or not-demonstrated"
            )
        confidence = self._ask_confidence(io)
        if result != "demonstrated" and item.follow_up:
            io.write(f"Follow-up challenge\n{item.follow_up}")
            support.append(SupportAction(kind="follow-up-offered"))

        event_id = f"attempt-{uuid.uuid4().hex}"
        attempt = AttemptRecord(
            event_id=event_id,
            item_id=item.item_id,
            item_content_hash=item.content_hash,
            occurred_at=observed_at,
            initial_response=initial_response,
            initial_latency_ms=initial_latency_ms,
            result=result,
            confidence=confidence,
            open_notes=open_notes,
            agent_help=agent_help,
            support_actions=tuple(support),
        )
        proposal = self._schedule(item=item, attempt=attempt)
        self._persist(attempt=attempt, proposal=proposal)

        result_label = result.replace("-", " ")
        io.write(f"Evidence: {result_label}; confidence {confidence}/5; help {agent_help}")
        io.write(
            "Next review proposal: "
            f"{proposal.due_at.isoformat()} via {proposal.algorithm} "
            f"{proposal.algorithm_version} ({proposal.learning_context})."
        )
        return PracticeResult(attempt=attempt, proposal=proposal)

    def _schedule(
        self, *, item: LearningItem, attempt: AttemptRecord
    ) -> SchedulerProposal:
        scheduler_config = self.workspace.configuration().get("scheduler")
        if not isinstance(scheduler_config, dict):
            raise PracticeError("workspace scheduler configuration is missing")
        if scheduler_config.get("algorithm") != "fsrs":
            raise PracticeError(
                f"unsupported built-in scheduler: {scheduler_config.get('algorithm')!r}"
            )
        context = scheduler_config.get("context", item.learning_context)
        if not isinstance(context, str) or not context.strip():
            raise PracticeError("scheduler context must be a non-empty string")
        desired_retention = scheduler_config.get("desired_retention", 0.9)
        if (
            not isinstance(desired_retention, (int, float))
            or isinstance(desired_retention, bool)
            or not 0 < float(desired_retention) < 1
        ):
            raise PracticeError("scheduler desired_retention must be a number between 0 and 1")
        desired_retention = float(desired_retention)
        enable_fuzzing = scheduler_config.get("enable_fuzzing", False)
        if not isinstance(enable_fuzzing, bool):
            raise PracticeError("scheduler enable_fuzzing must be true or false")
        configuration: dict[str, object] = {
            "desired_retention": desired_retention,
            "enable_fuzzing": enable_fuzzing,
        }
        version = importlib.metadata.version("fsrs")
        previous_state, previous_source_event_id = self.workspace.scheduler_snapshot(
            item_id=item.item_id,
            algorithm="fsrs",
            learning_context=context,
        )
        try:
            card = (
                Card.from_json(previous_state)
                if previous_state
                else Card(due=attempt.occurred_at)
            )
        except (TypeError, ValueError, KeyError) as exc:
            raise PracticeError(f"stored FSRS state is invalid: {exc}") from exc
        rating = {
            "demonstrated": Rating.Good,
            "partial": Rating.Hard,
            "not-demonstrated": Rating.Again,
        }[attempt.result]
        scheduler = Scheduler(
            desired_retention=desired_retention,
            enable_fuzzing=enable_fuzzing,
        )
        try:
            next_card, _review_log = scheduler.review_card(
                card,
                rating,
                review_datetime=attempt.occurred_at,
                review_duration=attempt.initial_latency_ms,
            )
        except (TypeError, ValueError) as exc:
            raise PracticeError(f"FSRS could not schedule this attempt: {exc}") from exc

        created_at = attempt.occurred_at
        proposed_state = next_card.to_json()
        rationale = (
            f"FSRS rating {rating.name} from result {attempt.result}; "
            f"latency {attempt.initial_latency_ms} ms and support are retained as evidence "
            "but do not assert competence."
        )
        return SchedulerProposal(
            proposal_id=f"proposal-{uuid.uuid4().hex}",
            source_event_id=attempt.event_id,
            item_id=item.item_id,
            algorithm="fsrs",
            algorithm_version=version,
            learning_context=context,
            configuration=configuration,
            due_at=next_card.due,
            rationale=rationale,
            previous_state_json=previous_state,
            proposed_state_json=proposed_state,
            previous_source_event_id=previous_source_event_id,
            created_at=created_at,
        )

    def _persist(
        self, *, attempt: AttemptRecord, proposal: SchedulerProposal
    ) -> None:
        try:
            self.workspace.record_attempt(
                attempt={
                    "event_id": attempt.event_id,
                    "item_id": attempt.item_id,
                    "item_content_hash": attempt.item_content_hash,
                    "occurred_at": attempt.occurred_at.isoformat(),
                    "initial_response": attempt.initial_response,
                    "initial_latency_ms": attempt.initial_latency_ms,
                    "result": attempt.result,
                    "confidence": attempt.confidence,
                    "open_notes": attempt.open_notes,
                    "agent_help": attempt.agent_help,
                    "support_actions": [
                        asdict(action) for action in attempt.support_actions
                    ],
                },
                proposal={
                    "proposal_id": proposal.proposal_id,
                    "source_event_id": proposal.source_event_id,
                    "item_id": proposal.item_id,
                    "algorithm": proposal.algorithm,
                    "algorithm_version": proposal.algorithm_version,
                    "learning_context": proposal.learning_context,
                    "configuration": proposal.configuration,
                    "previous_state_json": proposal.previous_state_json,
                    "previous_source_event_id": proposal.previous_source_event_id,
                    "due_at": proposal.due_at.isoformat(),
                    "rationale": proposal.rationale,
                    "created_at": proposal.created_at.isoformat(),
                },
                state_json=proposal.proposed_state_json,
            )
        except WorkspaceError as exc:
            raise PracticeError(str(exc)) from exc

    def _timed_answer(self, io: PracticeIO, prompt: str) -> tuple[str, int]:
        started = self.clock.monotonic()
        response = io.ask(prompt)
        ended = self.clock.monotonic()
        latency_ms = max(0, round((ended - started) * 1000))
        return response, latency_ms

    @staticmethod
    def _ask_choice(io: PracticeIO, prompt: str, choices: set[str]) -> str:
        normalized = {choice.lower(): choice for choice in choices}
        while True:
            answer = io.ask(prompt).strip().lower()
            if answer in normalized:
                return normalized[answer]
            io.write("Choose one of: " + ", ".join(sorted(choices)))

    @staticmethod
    def _ask_yes_no(io: PracticeIO, prompt: str) -> bool:
        while True:
            answer = io.ask(prompt).strip().lower()
            if answer in {"", "n", "no"}:
                return False
            if answer in {"y", "yes"}:
                return True
            io.write("Enter yes or no.")

    @staticmethod
    def _ask_confidence(io: PracticeIO) -> int:
        while True:
            answer = io.ask("Confidence [1-5]: ").strip()
            try:
                value = int(answer)
            except ValueError:
                value = 0
            if 1 <= value <= 5:
                return value
            io.write("Confidence must be an integer from 1 to 5.")
