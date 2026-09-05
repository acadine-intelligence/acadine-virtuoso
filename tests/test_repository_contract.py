from __future__ import annotations

import importlib.metadata
import json
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
    def test_v010_release_metadata_agrees(self) -> None:
        expected = "0.1.0"
        project = tomllib.loads(
            (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        )["project"]
        product = json.loads((ROOT / "product.json").read_text(encoding="utf-8"))
        hermes_manifest = (ROOT / "plugins" / "hermes" / "plugin.yaml").read_text(
            encoding="utf-8"
        )
        hermes_version = next(
            line.split(":", 1)[1].strip()
            for line in hermes_manifest.splitlines()
            if line.startswith("version:")
        )
        obsidian_root = ROOT / "plugins" / "obsidian"
        obsidian_manifest = json.loads(
            (obsidian_root / "manifest.json").read_text(encoding="utf-8")
        )
        obsidian_package = json.loads(
            (obsidian_root / "package.json").read_text(encoding="utf-8")
        )
        obsidian_lock = json.loads(
            (obsidian_root / "package-lock.json").read_text(encoding="utf-8")
        )
        obsidian_versions = json.loads(
            (obsidian_root / "versions.json").read_text(encoding="utf-8")
        )
        observed = {
            "pyproject": project["version"],
            "product": product["release"]["version"],
            "installed Python distribution": importlib.metadata.version(
                "acadine-virtuoso"
            ),
            "Python package": __import__("virtuoso").__version__,
            "Hermes plugin": hermes_version,
            "Obsidian manifest": obsidian_manifest["version"],
            "Obsidian package": obsidian_package["version"],
            "Obsidian lock": obsidian_lock["version"],
            "Obsidian lock root": obsidian_lock["packages"][""]["version"],
        }
        self.assertEqual(observed, {name: expected for name in observed})
        self.assertEqual(obsidian_versions, {expected: obsidian_manifest["minAppVersion"]})

    def test_v010_contract_has_no_stale_development_version_fixture(self) -> None:
        paths = (
            ROOT / "README.md",
            ROOT / "docs" / "12-cli-reference.md",
            ROOT / "docs" / "15-release-notes.md",
            ROOT / "plugins" / "obsidian" / "src" / "test" / "cli-client.test.ts",
        )
        for path in paths:
            with self.subTest(path=path.relative_to(ROOT)):
                self.assertNotIn("0.1.0.dev0", path.read_text(encoding="utf-8"))

    def test_cli_reference_covers_current_query_search_and_review_contracts(
        self,
    ) -> None:
        reference = (ROOT / "docs" / "12-cli-reference.md").read_text(
            encoding="utf-8"
        )
        commands = (
            "virtuoso --workspace PATH compose [--focus FOCUS] [--json]",
            "virtuoso --workspace PATH compose decide --id PROPOSAL_ID",
            "virtuoso --workspace PATH benchmark import --file ARTIFACT.json",
            "virtuoso --workspace PATH benchmark propose",
            "virtuoso --workspace PATH benchmark rerun --file ARTIFACT.json --baseline RUN_ID",
            "virtuoso --workspace PATH benchmark export --run-id RUN_ID",
            "virtuoso --workspace PATH learn --item ID",
            "virtuoso --workspace PATH queries focus [--json]",
            "virtuoso --workspace PATH queries history --item ITEM [--json]",
            "virtuoso --workspace PATH queries workload [--json]",
            "virtuoso --workspace PATH queries learning [--json]",
            "virtuoso --workspace PATH queries stale-links [--json]",
            "virtuoso --workspace PATH search lex --query TEXT [--limit N] [--json]",
            "virtuoso --workspace PATH search embed --item ITEM --model MODEL",
            "virtuoso --workspace PATH search sem --model MODEL --vector JSON",
            "virtuoso --workspace PATH search status [--json]",
            "virtuoso --workspace PATH review due --json",
            "virtuoso --workspace PATH review load --item ID --json",
            "virtuoso --workspace PATH review record --json",
            "virtuoso --workspace PATH review skip --json",
            "virtuoso --workspace PATH scheduler show [--json]",
            "virtuoso --workspace PATH scheduler switch --to ALGORITHM [--json]",
            "virtuoso --workspace PATH scheduler history [--json]",
        )
        schemas = (
            "virtuoso/item@0.2",
            "virtuoso/next-action@0.1",
            "virtuoso/scheduler-settings@0.1",
            "virtuoso/scheduler-switch@0.1",
            "virtuoso/scheduler-history@0.1",
            "virtuoso/benchmark-run@0.1",
            "virtuoso/focus-proposal@0.1",
            "virtuoso/learner-decision@0.1",
            "virtuoso/learning-state@0.1",
            "virtuoso/focus-performance@0.1",
            "virtuoso/item-history@0.1",
            "virtuoso/workload-by-focus@0.1",
            "virtuoso/stale-links@0.1",
            "virtuoso/lexical-search@0.1",
            "virtuoso/embed-upsert@0.1",
            "virtuoso/semantic-search@0.1",
            "virtuoso/search-status@0.1",
            "virtuoso/review-queue@0.1",
            "virtuoso/review-item@0.1",
            "virtuoso/review-attempt-result@0.1",
            "virtuoso/review-skip-result@0.1",
            "virtuoso/review-error@0.1",
        )
        for marker in (*commands, *schemas):
            with self.subTest(marker=marker):
                self.assertIn(marker, reference)
        self.assertIn("`legacy_files`", reference)
        self.assertIn("`path`, `reason`", reference)

    def test_architecture_lists_real_modules_and_labels_target_design(self) -> None:
        architecture = (ROOT / "docs" / "04-architecture.md").read_text(
            encoding="utf-8"
        )
        modules = (
            "__init__.py",
            "candidates.py",
            "cli.py",
            "errors.py",
            "learning.py",
            "learning_state.py",
            "modules.py",
            "practice.py",
            "queries.py",
            "review.py",
            "schedulers.py",
            "search.py",
            "workspace.py",
        )
        for module in modules:
            with self.subTest(module=module):
                self.assertIn(f"`{module}`", architecture)
        for fictional_package in (
            "- `domain`:",
            "- `application`:",
            "- `infrastructure`:",
        ):
            self.assertNotIn(fictional_package, architecture)
        self.assertIn("## Target design", architecture)

    def test_module_docs_describe_calling_code_opt_in_without_a_cli_command(
        self,
    ) -> None:
        paths = (
            ROOT / "README.md",
            ROOT / "product.json",
            ROOT / "docs" / "04-architecture.md",
            ROOT / "docs" / "07-delivery-contract.md",
            ROOT / "docs" / "14-api-consideration.md",
        )
        combined = "\n".join(path.read_text(encoding="utf-8") for path in paths)
        for stale_claim in (
            "explicit per-call consent",
            "require explicit consent",
            "missing consent",
            "under consent",
        ):
            with self.subTest(stale_claim=stale_claim):
                self.assertNotIn(stale_claim, combined)
        self.assertIn("allow_trusted=True", combined)
        self.assertIn("no public CLI command", combined)

    def test_documentation_index_separates_current_and_historical_records(self) -> None:
        index_path = ROOT / "docs" / "README.md"
        self.assertTrue(index_path.is_file(), "docs/README.md is missing")
        index = index_path.read_text(encoding="utf-8")
        for heading in (
            "## Current user guides",
            "## Planning, research, and design records",
            "## Historical verification and release records",
        ):
            self.assertIn(heading, index)
        for document in (
            "12-cli-reference.md",
            "13-agent-usage.md",
            "15-release-notes.md",
            "16-verification-history.md",
            "00-product-brief.md",
            "11-evidence-ranked-design.md",
            "14-api-consideration.md",
        ):
            with self.subTest(document=document):
                self.assertIn(document, index)
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("[Documentation index](docs/README.md)", readme)

    def test_agent_guides_name_the_real_optional_adapters_and_tools(self) -> None:
        agent_guide = (ROOT / "docs" / "13-agent-usage.md").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("No MCP server, no plugin", agent_guide)
        self.assertIn("optional Hermes plugin", agent_guide)
        self.assertIn("optional Obsidian plugin", agent_guide)
        for command in (
            "queries focus --json",
            "queries history --item",
            "queries workload --json",
            "queries stale-links --json",
            "search lex --query",
            "search embed --item",
            "search sem --model",
            "search status --json",
        ):
            with self.subTest(command=command):
                self.assertIn(command, agent_guide)

        hermes_guide = (ROOT / "plugins" / "hermes" / "README.md").read_text(
            encoding="utf-8"
        )
        manifest = (ROOT / "plugins" / "hermes" / "plugin.yaml").read_text(
            encoding="utf-8"
        )
        registered_tools = {
            line.strip()[2:]
            for line in manifest.splitlines()
            if line.startswith("  - virtuoso_")
        }
        self.assertEqual(
            registered_tools,
            {
                "virtuoso_due",
                "virtuoso_next",
                "virtuoso_transfer_record",
                "virtuoso_status",
            },
        )
        for tool in registered_tools:
            self.assertIn(f"`{tool}`", hermes_guide)
        self.assertIn("workspace:", hermes_guide)

    def test_public_docs_avoid_unsupported_release_and_interface_claims(self) -> None:
        paths = (
            ROOT / "README.md",
            ROOT / "docs" / "04-architecture.md",
            ROOT / "docs" / "07-delivery-contract.md",
            ROOT / "docs" / "12-cli-reference.md",
            ROOT / "docs" / "13-agent-usage.md",
            ROOT / "docs" / "14-api-consideration.md",
            ROOT / "plugins" / "hermes" / "README.md",
            ROOT / "plugins" / "obsidian" / "README.md",
        )
        forbidden = (
            "/Users/",
            "cmd+alt",
            "automatically publishes",
            "automatic GitHub Release",
            "OS-sandboxed",
        )
        for path in paths:
            text = path.read_text(encoding="utf-8")
            for marker in forbidden:
                with self.subTest(path=path.relative_to(ROOT), marker=marker):
                    self.assertNotIn(marker, text)

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
                self.assertIn(f"[{target}]({target})", readme)
        self.assertIn("CONTRIBUTING.md", agent_contract)
        self.assertIn(".github/workflows/ci.yml", agent_contract)


if __name__ == "__main__":
    unittest.main()
