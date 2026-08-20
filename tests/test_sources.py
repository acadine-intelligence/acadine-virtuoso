from __future__ import annotations

import json
import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from virtuoso.workspace import WorkspaceError, WorkspaceService


class SourceIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve() / "learner"
        self.vault = Path(self.tmp.name).resolve() / "vault"
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
        alias = Path(self.tmp.name).resolve() / "vault-alias"
        alias.symlink_to(self.vault, target_is_directory=True)

        with self.assertRaisesRegex(WorkspaceError, "source root must not be a symlink"):
            self.service.add_source(source_id="alias", kind="obsidian", root=alias)

    def test_connect_source_rejects_equal_ancestor_and_descendant_workspace_roots(self) -> None:
        overlapping_roots = {
            "equal": self.root,
            "ancestor": self.root.parent,
            "descendant": self.root / "items",
        }
        for source_id, root in overlapping_roots.items():
            with self.subTest(source_id=source_id), self.assertRaisesRegex(
                WorkspaceError, "overlaps the Virtuoso workspace"
            ):
                self.service.add_source(
                    source_id=source_id,
                    kind="markdown",
                    root=root,
                )

    def test_open_rejects_incompatible_source_schema(self) -> None:
        import sqlite3

        with sqlite3.connect(self.service.db_path) as db:
            db.execute("DROP TABLE source_documents")
            db.execute("CREATE TABLE source_documents(source_id TEXT)")

        with self.assertRaisesRegex(WorkspaceError, "source_documents"):
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
        outside = Path(self.tmp.name).resolve() / "outside.md"
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

    def test_markdown_candidate_limit_counts_oversized_files(self) -> None:
        for index in range(3):
            (self.vault / f"oversized-{index}.md").write_text(
                "# Oversized\n" + "x" * 100,
                encoding="utf-8",
            )
        self.service.add_source(source_id="notes", kind="markdown", root=self.vault)

        with self.assertRaisesRegex(WorkspaceError, "file limit"):
            self.service.scan_source("notes", max_files=1, max_file_bytes=16)
        self.assertEqual(self.service.list_source_documents("notes"), [])

    def test_traversal_error_preserves_previously_indexed_metadata(self) -> None:
        restricted = self.vault / "restricted"
        restricted.mkdir()
        note = restricted / "kept.md"
        note.write_text("# Kept\n\nPreviously indexed.\n", encoding="utf-8")
        self.service.add_source(source_id="notes", kind="markdown", root=self.vault)
        self.service.scan_source("notes")
        self.assertEqual(
            [document.relative_path for document in self.service.list_source_documents("notes")],
            ["restricted/kept.md"],
        )

        restricted.chmod(0)
        try:
            with self.assertRaisesRegex(WorkspaceError, "source traversal failed"):
                self.service.scan_source("notes")
        finally:
            restricted.chmod(0o700)

        self.assertEqual(
            [document.relative_path for document in self.service.list_source_documents("notes")],
            ["restricted/kept.md"],
        )

    def test_file_replaced_by_symlink_during_scan_is_never_indexed(self) -> None:
        note = self.vault / "raced.md"
        safe_body = "# Safe\n\nInside the declared source.\n"
        note.write_text(safe_body, encoding="utf-8")
        outside = Path(self.tmp.name).resolve() / "outside-private.md"
        outside_body = "# Outside\n\nMust never cross the source boundary.\n"
        outside.write_text(outside_body, encoding="utf-8")
        safe_hash = hashlib.sha256(safe_body.encode("utf-8")).hexdigest()
        outside_hash = hashlib.sha256(outside_body.encode("utf-8")).hexdigest()
        self.service.add_source(source_id="notes", kind="markdown", root=self.vault)

        original_is_symlink = Path.is_symlink
        swapped = False

        def replace_after_symlink_check(path: Path) -> bool:
            nonlocal swapped
            result = original_is_symlink(path)
            if path == note and not swapped:
                note.unlink()
                note.symlink_to(outside)
                swapped = True
            return result

        accepted_hash: str | None = None
        with patch.object(Path, "is_symlink", replace_after_symlink_check):
            try:
                self.service.scan_source("notes")
            except WorkspaceError:
                pass
            else:
                documents = self.service.list_source_documents("notes")
                accepted_hash = documents[0].content_hash if documents else None

        self.assertNotEqual(accepted_hash, outside_hash)
        if accepted_hash is not None:
            self.assertEqual(accepted_hash, safe_hash)

    def test_scan_prunes_symlinked_directories(self) -> None:
        outside = Path(self.tmp.name).resolve() / "outside"
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

    def test_doctor_marks_link_stale_if_source_root_becomes_symlink(self) -> None:
        note = self.vault / "Testing Effect.md"
        note.write_text("# Testing Effect\n\nOriginal.\n", encoding="utf-8")
        self.service.add_source(source_id="vault", kind="obsidian", root=self.vault)
        self.service.scan_source("vault")
        self.service.add_item(
            item_id="testing-effect",
            title="Explain the testing effect",
            focus="learning-science",
            prompt="Why does retrieval strengthen memory?",
            answer="Retrieval changes memory.",
        )
        self.service.link_item_source(
            item_id="testing-effect", source_id="vault", relative_path="Testing Effect.md"
        )

        original = Path(self.tmp.name).resolve() / "original-vault"
        self.vault.rename(original)
        outside = Path(self.tmp.name).resolve() / "outside-vault"
        outside.mkdir()
        (outside / "Testing Effect.md").write_text("# Private replacement\n", encoding="utf-8")
        self.vault.symlink_to(outside, target_is_directory=True)

        health = self.service.doctor()

        self.assertEqual(health["status"], "needs-attention")
        self.assertEqual(health["stale_source_links"][0]["source_id"], "vault")

    def test_doctor_does_not_follow_source_note_replaced_by_symlink(self) -> None:
        note = self.vault / "Testing Effect.md"
        body = "# Testing Effect\n\nOriginal.\n"
        note.write_text(body, encoding="utf-8")
        self.service.add_source(source_id="vault", kind="obsidian", root=self.vault)
        self.service.scan_source("vault")
        self.service.add_item(
            item_id="testing-effect",
            title="Explain the testing effect",
            focus="learning-science",
            prompt="Why does retrieval strengthen memory?",
            answer="Retrieval changes memory.",
        )
        self.service.link_item_source(
            item_id="testing-effect", source_id="vault", relative_path="Testing Effect.md"
        )
        outside = Path(self.tmp.name).resolve() / "outside-private.md"
        outside.write_text(body, encoding="utf-8")
        note.unlink()
        note.symlink_to(outside)

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

    def test_relink_item_source_rebinds_after_conscious_content_change(self) -> None:
        note = self.vault / "Testing Effect.md"
        note.write_text("# Testing Effect\n\nVersion one.\n", encoding="utf-8")
        self.service.add_source(source_id="vault", kind="obsidian", root=self.vault)
        self.service.scan_source("vault")
        self.service.add_item(
            item_id="testing-effect",
            title="Explain the testing effect",
            focus="learning-science",
            prompt="Why does retrieval strengthen memory?",
            answer="Retrieval changes memory.",
        )
        first = self.service.link_item_source(
            item_id="testing-effect",
            source_id="vault",
            relative_path="Testing Effect.md",
        )

        note.write_text("# Testing Effect\n\nVersion two, edited by the learner.\n", encoding="utf-8")
        self.service.scan_source("vault")
        health = self.service.doctor()
        self.assertEqual(health["status"], "needs-attention")
        self.assertEqual(len(health["stale_source_links"]), 1)

        relink = self.service.relink_item_source(
            item_id="testing-effect",
            source_id="vault",
            relative_path="Testing Effect.md",
        )
        self.assertEqual(
            relink["source_content_hash"],
            self.service.list_source_documents("vault")[0].content_hash,
        )
        self.assertNotEqual(relink["source_content_hash"], first["source_content_hash"])
        self.assertEqual(self.service.doctor()["stale_source_links"], [])
        self.assertEqual(self.service.doctor()["status"], "healthy")

    def test_relink_rejects_link_that_is_not_stale(self) -> None:
        note = self.vault / "Testing Effect.md"
        note.write_text("# Testing Effect\n\nStable.\n", encoding="utf-8")
        self.service.add_source(source_id="vault", kind="obsidian", root=self.vault)
        self.service.scan_source("vault")
        self.service.add_item(
            item_id="testing-effect",
            title="Explain the testing effect",
            focus="learning-science",
            prompt="Why does retrieval strengthen memory?",
            answer="Retrieval changes memory.",
        )
        self.service.link_item_source(
            item_id="testing-effect",
            source_id="vault",
            relative_path="Testing Effect.md",
        )

        with self.assertRaises(WorkspaceError) as caught:
            self.service.relink_item_source(
                item_id="testing-effect",
                source_id="vault",
                relative_path="Testing Effect.md",
            )
        self.assertIn("not stale", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
