from __future__ import annotations

import json
import os
import re
import textwrap
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class InstallContractTests(unittest.TestCase):
    def test_project_declares_a_locked_development_environment(self) -> None:
        pin = ROOT / ".python-version"
        self.assertTrue(pin.is_file(), "Development must select Python without a machine-specific command")
        self.assertEqual(pin.read_text().strip(), "3.11.15")
        lock_path = ROOT / "uv.lock"
        self.assertTrue(lock_path.is_file(), "Project dependencies must have a committed lock")
        lock = tomllib.loads(lock_path.read_text())
        packages = {package["name"]: package["version"] for package in lock["package"]}
        self.assertEqual(packages["fsrs"], "6.3.2")
        self.assertEqual(packages["typing-extensions"], "4.16.0")
        self.assertEqual(packages["setuptools"], "80.9.0")

    def test_distribution_check_rejects_missing_artifacts(self) -> None:
        script = ROOT / "scripts/check_distributions.py"
        self.assertTrue(script.is_file(), "CI must exercise the distributions it delivers")
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                [sys.executable, str(script), "--dist-dir", tmp],
                text=True, capture_output=True, timeout=10,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("expected one wheel and one source distribution", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_one_required_check_waits_for_all_installations(self) -> None:
        ci = (ROOT / ".github/workflows/ci.yml").read_text()
        self.assertIn("  verified:", ci)
        summary = ci.split("  verified:", 1)[1]
        self.assertIn("needs: [python, install, obsidian-plugin]", summary)
        self.assertIn("always()", summary)
        self.assertIn("NEEDS_JSON", summary)
        self.assertIn("name: Python 3.11", summary)
        self.assertEqual(ci.count("name: Python 3.11"), 1)
        self.assertIn('result != "success"', summary)

    def test_summary_rejects_failed_skipped_cancelled_and_missing_jobs(self) -> None:
        ci = (ROOT / ".github/workflows/ci.yml").read_text()
        block = ci.split("  verified:", 1)[1]
        source = textwrap.dedent(block.split("python - <<'PY'\n", 1)[1].rsplit("          PY", 1)[0])
        for status in ("success", "failure", "skipped", "cancelled", None):
            with self.subTest(status=status):
                needs = {name: {"result": "success"} for name in ("python", "install", "obsidian-plugin")}
                if status is None:
                    del needs["install"]
                else:
                    needs["install"]["result"] = status
                result = subprocess.run([sys.executable, "-c", source], capture_output=True,
                                        text=True, env={**os.environ, "NEEDS_JSON": json.dumps(needs)})
                self.assertEqual(result.returncode == 0, status == "success", result.stderr)

    def test_matrix_uses_uv_for_the_requested_interpreter(self) -> None:
        ci = (ROOT / ".github/workflows/ci.yml").read_text()
        install = ci.split("  install:", 1)[1].split("  obsidian-plugin:", 1)[0]
        self.assertIn('uv python install "$TEST_PYTHON"', install)
        self.assertIn('uv run --no-project --python "$TEST_PYTHON" python scripts/check_distributions.py', install)
        self.assertIn("TEST_PYTHON: ${{ matrix.python }}", install)

    def test_ci_uses_locked_sync_and_commit_pinned_actions(self) -> None:
        for name in ("ci.yml", "release.yml"):
            with self.subTest(workflow=name):
                workflow = (ROOT / ".github/workflows" / name).read_text()
                self.assertIn("uv sync --locked --group build", workflow)
                self.assertIn("--require-hashes -r ci/bootstrap.txt", workflow)
                for action in re.findall(r"uses: ([^\s]+)", workflow):
                    if action.startswith("./"):
                        continue
                    self.assertRegex(action, r"^actions/[a-z-]+@[0-9a-f]{40}$")


if __name__ == "__main__":
    unittest.main()
