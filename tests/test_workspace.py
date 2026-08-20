from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from virtuoso.workspace import WorkspaceError, WorkspaceService


class WorkspaceServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "learner"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_init_creates_owned_markdown_and_sqlite_boundaries(self) -> None:
        summary = WorkspaceService.init(self.root)

        self.assertEqual(summary.root, self.root.resolve())
        self.assertTrue((self.root / "items").is_dir())
        self.assertTrue((self.root / ".virtuoso" / "state.sqlite3").is_file())
        config = json.loads((self.root / "virtuoso.json").read_text())
        self.assertEqual(config["schema"], "virtuoso/workspace@0.1")
        self.assertEqual(config["mode"], "simple")
        self.assertEqual(config["scheduler"]["algorithm"], "fsrs")

        with sqlite3.connect(self.root / ".virtuoso" / "state.sqlite3") as db:
            migration = db.execute(
                "SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1"
            ).fetchone()
            self.assertEqual(migration, (1,))

    def test_init_refuses_to_overwrite_existing_workspace(self) -> None:
        WorkspaceService.init(self.root)
        with self.assertRaisesRegex(WorkspaceError, "already exists"):
            WorkspaceService.init(self.root)

    def test_add_item_writes_human_owned_markdown_and_index(self) -> None:
        service = WorkspaceService.init(self.root)
        item = service.add_item(
            item_id="testing-effect",
            title="Explain the testing effect",
            focus="learning-science",
            prompt="Why does retrieval strengthen later recall?",
            answer="Retrieval itself changes memory and improves later access.",
            hint="Think about effortful retrieval rather than rereading.",
            follow_up="Name one way to use it in a coding project.",
        )

        text = item.path.read_text()
        self.assertIn("schema: virtuoso/item@0.1", text)
        self.assertIn("# Prompt\n\nWhy does retrieval strengthen later recall?", text)
        self.assertIn("# Answer\n\nRetrieval itself changes memory", text)
        self.assertIn("# Hint", text)
        self.assertIn("# Follow-up challenge", text)

        with sqlite3.connect(self.root / ".virtuoso" / "state.sqlite3") as db:
            row = db.execute(
                "SELECT item_id, focus, content_hash FROM items WHERE item_id = ?",
                ("testing-effect",),
            ).fetchone()
        self.assertEqual(row[0:2], ("testing-effect", "learning-science"))
        self.assertEqual(len(row[2]), 64)

    def test_add_item_rejects_unsafe_or_duplicate_ids(self) -> None:
        service = WorkspaceService.init(self.root)
        with self.assertRaisesRegex(WorkspaceError, "lowercase"):
            service.add_item(
                item_id="../escape",
                title="No",
                focus="test",
                prompt="No",
                answer="No",
            )
        service.add_item(
            item_id="safe-id",
            title="Safe",
            focus="test",
            prompt="Prompt",
            answer="Answer",
        )
        with self.assertRaisesRegex(WorkspaceError, "already exists"):
            service.add_item(
                item_id="safe-id",
                title="Duplicate",
                focus="test",
                prompt="Prompt",
                answer="Answer",
            )


if __name__ == "__main__":
    unittest.main()
