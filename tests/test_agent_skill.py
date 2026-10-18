"""The bundled agent skill must only teach commands the CLI accepts."""

from __future__ import annotations

import contextlib
import io
import json
import re
import unittest
from pathlib import Path

from virtuoso.cli import _parser


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "examples" / "agent-skills" / "virtuoso" / "SKILL.md"
CLI_REFERENCE = ROOT / "docs" / "12-cli-reference.md"

# Each command line the skill teaches, with placeholder values filled in.
TAUGHT_COMMANDS = (
    ["add", "--id", "i", "--title", "t", "--focus", "f", "--prompt", "p",
     "--answer", "a", "--hint", "h", "--json"],
    ["add", "--id", "i", "--title", "t", "--focus", "f", "--prompt", "p",
     "--answer", "a", "--entry-mode", "learn-first", "--learning-unit", "u", "--json"],
    ["next", "--json"],
    ["next", "--focus", "f", "--json"],
    ["review", "study-load", "--item", "i", "--json"],
    ["review", "study-record", "--json"],
    ["practice", "--item", "i", "--administer", "--response", "r",
     "--result", "partial", "--confidence", "3", "--agent-help", "light", "--json"],
    ["queries", "workload", "--json"],
    ["queries", "history", "--item", "i", "--json"],
    ["doctor", "--json"],
)


class AgentSkillContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = SKILL.read_text(encoding="utf-8")

    def test_frontmatter_names_the_skill(self) -> None:
        match = re.match(r"---\n(.*?)\n---\n", self.text, re.S)
        assert match is not None
        fields = dict(
            line.split(":", 1) for line in match.group(1).splitlines() if ":" in line
        )
        self.assertEqual(fields["name"].strip(), "virtuoso")
        self.assertTrue(fields["description"].strip().startswith("Use when"))

    def test_every_taught_command_parses(self) -> None:
        parser = _parser()
        for argv in TAUGHT_COMMANDS:
            with self.subTest(argv=argv):
                with contextlib.redirect_stderr(io.StringIO()):
                    parser.parse_args(["--workspace", "w", *argv])

    def test_every_taught_flag_appears_in_the_skill(self) -> None:
        for argv in TAUGHT_COMMANDS:
            for token in argv:
                if token.startswith("--"):
                    with self.subTest(flag=token):
                        self.assertIn(token, self.text)

    def test_study_completion_request_matches_cli_reference(self) -> None:
        skill_request = re.search(r'`(\{"schema":"virtuoso/study-completion@0\.1".*?\})`', self.text)
        assert skill_request is not None
        skill_fields = set(re.findall(r'"(\w+)":', skill_request.group(1)))
        reference = CLI_REFERENCE.read_text(encoding="utf-8")
        block = re.search(
            r'```json\n(\{\n  "schema": "virtuoso/study-completion@0\.1".*?\})\n```',
            reference,
            re.S,
        )
        assert block is not None
        self.assertEqual(skill_fields, set(json.loads(block.group(1))))


if __name__ == "__main__":
    unittest.main()
