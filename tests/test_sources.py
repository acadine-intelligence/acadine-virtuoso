from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from virtuoso.workspace import WorkspaceError, WorkspaceService


class SourceIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "learner"
        self.vault = Path(self.tmp.name) / "vault"
        self.vault.mkdir()
        self.service = WorkspaceService.init(self.root)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_connect_source_records_read_only_external_root(self) -> None:
        source = self.service.add_source(
            source_id="core-vault", kind="obsidian", root=self.vault
        )

        self.assertEqual(source.source_id, "core-vault")
        self.assertEqual(source.kind, "obsidian")
        self.assertEqual(source.root, self.vault.resolve())
        self.assertTrue(source.read_only)
        self.assertEqual(self.service.list_sources(), [source])

    def test_connect_source_rejects_unsafe_id_missing_root_and_duplicate(self) -> None:
        with self.assertRaisesRegex(WorkspaceError, "source id"):
            self.service.add_source(source_id="../vault", kind="obsidian", root=self.vault)
        with self.assertRaisesRegex(WorkspaceError, "does not exist"):
            self.service.add_source(
                source_id="missing", kind="markdown", root=self.vault / "missing"
            )
        self.service.add_source(source_id="core-vault", kind="obsidian", root=self.vault)
        with self.assertRaisesRegex(WorkspaceError, "already exists"):
            self.service.add_source(
                source_id="core-vault", kind="obsidian", root=self.vault
            )

    def test_connect_source_rejects_symlinked_root(self) -> None:
        alias = Path(self.tmp.name) / "vault-alias"
        alias.symlink_to(self.vault, target_is_directory=True)

        with self.assertRaisesRegex(WorkspaceError, "source root must not be a symlink"):
            self.service.add_source(source_id="alias", kind="obsidian", root=alias)

    def test_open_rejects_incompatible_source_schema(self) -> None:
        import sqlite3

        with sqlite3.connect(self.service.db_path) as db:
            db.execute("DROP TABLE source_documents")
            db.execute("CREATE TABLE source_documents(source_id TEXT)")

        with self.assertRaisesRegex(WorkspaceError, "source_documents is missing"):
            WorkspaceService.open(self.root)

    def test_scan_indexes_metadata_and_wikilinks_without_copying_bodies(self) -> None:
        note = self.vault / "Learning" / "Testing Effect.md"
        note.parent.mkdir()
        body = """---
title: Testing Effect in Practice
---

# Ignored fallback title

Secret private prose that must stay in the vault.
Links: [[Active Recall]], [[Spacing#Intervals|spacing]], [[Active Recall]].
"""
        note.write_text(body, encoding="utf-8")
        self.service.add_source(source_id="core-vault", kind="obsidian", root=self.vault)

        receipt = self.service.scan_source("core-vault")
        documents = self.service.list_source_documents("core-vault")

        self.assertEqual(receipt.indexed, 1)
        self.assertEqual(receipt.removed, 0)
        self.assertEqual(len(documents), 1)
        document = documents[0]
        self.assertEqual(document.relative_path, "Learning/Testing Effect.md")
        self.assertEqual(document.title, "Testing Effect in Practice")
        self.assertEqual(document.wikilinks, ("Active Recall", "Spacing"))
        self.assertEqual(len(document.content_hash), 64)

        database_bytes = (self.root / ".virtuoso" / "state.sqlite3").read_bytes()
        self.assertNotIn(b"Secret private prose", database_bytes)
        config = json.loads((self.root / "virtuoso.json").read_text())
        self.assertNotIn("Secret private prose", json.dumps(config))

    def test_rescan_removes_deleted_metadata_and_keeps_source_unchanged(self) -> None:
        note = self.vault / "Atomic.md"
        note.write_text("# Atomic\n\nHuman-authored source.\n", encoding="utf-8")
        original = note.read_bytes()
        self.service.add_source(source_id="notes", kind="markdown", root=self.vault)
        self.service.scan_source("notes")
        note.unlink()

        receipt = self.service.scan_source("notes")

        self.assertEqual(receipt.indexed, 0)
        self.assertEqual(receipt.removed, 1)
        self.assertEqual(self.service.list_source_documents("notes"), [])
        self.assertEqual(original, b"# Atomic\n\nHuman-authored source.\n")

    def test_scan_rejects_markdown_symlink_and_file_limit_without_partial_update(self) -> None:
        outside = Path(self.tmp.name) / "outside.md"
        outside.write_text("# Outside\n", encoding="utf-8")
        (self.vault / "escape.md").symlink_to(outside)
        self.service.add_source(source_id="notes", kind="markdown", root=self.vault)

        with self.assertRaisesRegex(WorkspaceError, "Markdown symlink"):
            self.service.scan_source("notes")
        self.assertEqual(self.service.list_source_documents("notes"), [])

        (self.vault / "escape.md").unlink()
        (self.vault / "one.md").write_text("# One\n", encoding="utf-8")
        (self.vault / "two.md").write_text("# Two\n", encoding="utf-8")
        with self.assertRaisesRegex(WorkspaceError, "file limit"):
            self.service.scan_source("notes", max_files=1)
        self.assertEqual(self.service.list_source_documents("notes"), [])

    def test_scan_prunes_symlinked_directories(self) -> None:
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir()
        (outside / "private.md").write_text("# Private\n\nDo not index.\n", encoding="utf-8")
        (self.vault / "linked-dir").symlink_to(outside, target_is_directory=True)
        (self.vault / "safe.md").write_text("# Safe\n", encoding="utf-8")
        self.service.add_source(source_id="notes", kind="markdown", root=self.vault)

        receipt = self.service.scan_source("notes")

        self.assertEqual(receipt.indexed, 1)
        self.assertEqual(
            [document.relative_path for document in self.service.list_source_documents("notes")],
            ["safe.md"],
        )

    def test_scan_enforces_file_and_total_byte_limits_without_partial_update(self) -> None:
        (self.vault / "large.md").write_text("# Large\n" + "x" * 100, encoding="utf-8")
        self.service.add_source(source_id="notes", kind="markdown", root=self.vault)

        skipped = self.service.scan_source("notes", max_file_bytes=16)
        self.assertEqual(skipped.indexed, 0)
        self.assertEqual(skipped.skipped, 1)

        (self.vault / "small.md").write_text("# Small\n" + "y" * 12, encoding="utf-8")
        with self.assertRaisesRegex(WorkspaceError, "total Markdown byte limit"):
            self.service.scan_source("notes", max_file_bytes=1_000, max_total_bytes=20)
        self.assertEqual(self.service.list_source_documents("notes"), [])

    def test_item_can_link_to_indexed_note_and_doctor_detects_source_drift(self) -> None:
        note = self.vault / "Testing Effect.md"
        note.write_text("# Testing Effect\n\nVersion one.\n", encoding="utf-8")
        self.service.add_source(source_id="vault", kind="obsidian", root=self.vault)
        self.service.scan_source("vault")
        self.service.add_item(
            item_id="testing-effect",
            title="Explain the testing effect",
            focus="learning-science",
            prompt="Why does retrieval strengthen memory?",
            answer="Retrieval changes memory and improves later access.",
        )

        link = self.service.link_item_source(
            item_id="testing-effect",
            source_id="vault",
            relative_path="Testing Effect.md",
        )
        self.assertEqual(link["source_content_hash"], self.service.list_source_documents("vault")[0].content_hash)
        self.assertEqual(self.service.doctor()["stale_source_links"], [])

        note.unlink()
        receipt = self.service.scan_source("vault")
        self.assertEqual(receipt.removed, 1)
        health = self.service.doctor()
        self.assertEqual(health["status"], "needs-attention")
        self.assertEqual(
            health["stale_source_links"],
            [
                {
                    "item_id": "testing-effect",
                    "source_id": "vault",
                    "relative_path": "Testing Effect.md",
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
