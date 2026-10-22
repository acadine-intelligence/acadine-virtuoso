"""Issue #78: portable archives preserve learner prose and append-only evidence.

All workspaces and credential fixtures are synthetic and temporary. No test
contacts a remote annotator or reads a real credential source.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import shutil
import sqlite3
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import test_annotator_credentials as credential_fixtures
from virtuoso import __version__
from virtuoso.annotations import AnnotationService
from virtuoso.modules import ModuleRunner
from virtuoso.practice import PracticeService
from virtuoso.search import lexical_search
from virtuoso.workspace import WorkspaceService
from virtuoso.workspace_archive import export_workspace, import_workspace

NOW = datetime(2026, 1, 2, 12, tzinfo=timezone.utc)
MANIFEST = "virtuoso-export.json"
REPOSITORY = Path(__file__).resolve().parents[1]


class WorkspaceArchiveAcceptanceTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.workspace = WorkspaceService.init(self.base / "source")
        self.archive = self.base / "workspace.tar.gz"
        self.destination = self.base / "restored"

    def _add(self, item_id: str = "retrieval", *, learn_first: bool = False) -> None:
        self.workspace.add_item(
            item_id=item_id,
            title="Explain retrieval practice",
            focus="learning-science",
            prompt="Why practice retrieval?",
            answer="Retrieval strengthens later access to a memory.",
            hint="Think about recalling without notes.",
            entry_mode="learn-first" if learn_first else "recall-first",
            learning_unit="Read this synthetic learning unit." if learn_first else None,
        )

    def _rich_workspace(self) -> None:
        self._add()
        self._add("study-first", learn_first=True)
        practice = PracticeService(self.workspace)
        for offset, (grader, surface, client) in enumerate((
            ("learner", "cli", None),
            ("agent", "mcp", "Synthetic MCP Client"),
            ("learner", "http", "Synthetic HTTP Client"),
        )):
            practice.run_administered(
                item_id="retrieval",
                response="Recall strengthens access.",
                result="partial",
                confidence=3,
                agent_help="none",
                now=NOW + timedelta(minutes=offset),
                graded_by=grader,
                surface=surface,
                client=client,
            )
        item = self.workspace.load_item("retrieval")
        self.workspace.record_review_skip(
            event_id="skip-" + "a" * 32,
            item_id=item.item_id,
            item_content_hash=item.content_hash,
            occurred_at=(NOW + timedelta(minutes=3)).isoformat(),
            surface="http",
        )
        study = self.workspace.load_item("study-first")
        self.workspace.record_study_completion(
            item_id=study.item_id,
            item_content_hash=study.content_hash,
            learning_unit_hash=study.learning_unit_hash,
            occurred_at=NOW + timedelta(minutes=4),
            surface="mcp",
        )
        module = self.workspace.root / "modules" / "rule-annotator"
        shutil.copytree(REPOSITORY / "examples" / "modules" / "rule-annotator", module)
        module.parent.chmod(0o700)
        module.chmod(0o700)
        for path in module.iterdir():
            path.chmod(0o600)
        # Use this interpreter while retaining a relative module script path.
        manifest_path = module / "virtuoso.module.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["command"]["argv"][0] = sys.executable
        manifest_path.write_text(json.dumps(manifest))
        AnnotationService(self.workspace).annotate_item(
            item_id="retrieval", module_id="rule-annotator"
        )
        self.workspace.switch_scheduler(to_algorithm="sm2", now=NOW + timedelta(minutes=5))
        practice.run_administered(
            item_id="retrieval", response="A later recall.", result="demonstrated",
            confidence=4, agent_help="none", now=NOW + timedelta(minutes=6),
        )
        # Unindexed learner Markdown and empty nested directories must survive too.
        (self.workspace.items_dir / "notes.md").write_text("# Synthetic learner notes\n\nKeep this prose.\n")
        (module / "resources" / "empty").mkdir(parents=True)
        (module / "resources" / "example.json").write_text('{"synthetic": true}\n')
        self.assertTrue(lexical_search(self.workspace, "retrieval"))

    def _run_cli(self, workspace: Path | None, *args: str) -> dict:
        command = [sys.executable, "-m", "virtuoso.cli"]
        if workspace is not None:
            command += ["--workspace", str(workspace)]
        completed = subprocess.run(
            [*command, *args], capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        return json.loads(completed.stdout)

    def test_empty_workspace_round_trip_opens_with_private_permissions(self) -> None:
        self.destination.mkdir(mode=0o755)
        export_workspace(self.workspace, self.archive)
        imported = import_workspace(self.archive, self.destination)
        restored = WorkspaceService.open(self.destination)
        self.assertEqual(restored.doctor()["status"], "healthy")
        self.assertEqual(imported["doctor"]["status"], "healthy")
        self.assertTrue(restored.items_dir.is_dir())
        self.assertEqual(stat.S_IMODE(self.archive.stat().st_mode), 0o600)
        for path in [self.destination, *self.destination.rglob("*")]:
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700 if path.is_dir() else 0o600)

    def test_rich_export_manifest_hashes_every_payload_and_preserves_evidence(self) -> None:
        self._rich_workspace()
        source_before = self.workspace.db_path.read_bytes()
        export_workspace(self.workspace, self.archive, now=NOW)
        self.assertEqual(self.workspace.db_path.read_bytes(), source_before)
        self.assertEqual(stat.S_IMODE(self.archive.stat().st_mode), 0o600)
        with tarfile.open(self.archive, "r:gz") as archive:
            members = archive.getmembers()
            self.assertEqual(len({member.name for member in members}), len(members))
            manifest = json.load(archive.extractfile(MANIFEST))
            self.assertEqual(manifest["schema"], "virtuoso/workspace-export@0.1")
            self.assertEqual(manifest["virtuoso_version"], __version__)
            self.assertEqual(datetime.fromisoformat(manifest["created_at"]), NOW)
            with sqlite3.connect(self.workspace.db_path) as source:
                version = source.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
            self.assertEqual(manifest["database_schema_version"], version)
            paths = {entry["path"] for entry in manifest["files"]}
            self.assertEqual(paths, {member.name for member in members if member.isfile()} - {MANIFEST})
            self.assertTrue({
                "virtuoso.json", ".virtuoso/state.sqlite3", "items/retrieval.md",
                "items/study-first.md", "items/notes.md",
                "modules/rule-annotator/virtuoso.module.json",
                "modules/rule-annotator/rule_annotator.py",
                "modules/rule-annotator/resources/example.json",
            }.issubset(paths))
            self.assertIn("modules/rule-annotator/resources/empty", manifest["directories"])
            self.assertIn(".virtuoso/search.sqlite3", manifest["excluded_paths"])
            self.assertNotIn(".virtuoso/search.sqlite3", paths)
            self.assertFalse(any(name.endswith(("-wal", "-shm")) for name in paths))
            for entry in manifest["files"]:
                data = archive.extractfile(entry["path"]).read()
                self.assertEqual(len(data), entry["size"], entry["path"])
                self.assertEqual(hashlib.sha256(data).hexdigest(), entry["sha256"], entry["path"])
            database = self.base / "snapshot.sqlite3"
            database.write_bytes(archive.extractfile(".virtuoso/state.sqlite3").read())
        with sqlite3.connect(database) as snapshot:
            self.assertEqual(snapshot.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            for table, count in (("attempts", 4), ("review_skips", 1), ("study_events", 1),
                                 ("annotations", 1), ("scheduler_switches", 1)):
                self.assertEqual(snapshot.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], count)
            attribution = snapshot.execute("SELECT graded_by, surface, client FROM attempts").fetchall()
        self.assertIn(("agent", "mcp", "Synthetic MCP Client"), attribution)
        self.assertIn(("learner", "http", "Synthetic HTTP Client"), attribution)

    def test_cli_round_trip_keeps_all_projections_after_source_is_removed(self) -> None:
        self._rich_workspace()
        commands = (
            ("attempts", "--json"),
            ("queries", "history", "--item", "retrieval", "--json"),
            ("annotate", "list", "--json"),
            ("review", "due", "--json"),
            ("doctor", "--json"),
            ("scheduler", "history", "--json"),
        )
        before = {command: self._run_cli(self.workspace.root, *command) for command in commands}
        owned_files = {
            str(path.relative_to(self.workspace.root)): path.read_bytes()
            for directory in (self.workspace.items_dir, self.workspace.root / "modules")
            for path in directory.rglob("*") if path.is_file()
        }
        self._run_cli(self.workspace.root, "export", "workspace", "--out", str(self.archive), "--json")
        shutil.rmtree(self.workspace.root)
        imported = self._run_cli(
            None, "import", "workspace", "--archive", str(self.archive),
            "--into", str(self.destination), "--json",
        )
        self.assertEqual(imported["doctor"]["status"], "healthy")
        # No fields are ignored: all evidence times are stored values, and these
        # deliberately old due dates cannot cross a clock boundary during the test.
        for command, expected in before.items():
            with self.subTest(command=command):
                self.assertEqual(self._run_cli(self.destination, *command), expected)
        for relative, data in owned_files.items():
            self.assertEqual((self.destination / relative).read_bytes(), data)
        self.assertTrue((self.destination / "modules/rule-annotator/resources/empty").is_dir())
        for path in [self.destination, *self.destination.rglob("*")]:
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700 if path.is_dir() else 0o600)
        self.assertFalse((self.destination / ".virtuoso/search.sqlite3").exists())
        self.assertTrue(lexical_search(WorkspaceService.open(self.destination), "retrieval"))

    def test_import_never_executes_archived_module_code(self) -> None:
        self._rich_workspace()
        export_workspace(self.workspace, self.archive)
        with mock.patch.object(ModuleRunner, "run", side_effect=AssertionError("module executed during import")):
            imported = import_workspace(self.archive, self.destination)
        self.assertEqual(imported["doctor"]["status"], "healthy")

    def test_sqlite_backup_includes_committed_wal_and_excludes_open_write_transaction(self) -> None:
        self._add()
        writer = sqlite3.connect(self.workspace.db_path)
        self.addCleanup(writer.close)
        self.assertEqual(writer.execute("PRAGMA journal_mode=WAL").fetchone()[0], "wal")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        PracticeService(self.workspace).run_administered(
            item_id="retrieval", response="Committed in the WAL.", result="partial",
            confidence=3, agent_help="none", now=NOW,
        )
        self.assertTrue(Path(str(self.workspace.db_path) + "-wal").exists())
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("UPDATE items SET title = 'Uncommitted synthetic title'")
        try:
            export_workspace(self.workspace, self.archive)
        finally:
            writer.rollback()
        with tarfile.open(self.archive, "r:gz") as archive:
            manifest = json.load(archive.extractfile(MANIFEST))
            self.assertIn(".virtuoso/state.sqlite3-wal", manifest["excluded_paths"])
            self.assertIn(".virtuoso/state.sqlite3-shm", manifest["excluded_paths"])
            self.assertFalse(any(name.endswith(("-wal", "-shm")) for name in archive.getnames()))
        import_workspace(self.archive, self.destination)
        restored = WorkspaceService.open(self.destination)
        with sqlite3.connect(restored.db_path) as database:
            self.assertEqual(database.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(database.execute("SELECT COUNT(*) FROM attempts").fetchone()[0], 1)
            self.assertEqual(database.execute("SELECT title FROM items").fetchone()[0], "Explain retrieval practice")
        self.assertEqual(restored.list_attempts(), self.workspace.list_attempts())
        self.assertEqual(restored.doctor()["status"], "healthy")


class WorkspaceArchiveCredentialTests(credential_fixtures.CredentialFixture):
    def test_env_credential_and_escaped_forms_never_enter_any_archive_member(self) -> None:
        self.workspace.configure_annotator(allow_remote=True)
        self._install(credentials=[{
            "name": credential_fixtures.KEY_NAME, "provider": "typesafe", "sources": ["env"],
        }])
        value = credential_fixtures.ESCAPING_VALUE
        record = AnnotationService(self.workspace).annotate_item(
            item_id="testing-effect", module_id="remote-judge",
            environ={credential_fixtures.KEY_NAME: value},
        )
        self.assertEqual(record["credential_source"], "env")
        self.assertEqual(self._captured()["value_sha256"], self._sha(value))
        self.assertEqual(self._keychain_calls(), [])
        archive_path = Path(self.tmp.name).resolve() / "workspace.tar.gz"
        export_workspace(self.workspace, archive_path)
        forbidden = (value.encode(), repr(value).encode(), json.dumps(value).encode(),
                     json.dumps(value)[1:-1].encode())
        payloads = [archive_path.read_bytes(), gzip.decompress(archive_path.read_bytes())]
        with tarfile.open(archive_path, "r:gz") as archive:
            payloads.extend(archive.extractfile(member).read() for member in archive if member.isfile())
        for payload in payloads:
            for encoded in forbidden:
                self.assertNotIn(encoded, payload, "archive contains a resolved synthetic credential")


if __name__ == "__main__":
    unittest.main()
