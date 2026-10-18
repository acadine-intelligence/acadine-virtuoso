"""The bundled agent skill must only teach commands and fields the CLI accepts.

The commands are read from SKILL.md itself, so a new or changed command in the
skill is checked without editing this file.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import re
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from virtuoso.cli import _parser


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "examples" / "agent-skills" / "virtuoso" / "SKILL.md"
CLI = [sys.executable, "-c", "import sys; from virtuoso.cli import main; sys.exit(main())"]


def _strict(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """Disable prefix matching so a renamed flag cannot pass as an abbreviation."""
    parser.allow_abbrev = False
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for child in action.choices.values():
                _strict(child)
    return parser


def _options_with_choices(parser: argparse.ArgumentParser) -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for child in action.choices.values():
                for flag, values in _options_with_choices(child).items():
                    found.setdefault(flag, set()).update(values)
        elif action.choices and action.option_strings:
            for flag in action.option_strings:
                found.setdefault(flag, set()).update(str(c) for c in action.choices)
    return found


def _fill(token: str, choices: set[str] | None) -> str:
    placeholder = token.startswith("<") or (token.isupper() and token.isalpha())
    if placeholder and choices:
        return sorted(choices)[0]
    if token == "N":
        return "3"
    return "x" if placeholder else token


def _taught_commands(text: str) -> list[list[str]]:
    taught = []
    for span in re.findall(r"`([^`\n]+)`", text):
        tokens = shlex.split(re.sub(r"[\[\]]", "", span))
        # Any span that opens with a bare word and carries a flag is a command.
        # An unknown subcommand then fails to parse instead of being skipped.
        # The general usage line names the program itself; skip it.
        if tokens and tokens[0] != "virtuoso" and re.fullmatch(r"[a-z][a-z-]*", tokens[0]) and any(
            t.startswith("--") for t in tokens
        ):
            taught.append(tokens)
    return taught


def _run(workspace: Path, *argv: str, stdin: str | None = None) -> dict:
    result = subprocess.run(
        [*CLI, "--workspace", str(workspace), *argv],
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(f"{argv} exited {result.returncode}: {result.stderr}")
    return json.loads(result.stdout)


class AgentSkillContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = SKILL.read_text(encoding="utf-8")
        self.parser = _strict(_parser())
        self.choices = _options_with_choices(self.parser)

    def test_frontmatter_names_the_skill(self) -> None:
        match = re.match(r"---\n(.*?)\n---\n", self.text, re.S)
        assert match is not None
        fields = dict(
            line.split(":", 1) for line in match.group(1).splitlines() if ":" in line
        )
        self.assertEqual(fields["name"].strip(), "virtuoso")
        self.assertTrue(fields["description"].strip().startswith("Use when"))

    def test_every_taught_command_parses_strictly(self) -> None:
        taught = _taught_commands(self.text)
        self.assertGreaterEqual(len(taught), 8)
        for tokens in taught:
            filled, previous = [], ""
            for token in tokens:
                filled.append(_fill(token, self.choices.get(previous)))
                previous = token
            with self.subTest(command=" ".join(tokens)):
                with contextlib.redirect_stderr(io.StringIO()):
                    try:
                        self.parser.parse_args(["--workspace", "w", *filled])
                    except SystemExit as exc:
                        self.fail(f"CLI rejects taught command (exit {exc.code})")

    def test_every_taught_choice_value_is_accepted(self) -> None:
        pairs = re.findall(r"(--[a-z][a-z-]*) ([a-z][a-z-]*)\b", self.text)
        checked = 0
        for flag, value in pairs:
            if flag in self.choices:
                checked += 1
                with self.subTest(flag=flag, value=value):
                    self.assertIn(value, self.choices[flag])
        self.assertGreaterEqual(checked, 2)

    def test_taught_grades_are_exactly_the_result_choices(self) -> None:
        line = next(line for line in self.text.splitlines() if "grade themselves" in line)
        grades = set(re.findall(r"`([a-z-]+)`", line))
        self.assertEqual(grades, self.choices["--result"])

    def test_taught_journey_runs_against_the_cli(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp).resolve() / "ws"
            _run(workspace, "init", "--json")
            _run(workspace, "add", "--id", "recall-item", "--title", "T", "--focus", "f",
                 "--prompt", "Why?", "--answer", "Because.", "--json")
            _run(workspace, "add", "--id", "learn-item", "--title", "L", "--focus", "g",
                 "--prompt", "How?", "--answer", "Thus.", "--entry-mode", "learn-first",
                 "--learning-unit", "Read this first.", "--json")

            # next returns the fields the skill names, and never the answer.
            action = _run(workspace, "next", "--focus", "f", "--json")
            for field in ("action", "item_id", "title", "prompt"):
                self.assertIn(f"`{field}`", self.text)
                self.assertIn(field, action)
            self.assertNotIn("Because.", json.dumps(action))

            # review load returns item.answer, as the skill says.
            loaded = _run(workspace, "review", "load", "--item", "recall-item", "--json")
            self.assertIn("`item.answer`", self.text)
            self.assertEqual(loaded["item"]["answer"], "Because.")

            # The study-completion request, built exactly as the skill describes.
            unit = _run(workspace, "review", "study-load", "--item", "learn-item", "--json")
            for path in ("item.learning_unit", "item.content_hash", "item.learning_unit_hash"):
                self.assertIn(f"`{path}`", self.text)
            template = re.search(
                r'`(\{"schema":"virtuoso/study-completion@0\.1".*?\})`', self.text
            )
            assert template is not None
            request = (
                template.group(1)
                .replace("ITEM", '"learn-item"')
                .replace(
                    '"item_content_hash":HASH',
                    f'"item_content_hash":"{unit["item"]["content_hash"]}"',
                )
                .replace(
                    '"learning_unit_hash":HASH',
                    f'"learning_unit_hash":"{unit["item"]["learning_unit_hash"]}"',
                )
            )
            _run(workspace, "review", "study-record", "--json", stdin=request)

            # A give-up after the prompt is recordable as the skill instructs,
            # and the default help level is the one the skill states.
            recorded = _run(workspace, "practice", "--item", "recall-item", "--administer",
                            "--response", "", "--result", "not-demonstrated",
                            "--confidence", "1", "--json")
            stated = re.search(
                r"Without `--agent-help`, an administered attempt records `([a-z]+)`", self.text
            )
            assert stated is not None
            self.assertEqual(recorded["agent_help"], stated.group(1))


if __name__ == "__main__":
    unittest.main()
