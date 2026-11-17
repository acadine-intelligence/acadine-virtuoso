from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from virtuoso import gaps
from virtuoso.gaps import GapError

NOW = datetime(2026, 11, 17, 9, 0, tzinfo=timezone.utc)


def day(offset: int, *, base: datetime = NOW) -> str:
    return (base + timedelta(days=offset)).date().isoformat()


def gap(gap_id: str, **overrides: object) -> dict[str, object]:
    item: dict[str, object] = {
        "id": gap_id,
        "project": "builder",
        "title": f"What {gap_id} does",
        "unsure_of": "How the retry path decides to give up.",
        "evidence": [
            {"session": "session-a", "date": day(-3), "note": "asked twice"}
        ],
        "recurrences": 1,
        "first_seen": day(-10),
        "last_seen": day(0),
        "severity": "medium",
        "score": 2.0,
        "grill_question": f"Explain what happens when {gap_id} fails.",
        "source_of_truth": f"docs/{gap_id}.md",
    }
    item.update(overrides)
    return item


class GapTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name).resolve() / "gaps"
        self.dir.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write(self, name: str, items: object) -> None:
        (self.dir / name).write_text(json.dumps(items), encoding="utf-8")

    def ranked_ids(self, **kwargs: object) -> list[str]:
        return [
            g.item.gap_id
            for g in gaps.ranked_gaps(self.dir, now=NOW, **kwargs)  # type: ignore[arg-type]
        ]


class ScoringTests(GapTestCase):
    def test_recency_decays_by_half_life_with_floor(self) -> None:
        self.assertEqual(gaps.recency_factor(day(0), NOW), 1.0)
        self.assertAlmostEqual(gaps.recency_factor(day(-30), NOW), 0.5)
        self.assertEqual(gaps.recency_factor(day(-365), NOW), gaps.RECENCY_FLOOR)
        self.assertEqual(gaps.recency_factor(None, NOW), 1.0)

    def test_recent_gap_outranks_old_gap_of_equal_score(self) -> None:
        self.write(
            "a.json",
            [gap("old", last_seen=day(-60)), gap("fresh", last_seen=day(-1))],
        )
        self.assertEqual(self.ranked_ids(), ["fresh", "old"])

    def test_severity_is_used_when_score_is_absent(self) -> None:
        items = [gap("low", severity="low"), gap("high", severity="high")]
        for item in items:
            del item["score"]
        self.write("a.json", items)
        ranked = gaps.ranked_gaps(self.dir, now=NOW)
        self.assertEqual([g.item.gap_id for g in ranked], ["high", "low"])
        self.assertEqual(ranked[0].factors["base"], 3.0)

    def test_wrong_and_partial_boost_and_knows_demotes(self) -> None:
        self.write(
            "a.json",
            [gap("knows-it"), gap("unseen"), gap("partial-it"), gap("wrong-it")],
        )
        for gap_id, grade in (
            ("knows-it", "knows"),
            ("partial-it", "partial"),
            ("wrong-it", "wrong"),
        ):
            gaps.record_grade(self.dir, gap_id, grade, now=NOW)
        ranked = gaps.ranked_gaps(self.dir, now=NOW)
        self.assertEqual(
            [g.item.gap_id for g in ranked],
            ["wrong-it", "partial-it", "unseen", "knows-it"],
        )
        by_id = {g.item.gap_id: g for g in ranked}
        self.assertEqual(by_id["unseen"].factors["never_grilled"], 1.25)
        self.assertEqual(by_id["wrong-it"].factors["never_grilled"], 1.0)
        self.assertFalse(by_id["knows-it"].stale)
        self.assertEqual(by_id["knows-it"].effective_status, "knows")

    def test_knows_goes_stale_after_thirty_days(self) -> None:
        self.write("a.json", [gap("one", last_seen=day(-40))])
        gaps.record_grade(self.dir, "one", "knows", now=NOW - timedelta(days=31))
        [ranked] = gaps.ranked_gaps(self.dir, now=NOW)
        self.assertTrue(ranked.stale)
        self.assertEqual(ranked.stale_reason, "grade-expired")
        self.assertEqual(ranked.effective_status, "unverified")
        self.assertEqual(ranked.status, "knows")

    def test_knows_goes_stale_when_source_changes(self) -> None:
        self.write("a.json", [gap("one")])
        gaps.record_grade(self.dir, "one", "knows", now=NOW)
        self.write("a.json", [gap("one", source_of_truth="docs/new.md")])
        [ranked] = gaps.ranked_gaps(self.dir, now=NOW)
        self.assertEqual(ranked.stale_reason, "source-changed")
        self.assertEqual(ranked.factors["status"], 1.0)

    def test_knows_goes_stale_when_gap_is_seen_after_grill(self) -> None:
        self.write("a.json", [gap("one", last_seen=day(-5))])
        gaps.record_grade(self.dir, "one", "knows", now=NOW - timedelta(days=3))
        self.write("a.json", [gap("one", last_seen=day(-1))])
        [ranked] = gaps.ranked_gaps(self.dir, now=NOW)
        self.assertEqual(ranked.stale_reason, "seen-since-grill")

    def test_project_filter_and_limit(self) -> None:
        self.write(
            "a.json",
            [gap("a1", score=1.0), gap("a2", score=3.0), gap("b1", project="other")],
        )
        self.assertEqual(self.ranked_ids(project="builder"), ["a2", "a1"])
        self.assertEqual(self.ranked_ids(project="BUILDER", limit=1), ["a2"])
        with self.assertRaises(GapError):
            gaps.ranked_gaps(self.dir, limit=0, now=NOW)


