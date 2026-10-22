from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from virtuoso.workspace import WorkspaceService


class WorkspaceArchiveCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.workspace_path = self.root / "learner"
        self.workspace = WorkspaceService.init(self.workspace_path)
        self.workspace.add_item(
            item_id="archive-item",
            title="Portable learning evidence",
            focus="portability",
            prompt="What makes a workspace portable?",
            answer="Its prose, configuration and evidence travel together.",
        )
        self.archive = self.root / "workspace.tar.gz"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _run(self, *args: str, expected: int = 0) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [sys.executable, "-m", "virtuoso.cli", *args],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, expected, result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        return result

    def _export(self, *flags: str) -> subprocess.CompletedProcess[str]:
        return self._run(
            "--workspace", str(self.workspace_path),
            "export", "workspace", "--out", str(self.archive), *flags,
        )

    def _assert_json_error(self, result: subprocess.CompletedProcess[str]) -> None:
        self.assertEqual(result.stdout, "")
        payload = json.loads(result.stderr)
        self.assertEqual(payload["schema"], "virtuoso/review-error@0.1")
        self.assertEqual(payload["error"]["code"], "workspace-error")
        self.assertEqual(payload["error"]["recovery"], "check-paths")
        self.assertTrue(payload["error"]["message"])

    def test_export_and_import_json_without_a_source_workspace_for_import(self) -> None:
        exported = json.loads(self._export("--json").stdout)
        self.assertEqual(exported["schema"], "virtuoso/workspace-export@0.1")
        self.assertEqual(Path(exported["archive"]), self.archive)
        self.assertGreaterEqual(exported["file_count"], 3)
        self.assertTrue(exported["created_at"])
        destination = self.root / "restored"
        imported = json.loads(self._run(
            "import", "workspace", "--archive", str(self.archive),
            "--into", str(destination), "--json",
        ).stdout)
        self.assertEqual(imported["schema"], "virtuoso/workspace-import@0.1")
        self.assertEqual(Path(imported["workspace"]), destination)
        self.assertEqual(imported["doctor"]["status"], "healthy")
        doctor = json.loads(self._run(
            "--workspace", str(destination), "doctor", "--json",
        ).stdout)
        self.assertEqual(imported["doctor"], doctor)
        self.assertEqual(
            WorkspaceService.open(destination).items_dir.joinpath("archive-item.md").read_bytes(),
            self.workspace.items_dir.joinpath("archive-item.md").read_bytes(),
        )

    def test_plain_output_reports_archive_and_doctor(self) -> None:
        self.assertIn("archive:", self._export().stdout)
        destination = self.root / "restored"
        destination.mkdir()
        result = self._run(
            "import", "workspace", "--archive", str(self.archive),
            "--into", str(destination),
        )
        self.assertIn("doctor:", result.stdout)
        self.assertIn("healthy", result.stdout)
        self.assertEqual(result.stderr, "")

    def test_export_rejects_inside_workspace_with_json_error(self) -> None:
        destination = self.workspace_path / "workspace.tar.gz"
        result = self._run(
            "--workspace", str(self.workspace_path), "export", "workspace",
            "--out", str(destination), "--json", expected=2,
        )
        self._assert_json_error(result)
        self.assertFalse(destination.exists())

    def test_import_rejects_nonempty_destination_without_writing(self) -> None:
        self._export()
        destination = self.root / "occupied"
        destination.mkdir()
        marker = destination / "keep.txt"
        marker.write_text("keep this synthetic file", encoding="utf-8")
        result = self._run(
            "import", "workspace", "--archive", str(self.archive),
            "--into", str(destination), "--json", expected=2,
        )
        self._assert_json_error(result)
        self.assertEqual(list(destination.iterdir()), [marker])
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep this synthetic file")

    def test_import_bad_archive_reports_json_and_leaves_destination_absent(self) -> None:
        self.archive.write_bytes(b"not an archive")
        destination = self.root / "restored"
        result = self._run(
            "import", "workspace", "--archive", str(self.archive),
            "--into", str(destination), "--json", expected=2,
        )
        self._assert_json_error(result)
        self.assertFalse(destination.exists())

    def test_import_missing_archive_reports_plain_error(self) -> None:
        destination = self.root / "restored"
        result = self._run(
            "import", "workspace", "--archive", str(self.archive),
            "--into", str(destination), expected=2,
        )
        self.assertEqual(result.stdout, "")
        self.assertTrue(result.stderr.startswith("Error:"), result.stderr)
        self.assertFalse(destination.exists())

    def test_import_does_not_open_an_unrelated_workspace_argument(self) -> None:
        self._export()
        unrelated = self.root / "missing-workspace"
        destination = self.root / "restored"
        self._run(
            "--workspace", str(unrelated), "import", "workspace",
            "--archive", str(self.archive), "--into", str(destination), "--json",
        )
        self.assertFalse(unrelated.exists())
        self.assertEqual(WorkspaceService.open(destination).doctor()["status"], "healthy")

    def test_other_commands_still_require_workspace(self) -> None:
        result = self._run("doctor", "--json", expected=2)
        self.assertIn("--workspace", result.stderr)
        self.assertIn("required", result.stderr)


if __name__ == "__main__":
    unittest.main()
