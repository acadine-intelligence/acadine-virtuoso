from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from virtuoso.annotations import (
    ITEM_QUALITY_QUESTION_SET,
    AnnotationError,
    AnnotationService,
    QuestionSet,
    item_subject,
)
from virtuoso.composition import SessionComposer
from virtuoso.modules import ModuleError, ModuleManifest
from virtuoso.practice import PracticeService
from virtuoso.workspace import WorkspaceError, WorkspaceService

# A minimal annotator that records what it received and answers every question.
_RECORDING_ANNOTATOR = '''
import json, sys
from pathlib import Path
request = json.load(sys.stdin)
Path(sys.argv[1]).write_text(json.dumps(request, sort_keys=True))
projection = request["projections"]["annotation.request"]
answers = {}
for question in projection["questions"]:
    if question["primitive"] == "noul":
        value = 0.75
    elif question["primitive"] == "choice":
        value = question["options"][0]
    else:
        value = 3
    answers[question["id"]] = {
        "primitive": question["primitive"], "value": value, "confidence": 0.5,
    }
json.dump({
    "schema": "virtuoso/module-result@0.1",
    "module_id": "recorder",
    "kind": "annotation-answers",
    "payload": {"answers": answers},
}, sys.stdout)
'''

_BAD_ANSWER_ANNOTATOR = '''
import json, sys
request = json.load(sys.stdin)
json.dump({
    "schema": "virtuoso/module-result@0.1",
    "module_id": "recorder",
    "kind": "annotation-answers",
    "payload": {"answers": %s},
}, sys.stdout)
'''


class AnnotationContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve() / "learner"
        self.workspace = WorkspaceService.init(self.root)
        self.capture = Path(self.tmp.name).resolve() / "capture.json"
        self.workspace.add_item(
            item_id="testing-effect",
            title="Explain the testing effect",
            focus="learning-science",
            prompt="Why does retrieval improve recall?",
            answer="Retrieval strengthens later access.",
            hint="Think about what practice does to memory.",
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _install(
        self,
        *,
        module_id: str = "recorder",
        network: str | None = "none",
        script: str = _RECORDING_ANNOTATOR,
        category: str = "annotator",
        reads: list[str] | None = None,
        returns: str = "annotation-answers",
    ) -> Path:
        module_dir = self.root / "modules" / module_id
        module_dir.mkdir(parents=True, mode=0o700)
        module_dir.parent.chmod(0o700)
        script_path = module_dir / "annotator.py"
        script_path.write_text(script)
        script_path.chmod(0o700)
        manifest: dict[str, object] = {
            "schema": "virtuoso/module@0.1",
            "id": module_id,
            "version": "1.0.0",
            "category": category,
            "command": {
                "argv": [sys.executable, str(script_path), str(self.capture)],
                "timeout_seconds": 2,
            },
            "capabilities": {
                "reads": reads if reads is not None else ["annotation.request"],
                "returns": returns,
            },
            "trust": "local-executable",
        }
        if network is not None:
            manifest["network"] = network
        manifest_path = module_dir / "virtuoso.module.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        manifest_path.chmod(0o600)
        return manifest_path

    def _db(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.workspace.db_path)
        db.row_factory = sqlite3.Row
        return db

    # --- acceptance: the projection carries the item and nothing else ---

    def test_annotator_receives_item_text_and_no_evidence(self) -> None:
        self._install()
        record = AnnotationService(self.workspace).annotate_item(
            item_id="testing-effect", module_id="recorder"
        )
        received = json.loads(self.capture.read_text())
        self.assertEqual(set(received["projections"]), {"annotation.request"})
        projection = received["projections"]["annotation.request"]
        self.assertEqual(
            set(projection),
            {
                "subject_kind",
                "subject_id",
                "subject_hash",
                "question_set_id",
                "question_set_hash",
                "questions",
                "subject",
            },
        )
        item = self.workspace.load_item("testing-effect")
        self.assertEqual(projection["subject"], item_subject(item))
        self.assertEqual(projection["subject_hash"], item.content_hash)
        serialized = json.dumps(received)
        for forbidden in ("attempt", "due", "schedule", "stability", "relative_path"):
            self.assertNotIn(forbidden, serialized)
        self.assertNotIn(str(self.root), serialized)
        self.assertEqual(record["subject_id"], "testing-effect")
        self.assertFalse(record["claims_mastery"])
        self.assertEqual(
            set(record["answers"]), QuestionSet.bundled("item_quality_v1").question_ids()
        )

    def test_annotation_is_recorded_with_receipt_and_hashes(self) -> None:
        self._install()
        record = AnnotationService(self.workspace).annotate_item(
            item_id="testing-effect", module_id="recorder"
        )
        with self._db() as db:
            row = db.execute("SELECT * FROM annotations").fetchone()
            receipt = db.execute(
                "SELECT * FROM module_run_receipts WHERE receipt_id = ?",
                (row["module_receipt_id"],),
            ).fetchone()
        self.assertEqual(row["annotation_id"], record["annotation_id"])
        self.assertEqual(row["question_set_id"], "item_quality_v1")
        self.assertEqual(
            row["question_set_hash"], QuestionSet.bundled("item_quality_v1").question_set_hash
        )
        self.assertEqual(row["module_network"], "none")
        self.assertEqual(receipt["category"], "annotator")
        self.assertEqual(receipt["kind"], "annotation-answers")
        self.assertEqual(receipt["status"], "succeeded")
        self.assertEqual(json.loads(row["answers_json"]), record["answers"])

    # --- acceptance: remote consent ---

    def test_remote_annotator_refused_until_workspace_consents(self) -> None:
        self._install(network="remote")
        service = AnnotationService(self.workspace)
        before = self.workspace.db_path.read_bytes()
        with self.assertRaisesRegex(AnnotationError, "allow-remote"):
            service.annotate_item(item_id="testing-effect", module_id="recorder")
        self.assertFalse(self.capture.exists(), "module must not run without consent")
        self.assertEqual(self.workspace.db_path.read_bytes(), before)

        self.workspace.configure_annotator(allow_remote=True)
        record = service.annotate_item(item_id="testing-effect", module_id="recorder")
        self.assertEqual(record["module_network"], "remote")

        self.workspace.configure_annotator(allow_remote=False)
        with self.assertRaisesRegex(AnnotationError, "allow-remote"):
            service.annotate_item(item_id="testing-effect", module_id="recorder")

    def test_annotator_manifest_must_declare_network(self) -> None:
        manifest_path = self._install(network=None)
        with self.assertRaisesRegex(ModuleError, "network"):
            ModuleManifest.load(manifest_path)
        with self.assertRaisesRegex(AnnotationError, "network"):
            AnnotationService(self.workspace).annotate_item(
                item_id="testing-effect", module_id="recorder"
            )

    def test_scheduler_manifests_keep_loading_without_network(self) -> None:
        manifest_path = self._install(
            category="scheduler", reads=["scheduler.request"], returns="scheduler-proposal",
            network=None,
        )
        manifest = ModuleManifest.load(manifest_path)
        self.assertEqual(manifest.network, "none")

    def test_wrong_category_or_projection_is_refused(self) -> None:
        self._install(
            category="scheduler", reads=["scheduler.request"], returns="scheduler-proposal",
        )
        with self.assertRaisesRegex(AnnotationError, "category must be annotator"):
            AnnotationService(self.workspace).annotate_item(
                item_id="testing-effect", module_id="recorder"
            )

    # --- acceptance: answers must match the question set ---

    def test_answers_missing_a_question_are_refused_without_a_write(self) -> None:
        answers = {
            "atomic": {"primitive": "noul", "value": 0.5, "confidence": 0.5},
        }
        self._install(script=_BAD_ANSWER_ANNOTATOR % json.dumps(answers))
        with self.assertRaisesRegex(AnnotationError, "missing"):
            AnnotationService(self.workspace).annotate_item(
                item_id="testing-effect", module_id="recorder"
            )
        with self._db() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM annotations").fetchone()[0], 0)
            self.assertEqual(
                db.execute("SELECT count(*) FROM module_run_receipts").fetchone()[0], 0
            )

    def test_choice_outside_options_is_refused(self) -> None:
        good = {
            question["id"]: {
                "primitive": question["primitive"],
                "value": 0.5 if question["primitive"] == "noul" else "impossible",
                "confidence": 0.5,
            }
            for question in ITEM_QUALITY_QUESTION_SET["questions"]
        }
        self._install(script=_BAD_ANSWER_ANNOTATOR % json.dumps(good))
        with self.assertRaisesRegex(AnnotationError, "allowed: easy, moderate, hard"):
            AnnotationService(self.workspace).annotate_item(
                item_id="testing-effect", module_id="recorder"
            )

    def test_probability_outside_unit_interval_is_refused_by_the_module_boundary(self) -> None:
        bad = {
            question["id"]: {
                "primitive": question["primitive"],
                "value": 1.5 if question["primitive"] == "noul" else "easy",
                "confidence": 0.5,
            }
            for question in ITEM_QUALITY_QUESTION_SET["questions"]
        }
        self._install(script=_BAD_ANSWER_ANNOTATOR % json.dumps(bad))
        with self.assertRaisesRegex(AnnotationError, "probability"):
            AnnotationService(self.workspace).annotate_item(
                item_id="testing-effect", module_id="recorder"
            )

    # --- acceptance: append-only and stale ---

    def test_annotations_are_append_only(self) -> None:
        self._install()
        AnnotationService(self.workspace).annotate_item(
            item_id="testing-effect", module_id="recorder"
        )
        with self._db() as db:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                db.execute("UPDATE annotations SET surface = 'edited'")
            with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                db.execute("DELETE FROM annotations")

    def test_edited_item_marks_annotation_stale_and_blocks_new_run_until_sync(self) -> None:
        self._install()
        service = AnnotationService(self.workspace)
        service.annotate_item(item_id="testing-effect", module_id="recorder")
        item_path = self.workspace.load_item("testing-effect").path
        item_path.write_text(item_path.read_text() + "\nA new line.\n")

        listed = service.list(item_id="testing-effect")
        self.assertEqual(len(listed["annotations"]), 1)
        self.assertFalse(listed["claims_mastery"])
        # The stored row still points at the hash it judged; the item moved on.
        with self._db() as db:
            current = db.execute(
                "SELECT content_hash FROM items WHERE item_id = 'testing-effect'"
            ).fetchone()[0]
        self.assertEqual(listed["annotations"][0]["subject_hash"], current)
        with self.assertRaisesRegex(AnnotationError, "stale"):
            service.annotate_item(item_id="testing-effect", module_id="recorder")

    def test_list_flags_stale_when_index_hash_moves(self) -> None:
        self._install()
        service = AnnotationService(self.workspace)
        service.annotate_item(item_id="testing-effect", module_id="recorder")
        with self._db() as db:
            db.execute(
                "UPDATE items SET content_hash = ? WHERE item_id = 'testing-effect'",
                ("0" * 64,),
            )
        listed = service.list(item_id="testing-effect")
        self.assertTrue(listed["annotations"][0]["stale"])

    # --- acceptance: nothing downstream reads annotations ---

    def test_selection_and_scheduling_ignore_annotations(self) -> None:
        self._install()
        now = datetime.now(timezone.utc)
        before = self.workspace.select_next(now)
        AnnotationService(self.workspace).annotate_item(
            item_id="testing-effect", module_id="recorder"
        )
        after = self.workspace.select_next(now)
        self.assertEqual(before.item.item_id, after.item.item_id)
        self.assertEqual(before.rationale, after.rationale)
        session = SessionComposer(self.workspace).compose(now=now)
        self.assertNotIn("annotation", json.dumps(session, default=str).lower())
        outcome = PracticeService(self.workspace).run_administered(
            item_id="testing-effect",
            response="retrieval strengthens access",
            result="demonstrated",
            confidence=4,
        )
        self.assertNotIn("annotation", json.dumps(outcome, default=str).lower())

    def test_no_read_of_annotations_outside_the_annotation_paths(self) -> None:
        src = Path(__file__).resolve().parents[1] / "src" / "virtuoso"
        offenders = []
        for path in sorted(src.glob("*.py")):
            if path.name in {"annotations.py", "workspace.py", "cli.py"}:
                continue
            text = path.read_text()
            if "FROM annotations" in text or "list_annotations" in text:
                offenders.append(path.name)
        self.assertEqual(offenders, [], "only the annotation paths may touch the table")
        workspace_text = (src / "workspace.py").read_text()
        reads = [
            line.strip()
            for line in workspace_text.splitlines()
            if "FROM annotations" in line
        ]
        self.assertEqual(len(reads), 1, "one read path: list_annotations")

    # --- schema ---

    def test_migration_reaches_seventeen_with_annotations_table(self) -> None:
        with self._db() as db:
            version = db.execute(
                "SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1"
            ).fetchone()[0]
            tables = {
                row[0]
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        self.assertEqual(version, 17)
        self.assertIn("annotations", tables)

    def test_question_set_hash_is_stable_and_changes_with_wording(self) -> None:
        first = QuestionSet.bundled("item_quality_v1")
        second = QuestionSet.bundled("item_quality_v1")
        self.assertEqual(first.question_set_hash, second.question_set_hash)
        with self.assertRaisesRegex(AnnotationError, "unknown question set"):
            QuestionSet.bundled("nope")

    def test_unknown_module_id_is_refused_with_workspace_untouched(self) -> None:
        before = self.workspace.db_path.read_bytes()
        with self.assertRaisesRegex(AnnotationError, "manifest not found"):
            AnnotationService(self.workspace).annotate_item(
                item_id="testing-effect", module_id="missing"
            )
        self.assertEqual(self.workspace.db_path.read_bytes(), before)

    def test_annotator_configuration_is_validated(self) -> None:
        config = self.workspace.configuration()
        config["annotator"] = {"allow_remote": "yes"}
        self.workspace.config_path.write_text(json.dumps(config))
        with self.assertRaisesRegex(WorkspaceError, "allow_remote"):
            self.workspace.configuration()


if __name__ == "__main__":
    unittest.main()