class LoadingTests(GapTestCase):
    def test_items_load_across_files_and_skip_state(self) -> None:
        self.write("a.json", [gap("one")])
        self.write("b.json", [gap("two")])
        gaps.record_grade(self.dir, "one", "partial", now=NOW)
        self.assertEqual(sorted(i.gap_id for i in gaps.load_items(self.dir)), ["one", "two"])

    def test_duplicate_id_across_files_is_rejected(self) -> None:
        self.write("a.json", [gap("one")])
        self.write("b.json", [gap("one")])
        with self.assertRaisesRegex(GapError, "duplicate gap id 'one'"):
            gaps.load_items(self.dir)

    def test_invalid_items_are_rejected(self) -> None:
        cases = {
            "not a list": {"id": "x"},
            "bad id": [gap("-bad")],
            "missing question": [gap("x", grill_question="")],
            "bad score": [gap("x", score=-1)],
            "bad date": [gap("x", last_seen="yesterday")],
        }
        for label, payload in cases.items():
            with self.subTest(label=label):
                self.write("a.json", payload)
                with self.assertRaises(GapError):
                    gaps.load_items(self.dir)

    def test_missing_directory_is_an_error(self) -> None:
        with self.assertRaisesRegex(GapError, "gaps directory not found"):
            gaps.load_items(self.dir / "absent")


