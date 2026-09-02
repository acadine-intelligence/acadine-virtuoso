from __future__ import annotations

import contextlib
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from virtuoso import VirtuosoError
from virtuoso.cli import main
from virtuoso.modules import ModuleError
from virtuoso.practice import PracticeError
from virtuoso.queries import QueryError
from virtuoso.search import SearchError
from virtuoso.workspace import WorkspaceError, WorkspaceService


class CliErrorContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace_path = Path(self.tmp.name).resolve() / "learner"
        self.workspace = WorkspaceService.init(self.workspace_path)
        self.workspace.add_item(
            item_id="plain-text-search",
            title="Plain text search",
            focus="retrieval",
            prompt="Why should ordinary search text stay data?",
            answer="So FTS syntax cannot escape the public query contract.",
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _invoke(self, *args: str) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            returncode = main(["--workspace", str(self.workspace_path), *args])
        return returncode, stdout.getvalue(), stderr.getvalue()

    def assert_cli_error(self, result: tuple[int, str, str], text: str) -> None:
        returncode, stdout, stderr = result
        self.assertEqual(returncode, 2)
        self.assertEqual(stdout, "")
        self.assertTrue(stderr.startswith("Error:"), stderr)
        self.assertIn(text, stderr)
        self.assertNotIn("Traceback", stderr)

    def test_public_error_family_covers_every_domain_error(self) -> None:
        for error_type in (
            WorkspaceError,
            PracticeError,
            ModuleError,
            SearchError,
            QueryError,
        ):
            with self.subTest(error_type=error_type.__name__):
                self.assertTrue(issubclass(error_type, VirtuosoError))

    def test_lexical_domain_error_uses_cli_error_contract(self) -> None:
        self.assert_cli_error(
            self._invoke("search", "lex", "--query", "   ", "--json"),
            "non-empty",
        )

    def test_hyphenated_plain_text_is_successful_cli_input(self) -> None:
        returncode, stdout, stderr = self._invoke(
            "search", "lex", "--query", "plain-text", "--json"
        )

        self.assertEqual(returncode, 0)
        self.assertEqual(stderr, "")
        payload = json.loads(stdout)
        self.assertEqual([hit["item_id"] for hit in payload["hits"]], ["plain-text-search"])

    def test_invalid_vector_json_uses_cli_error_contract(self) -> None:
        self.assert_cli_error(
            self._invoke(
                "search",
                "embed",
                "--item",
                "plain-text-search",
                "--model",
                "test-model",
                "--vector",
                "not json",
                "--json",
            ),
            "invalid vector JSON",
        )

    def test_invalid_search_input_changes_no_workspace_state(self) -> None:
        database_before = self.workspace.db_path.read_bytes()
        state_files_before = sorted(path.name for path in self.workspace.state_dir.iterdir())

        self.assert_cli_error(
            self._invoke("search", "lex", "--query", "   ", "--json"),
            "non-empty",
        )
        self.assert_cli_error(
            self._invoke(
                "search",
                "embed",
                "--item",
                "plain-text-search",
                "--model",
                "test-model",
                "--vector",
                "not json",
                "--json",
            ),
            "invalid vector JSON",
        )

        self.assertEqual(self.workspace.db_path.read_bytes(), database_before)
        self.assertEqual(
            sorted(path.name for path in self.workspace.state_dir.iterdir()),
            state_files_before,
        )

    def test_non_numeric_vector_uses_cli_error_contract(self) -> None:
        self.assert_cli_error(
            self._invoke(
                "search",
                "embed",
                "--item",
                "plain-text-search",
                "--model",
                "test-model",
                "--vector",
                '["not-a-number"]',
                "--json",
            ),
            "numbers",
        )

    def test_semantic_dimension_mismatch_uses_cli_error_contract(self) -> None:
        stored = self._invoke(
            "search",
            "embed",
            "--item",
            "plain-text-search",
            "--model",
            "test-model",
            "--vector",
            "[1, 0]",
            "--json",
        )
        self.assertEqual(stored[0], 0)
        self.assert_cli_error(
            self._invoke(
                "search",
                "sem",
                "--model",
                "test-model",
                "--vector",
                "[1, 0, 0]",
                "--json",
            ),
            "dimension mismatch",
        )

    def test_query_error_uses_cli_error_contract(self) -> None:
        with patch(
            "virtuoso.queries.focus_performance",
            side_effect=QueryError("synthetic query failure"),
        ):
            self.assert_cli_error(
                self._invoke("queries", "focus", "--json"),
                "synthetic query failure",
            )

    def test_unwrapped_sqlite_error_uses_database_error_contract(self) -> None:
        with patch(
            "virtuoso.search.lexical_search",
            side_effect=sqlite3.OperationalError("synthetic database failure"),
        ):
            self.assert_cli_error(
                self._invoke("search", "lex", "--query", "ordinary", "--json"),
                "database unavailable",
            )


if __name__ == "__main__":
    unittest.main()
