from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RELEASE_WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _top_level_block(text: str, heading: str, next_heading: str) -> str:
    start = text.find(f"{heading}:\n")
    end = text.find(f"\n{next_heading}:\n", start + 1)
    if start < 0 or end < 0:
        return ""
    return text[start:end]


def _job_block(text: str, job: str) -> str:
    marker = f"  {job}:\n"
    start = text.find(marker)
    if start < 0:
        return ""
    match = re.search(r"\n  [a-z0-9][a-z0-9-]*:\n", text[start + len(marker) :])
    if match is None:
        return text[start:]
    return text[start : start + len(marker) + match.start()]


class ReleaseWorkflowContractTests(unittest.TestCase):
    def test_release_workflow_is_manual_and_main_only(self) -> None:
        workflow = _text(RELEASE_WORKFLOW)
        trigger = _top_level_block(workflow, "on", "permissions")
        self.assertEqual(trigger.strip(), "on:\n  workflow_dispatch:")
        guard = _job_block(workflow, "guard")
        self.assertIn('refs/heads/main', guard)
        self.assertIn('${{ github.ref }}', guard)
        for forbidden_trigger in ("push:", "pull_request:", "schedule:"):
            self.assertNotIn(forbidden_trigger, trigger)

    def test_release_reuses_required_ci_before_build(self) -> None:
        ci = _text(CI_WORKFLOW)
        workflow = _text(RELEASE_WORKFLOW)
        self.assertIn("  workflow_call:", ci)
        required_ci = _job_block(workflow, "required-ci")
        build = _job_block(workflow, "build")
        release = _job_block(workflow, "release")
        self.assertIn("needs: guard", required_ci)
        self.assertIn("uses: ./.github/workflows/ci.yml", required_ci)
        self.assertIn("needs: required-ci", build)
        self.assertIn("needs: build", release)

    def test_release_permissions_and_actions_are_narrow(self) -> None:
        workflow = _text(RELEASE_WORKFLOW)
        global_permissions = _top_level_block(workflow, "permissions", "concurrency")
        release = _job_block(workflow, "release")
        self.assertIn("contents: read", global_permissions)
        self.assertEqual(workflow.count("contents: write"), 1)
        self.assertIn("contents: write", release)
        allowed_uses = {
            "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
            "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97",
            "actions/setup-node@820762786026740c76f36085b0efc47a31fe5020",
            "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
            "actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c",
            "./.github/workflows/ci.yml",
        }
        observed = {
            line.split("uses:", 1)[1].split("#", 1)[0].strip()
            for line in workflow.splitlines()
            if "uses:" in line
        }
        self.assertTrue(observed)
        self.assertEqual(observed - allowed_uses, set())

    def test_release_builds_and_reverifies_fixed_assets(self) -> None:
        workflow = _text(RELEASE_WORKFLOW)
        build = _job_block(workflow, "build")
        release = _job_block(workflow, "release")
        for marker in (
            "test ! -L dist",
            "rm -rf dist build",
            "uv sync --locked --group build",
            "name: python-dist-${{ github.sha }}",
            "scripts/check_distributions.py",
            "npm ci",
            "npm run typecheck",
            "npm test",
            "npm run build",
            "scripts/release_artifacts.py assemble",
            "scripts/release_artifacts.py verify",
            "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, build)
        self.assertIn("actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c", release)
        self.assertIn("scripts/release_artifacts.py verify", release)
        self.assertIn("dist/release", workflow)
        self.assertNotIn("uv build", build, "Release must reuse the tested distributions")
        ci = _text(CI_WORKFLOW)
        self.assertIn("uv build --no-build-isolation --out-dir dist/python", ci)
        self.assertIn("name: python-dist-${{ github.sha }}", ci)
        install = _job_block(ci, "install")
        self.assertIn("needs: python", install)
        self.assertIn("python scripts/check_distributions.py", install)
        self.assertIn("macos-14", install)
        self.assertIn("ubuntu-24.04", install)

    def test_release_creation_is_draft_only_and_fails_closed(self) -> None:
        workflow = _text(RELEASE_WORKFLOW)
        release = _job_block(workflow, "release")
        for marker in (
            "git rev-parse",
            "RELEASE_TAGS=$(gh api --paginate",
            "grep -Fxq",
            "gh release create",
            "--draft",
            "--latest=false",
            '--target "$GITHUB_SHA"',
            'docs/releases/v$VERSION.md',
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, release)
        lowered = workflow.lower()
        for forbidden in ("pypi", "twine", "npm publish", "deploy"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, lowered)


if __name__ == "__main__":
    unittest.main()