class StateMergeTests(GapTestCase):
    def test_grade_appends_history_and_preserves_other_entries(self) -> None:
        self.write("a.json", [gap("one"), gap("two")])
        gaps.record_grade(self.dir, "two", "knows", now=NOW - timedelta(days=1))
        gaps.record_grade(self.dir, "one", "wrong", note="mixed up retries", now=NOW - timedelta(hours=2))
        result = gaps.record_grade(self.dir, "one", "partial", now=NOW)
        self.assertEqual(result["previous_status"], "wrong")
        self.assertEqual(result["grill_count"], 2)

        raw = json.loads((self.dir / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(raw["schema"], gaps.STATE_SCHEMA)
        one = raw["items"]["one"]
        self.assertEqual(one["status"], "partial")
        self.assertEqual(one["last_grilled"], "2026-11-17T09:00:00Z")
        self.assertEqual([h["grade"] for h in one["history"]], ["wrong", "partial"])
        self.assertEqual(one["history"][0]["note"], "mixed up retries")
        self.assertNotIn("note", one["history"][1])
        self.assertEqual(raw["items"]["two"]["status"], "knows")
        self.assertEqual(list(self.dir.glob(".state-*")), [])

    def test_state_for_removed_items_is_kept_but_not_listed(self) -> None:
        self.write("a.json", [gap("one"), gap("gone")])
        gaps.record_grade(self.dir, "gone", "wrong", now=NOW)
        self.write("a.json", [gap("one")])
        self.assertEqual(self.ranked_ids(), ["one"])
        gaps.record_grade(self.dir, "one", "knows", now=NOW)
        raw = json.loads((self.dir / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(raw["items"]), ["gone", "one"])

    def test_unknown_id_and_bad_grade_write_nothing(self) -> None:
        self.write("a.json", [gap("one")])
        with self.assertRaisesRegex(GapError, "unknown gap id"):
            gaps.record_grade(self.dir, "nope", "knows", now=NOW)
        with self.assertRaisesRegex(GapError, "grade must be one of"):
            gaps.record_grade(self.dir, "one", "maybe", now=NOW)
        self.assertFalse((self.dir / "state.json").exists())

    def test_corrupt_state_fails_closed(self) -> None:
        self.write("a.json", [gap("one")])
        (self.dir / "state.json").write_text('{"items": {"one": {"status": "great"}}}')
        with self.assertRaisesRegex(GapError, "unknown status"):
            gaps.ranked_gaps(self.dir, now=NOW)


class GapCliTests(GapTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.workspace = self.dir.parent

    def run_cli(self, *args: str, expected: int = 0) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(
            [sys.executable, "-m", "virtuoso.cli", "--workspace", str(self.workspace), *args],
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

    def test_gaps_grill_and_grade_journey(self) -> None:
        today = datetime.now(timezone.utc)
        self.write(
            "builder.json",
            [
                gap("retry-policy", score=3.0, last_seen=day(0, base=today)),
                gap("cache-layer", score=1.0, last_seen=day(0, base=today)),
                gap("other-tool", project="other", last_seen=day(0, base=today)),
            ],
        )
        listed = json.loads(self.run_cli("gaps", "--project", "builder", "--json").stdout)
        self.assertEqual(listed["schema"], "virtuoso/gap-queue@0.1")
        self.assertEqual(listed["count"], 2)
        self.assertEqual([g["id"] for g in listed["gaps"]], ["retry-policy", "cache-layer"])
        self.assertEqual([g["rank"] for g in listed["gaps"]], [1, 2])

        text = self.run_cli("gaps", "--limit", "1").stdout
        self.assertIn("retry-policy", text)
        self.assertNotIn("cache-layer", text)

        prompt = json.loads(self.run_cli("grill", "retry-policy", "--json").stdout)
        self.assertEqual(prompt["grill_question"], "Explain what happens when retry-policy fails.")
        self.assertEqual(prompt["source_of_truth"], "docs/retry-policy.md")
        self.assertFalse((self.dir / "state.json").exists())
        human = self.run_cli("grill", "retry-policy").stdout
        self.assertIn("Question: Explain what happens when retry-policy fails.", human)
        self.assertIn("Source of truth: docs/retry-policy.md", human)

        graded = json.loads(
            self.run_cli(
                "grill", "retry-policy", "--grade", "knows", "--note", "clear", "--json"
            ).stdout
        )
        self.assertEqual(graded["schema"], "virtuoso/gap-grade@0.1")
        self.assertEqual(graded["status"], "knows")
        self.assertEqual(graded["previous_status"], "unverified")

        after = json.loads(self.run_cli("gaps", "--project", "builder", "--json").stdout)
        self.assertEqual([g["id"] for g in after["gaps"]], ["cache-layer", "retry-policy"])

    def test_gaps_dir_override_and_errors(self) -> None:
        other = self.workspace / "elsewhere"
        other.mkdir()
        (other / "x.json").write_text(json.dumps([gap("one")]), encoding="utf-8")
        listed = json.loads(self.run_cli("gaps", "--gaps-dir", str(other), "--json").stdout)
        self.assertEqual([g["id"] for g in listed["gaps"]], ["one"])

        missing = self.run_cli("grill", "nope", "--gaps-dir", str(other), expected=2)
        self.assertEqual(missing.stdout, "")
        self.assertIn("Error: unknown gap id: nope", missing.stderr)
        note_only = self.run_cli("grill", "one", "--note", "x", "--gaps-dir", str(other), expected=2)
        self.assertIn("--note needs --grade", note_only.stderr)
        bad_grade = self.run_cli("grill", "one", "--grade", "maybe", "--gaps-dir", str(other), expected=2)
        self.assertIn("invalid choice", bad_grade.stderr)
        self.assertFalse((other / "state.json").exists())

    def test_empty_gaps_directory_lists_nothing(self) -> None:
        self.assertIn("No understanding gaps found.", self.run_cli("gaps").stdout)
        listed = json.loads(self.run_cli("gaps", "--json").stdout)
        self.assertEqual(listed["count"], 0)


if __name__ == "__main__":
    unittest.main()
