from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import unittest
from pathlib import Path
from unittest import mock

import test_annotations
from virtuoso.annotations import AnnotationError, AnnotationService, QuestionSet
from virtuoso.candidates import CandidateService
from virtuoso.workspace import WorkspaceService
from virtuoso.modules import ModuleError, ModuleRunner


class CandidateAnnotationTests(unittest.TestCase):
    _install = test_annotations.AnnotationContractTests._install
    _db = test_annotations.AnnotationContractTests._db
    tearDown = test_annotations.AnnotationContractTests.tearDown

    def setUp(self) -> None:
        test_annotations.AnnotationContractTests.setUp(self)
        self.vault = Path(self.tmp.name).resolve() / "vault"
        shutil.copytree(Path(__file__).parent / "fixtures" / "public-vault", self.vault)
        self.workspace.add_source(source_id="public", kind="markdown", root=self.vault)
        self.workspace.scan_source("public")
        run = CandidateService(self.workspace).generate(
            source_id="public", relative_path="curriculum/systems-foundations.md",
            adapter="curriculum",
        )
        self.candidate = run.candidates[0]
        self.service = AnnotationService(self.workspace)

    def annotate(self, **kwargs):
        return self.service.annotate_candidate(
            candidate_id=self.candidate.candidate_id, module_id="recorder", **kwargs
        )

    def test_bundled_question_set_hashes_are_frozen(self) -> None:
        self.assertEqual(
            QuestionSet.bundled("item_quality_v1").question_set_hash,
            "79d6d03fe6109ee77608d02c409512d4b4f84d02807a9b0f1e0ee795f15cc21e",
        )
        self.assertEqual(
            QuestionSet.bundled("intake_triage_v1").question_set_hash,
            "c06eb70b6b51a1f3e9173024394e42f86a6c372949fe54cb1f4e171b9a083df0",
        )

    def test_sentinel_collisions_refuse_triage_without_running_module(self) -> None:
        self._install()
        for item_id, focus in (("reserved-focus", "none-of-these"), ("none", "testing")):
            with self.subTest(item_id=item_id):
                self.workspace.add_item(item_id=item_id, title="Reserved label",
                                        focus=focus, prompt="A prompt?", answer="An answer.")
                before = self.workspace.db_path.read_bytes()
                with self.assertRaisesRegex(AnnotationError, "reserved triage option"):
                    self.annotate()
                self.assertEqual(self.workspace.db_path.read_bytes(), before)
                self.assertFalse(self.capture.exists())
                with self._db() as db:
                    db.execute("DELETE FROM items WHERE item_id = ?", (item_id,))

    def test_empty_workspace_supplies_only_sentinel_options(self) -> None:
        self.root = Path(self.tmp.name).resolve() / "empty"
        self.workspace = WorkspaceService.init(self.root)
        self.workspace.add_source(source_id="public", kind="markdown", root=self.vault)
        self.workspace.scan_source("public")
        self.candidate = CandidateService(self.workspace).generate(
            source_id="public", relative_path="curriculum/systems-foundations.md",
            adapter="curriculum",
        ).candidates[0]
        self.service = AnnotationService(self.workspace)
        self._install()
        record = self.annotate()
        self.assertEqual(record["answers"]["focus"]["value"], "none-of-these")
        self.assertEqual(record["answers"]["covered_by"]["value"], "none")

    def test_module_boundary_rejects_extra_subject_fields_and_existing_answers(self) -> None:
        self._install()
        self.annotate()
        request = json.loads(self.capture.read_text())
        for field, value in (("attempts", []), ("path", "/synthetic")):
            invalid = json.loads(json.dumps(request))
            invalid["projections"]["annotation.request"]["subject"][field] = value
            with self.assertRaises(ModuleError):
                ModuleRunner._validate_projections(invalid["projections"])
        invalid = json.loads(json.dumps(request))
        invalid["projections"]["annotation.request"]["subject"]["existing_items"][0]["answer"] = "excluded"
        with self.assertRaises(ModuleError):
            ModuleRunner._validate_projections(invalid["projections"])

    def test_missing_comparison_file_marks_stale_and_refuses_new_run(self) -> None:
        self._install()
        self.annotate()
        self.workspace.load_item("testing-effect").path.unlink()
        self.assertTrue(self.service.list(candidate_id=self.candidate.candidate_id)["annotations"][0]["stale"])
        before = self.workspace.db_path.read_bytes()
        with self.assertRaises(AnnotationError):
            self.annotate()
        self.assertEqual(self.workspace.db_path.read_bytes(), before)

    def test_candidate_projection_is_pinned_and_contains_no_existing_answers(self) -> None:
        self._install()
        record = self.annotate()
        request = json.loads(self.capture.read_text())["projections"]["annotation.request"]
        subject = request["subject"]
        self.assertEqual(set(subject), {
            "title", "kind", "rationale", "prompt", "answer", "hint", "focus",
            "focus_options", "covered_by_options", "existing_items",
            "answer_in_prompt_applicable",
        })
        self.assertEqual(subject["existing_items"], [{
            "item_id": "testing-effect", "title": "Explain the testing effect",
            "prompt": "Why does retrieval improve recall?",
        }])
        self.assertEqual(subject["focus_options"], ["learning-science", "none-of-these"])
        self.assertEqual(subject["covered_by_options"], ["testing-effect", "none"])
        self.assertEqual(subject["answer"], self.candidate.proposal["item"]["answer"])
        self.assertTrue(subject["answer_in_prompt_applicable"])
        serialized = json.dumps(request)
        for forbidden in ("Retrieval strengthens later access.", "relative_path", "schedule", "attempt", str(self.root), str(self.vault)):
            self.assertNotIn(forbidden, serialized)
        self.assertEqual(record["subject_hash"], hashlib.sha256(json.dumps(
            subject, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode()).hexdigest())
        self.assertEqual(request["question_set_hash"], QuestionSet.bundled("intake_triage_v1").question_set_hash)
        questions = {q["id"]: q for q in request["questions"]}
        self.assertEqual(questions["focus"]["options"], subject["focus_options"])
        self.assertEqual(questions["covered_by"]["options"], subject["covered_by_options"])

    def test_no_prompt_answer_pair_omits_leak_question_and_answer(self) -> None:
        self._install()
        (self.vault / "structural.md").write_text("# Structural\n\nSee [[New concept]].\n")
        self.workspace.scan_source("public")
        self.candidate = CandidateService(self.workspace).generate(
            source_id="public", relative_path="structural.md",
        ).candidates[0]
        record = self.annotate()
        projection = json.loads(self.capture.read_text())["projections"]["annotation.request"]
        self.assertFalse(projection["subject"]["answer_in_prompt_applicable"])
        self.assertNotIn("answer_in_prompt", record["answers"])
        self.assertEqual({q["id"] for q in projection["questions"]}, {"focus", "entry_mode", "covered_by"})
        self.assertIn("answer_in_prompt", QuestionSet.bundled("intake_triage_v1").question_ids())

    def test_repeated_annotation_appends_with_receipts_and_creates_no_item(self) -> None:
        self._install()
        with self._db() as db:
            items_before = [tuple(r) for r in db.execute("SELECT * FROM items")]
        first, second = self.annotate(), self.annotate()
        self.assertNotEqual(first["annotation_id"], second["annotation_id"])
        self.assertEqual(first["subject_hash"], second["subject_hash"])
        listed = self.service.list(candidate_id=self.candidate.candidate_id)["annotations"]
        self.assertEqual(len(listed), 2)
        self.assertEqual(self.service.latest_candidate_proposal(self.candidate.candidate_id)["annotation_id"], second["annotation_id"])
        with self._db() as db:
            self.assertEqual([tuple(r) for r in db.execute("SELECT * FROM items")], items_before)
            self.assertEqual(db.execute("SELECT count(*) FROM module_run_receipts").fetchone()[0], 2)
        self.assertTrue(all(not r["claims_mastery"] and not r["stale"] for r in listed))

    def test_subject_kind_and_missing_candidate_fail_without_writes(self) -> None:
        self._install()
        before = self.workspace.db_path.read_bytes()
        with self.assertRaisesRegex(AnnotationError, "does not annotate candidates"):
            self.annotate(question_set_id="item_quality_v1")
        with self.assertRaisesRegex(AnnotationError, "no review candidate"):
            self.service.annotate_candidate(candidate_id="missing", module_id="recorder")
        with self.assertRaisesRegex(AnnotationError, "does not annotate items"):
            self.service.annotate_item(item_id="testing-effect", module_id="recorder", question_set_id="intake_triage_v1")
        self.assertFalse(self.capture.exists())
        self.assertEqual(self.workspace.db_path.read_bytes(), before)

    def test_remote_consent_precedes_module_execution_and_credential_resolution(self) -> None:
        self._install(network="remote")
        before = self.workspace.db_path.read_bytes()
        with mock.patch("virtuoso.annotations.credential_resolution.resolve") as resolve:
            with self.assertRaisesRegex(AnnotationError, "allow-remote"):
                self.annotate()
            resolve.assert_not_called()
        self.assertFalse(self.capture.exists())
        self.assertEqual(self.workspace.db_path.read_bytes(), before)
        self.workspace.configure_annotator(allow_remote=True)
        self.assertEqual(self.annotate()["module_network"], "remote")

    def test_invalid_answers_fail_closed_without_receipt(self) -> None:
        good = {
            "focus": {"primitive": "choice", "value": "learning-science", "confidence": 0.5},
            "entry_mode": {"primitive": "choice", "value": "recall-ready", "confidence": 0.5},
            "covered_by": {"primitive": "choice", "value": "none", "confidence": 0.5},
            "answer_in_prompt": {"primitive": "noul", "value": 0.2, "confidence": 0.5},
        }
        self._install()
        script_path = self.root / "modules" / "recorder" / "annotator.py"
        variants = []
        for question in ("focus", "covered_by", "entry_mode"):
            bad = json.loads(json.dumps(good))
            bad[question]["value"] = "not-supplied"
            variants.append(bad)
        variants.append({k: v for k, v in good.items() if k != "answer_in_prompt"})
        variants.append({**good, "unexpected": good["focus"]})
        bad = json.loads(json.dumps(good))
        bad["answer_in_prompt"]["value"] = 1.1
        variants.append(bad)
        bad = json.loads(json.dumps(good))
        bad["focus"] = {"primitive": "noul", "value": 0.5, "confidence": 0.5}
        variants.append(bad)
        for answers in variants:
            with self.subTest(answers=answers):
                script_path.write_text(test_annotations._BAD_ANSWER_ANNOTATOR % json.dumps(answers))
                before = self.workspace.db_path.read_bytes()
                with self.assertRaises(AnnotationError):
                    self.annotate()
                self.assertEqual(self.workspace.db_path.read_bytes(), before)

    def test_options_and_existing_prompt_changes_mark_stale(self) -> None:
        self._install()
        self.annotate()
        self.workspace.add_item(item_id="another", title="Another", focus="systems", prompt="Another prompt?", answer="Another answer.")
        listed = self.service.list(candidate_id=self.candidate.candidate_id)["annotations"]
        self.assertTrue(listed[0]["stale"])
        second = self.annotate()
        self.assertEqual(second["question_set_hash"], listed[0]["question_set_hash"])
        self.assertNotEqual(second["subject_hash"], listed[0]["subject_hash"])
        report = self.workspace.doctor()
        self.assertEqual(report["status"], "healthy")
        self.assertEqual(report["annotations"], {"total": 2, "stale": 1, "stale_items": [], "stale_candidates": [self.candidate.candidate_id]})
        item = self.workspace.load_item("another")
        item.path.write_text(item.path.read_text().replace("Another prompt?", "Changed prompt?"))
        with self._db() as db:
            db.execute("UPDATE items SET content_hash = ? WHERE item_id = ?",
                       (hashlib.sha256(item.path.read_bytes()).hexdigest(), item.item_id))
        self.assertTrue(all(r["stale"] for r in self.service.list(candidate_id=self.candidate.candidate_id)["annotations"]))

    def test_existing_answer_is_excluded_from_subject_hash(self) -> None:
        self._install()
        self.annotate()
        item = self.workspace.load_item("testing-effect")
        item.path.write_text(item.path.read_text().replace("Retrieval strengthens later access.", "A revised existing answer."))
        with self._db() as db:
            db.execute("UPDATE items SET content_hash = ? WHERE item_id = ?",
                       (hashlib.sha256(item.path.read_bytes()).hexdigest(), item.item_id))
        self.assertFalse(self.service.list(candidate_id=self.candidate.candidate_id)["annotations"][0]["stale"])

    def test_item_retirement_invalidates_candidate_annotation(self) -> None:
        self._install()
        self.annotate()
        self.workspace.retire_item("testing-effect")
        self.assertTrue(self.service.list(candidate_id=self.candidate.candidate_id)["annotations"][0]["stale"])

    def test_option_change_during_module_run_refuses_both_writes(self) -> None:
        self._install()
        run = ModuleRunner.run
        def change_options(runner, *args, **kwargs):
            result = run(runner, *args, **kwargs)
            self.workspace.add_item(item_id="during-run", title="During run", focus="new-focus", prompt="Prompt?", answer="Answer.")
            return result
        with mock.patch.object(ModuleRunner, "run", change_options):
            with self.assertRaisesRegex(AnnotationError, "changed while the annotator ran"):
                self.annotate()
        with self._db() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM annotations").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT count(*) FROM module_run_receipts").fetchone()[0], 0)

    def test_candidate_projection_change_changes_subject_hash(self) -> None:
        self._install()
        self.annotate()
        # Candidates are immutable in ordinary use; simulate a changed snapshot
        # without mutating evidence to pin hashing of every projected field.
        original = self.workspace.candidate_annotation_subject
        subject = original(self.candidate.candidate_id)
        for field in ("title", "kind", "rationale", "prompt", "answer", "hint", "focus"):
            with self.subTest(field=field):
                changed = {**subject, field: "changed"}
                with mock.patch.object(self.workspace, "_candidate_annotation_subject", return_value=changed):
                    self.assertTrue(self.service.list(candidate_id=self.candidate.candidate_id)["annotations"][0]["stale"])

    def test_item_and_candidate_filters_are_separate(self) -> None:
        self._install()
        self.annotate()
        self.service.annotate_item(item_id="testing-effect", module_id="recorder")
        self.assertEqual(len(self.service.list()["annotations"]), 2)
        self.assertEqual(len(self.service.list(item_id="testing-effect")["annotations"]), 1)
        self.assertEqual(len(self.service.list(candidate_id=self.candidate.candidate_id)["annotations"]), 1)
        with self.assertRaises(AnnotationError):
            self.service.list(item_id="testing-effect", candidate_id=self.candidate.candidate_id)


if __name__ == "__main__":
    unittest.main()
