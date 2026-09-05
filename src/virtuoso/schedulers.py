"""Built-in spaced-repetition scheduler backends.

Every backend implements one protocol: validate its own configuration,
turn one attempt plus the previous stored state into a proposal, and read
the due time back out of a stored state. The workspace stays the only
scheduler-of-record: backends compute, `WorkspaceService.record_attempt`
validates and stores.

Two built-ins ship here. `fsrs` wraps the `fsrs` package (see
docs/17-acknowledgements.md). `sm2` is written from Piotr Wozniak's
published 1990 SuperMemo 2 description, not copied from any existing
implementation.
"""

from __future__ import annotations

import importlib.metadata
import json
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Protocol

from fsrs import Card, Rating, Scheduler

from .errors import VirtuosoError


class SchedulerError(VirtuosoError):
    """A scheduler cannot produce an honest proposal."""


class SchedulerConfigurationError(SchedulerError):
    """The scheduler configuration in virtuoso.json is invalid."""


class SchedulerStateError(SchedulerError):
    """Stored scheduler state cannot be read or extended."""


_RESULTS = ("demonstrated", "partial", "not-demonstrated")


@dataclass(frozen=True)
class AttemptFacts:
    """The attempt facts a scheduler may use. Nothing else is passed."""

    result: str
    confidence: int
    occurred_at: datetime
    latency_ms: int | None
    administered: bool


@dataclass(frozen=True)
class SchedulerOutcome:
    proposed_state_json: str
    due_at: datetime
    rationale: str


class SchedulerBackend(Protocol):
    name: str
    version: str

    def default_configuration(self) -> dict[str, Any]:
        """The configuration `scheduler switch` writes for this backend."""
        ...

    def validate_configuration(self, raw: Mapping[str, Any]) -> dict[str, Any]:
        """Return the normalized configuration or raise SchedulerConfigurationError."""
        ...

    def propose(
        self,
        *,
        previous_state_json: str | None,
        attempt: AttemptFacts,
        configuration: Mapping[str, Any],
    ) -> SchedulerOutcome:
        """Schedule one attempt. Never writes anything."""
        ...

    def due_from_state(self, state_json: str) -> datetime:
        """Read the due time recorded inside a stored state."""
        ...


def configurations_compatible(
    algorithm: str, previous: object, proposed: Mapping[str, Any]
) -> bool:
    """Only the FSRS interval floor may change without resetting memory state."""
    if not isinstance(previous, dict):
        return False
    if algorithm != "fsrs":
        return previous == proposed
    backend = resolve_backend(algorithm)
    try:
        backend.validate_configuration(previous)
        backend.validate_configuration(proposed)
    except SchedulerError:
        return False
    old, new = dict(previous), dict(proposed)
    old.pop("minimum_interval_days", None)
    new.pop("minimum_interval_days", None)
    return old == new


