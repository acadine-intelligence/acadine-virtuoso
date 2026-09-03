from __future__ import annotations

import importlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from conftest import downgrade_attempt_chain_to_v9
from virtuoso.workspace import WorkspaceService


class StructuralCandidateTests(unittest.TestCase):
    PRIVATE_MARKER = "PRIVATE-BODY-MARKER-7f4fb8c0"

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace_root = Path(self.tmp.name).resolve() / "learner"
        self.vault = Path(self.tmp.name).resolve() / "vault"
        self.vault.mkdir()
        self.workspace = WorkspaceService.init(self.workspace_root)
        self.workspace.add_source(source_id="vault", kind="obsidian", root=self.vault)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _candidate_service(self) -> Any:
        try:
            module = importlib.import_module("virtuoso.candidates")
        except ModuleNotFoundError:
            self.fail("structural candidate service is not implemented")
        return module.CandidateService(self.workspace)

    def _write_note(self, relative_path: str, body: str) -> Path:
        path = self.vault / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        return path

    def _vault_snapshot(self) -> dict[str, tuple[bytes, int]]:
        return {
            path.relative_to(self.vault).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in sorted(self.vault.rglob("*"))
            if path.is_file() and not path.is_symlink()
        }

    def _table_count(self, table: str) -> int:
        with sqlite3.connect(self.workspace.db_path) as db:
            return int(db.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])

    def _table_rows(self, table: str) -> list[tuple[object, ...]]:
        with sqlite3.connect(self.workspace.db_path) as db:
            return [
                tuple(row)
                for row in db.execute(f'SELECT * FROM "{table}" ORDER BY 1').fetchall()
            ]

    def _candidate_module(self) -> Any:
        return importlib.import_module("virtuoso.candidates")

    def _run_cli(
        self, *args: str, expected: int = 0
    ) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "virtuoso.cli",
                "--workspace",
                str(self.workspace_root),
                *args,
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            expected,
            msg=f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}",
        )
        return completed

    def test_unresolved_wikilink_surfaces_atomic_note_need_without_draft_body(self) -> None:
        note = self._write_note(
            "Learning/Origin.md",
            "# Origin\n\n"
            f"{self.PRIVATE_MARKER}: learner-authored private prose.\n\n"
            "A structural link points to [[Unwritten Concept]].\n",
        )
        self.workspace.scan_source("vault")
        indexed = self.workspace.list_source_documents("vault")[0]
        before = self._vault_snapshot()

        run = self._candidate_service().generate(
            source_id="vault",
            relative_path="Learning/Origin.md",
        )

        self.assertEqual(run.source_id, "vault")
        self.assertEqual(run.scope_relative_path, "Learning/Origin.md")
        self.assertEqual(run.candidate_count, 1)
        self.assertEqual(run.omitted_count, 0)
        self.assertFalse(run.truncated)
        self.assertEqual(len(run.snapshot_sha256), 64)
        candidate = run.candidates[0]
        self.assertEqual(candidate.kind, "atomic-note")
        self.assertEqual(candidate.reason_code, "unresolved-wikilink")
        self.assertEqual(candidate.authority, "proposal")
        self.assertEqual(candidate.review_state, "proposed")
        self.assertFalse(candidate.claims_mastery)
        self.assertEqual(candidate.source_status, "current")
        self.assertEqual(candidate.proposal["schema"], "virtuoso/atomic-note-candidate@0.1")
        self.assertEqual(candidate.proposal["observed_target"], "Unwritten Concept")
        self.assertIsNone(candidate.proposal["claim"])
        self.assertTrue(candidate.proposal["requires_human_drafting"])
        self.assertEqual(candidate.source_refs[0].content_hash, indexed.content_hash)
        self.assertEqual(candidate.source_refs[0].relative_path, indexed.relative_path)
        self.assertIn("human verification", candidate.uncertainty or "")

        serialized = json.dumps(run.to_dict(), sort_keys=True)
        self.assertNotIn(self.PRIVATE_MARKER, serialized)
        self.assertNotIn(str(note), serialized)
        self.assertNotIn(self.PRIVATE_MARKER.encode(), self.workspace.db_path.read_bytes())
        self.assertEqual(self._table_count("candidate_runs"), 1)
        self.assertEqual(self._table_count("review_candidates"), 1)
        self.assertEqual(self._table_count("candidate_source_refs"), 1)
        self.assertEqual(self._vault_snapshot(), before)

    def test_ambiguous_wikilink_surfaces_link_disambiguation_without_inference(self) -> None:
        self._write_note(
            "Origin.md",
            "# Origin\n\n"
            f"{self.PRIVATE_MARKER}: origin prose.\n\n"
            "Review [[Topic]].\n",
        )
        first = self._write_note(
            "A/First.md",
            "---\ntitle: Topic\n---\n\n"
            f"{self.PRIVATE_MARKER}: first target prose.\n",
        )
        second = self._write_note(
            "B/Topic.md",
            "# A differently titled note\n\n"
            f"{self.PRIVATE_MARKER}: second target prose.\n",
        )
        self.workspace.scan_source("vault")
        before = self._vault_snapshot()

        try:
            run = self._candidate_service().generate(
                source_id="vault",
                relative_path="Origin.md",
            )
        except Exception as exc:  # RED must report missing behavior as an assertion.
            self.fail(f"ambiguous wikilink was not surfaced: {exc}")

        self.assertEqual(run.candidate_count, 1)
        candidate = run.candidates[0]
        self.assertEqual(candidate.kind, "link")
        self.assertEqual(candidate.reason_code, "ambiguous-wikilink")
        self.assertEqual(candidate.proposal["schema"], "virtuoso/link-candidate@0.1")
        self.assertEqual(candidate.proposal["mode"], "disambiguate-existing-wikilink")
        self.assertEqual(candidate.proposal["observed_target"], "Topic")
        self.assertIsNone(candidate.proposal["selected_target"])
        self.assertTrue(candidate.proposal["requires_human_choice"])
        expected_options = [
            {
                "source_id": document.source_id,
                "relative_path": document.relative_path,
                "content_hash": document.content_hash,
            }
            for document in self.workspace.list_source_documents("vault")
            if document.relative_path in {"A/First.md", "B/Topic.md"}
        ]
        self.assertEqual(candidate.proposal["options"], expected_options)
        self.assertEqual(
            [ref.relative_path for ref in candidate.source_refs],
            ["Origin.md", "A/First.md", "B/Topic.md"],
        )
        disclosed = json.dumps(candidate.to_dict(), sort_keys=True)
        for forbidden in (
            self.PRIVATE_MARKER,
            str(first),
            str(second),
            "prerequisite",
            "capability",
            "project_priority",
            "scheduler",
            "learning_item",
        ):
            self.assertNotIn(forbidden, disclosed)
        self.assertEqual(self._vault_snapshot(), before)

    def test_resolved_link_surfaces_connect_practice_without_creating_evidence(self) -> None:
        self._write_note(
            "Origin.md",
            "# Origin\n\n"
            f"{self.PRIVATE_MARKER}: private origin body.\n\n"
            "Connect this note to [[Target]].\n",
        )
        self._write_note(
            "Knowledge/Target.md",
            "# Target\n\n"
            f"{self.PRIVATE_MARKER}: private target body.\n",
        )
        self.workspace.scan_source("vault")
        item = self.workspace.add_item(
            item_id="existing-item",
            title="Existing learning item",
            focus="separation",
            prompt="What must remain unchanged?",
            answer="Learning and evidence state.",
        )
        self.workspace.record_transfer(
            item_id=item.item_id,
            project_id="existing-project",
            use_case="Existing historical transfer evidence.",
            outcome="partial",
            independence="guided",
        )
        protected_tables = (
            "items",
            "attempts",
            "scheduler_state",
            "scheduler_proposals",
            "transfer_events",
            "transfer_checks",
            "transfer_check_predictions",
            "transfer_check_completions",
            "item_source_links",
        )
        before = {table: self._table_rows(table) for table in protected_tables}

        try:
            run = self._candidate_service().generate(
                source_id="vault",
                relative_path="Origin.md",
            )
        except Exception as exc:
            self.fail(f"resolved wikilink practice candidate was not surfaced: {exc}")

        candidate = run.candidates[0]
        self.assertEqual(candidate.kind, "practice")
        self.assertEqual(candidate.reason_code, "resolved-link-practice")
        self.assertEqual(candidate.proposal["schema"], "virtuoso/practice-candidate@0.1")
        self.assertEqual(candidate.proposal["mode"], "connect")
        self.assertIsNone(candidate.proposal["answer"])
        self.assertTrue(candidate.proposal["requires_human_answer"])
        self.assertFalse(candidate.proposal["creates_learning_item"])
        self.assertFalse(candidate.proposal["creates_evidence_event"])
        self.assertFalse(candidate.claims_mastery)
        self.assertEqual(
            [ref.relative_path for ref in candidate.source_refs],
            ["Origin.md", "Knowledge/Target.md"],
        )
        self.assertNotIn(self.PRIVATE_MARKER, json.dumps(candidate.to_dict()))
        self.assertEqual(
            {table: self._table_rows(table) for table in protected_tables},
            before,
        )

    def test_note_without_links_surfaces_one_explain_practice_candidate(self) -> None:
        self._write_note(
            "Isolated.md",
            "# Isolated concept\n\n"
            f"{self.PRIVATE_MARKER}: private prose with no outgoing links.\n",
        )
        self.workspace.scan_source("vault")

        try:
            run = self._candidate_service().generate(
                source_id="vault",
                relative_path="Isolated.md",
            )
        except Exception as exc:
            self.fail(f"isolated-note practice candidate was not surfaced: {exc}")

        self.assertEqual(run.candidate_count, 1)
        candidate = run.candidates[0]
        self.assertEqual(candidate.kind, "practice")
        self.assertEqual(candidate.reason_code, "isolated-note-practice")
        self.assertEqual(candidate.proposal["mode"], "explain")
        self.assertIsNone(candidate.proposal["answer"])
        self.assertEqual(len(candidate.source_refs), 1)
        self.assertNotIn(self.PRIVATE_MARKER, json.dumps(run.to_dict()))

    def test_same_snapshot_is_deterministic_and_idempotent(self) -> None:
        self._write_note(
            "Origin.md",
            "# Origin\n\n"
            "Links: [[Zeta Missing]], [[Topic]], [[ＣＡＦÉ]].\n",
        )
        self._write_note("A/Topic.md", "# First topic\n",)
        self._write_note("B/Other.md", "---\ntitle: Topic\n---\n",)
        self._write_note("C/Cafe.md", "---\ntitle: Cafe\u0301\n---\n",)
        self.workspace.scan_source("vault")
        service = self._candidate_service()

        try:
            first = service.generate(source_id="vault", relative_path="Origin.md", limit=20)
            second = service.generate(source_id="vault", relative_path="Origin.md", limit=20)
        except Exception as exc:
            self.fail(f"unchanged candidate snapshot was not idempotent: {exc}")

        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertEqual(first.run_id, second.run_id)
        self.assertEqual(
            [candidate.kind for candidate in first.candidates],
            ["atomic-note", "link", "practice"],
        )
        self.assertEqual(
            [candidate.candidate_id for candidate in first.candidates],
            [candidate.candidate_id for candidate in second.candidates],
        )
        self.assertEqual(self._table_count("candidate_runs"), 1)
        self.assertEqual(self._table_count("review_candidates"), 3)
        self.assertEqual(self._table_count("candidate_source_refs"), 6)

    def test_candidate_order_uses_normalized_observed_wikilink_target(self) -> None:
        self._write_note(
            "Origin.md",
            "# Origin\n\n[[Z/Second]] and [[A/First]].\n",
        )
        self._write_note("A/First.md", "# Zulu title\n")
        self._write_note("Z/Second.md", "# Alpha title\n")
        self.workspace.scan_source("vault")

        run = self._candidate_service().generate(
            source_id="vault",
            relative_path="Origin.md",
        )

        self.assertEqual(
            [candidate.source_refs[1].relative_path for candidate in run.candidates],
            ["A/First.md", "Z/Second.md"],
        )

    def test_candidate_run_is_bounded_and_atomic(self) -> None:
        self._write_note(
            "Origin.md",
            "# Origin\n\n[[Gamma]], [[Alpha]], and [[Beta]].\n",
        )
        self.workspace.scan_source("vault")
        service = self._candidate_service()
        run = service.generate(source_id="vault", relative_path="Origin.md", limit=2)
        self.assertEqual(run.candidate_count, 2)
        self.assertEqual(run.omitted_count, 1)
        self.assertTrue(run.truncated)
        self.assertEqual(
            [candidate.proposal["observed_target"] for candidate in run.candidates],
            ["Alpha", "Beta"],
        )
        baseline = {
            table: self._table_rows(table)
            for table in ("candidate_runs", "review_candidates", "candidate_source_refs")
        }

        module = self._candidate_module()
        catalog = tuple(self.workspace.list_source_documents("vault"))
        origin = next(document for document in catalog if document.relative_path == "Origin.md")
        complete = module.generate_structural_candidates(origin, catalog, limit=50)
        over_limit = module.CandidateBatch(
            drafts=complete.drafts,
            snapshot_sha256=complete.snapshot_sha256,
            omitted_count=0,
            truncated=False,
        )
        with patch.object(module, "generate_structural_candidates", return_value=over_limit):
            try:
                with self.assertRaisesRegex(module.CandidateError, "limit|bounded|output"):
                    service.generate(source_id="vault", relative_path="Origin.md", limit=2)
            except Exception as exc:
                self.fail(f"over-limit generator output did not fail closed: {exc}")
        self.assertEqual(
            {
                table: self._table_rows(table)
                for table in ("candidate_runs", "review_candidates", "candidate_source_refs")
            },
            baseline,
        )

        bad_ref = module.IndexedNoteRef(
            source_id="vault",
            relative_path="Origin.md",
            title="Origin",
            content_hash="0" * 64,
        )
        malformed = module.CandidateDraft(
            kind="atomic-note",
            title="Malformed draft",
            reason_code="unresolved-wikilink",
            rationale="This malformed draft must roll back the complete run.",
            uncertainty="Human verification is required.",
            proposal={
                "schema": "virtuoso/atomic-note-candidate@0.1",
                "mode": "unresolved-wikilink",
                "observed_target": "Gamma",
                "suggested_title": "Gamma",
                "claim": "Invented body",
                "requires_human_drafting": True,
                "draft_body": self.PRIVATE_MARKER,
            },
            source_refs=(bad_ref,),
        )
        malformed_batch = module.CandidateBatch(
            drafts=(complete.drafts[0], malformed),
            snapshot_sha256=complete.snapshot_sha256,
            omitted_count=1,
            truncated=True,
        )
        with patch.object(module, "generate_structural_candidates", return_value=malformed_batch):
            try:
                with self.assertRaisesRegex(
                    module.CandidateError, "proposal|source|hash|malformed"
                ):
                    service.generate(source_id="vault", relative_path="Origin.md", limit=3)
            except Exception as exc:
                self.fail(f"malformed candidate batch did not fail closed: {exc}")
        self.assertEqual(
            {
                table: self._table_rows(table)
                for table in ("candidate_runs", "review_candidates", "candidate_source_refs")
            },
            baseline,
        )
        self.assertNotIn(self.PRIVATE_MARKER.encode(), self.workspace.db_path.read_bytes())

        for invalid_limit in (0, 51, True):
            with self.subTest(limit=invalid_limit), self.assertRaisesRegex(
                module.CandidateError, "between 1 and 50"
            ):
                service.generate(
                    source_id="vault",
                    relative_path="Origin.md",
                    limit=invalid_limit,
                )

    def test_source_drift_is_visible_without_refresh_or_deletion(self) -> None:
        note = self._write_note(
            "Origin.md",
            "# Origin\n\n"
            f"{self.PRIVATE_MARKER}: immutable source provenance.\n\n"
            "[[Missing target]]\n",
        )
        original_bytes = note.read_bytes()
        self.workspace.scan_source("vault")
        service = self._candidate_service()
        run = service.generate(source_id="vault", relative_path="Origin.md")
        candidate_id = run.candidates[0].candidate_id
        stored_hash = run.candidates[0].source_refs[0].content_hash
        baseline_counts = {
            table: self._table_count(table)
            for table in ("candidate_runs", "review_candidates", "candidate_source_refs")
        }

        try:
            self.assertEqual(service.get(candidate_id).source_status, "current")
            self.assertEqual(
                [candidate.candidate_id for candidate in service.list(current_only=True)],
                [candidate_id],
            )
        except (AttributeError, NotImplementedError) as exc:
            self.fail(f"candidate stale-state API is not implemented: {exc}")

        note.write_bytes(original_bytes + b"\nChanged after indexing.\n")
        changed = service.get(candidate_id)
        self.assertEqual(changed.source_status, "changed")
        self.assertEqual(service.list(current_only=True), [])
        self.assertEqual(service.list()[0].candidate_id, candidate_id)
        self.assertEqual(service.list()[0].source_status, "changed")
        module = self._candidate_module()
        with self.assertRaisesRegex(module.CandidateError, "stale|changed|rescan"):
            service.generate(
                source_id="vault",
                relative_path="Origin.md",
                limit=19,
            )

        note.unlink()
        self.assertEqual(service.get(candidate_id).source_status, "missing")
        outside = Path(self.tmp.name).resolve() / "outside.md"
        outside.write_bytes(original_bytes)
        note.symlink_to(outside)
        self.assertEqual(service.get(candidate_id).source_status, "unsafe")

        with sqlite3.connect(self.workspace.db_path) as db:
            immutable_hash = db.execute(
                "SELECT content_hash FROM candidate_source_refs WHERE candidate_id = ?",
                (candidate_id,),
            ).fetchone()[0]
        self.assertEqual(immutable_hash, stored_hash)
        self.assertEqual(
            {
                table: self._table_count(table)
                for table in ("candidate_runs", "review_candidates", "candidate_source_refs")
            },
            baseline_counts,
        )

    def test_candidate_migration_seven_is_atomic_and_fail_closed(self) -> None:
        with sqlite3.connect(self.workspace.db_path) as db:
            for table in (
                "candidate_source_refs",
                "review_candidates",
                "candidate_runs",
                "candidate_decisions",
            ):
                db.execute(f'DROP TABLE "{table}"')
            # v6 predates migration 10's attempt-chain rebuild.
            downgrade_attempt_chain_to_v9(db)
            db.execute("PRAGMA legacy_alter_table = ON")
            db.execute("ALTER TABLE items RENAME TO items_with_retired")
            db.execute(
                """CREATE TABLE items (
                    item_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    focus TEXT NOT NULL,
                    relative_path TEXT NOT NULL UNIQUE,
                    content_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )"""
            )
            db.execute(
                "INSERT INTO items(item_id, title, focus, relative_path, "
                "content_hash, created_at) "
                "SELECT item_id, title, focus, relative_path, content_hash, "
                "created_at FROM items_with_retired"
            )
            db.execute("DROP TABLE items_with_retired")
            db.execute("PRAGMA legacy_alter_table = OFF")
            db.execute("DELETE FROM schema_migrations WHERE version >= 7")
            sources_before = db.execute("SELECT * FROM sources ORDER BY source_id").fetchall()

        reopened = WorkspaceService.open(self.workspace_root)
        with sqlite3.connect(reopened.db_path) as db:
            versions = [
                row[0]
                for row in db.execute(
                    "SELECT version FROM schema_migrations ORDER BY version"
                ).fetchall()
            ]
            tables = {
                row[0]
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            sources_after = db.execute("SELECT * FROM sources ORDER BY source_id").fetchall()
        self.assertEqual(versions, [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14])
        self.assertTrue(
            {"candidate_runs", "review_candidates", "candidate_source_refs"}.issubset(tables)
        )
        self.assertEqual(sources_after, sources_before)

        failed_root = Path(self.tmp.name).resolve() / "failed-migration"
        failed = WorkspaceService.init(failed_root)
        with sqlite3.connect(failed.db_path) as db:
            for table in (
                "candidate_source_refs",
                "review_candidates",
                "candidate_runs",
                "candidate_decisions",
            ):
                db.execute(f'DROP TABLE "{table}"')
            # v6 predates migration 10's attempt-chain rebuild.
            downgrade_attempt_chain_to_v9(db)
            db.execute("PRAGMA legacy_alter_table = ON")
            db.execute("ALTER TABLE items RENAME TO items_with_retired")
            db.execute(
                """CREATE TABLE items (
                    item_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    focus TEXT NOT NULL,
                    relative_path TEXT NOT NULL UNIQUE,
                    content_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )"""
            )
            db.execute(
                "INSERT INTO items(item_id, title, focus, relative_path, "
                "content_hash, created_at) "
                "SELECT item_id, title, focus, relative_path, content_hash, "
                "created_at FROM items_with_retired"
            )
            db.execute("DROP TABLE items_with_retired")
            db.execute("PRAGMA legacy_alter_table = OFF")
            db.execute("DELETE FROM schema_migrations WHERE version >= 7")
            db.execute("CREATE TABLE review_candidates(candidate_id TEXT PRIMARY KEY)")

        with self.assertRaisesRegex(
            Exception, "review_candidates|migration failed|incompatible database schema"
        ):
            WorkspaceService.open(failed_root)
        with sqlite3.connect(failed.db_path) as db:
            failed_versions = [
                row[0]
                for row in db.execute(
                    "SELECT version FROM schema_migrations ORDER BY version"
                ).fetchall()
            ]
            failed_tables = {
                row[0]
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
        self.assertEqual(failed_versions, [1, 2, 3, 4, 5, 6])
        self.assertIn("review_candidates", failed_tables)
        self.assertNotIn("candidate_runs", failed_tables)
        self.assertNotIn("candidate_source_refs", failed_tables)

    def test_candidate_schema_rejects_approved_canonical_or_mastery_rows(self) -> None:
        run_id = "candidate-run-" + "a" * 64
        with sqlite3.connect(self.workspace.db_path) as db:
            db.execute("PRAGMA foreign_keys = ON")
            db.execute(
                """
                INSERT INTO candidate_runs(
                    run_id, generator_id, generator_version, source_id,
                    scope_relative_path, snapshot_sha256, max_candidates,
                    candidate_count, omitted_count, truncated, created_at
                ) VALUES (?, 'test', '0.1', 'vault', 'Origin.md', ?, 20, 1, 0, 0, ?)
                """,
                (run_id, "b" * 64, "2026-08-20T10:00:00+00:00"),
            )
            base = {
                "run_id": run_id,
                "ordinal": 0,
                "kind": "atomic-note",
                "title": "Atomic note needed",
                "reason_code": "unresolved-wikilink",
                "rationale": "An explicit indexed link has no indexed match.",
                "uncertainty": "Human verification is required.",
                "proposal_json": json.dumps(
                    {
                        "schema": "virtuoso/atomic-note-candidate@0.1",
                        "mode": "unresolved-wikilink",
                        "observed_target": "Target",
                        "suggested_title": "Target",
                        "claim": None,
                        "requires_human_drafting": True,
                    }
                ),
                "created_at": "2026-08-20T10:00:00+00:00",
            }
            for index, (authority, review_state, claims_mastery) in enumerate(
                (
                    ("canonical", "proposed", 0),
                    ("proposal", "approved", 0),
                    ("proposal", "proposed", 1),
                )
            ):
                with self.subTest(
                    authority=authority,
                    review_state=review_state,
                    claims_mastery=claims_mastery,
                ), self.assertRaises(sqlite3.IntegrityError):
                    db.execute(
                        """
                        INSERT INTO review_candidates(
                            candidate_id, run_id, ordinal, kind, title, reason_code,
                            rationale, uncertainty, proposal_json, authority,
                            review_state, claims_mastery, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            "candidate-" + str(index) * 64,
                            base["run_id"],
                            base["ordinal"],
                            base["kind"],
                            base["title"],
                            base["reason_code"],
                            base["rationale"],
                            base["uncertainty"],
                            base["proposal_json"],
                            authority,
                            review_state,
                            claims_mastery,
                            base["created_at"],
                        ),
                    )

        with sqlite3.connect(self.workspace.db_path) as db:
            db.execute("DROP TABLE candidate_source_refs")
            db.execute("DROP TABLE review_candidates")
            db.execute("DROP TABLE candidate_runs")
            db.execute(
                """CREATE TABLE candidate_runs(
                    run_id TEXT PRIMARY KEY,
                    generator_id TEXT,
                    generator_version TEXT,
                    source_id TEXT,
                    scope_relative_path TEXT,
                    snapshot_sha256 TEXT,
                    max_candidates INTEGER,
                    candidate_count INTEGER,
                    omitted_count INTEGER,
                    truncated INTEGER,
                    created_at TEXT
                )"""
            )
        with self.assertRaisesRegex(Exception, "candidate_runs|incompatible database schema"):
            WorkspaceService.open(self.workspace_root)

    def test_candidate_cli_generate_list_show_has_no_apply_path(self) -> None:
        note = self._write_note(
            "Origin.md",
            "# Origin\n\n"
            f"{self.PRIVATE_MARKER}: CLI-private note body.\n\n"
            "[[Unwritten Concept]]\n",
        )
        self.workspace.scan_source("vault")
        before = self._vault_snapshot()

        help_text = self._run_cli("candidate", "--help").stdout.lower()
        for command in ("generate", "delta", "list", "show", "decide"):
            self.assertIn(command, help_text)
        for forbidden in (
            "apply",
            "materialize",
            "create-note",
            "add-item",
            "sync",
        ):
            self.assertNotIn(forbidden, help_text)

        generated_process = self._run_cli(
            "candidate",
            "generate",
            "--source",
            "vault",
            "--path",
            "Origin.md",
            "--limit",
            "20",
            "--json",
        )
        generated = json.loads(generated_process.stdout)
        self.assertEqual(
            set(generated),
            {
                "schema",
                "run_id",
                "generator_id",
                "generator_version",
                "source_id",
                "scope_relative_path",
                "snapshot_sha256",
                "max_candidates",
                "candidate_count",
                "omitted_count",
                "truncated",
                "created_at",
                "candidates",
            },
        )
        candidate = generated["candidates"][0]
        self.assertEqual(
            set(candidate),
            {
                "schema",
                "candidate_id",
                "run_id",
                "kind",
                "title",
                "reason_code",
                "rationale",
                "uncertainty",
                "authority",
                "review_state",
                "claims_mastery",
                "source_refs",
                "proposal",
                "source_status",
            },
        )
        self.assertEqual(candidate["authority"], "proposal")
        self.assertEqual(candidate["review_state"], "proposed")
        self.assertFalse(candidate["claims_mastery"])
        self.assertEqual(candidate["source_status"], "current")

        listed = json.loads(
            self._run_cli(
                "candidate",
                "list",
                "--source",
                "vault",
                "--kind",
                "atomic-note",
                "--run",
                generated["run_id"],
                "--current-only",
                "--json",
            ).stdout
        )
        self.assertEqual(set(listed), {"schema", "candidates"})
        self.assertEqual(listed["schema"], "virtuoso/review-candidate-list@0.1")
        self.assertEqual(listed["candidates"], [candidate])
        shown_process = self._run_cli(
            "candidate",
            "show",
            "--id",
            candidate["candidate_id"],
            "--json",
        )
        self.assertEqual(json.loads(shown_process.stdout), candidate)
        plain = self._run_cli(
            "candidate", "show", "--id", candidate["candidate_id"]
        ).stdout

        disclosed = generated_process.stdout + shown_process.stdout + plain
        self.assertNotIn(self.PRIVATE_MARKER, disclosed)
        self.assertNotIn(str(note), disclosed)
        self.assertNotIn(self.PRIVATE_MARKER.encode(), self.workspace.db_path.read_bytes())
        self.assertEqual(self._vault_snapshot(), before)

        for args in (
            ("candidate", "generate", "--source", "vault", "--path", "Origin.md", "--limit", "0", "--json"),
            ("candidate", "generate", "--source", "vault", "--path", "Origin.md", "--limit", "51", "--json"),
            ("candidate", "generate", "--source", "missing", "--path", "Origin.md", "--json"),
            ("candidate", "generate", "--source", "vault", "--path", "Missing.md", "--json"),
        ):
            failed = self._run_cli(*args, expected=2)
            self.assertEqual(failed.stdout, "")
            self.assertNotIn("Traceback", failed.stderr)

        note.write_text(note.read_text(encoding="utf-8") + "\nChanged later.\n", encoding="utf-8")
        stale_list = json.loads(self._run_cli("candidate", "list", "--json").stdout)
        self.assertEqual(stale_list["candidates"][0]["source_status"], "changed")
        current_list = json.loads(
            self._run_cli("candidate", "list", "--current-only", "--json").stdout
        )
        self.assertEqual(current_list["candidates"], [])

        note.unlink()
        outside = Path(self.tmp.name).resolve() / "outside-cli.md"
        outside.write_text("# Outside\n", encoding="utf-8")
        note.symlink_to(outside)
        unsafe = self._run_cli(
            "candidate",
            "generate",
            "--source",
            "vault",
            "--path",
            "Origin.md",
            "--limit",
            "19",
            "--json",
            expected=2,
        )
        self.assertIn("unsafe", unsafe.stderr.lower())

        with sqlite3.connect(self.workspace.db_path) as db:
            db.execute("PRAGMA ignore_check_constraints = ON")
            db.execute(
                "UPDATE review_candidates SET proposal_json = '{' WHERE candidate_id = ?",
                (candidate["candidate_id"],),
            )
        malformed = self._run_cli("candidate", "list", "--json", expected=2)
        self.assertEqual(malformed.stdout, "")
        self.assertIn("proposal", malformed.stderr.lower())
        self.assertNotIn("Traceback", malformed.stderr)


if __name__ == "__main__":
    unittest.main()
