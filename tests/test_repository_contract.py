from __future__ import annotations

import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CURRENT_PUBLIC_GUIDES = (
    ROOT / "AGENTS.md",
    ROOT / "CONTRIBUTING.md",
    ROOT / "README.md",
    ROOT / "docs" / "07-delivery-contract.md",
    ROOT / "docs" / "08-production-readiness.md",
)


class PublicRepositoryContractTests(unittest.TestCase):
    def test_mit_license_and_package_metadata_agree(self) -> None:
        license_path = ROOT / "LICENSE"
        self.assertTrue(license_path.is_file(), "LICENSE is missing")
        license_text = license_path.read_text(encoding="utf-8")
        self.assertTrue(license_text.startswith("MIT License\n"))
        self.assertIn(
            "Copyright (c) 2026 Acadine Intelligence (Pty) Ltd",
            license_text,
        )
        self.assertIn("Permission is hereby granted, free of charge", license_text)
        self.assertIn('THE SOFTWARE IS PROVIDED "AS IS"', license_text)

        metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
            "project"
        ]
        self.assertEqual(metadata["license"], "MIT")
        self.assertEqual(metadata["license-files"], ["LICENSE"])

    def test_public_contributor_commands_match_ci(self) -> None:
        guide_path = ROOT / "CONTRIBUTING.md"
        self.assertTrue(guide_path.is_file(), "CONTRIBUTING.md is missing")
        guide = guide_path.read_text(encoding="utf-8")
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        commands = (
            "python -m venv .venv",
            ".venv/bin/python -m pip install -e .",
            ".venv/bin/python -m compileall -q src tests",
            ".venv/bin/python -m unittest discover -s tests -v",
            ".venv/bin/virtuoso --help",
            "npm ci",
            "npm run typecheck",
            "npm test",
            "npm run build",
        )
        for command in commands:
            with self.subTest(command=command):
                self.assertIn(command, guide)
                self.assertIn(command, workflow)

    def test_public_contributor_workflow_covers_a_draft_pull_request(self) -> None:
        guide = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
        for command in (
            "git switch -c",
            "git add",
            "git commit",
            "git push -u",
            "gh pr create --draft",
        ):
            with self.subTest(command=command):
                self.assertIn(command, guide)

    def test_private_build_adapter_is_absent(self) -> None:
        self.assertFalse((ROOT / ".buildos").exists())
        self.assertFalse((ROOT / ".project-meta.json").exists())
        self.assertTrue((ROOT / "product.json").is_file())

    def test_current_public_workflow_has_no_private_build_dependency(self) -> None:
        forbidden = (
            "Build OS",
            "buildos.py",
            "BUILDOS_HOME",
            ".buildos",
            ".project-meta.json",
            "/Users/",
        )
        for path in CURRENT_PUBLIC_GUIDES:
            text = path.read_text(encoding="utf-8")
            for marker in forbidden:
                with self.subTest(path=path.relative_to(ROOT), marker=marker):
                    self.assertNotIn(marker, text)

    def test_readme_and_agent_contract_link_public_guides(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        agent_contract = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        for target in ("CONTRIBUTING.md", "LICENSE"):
            with self.subTest(target=target):
                self.assertIn(target, readme)
        self.assertIn("CONTRIBUTING.md", agent_contract)
        self.assertIn(".github/workflows/ci.yml", agent_contract)


if __name__ == "__main__":
    unittest.main()
