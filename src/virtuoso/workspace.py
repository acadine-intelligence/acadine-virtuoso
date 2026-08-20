from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


WORKSPACE_SCHEMA = "virtuoso/workspace@0.1"
ITEM_SCHEMA = "virtuoso/item@0.1"
_ITEM_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class WorkspaceError(RuntimeError):
    """A workspace operation could not be completed without losing truth."""


@dataclass(frozen=True)
class ItemSummary:
    item_id: str
    title: str
    focus: str
    path: Path
    content_hash: str


@dataclass(frozen=True)
class LearningItem:
    item_id: str
    title: str
    focus: str
    path: Path
    content_hash: str
    prompt: str
    answer: str
    hint: str | None
    follow_up: str | None
    learning_context: str = "atomic-recall"


@dataclass(frozen=True)
class SelectionResult:
    item: LearningItem
    rationale: str
    alternatives: tuple[str, ...]
    uncertainty: str | None = None


class WorkspaceService:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.config_path = self.root / "virtuoso.json"
        self.items_dir = self.root / "items"
        self.state_dir = self.root / ".virtuoso"
        self.db_path = self.state_dir / "state.sqlite3"

    @classmethod
    def init(cls, root: Path | str) -> "WorkspaceService":
        service = cls(Path(root))
        if service.config_path.exists():
            raise WorkspaceError(f"workspace already exists at {service.root}")
        if service.root.exists() and any(service.root.iterdir()):
            raise WorkspaceError(
                f"directory is not empty and is not a Virtuoso workspace: {service.root}"
            )

        service.items_dir.mkdir(parents=True, exist_ok=True)
        service.state_dir.mkdir(parents=True, exist_ok=True)
        config = {
            "schema": WORKSPACE_SCHEMA,
            "mode": "simple",
            "scheduler": {
                "algorithm": "fsrs",
                "context": "atomic-recall",
                "desired_retention": 0.9,
                "enable_fuzzing": False,
            },
        }
        service.config_path.write_text(
            json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        service._migrate()
        return service

    @classmethod
    def open(cls, root: Path | str) -> "WorkspaceService":
        service = cls(Path(root))
        if not service.config_path.is_file() or not service.db_path.is_file():
            raise WorkspaceError(
                f"not a Virtuoso workspace: {service.root}; run 'virtuoso init' first"
            )
        config = service.configuration()
        if config.get("schema") != WORKSPACE_SCHEMA:
            raise WorkspaceError(
                f"unsupported workspace schema: {config.get('schema')!r}"
            )
        service._migrate()
        return service

    def configuration(self) -> dict[str, Any]:
        try:
            value = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise WorkspaceError(f"invalid workspace configuration: {exc}") from exc
        if not isinstance(value, dict):
            raise WorkspaceError("workspace configuration must be a JSON object")
        return value

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path)
        db.execute("PRAGMA foreign_keys = ON")
        db.row_factory = sqlite3.Row
        return db

    def _migrate(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS items (
                    item_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    focus TEXT NOT NULL,
                    relative_path TEXT NOT NULL UNIQUE,
                    content_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS attempts (
                    event_id TEXT PRIMARY KEY,
                    item_id TEXT NOT NULL REFERENCES items(item_id),
                    item_content_hash TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    initial_response TEXT NOT NULL,
                    initial_latency_ms INTEGER NOT NULL CHECK(initial_latency_ms >= 0),
                    result TEXT NOT NULL CHECK(result IN ('demonstrated','partial','not-demonstrated')),
                    confidence INTEGER NOT NULL CHECK(confidence BETWEEN 1 AND 5),
                    open_notes INTEGER NOT NULL CHECK(open_notes IN (0,1)),
                    agent_help TEXT NOT NULL CHECK(agent_help IN ('none','light','substantial','unknown')),
                    support_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS scheduler_state (
                    item_id TEXT NOT NULL REFERENCES items(item_id),
                    algorithm TEXT NOT NULL,
                    algorithm_version TEXT NOT NULL,
                    learning_context TEXT NOT NULL,
                    configuration_json TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    source_event_id TEXT NOT NULL REFERENCES attempts(event_id),
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(item_id, algorithm, learning_context)
                );
                CREATE TABLE IF NOT EXISTS scheduler_proposals (
                    proposal_id TEXT PRIMARY KEY,
                    source_event_id TEXT NOT NULL UNIQUE REFERENCES attempts(event_id),
                    item_id TEXT NOT NULL REFERENCES items(item_id),
                    algorithm TEXT NOT NULL,
                    algorithm_version TEXT NOT NULL,
                    learning_context TEXT NOT NULL,
                    configuration_json TEXT NOT NULL,
                    previous_state_json TEXT,
                    proposed_state_json TEXT NOT NULL,
                    due_at TEXT NOT NULL,
                    rationale TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS module_receipts (
                    receipt_id TEXT PRIMARY KEY,
                    module_id TEXT NOT NULL,
                    module_version TEXT NOT NULL,
                    category TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    manifest_sha256 TEXT NOT NULL,
                    stdout_sha256 TEXT NOT NULL,
                    duration_ms INTEGER NOT NULL CHECK(duration_ms >= 0),
                    occurred_at TEXT NOT NULL
                );
                """
            )
            db.execute(
                "INSERT OR IGNORE INTO schema_migrations(version) VALUES (1)"
            )

    def add_item(
        self,
        *,
        item_id: str,
        title: str,
        focus: str,
        prompt: str,
        answer: str,
        hint: str | None = None,
        follow_up: str | None = None,
    ) -> ItemSummary:
        if not _ITEM_ID.fullmatch(item_id):
            raise WorkspaceError(
                "item id must be lowercase words or numbers separated by single dashes"
            )
        required = {
            "title": title,
            "focus": focus,
            "prompt": prompt,
            "answer": answer,
        }
        empty = [name for name, value in required.items() if not value.strip()]
        if empty:
            raise WorkspaceError(f"required item fields are empty: {', '.join(empty)}")

        path = self.items_dir / f"{item_id}.md"
        if path.exists():
            raise WorkspaceError(f"item already exists: {item_id}")

        text = self._render_item(
            item_id=item_id,
            title=title.strip(),
            focus=focus.strip(),
            prompt=prompt.strip(),
            answer=answer.strip(),
            hint=hint.strip() if hint and hint.strip() else None,
            follow_up=follow_up.strip() if follow_up and follow_up.strip() else None,
        )
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        path.write_text(text, encoding="utf-8")
        try:
            with self._connect() as db:
                db.execute(
                    """
                    INSERT INTO items(item_id, title, focus, relative_path, content_hash)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (item_id, title.strip(), focus.strip(), f"items/{path.name}", content_hash),
                )
        except sqlite3.IntegrityError as exc:
            path.unlink(missing_ok=True)
            raise WorkspaceError(f"item already exists: {item_id}") from exc

        return ItemSummary(
            item_id=item_id,
            title=title.strip(),
            focus=focus.strip(),
            path=path,
            content_hash=content_hash,
        )

    def load_item(self, item_id: str) -> LearningItem:
        with self._connect() as db:
            row = db.execute(
                "SELECT item_id, title, focus, relative_path, content_hash "
                "FROM items WHERE item_id = ?",
                (item_id,),
            ).fetchone()
        if row is None:
            raise WorkspaceError(f"no learning item with id: {item_id}")
        path = (self.root / row["relative_path"]).resolve()
        try:
            path.relative_to(self.items_dir.resolve())
        except ValueError as exc:
            raise WorkspaceError(f"item path escapes workspace: {item_id}") from exc
        if not path.is_file():
            raise WorkspaceError(f"item file is missing: {path}")
        text = path.read_text(encoding="utf-8")
        current_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if current_hash != row["content_hash"]:
            raise WorkspaceError(
                f"item is stale because its Markdown changed: {item_id}; sync it before practice"
            )

        return LearningItem(
            item_id=row["item_id"],
            title=row["title"],
            focus=row["focus"],
            path=path,
            content_hash=current_hash,
            prompt=self._section(text, "Prompt", required=True),
            answer=self._section(text, "Answer", required=True),
            hint=self._section(text, "Hint", required=False),
            follow_up=self._section(text, "Follow-up challenge", required=False),
        )

    def scheduler_state(
        self, *, item_id: str, algorithm: str, learning_context: str
    ) -> str | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT state_json FROM scheduler_state "
                "WHERE item_id = ? AND algorithm = ? AND learning_context = ?",
                (item_id, algorithm, learning_context),
            ).fetchone()
        return row["state_json"] if row else None

    def record_attempt(
        self,
        *,
        attempt: dict[str, Any],
        proposal: dict[str, Any],
        state_json: str,
    ) -> None:
        support_json = json.dumps(attempt["support_actions"], sort_keys=True)
        config_json = json.dumps(proposal["configuration"], sort_keys=True)
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO attempts(
                    event_id, item_id, item_content_hash, occurred_at,
                    initial_response, initial_latency_ms, result, confidence,
                    open_notes, agent_help, support_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    attempt["event_id"],
                    attempt["item_id"],
                    attempt["item_content_hash"],
                    attempt["occurred_at"],
                    attempt["initial_response"],
                    attempt["initial_latency_ms"],
                    attempt["result"],
                    attempt["confidence"],
                    int(attempt["open_notes"]),
                    attempt["agent_help"],
                    support_json,
                ),
            )
            db.execute(
                """
                INSERT INTO scheduler_proposals(
                    proposal_id, source_event_id, item_id, algorithm,
                    algorithm_version, learning_context, configuration_json,
                    previous_state_json, proposed_state_json, due_at,
                    rationale, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    proposal["proposal_id"],
                    proposal["source_event_id"],
                    proposal["item_id"],
                    proposal["algorithm"],
                    proposal["algorithm_version"],
                    proposal["learning_context"],
                    config_json,
                    proposal["previous_state_json"],
                    state_json,
                    proposal["due_at"],
                    proposal["rationale"],
                    proposal["created_at"],
                ),
            )
            db.execute(
                """
                INSERT INTO scheduler_state(
                    item_id, algorithm, algorithm_version, learning_context,
                    configuration_json, state_json, source_event_id, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(item_id, algorithm, learning_context) DO UPDATE SET
                    algorithm_version = excluded.algorithm_version,
                    configuration_json = excluded.configuration_json,
                    state_json = excluded.state_json,
                    source_event_id = excluded.source_event_id,
                    updated_at = excluded.updated_at
                """,
                (
                    proposal["item_id"],
                    proposal["algorithm"],
                    proposal["algorithm_version"],
                    proposal["learning_context"],
                    config_json,
                    state_json,
                    proposal["source_event_id"],
                    proposal["created_at"],
                ),
            )

    def list_attempts(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM attempts ORDER BY occurred_at, event_id"
            ).fetchall()
        attempts: list[dict[str, Any]] = []
        for row in rows:
            attempt = dict(row)
            attempt["open_notes"] = bool(attempt["open_notes"])
            attempt["support_actions"] = json.loads(attempt["support_json"])
            attempts.append(attempt)
        return attempts

    def list_proposals(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT proposal_id, source_event_id, item_id, algorithm,
                       algorithm_version, learning_context, configuration_json,
                       due_at, rationale, created_at
                FROM scheduler_proposals
                ORDER BY created_at, proposal_id
                """
            ).fetchall()
        proposals: list[dict[str, Any]] = []
        for row in rows:
            proposal = dict(row)
            proposal["configuration"] = json.loads(
                proposal.pop("configuration_json")
            )
            proposals.append(proposal)
        return proposals

    def record_module_receipt(
        self, *, manifest: Any, result: Any
    ) -> dict[str, Any]:
        try:
            manifest_sha256 = hashlib.sha256(manifest.path.read_bytes()).hexdigest()
        except OSError as exc:
            raise WorkspaceError(f"cannot hash module manifest: {exc}") from exc
        receipt = {
            "receipt_id": f"module-{uuid.uuid4().hex}",
            "module_id": result.module_id,
            "module_version": result.module_version,
            "category": manifest.category,
            "kind": result.kind,
            "manifest_sha256": manifest_sha256,
            "stdout_sha256": result.stdout_sha256,
            "duration_ms": result.duration_ms,
            "occurred_at": datetime.now(timezone.utc).isoformat(),
        }
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO module_receipts(
                    receipt_id, module_id, module_version, category, kind,
                    manifest_sha256, stdout_sha256, duration_ms, occurred_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                tuple(receipt.values()),
            )
        return receipt

    def list_module_receipts(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM module_receipts ORDER BY occurred_at, receipt_id"
            ).fetchall()
        return [dict(row) for row in rows]

    def select_next(self, now: datetime) -> SelectionResult:
        if now.tzinfo is None or now.utcoffset() is None:
            raise WorkspaceError("selection timestamp must be timezone-aware")
        now_utc = now.astimezone(timezone.utc)
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT i.item_id, p.due_at
                FROM items AS i
                LEFT JOIN scheduler_state AS s
                  ON s.item_id = i.item_id
                 AND s.algorithm = 'fsrs'
                 AND s.learning_context = 'atomic-recall'
                LEFT JOIN scheduler_proposals AS p
                  ON p.source_event_id = s.source_event_id
                ORDER BY i.item_id
                """
            ).fetchall()

        due: list[tuple[datetime, str]] = []
        new: list[str] = []
        for row in rows:
            if row["due_at"] is None:
                new.append(row["item_id"])
                continue
            due_at = datetime.fromisoformat(row["due_at"]).astimezone(timezone.utc)
            if due_at <= now_utc:
                due.append((due_at, row["item_id"]))

        due.sort(key=lambda value: (value[0], value[1]))
        new.sort()
        candidates = [item_id for _, item_id in due] + new
        if not candidates:
            raise WorkspaceError("no learning item is due; add an item or return later")

        selected_id = candidates[0]
        if due and selected_id == due[0][1]:
            rationale = "Selected the earliest due item; ties use item id."
        else:
            rationale = "Selected a new item in deterministic item-id order."
        return SelectionResult(
            item=self.load_item(selected_id),
            rationale=rationale,
            alternatives=tuple(candidates[1:]),
        )

    def doctor(self) -> dict[str, Any]:
        config = self.configuration()
        stale_items: list[str] = []
        with self._connect() as db:
            database = db.execute("PRAGMA quick_check").fetchone()[0]
            rows = db.execute(
                "SELECT item_id, relative_path, content_hash FROM items ORDER BY item_id"
            ).fetchall()
            attempt_count = db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0]
            proposal_count = db.execute(
                "SELECT COUNT(*) FROM scheduler_proposals"
            ).fetchone()[0]
        for row in rows:
            path = self.root / row["relative_path"]
            try:
                current_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError:
                stale_items.append(row["item_id"])
                continue
            if current_hash != row["content_hash"]:
                stale_items.append(row["item_id"])
        healthy = database == "ok" and not stale_items
        return {
            "status": "healthy" if healthy else "needs-attention",
            "workspace_schema": config.get("schema"),
            "database": database,
            "items": len(rows),
            "attempts": attempt_count,
            "proposals": proposal_count,
            "stale_items": stale_items,
        }

    @staticmethod
    def _section(text: str, heading: str, *, required: bool) -> str | None:
        match = re.search(
            rf"(?ms)^# {re.escape(heading)}\n\n(.*?)(?=\n# |\Z)", text
        )
        value = match.group(1).strip() if match else ""
        if required and not value:
            raise WorkspaceError(f"learning item is missing section: {heading}")
        return value or None

    @staticmethod
    def _render_item(
        *,
        item_id: str,
        title: str,
        focus: str,
        prompt: str,
        answer: str,
        hint: str | None,
        follow_up: str | None,
    ) -> str:
        lines = [
            "---",
            f"schema: {ITEM_SCHEMA}",
            f"id: {json.dumps(item_id)}",
            f"title: {json.dumps(title, ensure_ascii=False)}",
            f"focus: {json.dumps(focus, ensure_ascii=False)}",
            "practice-format: active-recall",
            "learning-context: atomic-recall",
            "---",
            "",
            "# Prompt",
            "",
            prompt,
            "",
            "# Answer",
            "",
            answer,
        ]
        if hint:
            lines.extend(["", "# Hint", "", hint])
        if follow_up:
            lines.extend(["", "# Follow-up challenge", "", follow_up])
        return "\n".join(lines) + "\n"
