from __future__ import annotations

import hashlib
import json
import os
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
_SOURCE_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_WIKILINK = re.compile(r"\[\[([^\[\]]+)\]\]")
_SOURCE_KINDS = {"markdown", "obsidian"}


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


@dataclass(frozen=True)
class SourceSummary:
    source_id: str
    kind: str
    root: Path
    read_only: bool = True


@dataclass(frozen=True)
class SourceDocument:
    source_id: str
    relative_path: str
    title: str
    content_hash: str
    wikilinks: tuple[str, ...]
    modified_ns: int
    byte_size: int


@dataclass(frozen=True)
class SourceScanReceipt:
    receipt_id: str
    source_id: str
    indexed: int
    removed: int
    skipped: int
    total_bytes: int
    occurred_at: str


class WorkspaceService:
    def __init__(self, root: Path) -> None:
        self.requested_root = root.expanduser().absolute()
        self.root = self.requested_root.resolve(strict=False)
        self.config_path = self.root / "virtuoso.json"
        self.items_dir = self.root / "items"
        self.state_dir = self.root / ".virtuoso"
        self.db_path = self.state_dir / "state.sqlite3"

    @classmethod
    def init(cls, root: Path | str) -> "WorkspaceService":
        service = cls(Path(root))
        service._validate_owned_paths()
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
        service._validate_owned_paths(require_database=True)
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

    def _validate_owned_paths(self, *, require_database: bool = False) -> None:
        if self.requested_root.is_symlink():
            raise WorkspaceError(
                f"workspace root must not be a symlink: {self.requested_root}"
            )
        owned = (self.root, self.config_path, self.items_dir, self.state_dir)
        if require_database:
            owned = (*owned, self.db_path)
        for path in owned:
            if path.is_symlink():
                raise WorkspaceError(f"Virtuoso-owned path must not be a symlink: {path}")
        if self.root.exists() and not self.root.is_dir():
            raise WorkspaceError(f"workspace root is not a directory: {self.root}")
        if self.items_dir.exists() and not self.items_dir.is_dir():
            raise WorkspaceError(f"items path is not a directory: {self.items_dir}")
        if self.state_dir.exists() and not self.state_dir.is_dir():
            raise WorkspaceError(f"state path is not a directory: {self.state_dir}")

    def configuration(self) -> dict[str, Any]:
        try:
            value = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise WorkspaceError(f"invalid workspace configuration: {exc}") from exc
        if not isinstance(value, dict):
            raise WorkspaceError("workspace configuration must be a JSON object")
        return value

    def _connect(self) -> sqlite3.Connection:
        try:
            db = sqlite3.connect(self.db_path)
            db.execute("PRAGMA foreign_keys = ON")
            db.row_factory = sqlite3.Row
            return db
        except sqlite3.Error as exc:
            raise WorkspaceError(f"cannot open workspace database: {exc}") from exc

    def _migrate(self) -> None:
        try:
            self._migrate_unchecked()
        except sqlite3.Error as exc:
            raise WorkspaceError(f"workspace database migration failed: {exc}") from exc

    def _migrate_unchecked(self) -> None:
        statements = (
            """CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""",
            """CREATE TABLE IF NOT EXISTS items (
                item_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                focus TEXT NOT NULL,
                relative_path TEXT NOT NULL UNIQUE,
                content_hash TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""",
            """CREATE TABLE IF NOT EXISTS attempts (
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
            )""",
            """CREATE TABLE IF NOT EXISTS scheduler_state (
                item_id TEXT NOT NULL REFERENCES items(item_id),
                algorithm TEXT NOT NULL,
                algorithm_version TEXT NOT NULL,
                learning_context TEXT NOT NULL,
                configuration_json TEXT NOT NULL,
                state_json TEXT NOT NULL,
                source_event_id TEXT NOT NULL REFERENCES attempts(event_id),
                updated_at TEXT NOT NULL,
                PRIMARY KEY(item_id, algorithm, learning_context)
            )""",
            """CREATE TABLE IF NOT EXISTS scheduler_proposals (
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
            )""",
            """CREATE TABLE IF NOT EXISTS module_receipts (
                receipt_id TEXT PRIMARY KEY,
                module_id TEXT NOT NULL,
                module_version TEXT NOT NULL,
                category TEXT NOT NULL,
                kind TEXT NOT NULL,
                manifest_sha256 TEXT NOT NULL,
                stdout_sha256 TEXT NOT NULL,
                duration_ms INTEGER NOT NULL CHECK(duration_ms >= 0),
                occurred_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS sources (
                source_id TEXT PRIMARY KEY,
                kind TEXT NOT NULL CHECK(kind IN ('markdown','obsidian')),
                root_path TEXT NOT NULL,
                read_only INTEGER NOT NULL CHECK(read_only = 1),
                created_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS source_documents (
                source_id TEXT NOT NULL REFERENCES sources(source_id) ON DELETE CASCADE,
                relative_path TEXT NOT NULL,
                title TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                wikilinks_json TEXT NOT NULL,
                modified_ns INTEGER NOT NULL,
                byte_size INTEGER NOT NULL CHECK(byte_size >= 0),
                indexed_at TEXT NOT NULL,
                PRIMARY KEY(source_id, relative_path)
            )""",
            """CREATE TABLE IF NOT EXISTS source_scan_receipts (
                receipt_id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL REFERENCES sources(source_id),
                indexed INTEGER NOT NULL CHECK(indexed >= 0),
                removed INTEGER NOT NULL CHECK(removed >= 0),
                skipped INTEGER NOT NULL CHECK(skipped >= 0),
                total_bytes INTEGER NOT NULL CHECK(total_bytes >= 0),
                occurred_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS item_source_links (
                item_id TEXT NOT NULL REFERENCES items(item_id) ON DELETE CASCADE,
                source_id TEXT NOT NULL REFERENCES sources(source_id) ON DELETE CASCADE,
                source_relative_path TEXT NOT NULL,
                source_content_hash TEXT NOT NULL,
                linked_at TEXT NOT NULL,
                PRIMARY KEY(item_id, source_id, source_relative_path)
            )""",
        )
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for statement in statements:
                db.execute(statement)
            self._validate_database_schema(db)
            db.execute(
                "INSERT OR IGNORE INTO schema_migrations(version) VALUES (1)"
            )
            db.execute(
                "INSERT OR IGNORE INTO schema_migrations(version) VALUES (2)"
            )

    def add_source(
        self, *, source_id: str, kind: str, root: Path | str
    ) -> SourceSummary:
        if not _SOURCE_ID.fullmatch(source_id):
            raise WorkspaceError(
                "source id must be lowercase words or numbers separated by single dashes"
            )
        if kind not in _SOURCE_KINDS:
            raise WorkspaceError("source kind must be 'markdown' or 'obsidian'")
        requested_root = Path(root).expanduser().absolute()
        if requested_root.is_symlink():
            raise WorkspaceError(f"source root must not be a symlink: {requested_root}")
        source_root = requested_root.resolve()
        if not source_root.exists():
            raise WorkspaceError(f"source root does not exist: {source_root}")
        if not source_root.is_dir():
            raise WorkspaceError(f"source root is not a directory: {source_root}")
        created_at = datetime.now(timezone.utc).isoformat()
        try:
            with self._connect() as db:
                db.execute(
                    """
                    INSERT INTO sources(source_id, kind, root_path, read_only, created_at)
                    VALUES (?, ?, ?, 1, ?)
                    """,
                    (source_id, kind, str(source_root), created_at),
                )
        except sqlite3.IntegrityError as exc:
            raise WorkspaceError(f"source already exists: {source_id}") from exc
        return SourceSummary(source_id=source_id, kind=kind, root=source_root)

    def list_sources(self) -> list[SourceSummary]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT source_id, kind, root_path, read_only FROM sources ORDER BY source_id"
            ).fetchall()
        return [
            SourceSummary(
                source_id=row["source_id"],
                kind=row["kind"],
                root=Path(row["root_path"]),
                read_only=bool(row["read_only"]),
            )
            for row in rows
        ]

    def scan_source(
        self,
        source_id: str,
        *,
        max_files: int = 10_000,
        max_file_bytes: int = 2_000_000,
        max_total_bytes: int = 64_000_000,
    ) -> SourceScanReceipt:
        source = self._source(source_id)
        documents: list[SourceDocument] = []
        skipped = 0
        total_bytes = 0

        for directory, dirnames, filenames in os.walk(source.root, followlinks=False):
            directory_path = Path(directory)
            dirnames[:] = sorted(
                name
                for name in dirnames
                if not (directory_path / name).is_symlink()
            )
            for filename in sorted(filenames):
                path = directory_path / filename
                if path.suffix.lower() != ".md":
                    continue
                if path.is_symlink():
                    raise WorkspaceError(f"source contains a Markdown symlink: {path}")
                if len(documents) >= max_files:
                    raise WorkspaceError(f"source exceeds Markdown file limit: {max_files}")
                stat = path.stat()
                if stat.st_size > max_file_bytes:
                    skipped += 1
                    continue
                total_bytes += stat.st_size
                if total_bytes > max_total_bytes:
                    raise WorkspaceError(
                        f"source exceeds total Markdown byte limit: {max_total_bytes}"
                    )
                try:
                    text = path.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    skipped += 1
                    continue
                relative_path = path.relative_to(source.root).as_posix()
                documents.append(
                    SourceDocument(
                        source_id=source_id,
                        relative_path=relative_path,
                        title=self._source_title(text, path.stem),
                        content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                        wikilinks=self._wikilinks(text),
                        modified_ns=stat.st_mtime_ns,
                        byte_size=stat.st_size,
                    )
                )

        occurred_at = datetime.now(timezone.utc).isoformat()
        receipt_id = f"scan-{uuid.uuid4().hex}"
        current_paths = {document.relative_path for document in documents}
        with self._connect() as db:
            existing = {
                row["relative_path"]
                for row in db.execute(
                    "SELECT relative_path FROM source_documents WHERE source_id = ?",
                    (source_id,),
                ).fetchall()
            }
            removed_paths = existing - current_paths
            for relative_path in sorted(removed_paths):
                db.execute(
                    "DELETE FROM source_documents WHERE source_id = ? AND relative_path = ?",
                    (source_id, relative_path),
                )
            for document in documents:
                db.execute(
                    """
                    INSERT INTO source_documents(
                        source_id, relative_path, title, content_hash, wikilinks_json,
                        modified_ns, byte_size, indexed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(source_id, relative_path) DO UPDATE SET
                        title = excluded.title,
                        content_hash = excluded.content_hash,
                        wikilinks_json = excluded.wikilinks_json,
                        modified_ns = excluded.modified_ns,
                        byte_size = excluded.byte_size,
                        indexed_at = excluded.indexed_at
                    """,
                    (
                        document.source_id,
                        document.relative_path,
                        document.title,
                        document.content_hash,
                        json.dumps(document.wikilinks),
                        document.modified_ns,
                        document.byte_size,
                        occurred_at,
                    ),
                )
            db.execute(
                """
                INSERT INTO source_scan_receipts(
                    receipt_id, source_id, indexed, removed, skipped,
                    total_bytes, occurred_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    receipt_id,
                    source_id,
                    len(documents),
                    len(removed_paths),
                    skipped,
                    total_bytes,
                    occurred_at,
                ),
            )
        return SourceScanReceipt(
            receipt_id=receipt_id,
            source_id=source_id,
            indexed=len(documents),
            removed=len(removed_paths),
            skipped=skipped,
            total_bytes=total_bytes,
            occurred_at=occurred_at,
        )

    def list_source_documents(self, source_id: str) -> list[SourceDocument]:
        self._source(source_id)
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT source_id, relative_path, title, content_hash,
                       wikilinks_json, modified_ns, byte_size
                FROM source_documents
                WHERE source_id = ?
                ORDER BY relative_path
                """,
                (source_id,),
            ).fetchall()
        return [
            SourceDocument(
                source_id=row["source_id"],
                relative_path=row["relative_path"],
                title=row["title"],
                content_hash=row["content_hash"],
                wikilinks=tuple(json.loads(row["wikilinks_json"])),
                modified_ns=row["modified_ns"],
                byte_size=row["byte_size"],
            )
            for row in rows
        ]

    def link_item_source(
        self, *, item_id: str, source_id: str, relative_path: str
    ) -> dict[str, str]:
        normalized = Path(relative_path)
        if normalized.is_absolute() or ".." in normalized.parts:
            raise WorkspaceError("source relative path must stay inside its source root")
        normalized_path = normalized.as_posix()
        linked_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as db:
            item = db.execute(
                "SELECT item_id FROM items WHERE item_id = ?", (item_id,)
            ).fetchone()
            if item is None:
                raise WorkspaceError(f"no learning item with id: {item_id}")
            document = db.execute(
                """
                SELECT content_hash FROM source_documents
                WHERE source_id = ? AND relative_path = ?
                """,
                (source_id, normalized_path),
            ).fetchone()
            if document is None:
                raise WorkspaceError(
                    f"source note is not indexed: {source_id}/{normalized_path}; scan it first"
                )
            try:
                db.execute(
                    """
                    INSERT INTO item_source_links(
                        item_id, source_id, source_relative_path,
                        source_content_hash, linked_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        item_id,
                        source_id,
                        normalized_path,
                        document["content_hash"],
                        linked_at,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise WorkspaceError(
                    f"item source link already exists: {item_id} -> {source_id}/{normalized_path}"
                ) from exc
        return {
            "item_id": item_id,
            "source_id": source_id,
            "relative_path": normalized_path,
            "source_content_hash": document["content_hash"],
            "linked_at": linked_at,
        }

    def _source(self, source_id: str) -> SourceSummary:
        with self._connect() as db:
            row = db.execute(
                "SELECT source_id, kind, root_path, read_only FROM sources WHERE source_id = ?",
                (source_id,),
            ).fetchone()
        if row is None:
            raise WorkspaceError(f"no source with id: {source_id}")
        root = Path(row["root_path"])
        if root.is_symlink() or root.resolve(strict=False) != root:
            raise WorkspaceError(f"source root changed or became a symlink: {root}")
        if not root.is_dir():
            raise WorkspaceError(f"source root is unavailable: {root}")
        return SourceSummary(
            source_id=row["source_id"],
            kind=row["kind"],
            root=root,
            read_only=bool(row["read_only"]),
        )

    @staticmethod
    def _source_title(text: str, fallback: str) -> str:
        lines = text.splitlines()
        if lines and lines[0].strip() == "---":
            for line in lines[1:]:
                if line.strip() == "---":
                    break
                match = re.match(r"^title\s*:\s*(.+?)\s*$", line, re.IGNORECASE)
                if match:
                    return match.group(1).strip().strip("\"'") or fallback
        for line in lines:
            match = re.match(r"^#\s+(.+?)\s*$", line)
            if match:
                return match.group(1).strip()
        return fallback

    @staticmethod
    def _wikilinks(text: str) -> tuple[str, ...]:
        links: set[str] = set()
        for match in _WIKILINK.finditer(text):
            target = match.group(1).split("|", 1)[0].split("#", 1)[0].strip()
            if target:
                links.add(target)
        return tuple(sorted(links, key=str.casefold))

    @staticmethod
    def _validate_database_schema(db: sqlite3.Connection) -> None:
        required: dict[str, set[str]] = {
            "schema_migrations": {"version", "applied_at"},
            "items": {"item_id", "title", "focus", "relative_path", "content_hash"},
            "attempts": {
                "event_id", "item_id", "item_content_hash", "occurred_at",
                "initial_response", "initial_latency_ms", "result", "confidence",
                "open_notes", "agent_help", "support_json",
            },
            "scheduler_state": {
                "item_id", "algorithm", "algorithm_version", "learning_context",
                "configuration_json", "state_json", "source_event_id", "updated_at",
            },
            "scheduler_proposals": {
                "proposal_id", "source_event_id", "item_id", "algorithm",
                "algorithm_version", "learning_context", "configuration_json",
                "previous_state_json", "proposed_state_json", "due_at",
                "rationale", "created_at",
            },
            "module_receipts": {
                "receipt_id", "module_id", "module_version", "category", "kind",
                "manifest_sha256", "stdout_sha256", "duration_ms", "occurred_at",
            },
            "sources": {
                "source_id", "kind", "root_path", "read_only", "created_at",
            },
            "source_documents": {
                "source_id", "relative_path", "title", "content_hash",
                "wikilinks_json", "modified_ns", "byte_size", "indexed_at",
            },
            "source_scan_receipts": {
                "receipt_id", "source_id", "indexed", "removed", "skipped",
                "total_bytes", "occurred_at",
            },
            "item_source_links": {
                "item_id", "source_id", "source_relative_path",
                "source_content_hash", "linked_at",
            },
        }
        for name, expected_columns in required.items():
            row = db.execute(
                "SELECT type FROM sqlite_master WHERE name = ?", (name,)
            ).fetchone()
            if row is None or row[0] != "table":
                raise WorkspaceError(
                    f"incompatible database schema: {name} must be a table"
                )
            actual = {
                column[1] for column in db.execute(f'PRAGMA table_info("{name}")')
            }
            missing = sorted(expected_columns - actual)
            if missing:
                raise WorkspaceError(
                    f"incompatible database schema: {name} is missing "
                    + ", ".join(missing)
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
        self._validate_owned_paths(require_database=True)
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
        authored = [title, focus, prompt, answer, hint or "", follow_up or ""]
        if any(re.search(r"(?m)^# ", value) for value in authored):
            raise WorkspaceError(
                "item fields must not contain top-level Markdown headings; use ## or plain text"
            )

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
        self._validate_owned_paths(require_database=True)
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

    def scheduler_snapshot(
        self, *, item_id: str, algorithm: str, learning_context: str
    ) -> tuple[str | None, str | None]:
        with self._connect() as db:
            row = db.execute(
                "SELECT state_json, source_event_id FROM scheduler_state "
                "WHERE item_id = ? AND algorithm = ? AND learning_context = ?",
                (item_id, algorithm, learning_context),
            ).fetchone()
        if row is None:
            return None, None
        return row["state_json"], row["source_event_id"]

    def scheduler_state(
        self, *, item_id: str, algorithm: str, learning_context: str
    ) -> str | None:
        state, _source_event_id = self.scheduler_snapshot(
            item_id=item_id,
            algorithm=algorithm,
            learning_context=learning_context,
        )
        return state

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
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT source_event_id FROM scheduler_state "
                "WHERE item_id = ? AND algorithm = ? AND learning_context = ?",
                (
                    proposal["item_id"],
                    proposal["algorithm"],
                    proposal["learning_context"],
                ),
            ).fetchone()
            current_source = row["source_event_id"] if row else None
            expected_source = proposal.get("previous_source_event_id")
            if current_source != expected_source:
                raise WorkspaceError(
                    "scheduler state changed during practice; retry so the attempt can be rescheduled safely"
                )
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
        manifest_sha256 = getattr(manifest, "manifest_sha256", None)
        if not isinstance(manifest_sha256, str) or len(manifest_sha256) != 64:
            raise WorkspaceError("module manifest has no load-time SHA-256 identity")
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
        scheduler = self.configuration().get("scheduler")
        if not isinstance(scheduler, dict):
            raise WorkspaceError("workspace scheduler configuration is missing")
        algorithm = scheduler.get("algorithm")
        learning_context = scheduler.get("context")
        if not isinstance(algorithm, str) or not algorithm:
            raise WorkspaceError("scheduler algorithm must be a non-empty string")
        if not isinstance(learning_context, str) or not learning_context:
            raise WorkspaceError("scheduler context must be a non-empty string")
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT i.item_id, p.due_at
                FROM items AS i
                LEFT JOIN scheduler_state AS s
                  ON s.item_id = i.item_id
                 AND s.algorithm = ?
                 AND s.learning_context = ?
                LEFT JOIN scheduler_proposals AS p
                  ON p.source_event_id = s.source_event_id
                ORDER BY i.item_id
                """,
                (algorithm, learning_context),
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
        self._validate_owned_paths(require_database=True)
        config = self.configuration()
        stale_items: list[str] = []
        stale_source_links: list[dict[str, str]] = []
        with self._connect() as db:
            database = db.execute("PRAGMA quick_check").fetchone()[0]
            rows = db.execute(
                "SELECT item_id, relative_path, content_hash FROM items ORDER BY item_id"
            ).fetchall()
            attempt_count = db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0]
            proposal_count = db.execute(
                "SELECT COUNT(*) FROM scheduler_proposals"
            ).fetchone()[0]
            source_links = db.execute(
                """
                SELECT l.item_id, l.source_id, l.source_relative_path,
                       l.source_content_hash, s.root_path
                FROM item_source_links AS l
                JOIN sources AS s ON s.source_id = l.source_id
                ORDER BY l.item_id, l.source_id, l.source_relative_path
                """
            ).fetchall()
        for row in rows:
            path = self.root / row["relative_path"]
            try:
                current_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError:
                stale_items.append(row["item_id"])
                continue
            if current_hash != row["content_hash"]:
                stale_items.append(row["item_id"])
        for row in source_links:
            root = Path(row["root_path"])
            path = (root / row["source_relative_path"]).resolve()
            try:
                path.relative_to(root.resolve())
                current_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            except (OSError, ValueError):
                current_hash = ""
            if current_hash != row["source_content_hash"]:
                stale_source_links.append(
                    {
                        "item_id": row["item_id"],
                        "source_id": row["source_id"],
                        "relative_path": row["source_relative_path"],
                    }
                )
        healthy = database == "ok" and not stale_items and not stale_source_links
        return {
            "status": "healthy" if healthy else "needs-attention",
            "workspace_schema": config.get("schema"),
            "database": database,
            "items": len(rows),
            "attempts": attempt_count,
            "proposals": proposal_count,
            "stale_items": stale_items,
            "stale_source_links": stale_source_links,
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