def _require_aware(value: datetime, *, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise SchedulerStateError(f"{label} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _check_attempt(attempt: AttemptFacts) -> datetime:
    if attempt.result not in _RESULTS:
        raise SchedulerStateError(
            "attempt result must be one of demonstrated, partial, or not-demonstrated"
        )
    return _require_aware(attempt.occurred_at, label="attempt timestamp")


def _reject_unknown_fields(raw: Mapping[str, Any], allowed: set[str], *, name: str) -> None:
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise SchedulerConfigurationError(
            f"unknown scheduler configuration fields for {name}: "
            + ", ".join(unknown)
            + f"; run 'virtuoso scheduler switch --to {name}' to rewrite the "
            "scheduler configuration for that algorithm"
        )


def _load_state(state_json: str, *, name: str) -> dict[str, Any]:
    try:
        state = json.loads(state_json)
    except (TypeError, ValueError) as exc:
        raise SchedulerStateError(f"stored {name} state is invalid: {exc}") from exc
    if not isinstance(state, dict):
        raise SchedulerStateError(f"stored {name} state is invalid: expected an object")
    return state


def _due_from_state(state_json: str, *, name: str) -> datetime:
    state = _load_state(state_json, name=name)
    raw = state.get("due")
    if not isinstance(raw, str):
        raise SchedulerStateError(f"stored {name} state has no due timestamp")
    try:
        due = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise SchedulerStateError(
            f"stored {name} state has an invalid due timestamp: {raw!r}"
        ) from exc
    if due.tzinfo is None or due.utcoffset() is None:
        raise SchedulerStateError(
            f"stored {name} state due timestamp must include a timezone"
        )
    return due.astimezone(timezone.utc)


def evidence_clause(attempt: AttemptFacts) -> str:
    """Shared rationale tail: evidence is retained, competence is not asserted."""
    if attempt.administered:
        return (
            "latency unmeasured (agent-administered) and support are retained "
            "as evidence but do not assert competence."
        )
    return (
        f"latency {attempt.latency_ms} ms and support are retained "
        "as evidence but do not assert competence."
    )


class FsrsBackend:
    """FSRS through the `fsrs` package, with an optional post-review interval floor."""

    name = "fsrs"

    def __init__(self) -> None:
        self.version = importlib.metadata.version("fsrs")

    def default_configuration(self) -> dict[str, Any]:
        return {"desired_retention": 0.9, "enable_fuzzing": False}

    def validate_configuration(self, raw: Mapping[str, Any]) -> dict[str, Any]:
        _reject_unknown_fields(
            raw, {"desired_retention", "enable_fuzzing", "minimum_interval_days"},
            name=self.name,
        )
        desired_retention = raw.get("desired_retention", 0.9)
        if (
            not isinstance(desired_retention, (int, float))
            or isinstance(desired_retention, bool)
            or not math.isfinite(float(desired_retention))
            or not 0 < float(desired_retention) < 1
        ):
            raise SchedulerConfigurationError(
                "scheduler desired_retention must be a finite number between 0 and 1"
            )
        enable_fuzzing = raw.get("enable_fuzzing", False)
        if not isinstance(enable_fuzzing, bool):
            raise SchedulerConfigurationError(
                "scheduler enable_fuzzing must be true or false"
            )
        configuration = {
            "desired_retention": float(desired_retention),
            "enable_fuzzing": enable_fuzzing,
        }
        if "minimum_interval_days" in raw:
            minimum = raw["minimum_interval_days"]
            if (
                not isinstance(minimum, int)
                or isinstance(minimum, bool)
                or not 0 <= minimum <= 36500
            ):
                raise SchedulerConfigurationError(
                    "scheduler minimum_interval_days must be a whole number "
                    "of days from 0 through 36500"
                )
            configuration["minimum_interval_days"] = minimum
        return configuration

    def propose(
        self,
        *,
        previous_state_json: str | None,
        attempt: AttemptFacts,
        configuration: Mapping[str, Any],
    ) -> SchedulerOutcome:
        occurred_at = _check_attempt(attempt)
        configuration = self.validate_configuration(configuration)
        try:
            card = (
                Card.from_json(previous_state_json)
                if previous_state_json
                else Card(
                    card_id=int(occurred_at.timestamp() * 1000),
                    due=occurred_at,
                )
            )
        except (TypeError, ValueError, KeyError) as exc:
            raise SchedulerStateError(f"stored FSRS state is invalid: {exc}") from exc
        rating = {
            "demonstrated": Rating.Good,
            "partial": Rating.Hard,
            "not-demonstrated": Rating.Again,
        }[attempt.result]
        scheduler = Scheduler(
            desired_retention=float(configuration["desired_retention"]),
            enable_fuzzing=bool(configuration["enable_fuzzing"]),
        )
        try:
            next_card, _review_log = scheduler.review_card(
                card,
                rating,
                review_datetime=occurred_at,
                review_duration=attempt.latency_ms,
            )
        except (TypeError, ValueError) as exc:
            raise SchedulerStateError(
                f"FSRS could not schedule this attempt: {exc}"
            ) from exc
        rationale = (
            f"FSRS rating {rating.name} from result {attempt.result}; "
            + evidence_clause(attempt)
        )
        minimum = configuration.get("minimum_interval_days", 0)
        if minimum:
            try:
                floor = occurred_at + timedelta(days=minimum)
            except OverflowError as exc:
                raise SchedulerStateError(
                    "FSRS minimum interval exceeds the supported timestamp range"
                ) from exc
            original_due = next_card.due
            next_card.due = max(original_due, floor)
            rationale += (
                f" Configured minimum interval {minimum} day(s); original due "
                f"{original_due.isoformat()}; effective due {next_card.due.isoformat()}."
            )
        return SchedulerOutcome(
            proposed_state_json=next_card.to_json(),
            due_at=next_card.due,
            rationale=rationale,
        )

    def due_from_state(self, state_json: str) -> datetime:
        return _due_from_state(state_json, name="FSRS")


class Sm2Backend:
    """SuperMemo 2 (Wozniak, 1990), written from the published description.

    State per item: `easiness` (starts at 2.5, floored at
    `minimum_easiness`), `repetitions` (successful repetitions in a row),
    `interval_days`, `due`, and `last_review`.

    Response quality: demonstrated 4, partial 3, not-demonstrated 1.
    A quality below 3 restarts the repetition sequence at the first
    interval and leaves easiness unchanged (step 6 of the published
    algorithm). Otherwise easiness moves by
    `0.1 - (5 - q) * (0.08 + (5 - q) * 0.02)` and the interval ladder is
    `first_interval_days`, `second_interval_days`, then the previous
    interval times easiness, rounded half up to whole days. The published
    same-day re-drill of items scoring below 4 is not modelled: the next
    review is the next interval.
    """

    name = "sm2"
    version = "sm2-1990/1"
    _QUALITY = {"demonstrated": 4, "partial": 3, "not-demonstrated": 1}
    _FIELDS = {"first_interval_days", "second_interval_days", "minimum_easiness"}

    def default_configuration(self) -> dict[str, Any]:
        return {
            "first_interval_days": 1,
            "second_interval_days": 6,
            "minimum_easiness": 1.3,
        }

    def validate_configuration(self, raw: Mapping[str, Any]) -> dict[str, Any]:
        _reject_unknown_fields(raw, self._FIELDS, name=self.name)
        defaults = self.default_configuration()
        first = raw.get("first_interval_days", defaults["first_interval_days"])
        second = raw.get("second_interval_days", defaults["second_interval_days"])
        for label, value in (
            ("first_interval_days", first),
            ("second_interval_days", second),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise SchedulerConfigurationError(
                    f"scheduler {label} must be a whole number of days, at least 1"
                )
        if second < first:
            raise SchedulerConfigurationError(
                "scheduler second_interval_days must not be shorter than first_interval_days"
            )
        minimum = raw.get("minimum_easiness", defaults["minimum_easiness"])
        if (
            not isinstance(minimum, (int, float))
            or isinstance(minimum, bool)
            or not math.isfinite(float(minimum))
            or not 1.0 <= float(minimum) <= 2.5
        ):
            raise SchedulerConfigurationError(
                "scheduler minimum_easiness must be a finite number between 1.0 and 2.5"
            )
        return {
            "first_interval_days": int(first),
            "second_interval_days": int(second),
            "minimum_easiness": float(minimum),
        }

    def propose(
        self,
        *,
        previous_state_json: str | None,
        attempt: AttemptFacts,
        configuration: Mapping[str, Any],
    ) -> SchedulerOutcome:
        occurred_at = _check_attempt(attempt)
        first = int(configuration["first_interval_days"])
        second = int(configuration["second_interval_days"])
        minimum = float(configuration["minimum_easiness"])
        if previous_state_json is None:
            easiness = 2.5
            repetitions = 0
            interval = 0
        else:
            state = _load_state(previous_state_json, name="SM-2")
            easiness = state.get("easiness")
            repetitions = state.get("repetitions")
            interval = state.get("interval_days")
            if (
                not isinstance(easiness, (int, float))
                or isinstance(easiness, bool)
                or not math.isfinite(float(easiness))
                or not isinstance(repetitions, int)
                or isinstance(repetitions, bool)
                or repetitions < 0
                or not isinstance(interval, int)
                or isinstance(interval, bool)
                or interval < 0
            ):
                raise SchedulerStateError(
                    "stored SM-2 state is invalid: easiness, repetitions, and "
                    "interval_days must be finite non-negative numbers"
                )
            easiness = float(easiness)
        quality = self._QUALITY[attempt.result]
        if quality < 3:
            repetitions = 0
            interval = first
            movement = "easiness unchanged after a failed response"
        else:
            easiness = max(
                minimum, easiness + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
            )
            repetitions += 1
            if repetitions == 1:
                interval = first
            elif repetitions == 2:
                interval = second
            else:
                # Round half up to whole days, stated explicitly because
                # Python's round() rounds halves to even.
                interval = max(1, int(math.floor(interval * easiness + 0.5)))
            movement = f"easiness {easiness:.2f}"
        due_at = occurred_at + timedelta(days=interval)
        state_json = json.dumps(
            {
                "easiness": easiness,
                "repetitions": repetitions,
                "interval_days": interval,
                "due": due_at.isoformat(),
                "last_review": occurred_at.isoformat(),
            },
            sort_keys=True,
        )
        rationale = (
            f"SM-2 quality {quality} from result {attempt.result}; {movement}, "
            f"repetition {repetitions}, next interval {interval} day(s); "
            + evidence_clause(attempt)
        )
        return SchedulerOutcome(
            proposed_state_json=state_json, due_at=due_at, rationale=rationale
        )

    def due_from_state(self, state_json: str) -> datetime:
        return _due_from_state(state_json, name="SM-2")


_BUILTIN_BACKENDS: dict[str, SchedulerBackend] = {}


def builtin_algorithms() -> tuple[str, ...]:
    return ("fsrs", "sm2")


def resolve_backend(algorithm: object) -> SchedulerBackend:
    """Return the built-in backend for `algorithm` or fail closed."""
    if not isinstance(algorithm, str) or not algorithm.strip():
        raise SchedulerConfigurationError(
            "scheduler algorithm must be a non-empty string"
        )
    if algorithm not in builtin_algorithms():
        raise SchedulerConfigurationError(
            f"unsupported built-in scheduler: {algorithm!r}; "
            "built-in algorithms: " + ", ".join(builtin_algorithms())
        )
    backend = _BUILTIN_BACKENDS.get(algorithm)
    if backend is None:
        backend = FsrsBackend() if algorithm == "fsrs" else Sm2Backend()
        _BUILTIN_BACKENDS[algorithm] = backend
    return backend
