"""Evidence-aware session composition: FocusProposal and LearnerDecision."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .errors import VirtuosoError
from .learning_state import LearningStateDataError, current_learning_states
from .workload import WorkloadDataError, current_schedules, require_aware_utc
from .workspace import LearningItem, WorkspaceError, WorkspaceService

PROPOSAL_SCHEMA = "virtuoso/focus-proposal@0.1"
DECISION_SCHEMA = "virtuoso/learner-decision@0.1"
_GAP_RESULTS = ("not-demonstrated", "partial")
_SUPPORT_KINDS = ("hint", "worked-feedback", "follow-up")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")

# Gap severity ordering for deterministic selection. Higher severity wins; ties
# use item-id order so the same snapshot and clock always select the same item.
_GAP_SEVERITY = {
    "not-demonstrated": 3,
    "partial": 2,
    "assisted": 1,
}


class CompositionError(VirtuosoError):
    """Session composition cannot produce a trustworthy proposal or decision."""


@dataclass(frozen=True)
class FocusProposal:
    proposal_id: str
    focus_scope: str | None
    action: str
    primary_item_id: str
    rationale: str
    source_event_ids: tuple[str, ...]
    skipped: tuple[dict[str, Any], ...]
    alternatives: tuple[str, ...]
    uncertainty: str | None
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class LearnerDecision:
    decision_id: str
    proposal_id: str
    decision: str
    chosen_item_id: str | None
    chosen_item_content_hash: str | None
    reason: str | None
    occurred_at: str
    surface: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": DECISION_SCHEMA,
            "decision_id": self.decision_id,
            "proposal_id": self.proposal_id,
            "decision": self.decision,
            "chosen_item_id": self.chosen_item_id,
            "chosen_item_content_hash": self.chosen_item_content_hash,
            "reason": self.reason,
            "occurred_at": self.occurred_at,
            "surface": self.surface,
        }


class SessionComposer:
    def __init__(self, workspace: WorkspaceService) -> None:
        self.workspace = workspace

    def compose(self, *, now: datetime, focus: str | None = None) -> FocusProposal:
        try:
            now_utc = require_aware_utc(now, label="composition timestamp")
        except WorkloadDataError as exc:
            raise CompositionError(str(exc)) from exc
        if focus is not None and not focus.strip():
            raise CompositionError("focus filter must be a non-empty string")

        pending = self._pending_learn_states(focus)
        uncertainty: str | None = None
        if pending:
            state = pending[0]
            item = self._load(state.item_id)
            if item.entry_mode == "learn-first":
                primary_id = state.item_id
                action = "learn"
                rationale = (
                    f"Learning is required before practice for '{state.item_id}': "
                    "this learn-first item has no completed study event for its "
                    "current item and learning-unit hashes."
                )
                source_event_ids: tuple[str, ...] = ()
                skipped: list[dict[str, Any]] = []
                alternatives: tuple[str, ...] = tuple(s.item_id for s in pending[1:])
            else:
                primary_id, action, rationale, source_event_ids, skipped, alternatives = (
                    self._practice_composition(focus=focus, now=now_utc)
                )
        else:
            primary_id, action, rationale, source_event_ids, skipped, alternatives = (
                self._practice_composition(focus=focus, now=now_utc)
            )
            uncertainty = (
                "No pending learn-first study step; proposal follows evidence and "
                "schedule ordering."
            )

        if uncertainty is None and not source_event_ids:
            uncertainty = (
                "No recorded attempt evidence guides this proposal; it follows the "
                "deterministic selection order."
            )

        primary_item = self._load(primary_id)
        cited_hashes: dict[str, str] = {primary_id: primary_item.content_hash}
        for entry in skipped:
            entry_hash = entry.get("item_content_hash")
            if isinstance(entry_hash, str):
                cited_hashes[entry["item_id"]] = entry_hash
        primary_payload: dict[str, Any] = {
            "item_id": primary_item.item_id,
            "title": primary_item.title,
            "focus": primary_item.focus,
            "item_content_hash": primary_item.content_hash,
            "learning_unit_hash": primary_item.learning_unit_hash,
            "prompt": primary_item.prompt if action == "practice" else None,
        }
        occurred_at = now_utc.isoformat()
        payload_core: dict[str, Any] = {
            "focus_scope": focus,
            "action": action,
            "primary": primary_payload,
            "source_event_ids": list(source_event_ids),
            "skipped": skipped,
            "alternatives": list(alternatives),
            "uncertainty": uncertainty,
            "rationale": rationale,
            "occurred_at": occurred_at,
        }
        proposal_id = _deterministic_id("proposal-", payload_core)
        payload = {
            "schema": PROPOSAL_SCHEMA,
            "proposal_id": proposal_id,
            **payload_core,
        }
        proposal = FocusProposal(
            proposal_id=proposal_id,
            focus_scope=focus,
            action=action,
            primary_item_id=primary_id,
            rationale=rationale,
            source_event_ids=source_event_ids,
            skipped=tuple(skipped),
            alternatives=alternatives,
            uncertainty=uncertainty,
            payload=payload,
        )
        self._store_proposal(proposal, cited_hashes=cited_hashes)
        return proposal

    def decide(
        self,
        *,
        proposal_id: str,
        decision: str,
        now: datetime,
        surface: str,
        chosen_item_id: str | None = None,
        reason: str | None = None,
    ) -> LearnerDecision:
        try:
            now_utc = require_aware_utc(now, label="decision timestamp")
        except WorkloadDataError as exc:
            raise CompositionError(str(exc)) from exc
        if decision not in {"accept", "change", "reject"}:
            raise CompositionError("decision must be accept, change, or reject")
        if not (proposal_id or "").strip():
            raise CompositionError("proposal id must be non-empty")
        if not 1 <= len(surface) <= 64:
            raise CompositionError("decision surface must contain 1 to 64 characters")
        if decision == "accept" and chosen_item_id is not None:
            raise CompositionError("accept does not take a chosen item")
        if decision == "reject" and chosen_item_id is not None:
            raise CompositionError("reject does not take a chosen item")
        if decision == "change" and not chosen_item_id:
            raise CompositionError("change requires a chosen active item")
        if reason is not None and not reason.strip():
            raise CompositionError("decision reason must be non-empty when provided")

        with self.workspace._connect() as db:
            proposal_row = db.execute(
                """SELECT proposal_id, primary_item_id, focus_scope
                   FROM composition_proposals WHERE proposal_id = ?""",
                (proposal_id,),
            ).fetchone()
            if proposal_row is None:
                raise CompositionError(f"no composition proposal with id: {proposal_id}")
            cited_rows = db.execute(
                """SELECT item_id, item_content_hash FROM composition_proposal_items
                   WHERE proposal_id = ? ORDER BY item_id""",
                (proposal_id,),
            ).fetchall()

            chosen_item: LearningItem | None = None
            chosen_hash: str | None = None
            if decision == "accept":
                chosen_item = self._load(proposal_row["primary_item_id"])
            elif decision == "change":
                assert chosen_item_id is not None
                chosen_item = self._load(chosen_item_id)
                if (
                    proposal_row["focus_scope"] is not None
                    and chosen_item.focus != proposal_row["focus_scope"]
                ):
                    raise CompositionError(
                        "changed item must belong to the proposal focus"
                    )
            if chosen_item is not None:
                chosen_hash = chosen_item.content_hash

            for row in cited_rows:
                item_id = row["item_id"]
                try:
                    item = self._load(item_id)
                except WorkspaceError as exc:
                    raise CompositionError(f"stale proposal content: {exc}") from exc
                if (
                    not isinstance(row["item_content_hash"], str)
                    or _SHA256.fullmatch(row["item_content_hash"]) is None
                    or item.content_hash != row["item_content_hash"]
                ):
                    raise CompositionError(
                        f"stale proposal content for item {item_id}; "
                        "compose a new proposal"
                    )

            decision_id = f"decision-{uuid.uuid4().hex}"
            occurred_at = now_utc.isoformat()
            try:
                db.execute("BEGIN IMMEDIATE")
                db.execute(
                    """INSERT INTO composition_decisions(
                           decision_id, proposal_id, decision, chosen_item_id,
                           chosen_item_content_hash, reason, occurred_at, surface
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        decision_id,
                        proposal_id,
                        decision,
                        chosen_item.item_id if chosen_item is not None else None,
                        chosen_hash,
                        reason.strip() if reason else None,
                        occurred_at,
                        surface,
                    ),
                )
                db.execute("COMMIT")
            except sqlite3.IntegrityError as exc:
                db.execute("ROLLBACK")
                raise CompositionError(
                    f"proposal is already decided: {proposal_id}"
                ) from exc
            except sqlite3.Error as exc:
                db.execute("ROLLBACK")
                raise CompositionError(f"decision could not be appended: {exc}") from exc

        return LearnerDecision(
            decision_id=decision_id,
            proposal_id=proposal_id,
            decision=decision,
            chosen_item_id=chosen_item.item_id if chosen_item is not None else None,
            chosen_item_content_hash=chosen_hash,
            reason=reason.strip() if reason else None,
            occurred_at=occurred_at,
            surface=surface,
        )

    def show(self, *, proposal_id: str) -> dict[str, Any]:
        with self.workspace._connect() as db:
            row = db.execute(
                "SELECT payload_json FROM composition_proposals WHERE proposal_id = ?",
                (proposal_id,),
            ).fetchone()
            if row is None:
                raise CompositionError(f"no composition proposal with id: {proposal_id}")
            try:
                payload = json.loads(row["payload_json"])
            except (json.JSONDecodeError, TypeError) as exc:
                raise CompositionError("stored proposal payload is invalid") from exc
            decision_row = db.execute(
                """SELECT decision_id, proposal_id, decision, chosen_item_id,
                          chosen_item_content_hash, reason, occurred_at, surface
                   FROM composition_decisions WHERE proposal_id = ?""",
                (proposal_id,),
            ).fetchone()
        decision = None
        if decision_row is not None:
            decision = {"schema": DECISION_SCHEMA, **dict(decision_row)}
        return {"schema": PROPOSAL_SCHEMA, "proposal": payload, "decision": decision}

    def list(self, *, status: str = "pending", limit: int = 10) -> dict[str, Any]:
        if status not in {"pending", "decided", "all"}:
            raise CompositionError("status must be pending, decided, or all")
        if not 1 <= limit <= 100:
            raise CompositionError("limit must be between 1 and 100")
        query = """
            SELECT p.proposal_id, p.action, p.primary_item_id, p.occurred_at,
                   d.decision AS decision
            FROM composition_proposals AS p
            LEFT JOIN composition_decisions AS d ON d.proposal_id = p.proposal_id
        """
        parameters: list[object] = []
        if status == "pending":
            query += " WHERE d.decision_id IS NULL"
        elif status == "decided":
            query += " WHERE d.decision_id IS NOT NULL"
        query += " ORDER BY p.occurred_at DESC, p.proposal_id LIMIT ?"
        parameters.append(limit)
        with self.workspace._connect() as db:
            rows = db.execute(query, parameters).fetchall()
        return {
            "schema": PROPOSAL_SCHEMA,
            "proposals": [dict(row) for row in rows],
        }

    def _practice_composition(
        self, *, focus: str | None, now: datetime
    ) -> tuple[str, str, str, tuple[str, ...], list[dict[str, Any]], tuple[str, ...]]:
        scheduler = self.workspace.configuration().get("scheduler")
        if not isinstance(scheduler, dict):
            raise CompositionError("workspace scheduler configuration is missing")
        algorithm = scheduler.get("algorithm")
        learning_context = scheduler.get("context")
        if not isinstance(algorithm, str) or not algorithm:
            raise CompositionError("scheduler algorithm must be a non-empty string")
        if not isinstance(learning_context, str) or not learning_context:
            raise CompositionError("scheduler context must be a non-empty string")
        try:
            with self.workspace._connect() as db:
                schedules = current_schedules(
                    db,
                    algorithm=algorithm,
                    learning_context=learning_context,
                    focus=focus,
                )
                attempt_rows = db.execute(
                    """SELECT event_id, item_id, result, agent_help, support_json,
                              occurred_at, created_at
                       FROM attempts
                       ORDER BY occurred_at DESC, created_at DESC, event_id"""
                ).fetchall()
        except WorkloadDataError as exc:
            raise CompositionError(str(exc)) from exc

        due: list[tuple[datetime, str]] = []
        new: list[str] = []
        future: list[str] = []
        for schedule in schedules:
            if schedule.due_at is None:
                new.append(schedule.item_id)
            elif schedule.due_at <= now:
                due.append((schedule.due_at, schedule.item_id))
            else:
                future.append(schedule.item_id)
        due.sort(key=lambda value: (value[0], value[1]))
        new.sort()
        future.sort()
        candidates = [item_id for _, item_id in due] + new
        evaluable = [item_id for _, item_id in due] + new + future
        if not evaluable:
            if focus is not None:
                raise CompositionError(
                    f"no learning item is due in focus '{focus}'; "
                    "add an item with that focus or return later"
                )
            raise CompositionError("no learning item is due; add an item or return later")
        evaluable_set = set(evaluable)
        latest_by_item: dict[str, sqlite3.Row] = {}
        for row in attempt_rows:
            latest_by_item.setdefault(row["item_id"], row)

        def gap_severity(item_id: str) -> int:
            row = latest_by_item.get(item_id)
            if row is None:
                return 0
            if row["result"] == "not-demonstrated":
                return _GAP_SEVERITY["not-demonstrated"]
            if row["result"] == "partial":
                return _GAP_SEVERITY["partial"]
            support = self._support_kinds(row["support_json"], item_id=item_id)
            if any(kind in _SUPPORT_KINDS for kind in support):
                return _GAP_SEVERITY["assisted"]
            return 0

        gap: sqlite3.Row | None = None
        best_severity = 0
        for item_id in sorted(evaluable_set):
            severity = gap_severity(item_id)
            if severity > best_severity:
                best_severity = severity
                gap = latest_by_item[item_id]

        if gap is not None:
            primary_id = gap["item_id"]
            support = self._support_kinds(gap["support_json"], item_id=primary_id)
            aspects = []
            if gap["result"] in _GAP_RESULTS:
                aspects.append(f"a {gap['result']} result")
            if any(kind in _SUPPORT_KINDS for kind in support):
                aspects.append("assistance (" + ", ".join(support) + ")")
            rationale = (
                f"Targets the observed gap in '{primary_id}': the latest recorded "
                f"attempt shows {' and '.join(aspects)}, so this exact item version "
                "returns before new material."
            )
            source_event_ids = (gap["event_id"],)
            gap_item_id: str | None = primary_id
        else:
            primary_id = candidates[0] if candidates else evaluable[0]
            scope = f" within focus '{focus}'" if focus is not None else ""
            if due and primary_id == due[0][1]:
                rationale = f"Selected the earliest due item{scope}; ties use item id."
            elif primary_id == evaluable[0] and not candidates:
                rationale = (
                    f"Selected the next scheduled item{scope}; its review is approaching."
                )
            else:
                rationale = f"Selected a new item in deterministic item-id order{scope}."
            source_event_ids = ()
            gap_item_id = None

        skipped: list[dict[str, Any]] = []
        for item_id in evaluable:
            if item_id == primary_id or item_id == gap_item_id:
                continue
            attempt = latest_by_item.get(item_id)
            if attempt is None or attempt["result"] != "demonstrated":
                continue
            item = self._load(item_id)
            skipped.append(
                {
                    "item_id": item_id,
                    "item_content_hash": item.content_hash,
                    "reason": (
                        "Most recent attempt demonstrated the material; later review "
                        "follows the scheduler."
                    ),
                    "source_event_ids": [attempt["event_id"]],
                }
            )

        alternatives = tuple(item_id for item_id in evaluable if item_id != primary_id)
        return primary_id, "practice", rationale, source_event_ids, skipped, alternatives

    def _pending_learn_states(self, focus: str | None) -> list[Any]:
        try:
            with self.workspace._connect() as db:
                states = current_learning_states(db, focus=focus)
        except (LearningStateDataError, sqlite3.Error) as exc:
            raise CompositionError(str(exc)) from exc
        return [state for state in states if state.action == "learn"]

    def _load(self, item_id: str) -> LearningItem:
        try:
            return self.workspace.load_item(item_id)
        except WorkspaceError as exc:
            raise CompositionError(str(exc)) from exc

    def _store_proposal(
        self, proposal: FocusProposal, *, cited_hashes: dict[str, str]
    ) -> None:
        payload_json = json.dumps(
            proposal.payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        )
        with self.workspace._connect() as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                db.execute(
                    """INSERT OR IGNORE INTO composition_proposals(
                           proposal_id, focus_scope, action, primary_item_id,
                           payload_json, occurred_at
                       ) VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        proposal.proposal_id,
                        proposal.focus_scope,
                        proposal.action,
                        proposal.primary_item_id,
                        payload_json,
                        proposal.payload["occurred_at"],
                    ),
                )
                for item_id, content_hash in sorted(cited_hashes.items()):
                    if not isinstance(content_hash, str):
                        continue
                    db.execute(
                        """INSERT OR IGNORE INTO composition_proposal_items(
                               proposal_id, item_id, item_content_hash
                           ) VALUES (?, ?, ?)""",
                        (proposal.proposal_id, item_id, content_hash),
                    )
                db.execute("COMMIT")
            except sqlite3.Error as exc:
                db.execute("ROLLBACK")
                raise CompositionError(f"proposal could not be recorded: {exc}") from exc

    @staticmethod
    def _support_kinds(support_json: str, *, item_id: str) -> tuple[str, ...]:
        try:
            support = json.loads(support_json)
        except (json.JSONDecodeError, TypeError) as exc:
            raise CompositionError(
                f"invalid attempt support JSON for item {item_id}"
            ) from exc
        if not isinstance(support, list):
            raise CompositionError(f"invalid attempt support JSON for item {item_id}")
        kinds: list[str] = []
        for entry in support:
            if isinstance(entry, dict) and isinstance(entry.get("kind"), str):
                kinds.append(entry["kind"])
        return tuple(kinds)


def _deterministic_id(prefix: str, payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return prefix + hashlib.sha256(encoded).hexdigest()[:32]
