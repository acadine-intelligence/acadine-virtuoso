from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from virtuoso.candidates import CandidateService
from virtuoso.workspace import WorkspaceService


class CandidateAnnotationCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name).resolve()
        self.root = root / "workspace"
        self.workspace = WorkspaceService.init(self.root)
        self.vault = root / "source"
        self.vault.mkdir()
        item = {
            "schema": "virtuoso/practice-item@0.1",
            "id": "proposed-capital",
            "title": "Name the synthetic capital",
            "focus": "geography",
            "prompt": "The capital is Luma. What is the capital?",
            "answer": "Luma",
            "hint": "Name a city.",
            "follow_up": None,
            "state": "active",
            "historical_due_at": None,
        }
        (self.vault / "intake.md").write_text(
            "---\nschema: virtuoso/curriculum@0.1\ntitle: Synthetic intake\n---\n\n"
            "```virtuoso-practice\n" + json.dumps(item) + "\n```\n"
        )
        (self.vault / "topic.md").write_text("# Unstudied topic\n")
        self.workspace.add_source(source_id="source", kind="markdown", root=self.vault)
        self.workspace.scan_source("source")
        self.candidate = CandidateService(self.workspace).generate(
            source_id="source", relative_path="intake.md", adapter="curriculum"
        ).candidates[0]
        self.workspace.add_item(
            item_id="existing-capital", title="Existing capital", focus="geography",
            prompt=item["prompt"], answer="Existing item answer must stay private.",
        )
        module_source = Path(__file__).resolve().parents[1] / "examples/modules/rule-annotator"
        self.module_dir = self.root / "modules/rule-annotator"
        shutil.copytree(module_source, self.module_dir)
        self.module_dir.parent.chmod(0o700)
        self.module_dir.chmod(0o700)
        self.manifest_path = self.module_dir / "virtuoso.module.json"
        manifest = json.loads(self.manifest_path.read_text())
        manifest["command"]["argv"][0] = sys.executable
        self.manifest_path.write_text(json.dumps(manifest))
        self.manifest_path.chmod(0o600)

    def _cli(self, *args: str, expected: int = 0) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [sys.executable, "-m", "virtuoso.cli", "--workspace", str(self.root), *args],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def _annotate(self, candidate_id: str | None = None, *extra: str) -> dict:
        return json.loads(self._cli(
            "annotate", "candidate", "--candidate", candidate_id or self.candidate.candidate_id,
            "--module", "rule-annotator", "--json", *extra,
        ).stdout)

    def _counts(self) -> tuple[int, int, int]:
        with sqlite3.connect(self.workspace.db_path) as db:
            return tuple(db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                         for table in ("annotations", "module_run_receipts", "items"))

    def test_cli_records_latest_model_proposal_and_appends_without_creating_item(self) -> None:
        original_candidate = self.candidate.to_dict()
        first = self._annotate()
        second = self._annotate(None, "--question-set", "intake_triage_v1")
        self.assertNotEqual(first["annotation_id"], second["annotation_id"])
        self.assertEqual(second["subject_kind"], "candidate")
        self.assertEqual(second["question_set_id"], "intake_triage_v1")
        answers = second["answers"]
        self.assertEqual(answers["focus"]["value"], "geography")
        self.assertEqual(answers["entry_mode"]["value"], "recall-ready")
        self.assertEqual(answers["covered_by"]["value"], "existing-capital")
        self.assertEqual(answers["answer_in_prompt"]["value"], 0.9)
        shown = json.loads(self._cli(
            "candidate", "show", "--id", self.candidate.candidate_id, "--json",
        ).stdout)
        proposal = shown.pop("triage_proposal")
        self.assertEqual(shown, original_candidate)
        self.assertEqual(proposal["annotation_id"], second["annotation_id"])
        self.assertEqual(proposal["authority"], "model-proposal")
        self.assertFalse(proposal["stale"])
        self.assertFalse(proposal["claims_mastery"])
        listed = json.loads(self._cli("candidate", "list", "--json").stdout)
        self.assertEqual(listed["candidates"][0]["triage_proposal"], proposal)
        plain = self._cli("candidate", "show", "--id", self.candidate.candidate_id).stdout
        self.assertIn("Model proposal", plain)
        self.assertIn("recall-ready", plain)
        history = json.loads(self._cli(
            "annotate", "list", "--candidate", self.candidate.candidate_id, "--json",
        ).stdout)
        self.assertEqual(len(history["annotations"]), 2)
        self.assertEqual(self._counts(), (2, 2, 1))

    def test_example_handles_candidate_without_prompt_answer_and_unknown_focus(self) -> None:
        candidate = CandidateService(self.workspace).generate(
            source_id="source", relative_path="topic.md",
        ).candidates[0]
        annotation = self._annotate(candidate.candidate_id)
        self.assertEqual(set(annotation["answers"]), {"focus", "entry_mode", "covered_by"})
        self.assertEqual(annotation["answers"]["focus"]["value"], "none-of-these")
        self.assertEqual(annotation["answers"]["entry_mode"]["value"], "wish-only")
        self.assertEqual(annotation["answers"]["covered_by"]["value"], "none")

    def test_candidate_show_and_list_flag_stale_proposals(self) -> None:
        self._annotate()
        self.workspace.add_item(
            item_id="new-topic", title="New topic", focus="new-focus",
            prompt="A new prompt?", answer="A new answer.",
        )
        shown = json.loads(self._cli(
            "candidate", "show", "--id", self.candidate.candidate_id, "--json",
        ).stdout)
        self.assertTrue(shown["triage_proposal"]["stale"])
        listed = json.loads(self._cli("candidate", "list", "--json").stdout)
        self.assertTrue(listed["candidates"][0]["triage_proposal"]["stale"])
        plain = self._cli("candidate", "show", "--id", self.candidate.candidate_id).stdout
        self.assertIn("Model proposal (stale)", plain)

    def test_candidate_list_filter_is_exclusive_with_item(self) -> None:
        failed = self._cli(
            "annotate", "list", "--candidate", self.candidate.candidate_id,
            "--item", "existing-capital", "--json", expected=2,
        )
        self.assertEqual(failed.stdout, "")
        self.assertIn("not allowed with argument", failed.stderr)

    def test_cli_rejects_wrong_question_set_and_missing_candidate_without_writes(self) -> None:
        for candidate_id, question_set in (
            (self.candidate.candidate_id, "item_quality_v1"),
            ("missing-candidate", "intake_triage_v1"),
        ):
            with self.subTest(candidate_id=candidate_id):
                failed = self._cli(
                    "annotate", "candidate", "--candidate", candidate_id,
                    "--module", "rule-annotator", "--question-set", question_set,
                    "--json", expected=2,
                )
                self.assertEqual(failed.stdout, "")
                self.assertNotIn("Traceback", failed.stderr)
        self.assertEqual(self._counts(), (0, 0, 1))

    def test_fixture_answers_pass_same_fail_closed_contract(self) -> None:
        script_path = self.module_dir / "rule_annotator.py"
        original = script_path.read_text()
        invalid_answers = (
            'answers["focus"]["value"] = "unavailable-focus"',
            'answers["covered_by"]["value"] = "unavailable-item"',
            'answers["entry_mode"]["value"] = "automatic-accept"',
            'del answers["focus"]',
            'answers["extra"] = answers["focus"]',
            'answers["focus"]["primitive"] = "noul"; answers["focus"]["value"] = 0.5',
            'answers["answer_in_prompt"]["value"] = 1.5',
        )
        for mutation in invalid_answers:
            with self.subTest(mutation=mutation):
                script_path.write_text(original.replace(
                    '    response = {', f'    {mutation}\n\n    response = {{',
                ))
                failed = self._cli(
                    "annotate", "candidate", "--candidate", self.candidate.candidate_id,
                    "--module", "rule-annotator", "--json", expected=2,
                )
                self.assertEqual(failed.stdout, "")
                self.assertNotIn("Traceback", failed.stderr)
                self.assertEqual(self._counts(), (0, 0, 1))

    def test_example_keeps_item_quality_contract(self) -> None:
        annotation = json.loads(self._cli(
            "annotate", "item", "--item", "existing-capital",
            "--module", "rule-annotator", "--json",
        ).stdout)
        self.assertEqual(annotation["subject_kind"], "item")
        self.assertEqual(set(annotation["answers"]), {
            "atomic", "answer_supports_prompt", "difficulty", "hint_leaks_answer",
        })
        candidate_history = json.loads(self._cli(
            "annotate", "list", "--candidate", self.candidate.candidate_id, "--json",
        ).stdout)
        self.assertEqual(candidate_history["annotations"], [])

    def test_candidate_credential_flag_preserves_no_credentials_refusal(self) -> None:
        failed = self._cli(
            "annotate", "candidate", "--candidate", self.candidate.candidate_id,
            "--module", "rule-annotator", "--host-credential-stdin", expected=2,
        )
        self.assertIn("declares no credentials", failed.stderr)
        self.assertEqual(self._counts(), (0, 0, 1))

    def test_remote_candidate_annotation_requires_existing_consent_gate(self) -> None:
        manifest = json.loads(self.manifest_path.read_text())
        manifest["network"] = "remote"
        self.manifest_path.write_text(json.dumps(manifest))
        failed = self._cli(
            "annotate", "candidate", "--candidate", self.candidate.candidate_id,
            "--module", "rule-annotator", "--json", expected=2,
        )
        self.assertIn("has not allowed remote", failed.stderr)
        self.assertEqual(self._counts(), (0, 0, 1))
        disclosure = self._cli("annotate", "allow-remote", expected=2)
        self.assertIn("candidate title, kind, rationale", disclosure.stderr)
        self.assertIn("existing item titles and prompts", disclosure.stderr)
        self._cli("annotate", "allow-remote", "--yes", "--json")
        self.assertEqual(self._annotate()["module_network"], "remote")


if __name__ == "__main__":
    unittest.main()
