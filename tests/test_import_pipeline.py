from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from virtuoso.candidates import CandidateError, CandidateService, ReviewCandidate
from virtuoso.workspace import WorkspaceError, WorkspaceService


PUBLIC_VAULT = Path(__file__).parent / "fixtures" / "public-vault"
NEW_PRACTICE_BLOCK = """

```virtuoso-practice
{"schema":"virtuoso/practice-item@0.1","id":"systems-retry-boundary","title":"Explain retry ownership","focus":"distributed-systems","prompt":"Which component should decide whether a failed job is retried?","answer":"The component that owns attempt state and the retry policy should decide.","hint":null,"follow_up":null,"state":"active","historical_due_at":null}
```
"""


class ConsolidatedImportPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name).resolve()
        self.vault = root / "public-vault"
        shutil.copytree(PUBLIC_VAULT, self.vault)
        self.workspace_root = root / "workspace"
        self.workspace = WorkspaceService.init(self.workspace_root)
        self.workspace.add_source(
            source_id="public-curriculum",
            kind="obsidian",
            root=self.vault,
        )
        self.workspace.scan_source("public-curriculum")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _source_snapshot(self) -> dict[str, bytes]:
        return {
            path.relative_to(self.vault).as_posix(): path.read_bytes()
            for path in sorted(self.vault.rglob("*.md"))
        }

    def _count(self, table: str) -> int:
        with sqlite3.connect(self.workspace.db_path) as db:
            return int(db.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])

    @staticmethod
    def _proposal_item(candidate: ReviewCandidate) -> dict[str, object]:
        value = candidate.proposal.get("item")
        if not isinstance(value, dict):
            raise AssertionError("candidate has no item proposal")
        return value

    def _run_cli(
        self, *args: str, expected: int = 0
    ) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "virtuoso.cli",
                "--workspace",
                str(self.workspace_root),
                *args,
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            expected,
            msg=f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}",
        )
        return completed

    def test_curriculum_note_generates_bounded_import_candidates_without_importing(self) -> None:
        source_before = self._source_snapshot()

        run = CandidateService(self.workspace).generate(
            source_id="public-curriculum",
            relative_path="curriculum/systems-foundations.md",
            adapter="curriculum",
            limit=20,
        )

        self.assertEqual(run.generator_id, "curriculum-markdown-import")
        self.assertEqual(run.generator_version, "0.1")
        self.assertEqual(run.candidate_count, 2)
        self.assertEqual(run.omitted_count, 0)
        self.assertFalse(run.truncated)
        self.assertEqual(
            [self._proposal_item(candidate)["item_id"] for candidate in run.candidates],
            ["systems-idempotence", "systems-source-truth"],
        )
        for candidate in run.candidates:
            self.assertEqual(candidate.kind, "practice")
            self.assertEqual(candidate.reason_code, "curriculum-practice-import")
            self.assertTrue(candidate.proposal["creates_learning_item"])
            self.assertFalse(candidate.proposal["creates_scheduler_state"])
            self.assertFalse(candidate.proposal["creates_evidence_event"])
            self.assertEqual(candidate.review_state, "proposed")
            self.assertEqual(candidate.source_status, "current")
            self.assertEqual(len(candidate.source_refs), 1)
            source_ref = candidate.source_refs[0]
            self.assertEqual(
                source_ref.relative_path,
                "curriculum/systems-foundations.md",
            )
            self.assertEqual(
                source_ref.content_hash,
                hashlib.sha256(
                    (self.vault / source_ref.relative_path).read_bytes()
                ).hexdigest(),
            )

        repeated = CandidateService(self.workspace).generate(
            source_id="public-curriculum",
            relative_path="curriculum/systems-foundations.md",
            adapter="curriculum",
            limit=20,
        )
        self.assertEqual(repeated.to_dict(), run.to_dict())
        self.assertEqual(self._count("candidate_runs"), 1)
        self.assertEqual(self._count("review_candidates"), 2)
        self.assertEqual(self._count("items"), 0)
        self.assertEqual(self._count("attempts"), 0)
        self.assertEqual(self._count("scheduler_state"), 0)
        self.assertEqual(self._source_snapshot(), source_before)
        self.assertNotIn(
            str(self.vault),
            json.dumps(run.to_dict(), sort_keys=True),
        )

    def test_curriculum_dry_run_reports_exact_candidates_without_writes(self) -> None:
        source_before = self._source_snapshot()
        database_before = self.workspace.db_path.read_bytes()

        completed = self._run_cli(
            "candidate",
            "generate",
            "--source",
            "public-curriculum",
            "--path",
            "curriculum/systems-foundations.md",
            "--adapter",
            "curriculum",
            "--dry-run",
            "--json",
        )

        payload = json.loads(completed.stdout)
        self.assertEqual(payload["generator_id"], "curriculum-markdown-import")
        self.assertEqual(payload["candidate_count"], 2)
        self.assertEqual(
            [candidate["proposal"]["item"]["item_id"] for candidate in payload["candidates"]],
            ["systems-idempotence", "systems-source-truth"],
        )
        self.assertEqual(self._count("candidate_runs"), 0)
        self.assertEqual(self._count("review_candidates"), 0)
        self.assertEqual(self._count("candidate_decisions"), 0)
        self.assertEqual(self._count("items"), 0)
        self.assertEqual(self.workspace.db_path.read_bytes(), database_before)
        self.assertEqual(self._source_snapshot(), source_before)

    def test_existing_virtuoso_item_uses_the_same_candidate_pipeline(self) -> None:
        source_before = self._source_snapshot()

        run = CandidateService(self.workspace).generate(
            source_id="public-curriculum",
            relative_path="items/retrieval-practice.md",
            adapter="curriculum",
        )

        self.assertEqual(run.candidate_count, 1)
        candidate = run.candidates[0]
        item_proposal = self._proposal_item(candidate)
        self.assertEqual(candidate.kind, "practice")
        self.assertEqual(candidate.reason_code, "curriculum-practice-import")
        self.assertEqual(item_proposal["item_id"], "retrieval-practice")
        self.assertEqual(item_proposal["focus"], "learning-science")
        self.assertEqual(
            item_proposal["prompt"],
            "Why does retrieval practice improve later recall?",
        )
        self.assertEqual(item_proposal["historical_due_at"], None)
        self.assertEqual(
            candidate.source_refs[0].relative_path,
            "items/retrieval-practice.md",
        )
        self.assertEqual(self._source_snapshot(), source_before)
        self.assertEqual(self._count("items"), 0)
        self.assertEqual(self._count("scheduler_state"), 0)

    def test_accept_materializes_one_linked_item_without_scheduler_or_evidence(self) -> None:
        source_before = self._source_snapshot()
        generated = json.loads(
            self._run_cli(
                "candidate",
                "generate",
                "--source",
                "public-curriculum",
                "--path",
                "curriculum/systems-foundations.md",
                "--adapter",
                "curriculum",
                "--json",
            ).stdout
        )
        candidate = CandidateService(self.workspace).get(
            generated["candidates"][0]["candidate_id"]
        )

        completed = self._run_cli(
            "candidate",
            "decide",
            "--id",
            candidate.candidate_id,
            "--decision",
            "accept",
            "--json",
        )

        payload = json.loads(completed.stdout)
        self.assertEqual(payload["review_state"], "accepted")
        self.assertEqual(payload["decision"], "accept")
        self.assertEqual(payload["materialized_item_id"], "systems-idempotence")
        item = self.workspace.load_item("systems-idempotence")
        self.assertEqual(item.title, "Explain idempotence")
        self.assertEqual(item.focus, "distributed-systems")
        self.assertEqual(item.prompt, "What makes an operation idempotent?")
        self.assertEqual(
            item.answer,
            "Repeating the operation has the same intended effect as applying it once.",
        )
        self.assertEqual(item.hint, "Compare one application with repeated applications.")
        self.assertEqual(item.follow_up, "Give one idempotent API example.")
        with sqlite3.connect(self.workspace.db_path) as db:
            link = db.execute(
                """SELECT source_id, source_relative_path, source_content_hash
                   FROM item_source_links WHERE item_id = ?""",
                (item.item_id,),
            ).fetchone()
        self.assertEqual(
            link,
            (
                "public-curriculum",
                "curriculum/systems-foundations.md",
                candidate.source_refs[0].content_hash,
            ),
        )
        self.assertEqual(self._count("candidate_decisions"), 1)
        self.assertEqual(self._count("items"), 1)
        self.assertEqual(self._count("item_source_links"), 1)
        self.assertEqual(self._count("attempts"), 0)
        self.assertEqual(self._count("scheduler_state"), 0)
        self.assertEqual(self._count("scheduler_proposals"), 0)
        self.assertEqual(self._source_snapshot(), source_before)

    def test_database_rejects_import_accept_without_materialized_item(self) -> None:
        run = CandidateService(self.workspace).generate(
            source_id="public-curriculum",
            relative_path="curriculum/systems-foundations.md",
            adapter="curriculum",
        )
        candidate = run.candidates[0]

        with sqlite3.connect(self.workspace.db_path) as db:
            db.execute("PRAGMA foreign_keys = ON")
            with self.assertRaisesRegex(sqlite3.IntegrityError, "inconsistent"):
                db.execute(
                    """INSERT INTO candidate_decisions(
                           decision_id, candidate_id, decision, note, decided_at,
                           action, item_json, materialized_item_id
                       ) VALUES (?, ?, 'accept', NULL, ?, 'accept', NULL, NULL)""",
                    (
                        "decision-invalid-import",
                        candidate.candidate_id,
                        "2026-09-02T12:00:00+00:00",
                    ),
                )

        self.assertEqual(self._count("candidate_decisions"), 0)
        self.assertEqual(self._count("items"), 0)
        self.assertEqual(self._count("item_source_links"), 0)

    def test_edit_materializes_the_reviewed_fields_and_preserves_source_provenance(self) -> None:
        source_before = self._source_snapshot()
        run = CandidateService(self.workspace).generate(
            source_id="public-curriculum",
            relative_path="curriculum/systems-foundations.md",
            adapter="curriculum",
        )
        candidate = run.candidates[1]

        completed = self._run_cli(
            "candidate",
            "decide",
            "--id",
            candidate.candidate_id,
            "--decision",
            "edit",
            "--title",
            "Explain state ownership",
            "--prompt",
            "Why should one service own each mutable state?",
            "--note",
            "Tightened wording during review.",
            "--json",
        )

        payload = json.loads(completed.stdout)
        self.assertEqual(payload["review_state"], "accepted")
        self.assertEqual(payload["decision"], "edit")
        self.assertEqual(payload["materialized_item_id"], "systems-source-truth")
        item = self.workspace.load_item("systems-source-truth")
        self.assertEqual(item.title, "Explain state ownership")
        self.assertEqual(
            item.prompt,
            "Why should one service own each mutable state?",
        )
        self.assertEqual(
            item.answer,
            "One owner prevents conflicting writes and makes reconciliation explicit.",
        )
        with sqlite3.connect(self.workspace.db_path) as db:
            row = db.execute(
                """SELECT decision, action, note, item_json, materialized_item_id
                   FROM candidate_decisions WHERE candidate_id = ?""",
                (candidate.candidate_id,),
            ).fetchone()
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row[0], "accept")
        self.assertEqual(row[1], "edit")
        self.assertEqual(row[2], "Tightened wording during review.")
        self.assertEqual(json.loads(row[3])["title"], "Explain state ownership")
        self.assertEqual(row[4], "systems-source-truth")
        self.assertEqual(self._count("item_source_links"), 1)
        self.assertEqual(self._count("scheduler_state"), 0)
        self.assertEqual(self._source_snapshot(), source_before)

    def test_edit_revalidates_changed_item_fields_before_any_write(self) -> None:
        run = CandidateService(self.workspace).generate(
            source_id="public-curriculum",
            relative_path="curriculum/systems-foundations.md",
            adapter="curriculum",
        )
        candidate = run.candidates[0]
        source_before = self._source_snapshot()

        with self.assertRaisesRegex(WorkspaceError, "title.*1-256"):
            CandidateService(self.workspace).decide(
                candidate_id=candidate.candidate_id,
                decision="edit",
                note=None,
                edits={"title": "x" * 257},
            )

        self.assertEqual(self._count("candidate_decisions"), 0)
        self.assertEqual(self._count("items"), 0)
        self.assertEqual(self._count("item_source_links"), 0)
        self.assertEqual(self._source_snapshot(), source_before)

    def test_skip_records_the_decision_without_creating_an_item(self) -> None:
        source_before = self._source_snapshot()
        run = CandidateService(self.workspace).generate(
            source_id="public-curriculum",
            relative_path="curriculum/systems-foundations.md",
            adapter="curriculum",
        )
        candidate = run.candidates[0]

        payload = json.loads(
            self._run_cli(
                "candidate",
                "decide",
                "--id",
                candidate.candidate_id,
                "--decision",
                "skip",
                "--note",
                "Not useful for this workspace.",
                "--json",
            ).stdout
        )

        self.assertEqual(payload["review_state"], "skipped")
        self.assertEqual(payload["decision"], "skip")
        self.assertNotIn("materialized_item_id", payload)
        self.assertEqual(self._count("candidate_decisions"), 1)
        self.assertEqual(self._count("items"), 0)
        self.assertEqual(self._count("item_source_links"), 0)
        self.assertEqual(self._count("attempts"), 0)
        self.assertEqual(self._count("scheduler_state"), 0)
        self.assertEqual(self._source_snapshot(), source_before)

    def test_stale_candidate_cannot_import_or_record_a_decision(self) -> None:
        run = CandidateService(self.workspace).generate(
            source_id="public-curriculum",
            relative_path="curriculum/systems-foundations.md",
            adapter="curriculum",
        )
        candidate = run.candidates[0]
        note = self.vault / "curriculum" / "systems-foundations.md"
        note.write_text(
            note.read_text(encoding="utf-8") + "\nChanged after candidate generation.\n",
            encoding="utf-8",
        )
        changed_source = self._source_snapshot()

        failed = self._run_cli(
            "candidate",
            "decide",
            "--id",
            candidate.candidate_id,
            "--decision",
            "accept",
            "--json",
            expected=2,
        )

        self.assertEqual(failed.stdout, "")
        self.assertIn("changed", failed.stderr)
        self.assertEqual(self._count("candidate_decisions"), 0)
        self.assertEqual(self._count("items"), 0)
        self.assertEqual(self._count("item_source_links"), 0)
        self.assertEqual(self._source_snapshot(), changed_source)

    def test_duplicate_workspace_item_id_rejects_import_without_partial_writes(self) -> None:
        existing = self.workspace.add_item(
            item_id="systems-idempotence",
            title="Existing item",
            focus="existing",
            prompt="Existing prompt?",
            answer="Existing answer.",
        )
        existing_bytes = existing.path.read_bytes()
        run = CandidateService(self.workspace).generate(
            source_id="public-curriculum",
            relative_path="curriculum/systems-foundations.md",
            adapter="curriculum",
        )
        candidate = run.candidates[0]
        source_before = self._source_snapshot()

        failed = self._run_cli(
            "candidate",
            "decide",
            "--id",
            candidate.candidate_id,
            "--decision",
            "accept",
            "--json",
            expected=2,
        )

        self.assertEqual(failed.stdout, "")
        self.assertIn("already exists", failed.stderr)
        self.assertEqual(self._count("candidate_decisions"), 0)
        self.assertEqual(self._count("items"), 1)
        self.assertEqual(self._count("item_source_links"), 0)
        self.assertEqual(existing.path.read_bytes(), existing_bytes)
        self.assertEqual(self._source_snapshot(), source_before)

    def test_malformed_duplicate_or_unsupported_curriculum_writes_no_candidates(self) -> None:
        note = self.vault / "curriculum" / "systems-foundations.md"
        valid = note.read_text(encoding="utf-8")
        invalid_versions = (
            (
                valid.replace('"state":"active"', '"state":"retired"', 1),
                "unsupported practice item state",
            ),
            (
                valid.replace("systems-source-truth", "systems-idempotence"),
                "repeats practice item ids",
            ),
            (
                valid.replace(
                    '"schema":"virtuoso/practice-item@0.1"',
                    '"schema":"virtuoso/practice-item@9.9"',
                    1,
                ),
                "unsupported virtuoso-practice schema",
            ),
        )
        for text, message in invalid_versions:
            with self.subTest(message=message):
                note.write_text(text, encoding="utf-8")
                self.workspace.scan_source("public-curriculum")
                with self.assertRaisesRegex(CandidateError, message):
                    CandidateService(self.workspace).generate(
                        source_id="public-curriculum",
                        relative_path="curriculum/systems-foundations.md",
                        adapter="curriculum",
                    )
                self.assertEqual(self._count("candidate_runs"), 0)
                self.assertEqual(self._count("review_candidates"), 0)
        self.assertEqual(self._count("items"), 0)
        self.assertEqual(self._count("scheduler_state"), 0)

    def test_curriculum_generate_rejects_noncanonical_or_escaping_paths(self) -> None:
        for relative_path in (
            "curriculum//systems-foundations.md",
            "curriculum/../systems-foundations.md",
            "/curriculum/systems-foundations.md",
        ):
            with self.subTest(relative_path=relative_path):
                with self.assertRaisesRegex(CandidateError, "inside|path"):
                    CandidateService(self.workspace).generate(
                        source_id="public-curriculum",
                        relative_path=relative_path,
                        adapter="curriculum",
                    )
        self.assertEqual(self._count("candidate_runs"), 0)
        self.assertEqual(self._count("review_candidates"), 0)

    def test_curriculum_requires_at_least_one_complete_practice_block(self) -> None:
        note = self.vault / "curriculum" / "systems-foundations.md"
        source = note.read_text(encoding="utf-8")
        invalid_versions = (
            source.split("```virtuoso-practice", 1)[0],
            source.replace("\n```\n", "\n", 1),
        )
        for text in invalid_versions:
            with self.subTest(length=len(text)):
                note.write_text(text, encoding="utf-8")
                self.workspace.scan_source("public-curriculum")
                with self.assertRaisesRegex(CandidateError, "practice block"):
                    CandidateService(self.workspace).generate(
                        source_id="public-curriculum",
                        relative_path="curriculum/systems-foundations.md",
                        adapter="curriculum",
                    )
                self.assertEqual(self._count("candidate_runs"), 0)
                self.assertEqual(self._count("review_candidates"), 0)

    def test_delta_is_quiet_when_unchanged_and_supersedes_changed_source_history(self) -> None:
        command = (
            "candidate",
            "delta",
            "--source",
            "public-curriculum",
            "--path",
            "curriculum/systems-foundations.md",
            "--json",
        )
        first = json.loads(self._run_cli(*command).stdout)
        self.assertEqual(first["candidate_count"], 2)
        first_run_id = first["run_id"]
        first_candidate_id = first["candidates"][0]["candidate_id"]
        CandidateService(self.workspace).decide(
            candidate_id=first_candidate_id,
            decision="skip",
            note="Keep history across the next source revision.",
        )
        database_before = self.workspace.db_path.read_bytes()
        source_before = self._source_snapshot()

        unchanged = self._run_cli(*command)

        self.assertEqual(unchanged.stdout, "")
        self.assertEqual(unchanged.stderr, "")
        self.assertEqual(self.workspace.db_path.read_bytes(), database_before)
        self.assertEqual(self._source_snapshot(), source_before)
        self.assertEqual(self._count("candidate_runs"), 1)
        self.assertEqual(self._count("review_candidates"), 2)
        self.assertEqual(self._count("candidate_decisions"), 1)

        note = self.vault / "curriculum" / "systems-foundations.md"
        note.write_text(
            note.read_text(encoding="utf-8") + NEW_PRACTICE_BLOCK,
            encoding="utf-8",
        )
        changed_source = self._source_snapshot()

        changed = json.loads(self._run_cli(*command).stdout)

        self.assertNotEqual(changed["run_id"], first_run_id)
        self.assertEqual(changed["candidate_count"], 3)
        self.assertEqual(
            [
                candidate["proposal"]["item"]["item_id"]
                for candidate in changed["candidates"]
            ],
            [
                "systems-idempotence",
                "systems-retry-boundary",
                "systems-source-truth",
            ],
        )
        service = CandidateService(self.workspace)
        current = service.list(source_id="public-curriculum", current_only=True)
        self.assertEqual(len(current), 3)
        self.assertEqual({candidate.run_id for candidate in current}, {changed["run_id"]})
        historical = service.get(first_candidate_id)
        self.assertEqual(historical.source_status, "changed")
        self.assertEqual(historical.review_state, "skipped")
        self.assertEqual(historical.decision, "skip")
        self.assertEqual(self._count("candidate_runs"), 2)
        self.assertEqual(self._count("review_candidates"), 5)
        self.assertEqual(self._count("candidate_decisions"), 1)
        self.assertEqual(self._count("items"), 0)
        self.assertEqual(self._count("scheduler_state"), 0)
        self.assertEqual(self._source_snapshot(), changed_source)

    def test_concurrent_delta_reports_exactly_one_new_run(self) -> None:
        original_exists = self.workspace.candidate_run_exists
        both_checked = threading.Barrier(2)

        def synchronized_exists(run_id: str) -> bool:
            exists = original_exists(run_id)
            both_checked.wait(timeout=5)
            return exists

        self.workspace.candidate_run_exists = synchronized_exists  # type: ignore[method-assign]
        services = (CandidateService(self.workspace), CandidateService(self.workspace))
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(
                    pool.map(
                        lambda service: service.delta(
                            source_id="public-curriculum",
                            relative_path="curriculum/systems-foundations.md",
                        ),
                        services,
                    )
                )
        finally:
            self.workspace.candidate_run_exists = original_exists  # type: ignore[method-assign]

        self.assertEqual(sum(result is not None for result in results), 1)
        self.assertEqual(self._count("candidate_runs"), 1)
        self.assertEqual(self._count("review_candidates"), 2)


if __name__ == "__main__":
    unittest.main()
