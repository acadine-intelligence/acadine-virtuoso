"""Integration controls. Build distributions before running this directory."""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class DistributionProbeTests(unittest.TestCase):
    def test_wheel_without_declared_resource_fails_installation_check(self) -> None:
        wheels = list((ROOT / "dist/python").glob("*.whl"))
        sources = list((ROOT / "dist/python").glob("*.tar.gz"))
        self.assertEqual(len(wheels), 1, "Build exactly one wheel first")
        self.assertEqual(len(sources), 1, "Build exactly one source distribution first")
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            with zipfile.ZipFile(wheels[0]) as source:
                self.assertIn("virtuoso/py.typed", source.namelist())
                with zipfile.ZipFile(target / wheels[0].name, "w") as broken:
                    for member in source.infolist():
                        if member.filename != "virtuoso/py.typed":
                            broken.writestr(member, source.read(member.filename))
            shutil.copyfile(sources[0], target / sources[0].name)
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts/check_distributions.py"),
                 "--dist-dir", str(target)],
                cwd=target, text=True, capture_output=True, timeout=180,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("installed package has wrong origin, data files or version", result.stderr)
        self.assertNotIn("PASS", result.stdout)


if __name__ == "__main__":
    unittest.main()
