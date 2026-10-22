"""Archive boundaries are checked before publishing a restored workspace."""
from __future__ import annotations

import hashlib
import io
import json
import os
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from virtuoso.workspace import WorkspaceService
from virtuoso.workspace_archive import (
    WorkspaceArchiveError, export_workspace, import_workspace,
)


class WorkspaceArchiveSecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.workspace = WorkspaceService.init(self.base / "source")
        self.workspace.add_item(item_id="example", title="Example", focus="test",
                                prompt="Why?", answer="Because.")
        self.archive = self.base / "workspace.tar.gz"
        self.target = self.base / "restored"
        export_workspace(self.workspace, self.archive)

    def rewrite(self, change) -> Path:
        with tarfile.open(self.archive, "r:gz") as archive:
            entries = [(member, archive.extractfile(member).read() if member.isfile() else b"")
                       for member in archive]
        change(entries)
        output = self.base / "modified.tar.gz"
        with tarfile.open(output, "w:gz") as archive:
            for member, data in entries:
                member.size = len(data) if member.isfile() else 0
                archive.addfile(member, io.BytesIO(data) if member.isfile() else None)
        return output

    def assert_refused(self, archive: Path) -> None:
        for exists in (False, True):
            with self.subTest(existing_empty_destination=exists):
                if exists:
                    self.target.mkdir()
                with self.assertRaises(WorkspaceArchiveError):
                    import_workspace(archive, self.target)
                self.assertEqual(self.target.exists(), exists)
                if exists:
                    self.assertEqual(list(self.target.iterdir()), [])
                    self.target.rmdir()

    def test_rejects_unsafe_member_names(self) -> None:
        for name in ("/escape", "../escape", "items/../../escape", "items/./example.md",
                     "items//example.md", "C:/escape", "items\\escape"):
            with self.subTest(name=name):
                self.assert_refused(self.rewrite(lambda entries: entries.append((tarfile.TarInfo(name), b"bad"))))
        self.assertFalse((self.base / "escape").exists())

    def test_rejects_links_and_special_members(self) -> None:
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE, tarfile.CHRTYPE,
                     tarfile.BLKTYPE):
            member = tarfile.TarInfo("items/link")
            member.type = kind
            member.linkname = "../../escape"
            with self.subTest(kind=kind):
                self.assert_refused(self.rewrite(lambda entries: entries.append((member, b""))))

    def test_rejects_unlisted_and_duplicate_members(self) -> None:
        self.assert_refused(self.rewrite(lambda entries: entries.append((tarfile.TarInfo("items/extra.md"), b"extra"))))
        self.assert_refused(self.rewrite(lambda entries: entries.append(entries[-1])))

    def test_rejects_bad_hash_missing_payload_and_invalid_manifest(self) -> None:
        def corrupt(entries):
            for index, (member, data) in enumerate(entries):
                if member.name == "items/example.md":
                    entries[index] = (member, data + b"tampered")
        self.assert_refused(self.rewrite(corrupt))
        self.assert_refused(self.rewrite(lambda entries: entries.__setitem__(slice(None),
                            [e for e in entries if e[0].name != "items/example.md"])))
        def malformed(entries):
            for index, (member, _) in enumerate(entries):
                if member.name == "virtuoso-export.json":
                    entries[index] = (member, b'{"schema":[] }')
        self.assert_refused(self.rewrite(malformed))

    def test_rejects_manifest_authorized_database_sidecars(self) -> None:
        def sidecar(entries):
            member = tarfile.TarInfo(".virtuoso/state.sqlite3-wal")
            data = b"not database state"
            entries.append((member, data))
            for index, (info, raw) in enumerate(entries):
                if info.name == "virtuoso-export.json":
                    manifest = json.loads(raw)
                    manifest["files"].append({"path": member.name, "size": len(data),
                                              "sha256": hashlib.sha256(data).hexdigest()})
                    entries[index] = (info, json.dumps(manifest).encode())
        self.assert_refused(self.rewrite(sidecar))

    def test_total_size_limit_is_enforced_before_destination_changes(self) -> None:
        with patch("virtuoso.workspace_archive.MAX_TOTAL_SIZE", 32):
            self.assert_refused(self.archive)

    def test_nonempty_destination_is_untouched(self) -> None:
        self.target.mkdir()
        marker = self.target / "keep"
        marker.write_bytes(b"original")
        with self.assertRaises(WorkspaceArchiveError):
            import_workspace(self.archive, self.target)
        self.assertEqual(list(self.target.iterdir()), [marker])
        self.assertEqual(marker.read_bytes(), b"original")

    def test_invalid_database_is_rolled_back(self) -> None:
        def invalid_database(entries):
            bad = b"not a SQLite database"
            for index, (member, data) in enumerate(entries):
                if member.name == ".virtuoso/state.sqlite3":
                    entries[index] = (member, bad)
                elif member.name == "virtuoso-export.json":
                    manifest = json.loads(data)
                    for entry in manifest["files"]:
                        if entry["path"] == ".virtuoso/state.sqlite3":
                            entry.update(size=len(bad), sha256=hashlib.sha256(bad).hexdigest())
                    entries[index] = (member, json.dumps(manifest).encode())
        self.assert_refused(self.rewrite(invalid_database))

    def test_rejects_symlink_destination_and_ancestor(self) -> None:
        outside = self.base / "outside"
        outside.mkdir()
        self.target.symlink_to(outside, target_is_directory=True)
        for destination in (self.target, self.target / "child"):
            with self.subTest(destination=destination):
                with self.assertRaises(WorkspaceArchiveError):
                    import_workspace(self.archive, destination)
        self.assertEqual(list(outside.iterdir()), [])

    def test_export_refuses_symlinks_even_in_excluded_directories(self) -> None:
        outside = self.base / "external.md"
        outside.write_text("external")
        for relative in ("items/link.md", "modules/link", "ignored/link"):
            path = self.workspace.root / relative
            path.parent.mkdir(exist_ok=True)
            path.symlink_to(outside)
            with self.subTest(relative=relative):
                with self.assertRaises(WorkspaceArchiveError):
                    export_workspace(self.workspace, self.base / "refused.tar.gz")
                self.assertFalse((self.base / "refused.tar.gz").exists())
            path.unlink()

    def test_export_refuses_internal_destination_and_preserves_existing_archive_on_failure(self) -> None:
        with self.assertRaises(WorkspaceArchiveError):
            export_workspace(self.workspace, self.workspace.root / "backup.tar.gz")
        previous = self.archive.read_bytes()
        before = set(self.base.iterdir())
        with patch("virtuoso.workspace_archive.os.replace", side_effect=OSError("synthetic failure")):
            with self.assertRaises(WorkspaceArchiveError):
                export_workspace(self.workspace, self.archive)
        self.assertEqual(self.archive.read_bytes(), previous)
        self.assertEqual(set(self.base.iterdir()), before)

    def test_export_refuses_fifo_without_opening_it(self) -> None:
        fifo = self.workspace.root / "modules" / "pipe"
        fifo.parent.mkdir()
        os.mkfifo(fifo)
        with self.assertRaises(WorkspaceArchiveError):
            export_workspace(self.workspace, self.base / "refused.tar.gz")

    def test_rejects_duplicate_manifest_fields_and_path_case_aliases(self) -> None:
        def duplicate_field(entries):
            for index, (member, data) in enumerate(entries):
                if member.name == "virtuoso-export.json":
                    entries[index] = (member, b'{"files": [],' + data[1:])
        self.assert_refused(self.rewrite(duplicate_field))
        self.assert_refused(self.rewrite(lambda entries: entries.append(
            (tarfile.TarInfo("items/EXAMPLE.md"), b"alias"))))

    def test_database_paths_cannot_make_doctor_read_outside_archive(self) -> None:
        with self.workspace._connect() as database:
            database.execute("UPDATE items SET relative_path = '../outside.md'")
        export_workspace(self.workspace, self.archive)
        with patch.object(WorkspaceService, "doctor", side_effect=AssertionError("doctor must not run")):
            self.assert_refused(self.archive)

    def test_symlink_sidecar_is_refused_before_sqlite_backup(self) -> None:
        sidecar = Path(str(self.workspace.db_path) + "-wal")
        sidecar.symlink_to(self.base / "absent")
        with patch("virtuoso.workspace_archive._snapshot", side_effect=AssertionError("must not open SQLite")):
            with self.assertRaises(WorkspaceArchiveError):
                export_workspace(self.workspace, self.base / "refused.tar.gz")

    def test_rejects_fifo_archive_without_opening_blocking_stream(self) -> None:
        fifo = self.base / "pipe"
        os.mkfifo(fifo)
        self.assert_refused(fifo)

    def test_rejects_oversized_extension_metadata_before_reading_payload(self) -> None:
        import gzip
        info = tarfile.TarInfo("././@PaxHeader")
        info.type, info.size = tarfile.XHDTYPE, 65537
        bad = self.base / "metadata.tar.gz"
        bad.write_bytes(gzip.compress(info.tobuf()))
        self.assert_refused(bad)

    def test_truncated_archive_leaves_no_destination(self) -> None:
        bad = self.base / "truncated.tar.gz"
        bad.write_bytes(self.archive.read_bytes()[:150])
        self.assert_refused(bad)
