"""Tests for virtuoso.search: FTS5 lexical + embedding kNN retrieval."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from virtuoso.search import (
    SearchError,
    embed_upsert,
    lexical_search,
    search_status,
    semantic_search,
)
from virtuoso.workspace import WorkspaceError, WorkspaceService


def _unit(vector: list[float]) -> list[float]:
    norm = sum(v * v for v in vector) ** 0.5 or 1.0
    return [round(v / norm, 6) for v in vector]


def _replace_item_text(
    service: WorkspaceService, item_id: str, old: str, new: str
) -> None:
    item = service.load_item(item_id)
    text = item.path.read_text(encoding="utf-8")
    changed = text.replace(old, new)
    if changed == text:
        raise AssertionError(f"test fixture text is missing: {old}")
    item.path.write_text(changed, encoding="utf-8")
    content_hash = hashlib.sha256(changed.encode("utf-8")).hexdigest()
    with service._connect() as db:
        db.execute(
            "UPDATE items SET content_hash = ? WHERE item_id = ?",
            (content_hash, item_id),
        )


class SearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve() / "learner"
        self.workspace = WorkspaceService.init(self.root)
        self.workspace.add_item(
            item_id="broadcasting",
            title="NumPy broadcasting rules",
            focus="ml",
            prompt="State the broadcasting rule for (3,1) and (4,).",
            answer="Compare shapes right to left; size-1 dims stretch. Result (3,4).",
        )
        self.workspace.add_item(
            item_id="logistic-boundary",
            title="Logistic regression boundary",
            focus="ml",
            prompt="Where does the decision boundary sit?",
            answer="Where w.x + b = 0, exactly where the sigmoid outputs 0.5.",
        )
        self.workspace.add_item(
            item_id="goroutines",
            title="Goroutines and channels",
            focus="go",
            prompt="What are goroutines?",
            answer="Lightweight threads multiplexed onto OS threads by the Go runtime.",
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_lexical_search_finds_matching_items_with_snippet(self) -> None:
        hits = lexical_search(self.workspace, "broadcasting shapes")
        self.assertTrue(hits)
        self.assertEqual(hits[0].item_id, "broadcasting")
        self.assertIn("broadcast", hits[0].snippet.lower())

    def test_lexical_search_excludes_non_matching(self) -> None:
        hits = lexical_search(self.workspace, "goroutine")
        self.assertEqual([hit.item_id for hit in hits], ["goroutines"])

    def test_lexical_search_empty_query_fails_closed(self) -> None:
        with self.assertRaisesRegex(SearchError, "non-empty"):
            lexical_search(self.workspace, "   ")

    def test_semantic_search_ranks_by_cosine(self) -> None:
        # Two axes: numpy-ish vs go-ish. Query close to the numpy axis.
        embed_upsert(
            self.workspace,
            item_id="broadcasting",
            model="test-embed",
            vector=_unit([0.9, 0.1]),
        )
        embed_upsert(
            self.workspace,
            item_id="goroutines",
            model="test-embed",
            vector=_unit([0.1, 0.9]),
        )
        embed_upsert(
            self.workspace,
            item_id="logistic-boundary",
            model="test-embed",
            vector=_unit([0.8, 0.2]),
        )
        hits = semantic_search(self.workspace, model="test-embed", query_vector=_unit([1.0, 0.0]), limit=2)
        self.assertEqual(hits[0].item_id, "broadcasting")
        self.assertEqual(hits[1].item_id, "logistic-boundary")
        self.assertGreater(hits[0].score, hits[1].score)
        self.assertLessEqual(hits[0].score, 1.0 + 1e-9)

    def test_semantic_search_unknown_model_is_empty_not_error(self) -> None:
        self.assertEqual(
            semantic_search(self.workspace, model="nope", query_vector=_unit([1.0]), limit=5),
            [],
        )

    def test_semantic_search_rejects_mismatched_dims(self) -> None:
        embed_upsert(
            self.workspace, item_id="broadcasting", model="d", vector=_unit([1.0, 0.0])
        )
        with self.assertRaisesRegex(SearchError, "dimension"):
            semantic_search(self.workspace, model="d", query_vector=_unit([1.0, 1.0, 1.0]), limit=5)

    def test_upsert_replaces_same_item_model_vector(self) -> None:
        embed_upsert(self.workspace, item_id="broadcasting", model="m", vector=_unit([1.0, 0.0]))
        embed_upsert(self.workspace, item_id="broadcasting", model="m", vector=_unit([0.0, 1.0]))
        hits = semantic_search(self.workspace, model="m", query_vector=_unit([0.0, 1.0]), limit=5)
        self.assertEqual(len(hits), 1)
        self.assertGreater(hits[0].score, 0.99)

    def test_lexical_index_tracks_new_items(self) -> None:
        self.workspace.add_item(
            item_id="fresh",
            title="Fresh item",
            focus="misc",
            prompt="Quantum tunneling barrier width?",
            answer="Narrower barriers tunnel more.",
        )
        hits = lexical_search(self.workspace, "tunneling")
        self.assertEqual([hit.item_id for hit in hits], ["fresh"])

    def test_lexical_index_rebuilds_when_active_identity_changes_at_same_count(
        self,
    ) -> None:
        self.assertEqual(
            [hit.item_id for hit in lexical_search(self.workspace, "goroutine")],
            ["goroutines"],
        )
        self.workspace.retire_item("goroutines")
        self.workspace.add_item(
            item_id="replacement",
            title="Replacement retrieval item",
            focus="retrieval",
            prompt="Explain quuxreplacement.",
            answer="Quuxreplacement identifies the new active item.",
        )

        self.assertEqual(lexical_search(self.workspace, "goroutine"), [])
        self.assertEqual(
            [hit.item_id for hit in lexical_search(self.workspace, "quuxreplacement")],
            ["replacement"],
        )

    def test_lexical_index_rebuilds_when_active_content_hash_changes(self) -> None:
        self.workspace.add_item(
            item_id="mutable-text",
            title="Mutable retrieval text",
            focus="retrieval",
            prompt="Explain cobaltmarker.",
            answer="cobaltmarker is the original indexed term.",
        )
        self.assertEqual(
            [hit.item_id for hit in lexical_search(self.workspace, "cobaltmarker")],
            ["mutable-text"],
        )

        _replace_item_text(
            self.workspace, "mutable-text", "cobaltmarker", "ambermarker"
        )

        self.assertEqual(lexical_search(self.workspace, "cobaltmarker"), [])
        self.assertEqual(
            [hit.item_id for hit in lexical_search(self.workspace, "ambermarker")],
            ["mutable-text"],
        )

    def test_search_status_fingerprint_tracks_active_content(self) -> None:
        lexical_search(self.workspace, "goroutine")
        before = search_status(self.workspace)

        _replace_item_text(
            self.workspace, "goroutines", "Lightweight threads", "Scheduled functions"
        )
        after = search_status(self.workspace)

        self.assertRegex(before["fingerprint"], r"^[0-9a-f]{64}$")
        self.assertRegex(after["fingerprint"], r"^[0-9a-f]{64}$")
        self.assertNotEqual(before["fingerprint"], after["fingerprint"])
        self.assertTrue(after["lexical_fresh"])

    def test_lexical_search_treats_ordinary_punctuation_as_plain_text(self) -> None:
        self.workspace.add_item(
            item_id="plain-syntax",
            title="C++ and goroutine-channel",
            focus="retrieval",
            prompt="Why is it's ordinary text with an unbalanced quote and a leading minus?",
            answer="NOT foo and item_id foo remain search terms.",
        )

        queries = (
            "it's",
            "C++",
            "goroutine-channel",
            '"unbalanced',
            "NOT foo",
            "-leading",
            "item_id:foo",
        )
        for query in queries:
            with self.subTest(query=query):
                self.assertIn(
                    "plain-syntax",
                    [hit.item_id for hit in lexical_search(self.workspace, query)],
                )

    def test_lexical_search_title_match_returns_title_snippet(self) -> None:
        self.workspace.add_item(
            item_id="title-only",
            title="Chiaroscuro calibration marker",
            focus="retrieval",
            prompt="Name the visual calibration concept.",
            answer="Use the unique title term.",
        )

        hits = lexical_search(self.workspace, "chiaroscuro")

        self.assertEqual(hits[0].item_id, "title-only")
        self.assertIn("chiaroscuro", hits[0].snippet.lower())

    def test_embedding_vector_rejects_non_numeric_values_as_search_error(self) -> None:
        with self.assertRaisesRegex(SearchError, "numbers"):
            embed_upsert(
                self.workspace,
                item_id="broadcasting",
                model="bad-values",
                vector=["not-a-number"],  # type: ignore[list-item]
            )

    def test_semantic_search_excludes_retired_items(self) -> None:
        embed_upsert(
            self.workspace,
            item_id="broadcasting",
            model="retirement-model",
            vector=[1.0, 0.0],
        )
        embed_upsert(
            self.workspace,
            item_id="goroutines",
            model="retirement-model",
            vector=[1.0, 0.0],
        )
        self.workspace.retire_item("goroutines")

        hits = semantic_search(
            self.workspace,
            model="retirement-model",
            query_vector=[1.0, 0.0],
        )

        self.assertEqual([hit.item_id for hit in hits], ["broadcasting"])

    def test_upsert_rejects_mixed_model_dimensions_without_corruption(self) -> None:
        embed_upsert(
            self.workspace,
            item_id="broadcasting",
            model="fixed-dimension-model",
            vector=[1.0, 0.0],
        )

        with self.assertRaisesRegex(SearchError, "dimension"):
            embed_upsert(
                self.workspace,
                item_id="goroutines",
                model="fixed-dimension-model",
                vector=[1.0, 0.0, 0.0],
            )

        hits = semantic_search(
            self.workspace,
            model="fixed-dimension-model",
            query_vector=[1.0, 0.0],
        )
        self.assertEqual([hit.item_id for hit in hits], ["broadcasting"])
        model = next(
            entry
            for entry in search_status(self.workspace)["embedding_models"]
            if entry["model"] == "fixed-dimension-model"
        )
        self.assertEqual(model["vectors"], 1)

    def test_upsert_unknown_item_still_fails_closed(self) -> None:
        with self.assertRaisesRegex(WorkspaceError, "no learning item"):
            embed_upsert(
                self.workspace,
                item_id="missing-item",
                model="model",
                vector=[1.0, 0.0],
            )

    def test_json_vector_roundtrip(self) -> None:
        vector = _unit([0.3, 0.4, 0.5])
        embed_upsert(self.workspace, item_id="broadcasting", model="rt", vector=vector)
        hits = semantic_search(self.workspace, model="rt", query_vector=vector, limit=1)
        self.assertGreater(hits[0].score, 0.999)


if __name__ == "__main__":
    unittest.main()
