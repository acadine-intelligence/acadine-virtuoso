"""Understanding gap queue: rank what the learner cannot yet explain back.

Gap items arrive as JSON files written by an extractor (one list of objects per
file) in a gaps directory, by default ``<workspace>/gaps``. Each item names a
feature or tool the learner was unsure of, the evidence for that, a grill
question and the source of truth that answers it.

The learner's grill results live in ``state.json`` in the same directory. The
CLI is the only writer of that file. Item files are read-only inputs.

Ranking starts from the item's own score (or its severity), decays with time
since the gap was last seen, boosts items graded ``wrong`` or ``partial`` and
items never grilled, and demotes items graded ``knows`` until that grade goes
stale: after ``KNOWS_STALE_DAYS`` days, when the gap is seen again after the
grill, or when the item's question or source of truth changes.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from contextlib import contextmanager
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from .errors import VirtuosoError

STATE_FILE_NAME = "state.json"
STATE_SCHEMA = "virtuoso/gap-state@0.1"
GRADES = ("knows", "partial", "wrong")
STATUSES = ("unverified", *GRADES)

RECENCY_HALF_LIFE_DAYS = 30.0
RECENCY_FLOOR = 0.25
KNOWS_STALE_DAYS = 30
STATUS_FACTORS = {
    "wrong": 2.0,
    "partial": 1.5,
    "unverified": 1.0,
    "knows": 0.1,
}
NEVER_GRILLED_FACTOR = 1.25
SEVERITY_SCORES = {"critical": 4.0, "high": 3.0, "medium": 2.0, "low": 1.0}
DEFAULT_SEVERITY_SCORE = 2.0
MAX_NOTE_LENGTH = 2000

_GAP_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class GapError(VirtuosoError):
    """A gap queue operation failed without producing a result."""


@dataclass(frozen=True)
class Evidence:
    session: str
    date: str
    note: str


@dataclass(frozen=True)
class GapItem:
    gap_id: str
    project: str
    title: str
    unsure_of: str
    evidence: tuple[Evidence, ...]
    recurrences: int
    first_seen: str | None
    last_seen: str | None
    severity: str | None
    score: float | None
    grill_question: str
    source_of_truth: str
    source_file: str

    def fingerprint(self) -> str:
        """Hash of the parts that define what the learner must explain."""
        material = json.dumps(
            [self.title, self.unsure_of, self.grill_question, self.source_of_truth],
            ensure_ascii=False,
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def base_score(self) -> float:
        if self.score is not None:
            return self.score
        if self.severity is not None:
            try:
                numeric = float(self.severity)
            except ValueError:
                numeric = None
            if numeric is not None:
                return numeric
            return SEVERITY_SCORES.get(self.severity.lower(), DEFAULT_SEVERITY_SCORE)
        return DEFAULT_SEVERITY_SCORE


@dataclass(frozen=True)
class RankedGap:
    item: GapItem
    status: str
    effective_status: str
    stale: bool
    stale_reason: str | None
    last_grilled: str | None
    grill_count: int
    rank_score: float
    factors: dict[str, float]

    def to_payload(self, *, rank: int | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.item.gap_id,
            "project": self.item.project,
            "title": self.item.title,
            "unsure_of": self.item.unsure_of,
            "severity": self.item.severity,
            "recurrences": self.item.recurrences,
            "first_seen": self.item.first_seen,
            "last_seen": self.item.last_seen,
            "status": self.status,
            "effective_status": self.effective_status,
            "stale": self.stale,
            "stale_reason": self.stale_reason,
            "last_grilled": self.last_grilled,
            "grill_count": self.grill_count,
            "rank_score": self.rank_score,
            "factors": dict(self.factors),
        }
        if rank is not None:
            payload["rank"] = rank
        return payload


# ---------- loading ----------


def default_gaps_dir(workspace: Path) -> Path:
    return Path(workspace).expanduser() / "gaps"


def _require_text(raw: dict[str, Any], key: str, *, where: str, default: str | None = None) -> str:
    value = raw.get(key, default)
    if not isinstance(value, str) or (default is None and not value.strip()):
        raise GapError(f"{where}: field '{key}' must be a non-empty string")
    return value.strip()


def _optional_text(raw: dict[str, Any], key: str, *, where: str) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise GapError(f"{where}: field '{key}' must be a string")
    return value.strip() or None


def _severity(raw: dict[str, Any], *, where: str) -> str | None:
    """Accept a named severity or a number from 1 to 5."""
    value = raw.get("severity")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        numeric: float | None = float(value)
    else:
        text = _optional_text(raw, "severity", where=where)
        if text is None or text.lower() in SEVERITY_SCORES:
            return text
        try:
            numeric = float(text)
        except ValueError:
            return text
    if numeric is None or not math.isfinite(numeric) or not 1 <= numeric <= 5:
        raise GapError(f"{where}: field 'severity' must be between 1 and 5")
    return str(value) if not isinstance(value, str) else value.strip()


def _optional_day(raw: dict[str, Any], key: str, *, where: str) -> str | None:
    value = _optional_text(raw, key, where=where)
    if value is not None:
        _parse_day(value, field=f"{where}: field '{key}'")
    return value


def _parse_day(value: str, *, field: str) -> date:
    """Accept a date or a timezone-aware or naive ISO timestamp."""
    try:
        if len(value) == 10:
            return date.fromisoformat(value)
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise GapError(f"{field} must be an ISO date or timestamp") from exc
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc)
    return parsed.date()


def _parse_item(raw: object, *, where: str) -> GapItem:
    if not isinstance(raw, dict):
        raise GapError(f"{where}: each gap item must be an object")
    gap_id = raw.get("id")
    if not isinstance(gap_id, str) or not _GAP_ID.fullmatch(gap_id):
        raise GapError(
            f"{where}: field 'id' must be 1-128 letters, digits, '.', '_', ':' or '-'"
            " and start with a letter or digit"
        )
    where = f"{where} [{gap_id}]"
    evidence_raw = raw.get("evidence", [])
    if not isinstance(evidence_raw, list):
        raise GapError(f"{where}: field 'evidence' must be a list")
    evidence: list[Evidence] = []
    for entry in evidence_raw:
        if not isinstance(entry, dict):
            raise GapError(f"{where}: each evidence entry must be an object")
        evidence.append(
            Evidence(
                session=str(entry.get("session", "")),
                date=str(entry.get("date", "")),
                note=str(entry.get("note", "")),
            )
        )
    recurrences = raw.get("recurrences", len(evidence) or 1)
    if isinstance(recurrences, bool) or not isinstance(recurrences, int) or recurrences < 0:
        raise GapError(f"{where}: field 'recurrences' must be a non-negative integer")
    score = raw.get("score")
    if score is not None:
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score) or not 0 <= score <= 1e9:
            raise GapError(f"{where}: field 'score' must be a number from 0 to 1e9")
        score = float(score)
    return GapItem(
        gap_id=gap_id,
        project=_require_text(raw, "project", where=where),
        title=_require_text(raw, "title", where=where),
        unsure_of=_require_text(raw, "unsure_of", where=where, default=""),
        evidence=tuple(evidence),
        recurrences=recurrences,
        first_seen=_optional_day(raw, "first_seen", where=where),
        last_seen=_optional_day(raw, "last_seen", where=where),
        severity=_severity(raw, where=where),
        score=score,
        grill_question=_require_text(raw, "grill_question", where=where),
        source_of_truth=_require_text(raw, "source_of_truth", where=where, default=""),
        source_file=where.split(" [", 1)[0],
    )


def load_items(gaps_dir: Path) -> list[GapItem]:
    """Load every gap item from ``*.json`` in ``gaps_dir`` except the state file."""
    gaps_dir = Path(gaps_dir).expanduser()
    if not gaps_dir.is_dir():
        raise GapError(f"gaps directory not found: {gaps_dir}")
    items: dict[str, GapItem] = {}
    for path in sorted(gaps_dir.glob("*.json")):
        if path.name == STATE_FILE_NAME:
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError) as exc:
            raise GapError(f"{path.name}: cannot read gap file: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise GapError(f"{path.name}: invalid JSON: {exc.msg} (line {exc.lineno})") from exc
        if not isinstance(raw, list):
            raise GapError(f"{path.name}: a gap file must hold a JSON list of items")
        for index, entry in enumerate(raw):
            item = _parse_item(entry, where=f"{path.name}#{index}")
            if item.gap_id in items:
                raise GapError(
                    f"duplicate gap id '{item.gap_id}' in {path.name} and"
                    f" {items[item.gap_id].source_file.split('#', 1)[0]}"
                )
            items[item.gap_id] = item
    return list(items.values())


# ---------- state ----------


def state_path(gaps_dir: Path) -> Path:
    return Path(gaps_dir).expanduser() / STATE_FILE_NAME


def load_state(gaps_dir: Path) -> dict[str, dict[str, Any]]:
    path = state_path(gaps_dir)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GapError(f"{STATE_FILE_NAME}: cannot read grill state: {exc}") from exc
    if (
        not isinstance(raw, dict)
        or raw.get("schema") != STATE_SCHEMA
        or not isinstance(raw.get("items"), dict)
    ):
        raise GapError(
            f"{STATE_FILE_NAME}: expected schema '{STATE_SCHEMA}' with an 'items' object"
        )
    items: dict[str, dict[str, Any]] = {}
    for gap_id, entry in raw.get("items", {}).items():
        if not isinstance(entry, dict):
            raise GapError(f"{STATE_FILE_NAME}: state for '{gap_id}' must be an object")
        status = entry.get("status", "unverified")
        if status not in STATUSES:
            raise GapError(
                f"{STATE_FILE_NAME}: state for '{gap_id}' has unknown status '{status}'"
            )
        history = entry.get("history", [])
        if not isinstance(history, list):
            raise GapError(f"{STATE_FILE_NAME}: history for '{gap_id}' must be a list")
        items[gap_id] = entry
    return items


@contextmanager
def _state_lock(gaps_dir: Path) -> Iterator[None]:
    """Serialise read-modify-write of the grill state across processes."""
    import fcntl

    lock_path = Path(gaps_dir).expanduser() / ".state.lock"
    with open(lock_path, "a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _write_state(gaps_dir: Path, items: dict[str, dict[str, Any]]) -> None:
    path = state_path(gaps_dir)
    payload = {"schema": STATE_SCHEMA, "items": items}
    text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    fd, temp_name = tempfile.mkstemp(prefix=".state-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


# ---------- scoring ----------


def _now(now: datetime | None) -> datetime:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise GapError("now must be timezone-aware")
    return current.astimezone(timezone.utc)


def recency_factor(last_seen: str | None, now: datetime) -> float:
    """Halve every ``RECENCY_HALF_LIFE_DAYS`` since last seen, floored."""
    if last_seen is None:
        return 1.0
    days = max(0, (now.date() - _parse_day(last_seen, field="last_seen")).days)
    return max(RECENCY_FLOOR, float(0.5 ** (days / RECENCY_HALF_LIFE_DAYS)))


def _knows_staleness(
    item: GapItem, entry: dict[str, Any], now: datetime
) -> str | None:
    last_grilled = entry.get("last_grilled")
    if not isinstance(last_grilled, str):
        return "never-grilled"
    grilled_day = _parse_day(last_grilled, field="last_grilled")
    if (now.date() - grilled_day).days >= KNOWS_STALE_DAYS:
        return "grade-expired"
    fingerprint = entry.get("fingerprint")
    if isinstance(fingerprint, str) and fingerprint != item.fingerprint():
        return "source-changed"
    if item.last_seen is not None and _parse_day(item.last_seen, field="last_seen") > grilled_day:
        return "seen-since-grill"
    return None


def rank_item(item: GapItem, entry: dict[str, Any] | None, now: datetime) -> RankedGap:
    entry = entry or {}
    status = entry.get("status", "unverified")
    history = entry.get("history", [])
    last_grilled = entry.get("last_grilled") if isinstance(entry.get("last_grilled"), str) else None
    never_grilled = last_grilled is None
    stale_reason = _knows_staleness(item, entry, now) if status == "knows" else None
    stale = stale_reason is not None
    effective_status = "unverified" if stale else status
    base = item.base_score()
    recurrence = 1.0 + math.log2(1 + max(item.recurrences, 0)) / 4.0
    recency = recency_factor(item.last_seen, now)
    status_factor = STATUS_FACTORS[effective_status]
    never_factor = NEVER_GRILLED_FACTOR if never_grilled else 1.0
    rank_score = base * recurrence * recency * status_factor * never_factor
    return RankedGap(
        item=item,
        status=status,
        effective_status=effective_status,
        stale=stale,
        stale_reason=stale_reason,
        last_grilled=last_grilled,
        grill_count=len(history),
        rank_score=round(rank_score, 4),
        factors={
            "base": round(base, 4),
            "recurrence": round(recurrence, 4),
            "recency": round(recency, 4),
            "status": status_factor,
            "never_grilled": never_factor,
        },
    )


def ranked_gaps(
    gaps_dir: Path,
    *,
    project: str | None = None,
    limit: int | None = None,
    now: datetime | None = None,
) -> list[RankedGap]:
    current = _now(now)
    state = load_state(gaps_dir)
    items = load_items(gaps_dir)
    if project is not None:
        wanted = project.strip().lower()
        items = [item for item in items if item.project.lower() == wanted]
    ranked = [rank_item(item, state.get(item.gap_id), current) for item in items]
    ranked.sort(key=lambda gap: (-gap.rank_score, gap.item.project, gap.item.gap_id))
    if limit is not None:
        if limit < 1:
            raise GapError("limit must be a positive integer")
        ranked = ranked[:limit]
    return ranked


# ---------- grill ----------


def _find(gaps_dir: Path, gap_id: str) -> GapItem:
    for item in load_items(gaps_dir):
        if item.gap_id == gap_id:
            return item
    raise GapError(f"unknown gap id: {gap_id}")


def grill_prompt(gaps_dir: Path, gap_id: str, *, now: datetime | None = None) -> dict[str, Any]:
    """Return the grill question and its source of truth. Read-only."""
    current = _now(now)
    item = _find(gaps_dir, gap_id)
    entry = load_state(gaps_dir).get(gap_id)
    ranked = rank_item(item, entry, current)
    payload = ranked.to_payload()
    payload.update(
        {
            "schema": "virtuoso/gap-grill@0.1",
            "grill_question": item.grill_question,
            "source_of_truth": item.source_of_truth,
            "evidence": [
                {"session": e.session, "date": e.date, "note": e.note}
                for e in item.evidence
            ],
        }
    )
    return payload


def record_grade(
    gaps_dir: Path,
    gap_id: str,
    grade: str,
    *,
    note: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Record a grill result for one gap and return the updated state."""
    if grade not in GRADES:
        raise GapError(f"grade must be one of: {', '.join(GRADES)}")
    if note is not None:
        note = note.strip() or None
        if note is not None and len(note) > MAX_NOTE_LENGTH:
            raise GapError(f"note must be at most {MAX_NOTE_LENGTH} characters")
    current = _now(now)
    item = _find(gaps_dir, gap_id)
    with _state_lock(gaps_dir):
        state = load_state(gaps_dir)
        entry = dict(state.get(gap_id, {}))
        previous = entry.get("status", "unverified")
        at = current.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        history = list(entry.get("history", []))
        record: dict[str, Any] = {
            "at": at,
            "grade": grade,
            "previous_status": previous,
            "fingerprint": item.fingerprint(),
        }
        if note is not None:
            record["note"] = note
        history.append(record)
        entry.update(
            {
                "status": grade,
                "last_grilled": at,
                "fingerprint": item.fingerprint(),
                "history": history,
            }
        )
        state[gap_id] = entry
        _write_state(gaps_dir, state)
    ranked = rank_item(item, entry, current)
    payload = ranked.to_payload()
    payload.update(
        {
            "schema": "virtuoso/gap-grade@0.1",
            "grade": grade,
            "previous_status": previous,
            "note": note,
            "recorded_at": at,
        }
    )
    return payload
