/**
 * Parser test pack (NB-6): deck card splitting, `due` output section parsing,
 * and frontmatter key handling including the item_id fix (NB-1).
 *
 * Fixtures mirror the real generators:
 * - virtuoso.py cmd_due() output format (verified against live CLI 2026-08-22)
 * - 07-learning/nlp-llms-zong-2026-spaced-reps.md deck note structure
 * - virtuoso-learning-item frontmatter (snake_case keys, canonical)
 */
import { describe, expect, it } from "vitest";
import { deckChapterCards, fmKey, frontmatter, parseDueOutput } from "../parsing";

// ---------- deckChapterCards ----------

const DECK_FIXTURE = `## Deck

### Ch 1 — Introduction
- Q: What are the three broad historical phases of NLP methods? A: Rule/template-based rationalist methods (to mid-1980s) → corpus-based statistical methods (mid-1980s onward) → neural / pre-trained LLM era.
- Q: What platform hosts the book's code? A: Baidu AI Studio (Xinghe) Jupyter notebooks, PaddlePaddle stack, free GPU.

### Ch 2 — Basics of Neural Network
- Q: What does a perceptron compute? A: A weighted sum of inputs plus bias, passed through a threshold/activation; a linear binary classifier.
- Q: Why are non-linear activation functions necessary in deep networks? A: Without them, stacked layers collapse to a single linear transform — no added expressiveness.
- Q: One-sentence difference between CNN and RNN inductive bias? A: CNNs exploit local spatial patterns with shared filters; RNNs process sequences step-by-step carrying hidden state.

## Rep log

| Date | Chapters repped | Result summary |
|---|---|---|
`;

describe("deckChapterCards", () => {
	it("splits each chapter into its own card set", () => {
		const chapters = deckChapterCards(DECK_FIXTURE);
		expect(chapters.map((c) => c.ch)).toEqual(["1", "2"]);
		expect(chapters[0].title).toBe("Introduction");
		expect(chapters[1].title).toBe("Basics of Neural Network");
	});

	it("splits Q/A pairs on the inline ' A: ' separator", () => {
		const chapters = deckChapterCards(DECK_FIXTURE);
		expect(chapters[0].qa).toHaveLength(2);
		expect(chapters[0].qa[0].q).toBe("What are the three broad historical phases of NLP methods?");
		expect(chapters[0].qa[0].a).toBe(
			"Rule/template-based rationalist methods (to mid-1980s) → corpus-based statistical methods (mid-1980s onward) → neural / pre-trained LLM era.",
		);
	});

	it("keeps a question without an inline answer as q with empty a", () => {
		const raw = `### Ch 3 — Distributed Representation
- Q: Static vs dynamic word vectors? A: Static (Word2Vec/GloVe): one fixed vector per word. Dynamic (ELMo/BERT-style): representation depends on context.
- Q: Name the embedding that uses contexts to predict a word
- A: skip-gram (and CBOW inverts it).
`;
		const chapters = deckChapterCards(raw);
		expect(chapters[0].qa).toHaveLength(2);
		// The standalone "- A:" line fills the empty answer of the previous Q.
		expect(chapters[0].qa[1].a).toBe("skip-gram (and CBOW inverts it).");
	});

	it("stops the deck at the next top-level section (## Rep log)", () => {
		const chapters = deckChapterCards(DECK_FIXTURE);
		// The '## Rep log' table lines must not become Q/A cards.
		const allQa = chapters.flatMap((c) => c.qa);
		expect(allQa.some((qa) => qa.q.includes("Date | Chapters"))).toBe(false);
	});

	it("returns [] for a note with no deck sections", () => {
		expect(deckChapterCards("# Just a note\n\nbody text\n")).toEqual([]);
	});
});

// ---------- parseDueOutput ----------

const DUE_ALL_SECTIONS = `TRANSFER CHECKS DUE:
  [virtuoso-pilot-005] (due 2026-08-20) practice — capability: learning-science.transfer-checks
REVIEWS DUE:
  [virtuoso-pilot-002] (due 2026-08-19) note-review / recall
  [virtuoso-pilot-003] (due 2026-08-17) explain / prompt
BOOK DECK DUE (NLP & LLMs, Zong/Zhao/Ma):
  Ch 2 — Basics of Neural Network (ready to introduce; previous chapters repped)
  Ch 3 — Distributed Representation (due 2026-08-24, interval 3d)
AWAITING YOUR REVIEW: 2 proposed item(s) — virtuoso-pilot-004, virtuoso-pilot-007-firecrawl-developer-index
`;

describe("parseDueOutput", () => {
	it("extracts bracketed item ids from the REVIEWS DUE section only", () => {
		const out = parseDueOutput(DUE_ALL_SECTIONS);
		expect(out.itemsDue).toEqual(["virtuoso-pilot-002", "virtuoso-pilot-003"]);
	});

	it("extracts due chapter numbers from the BOOK DECK DUE section", () => {
		const out = parseDueOutput(DUE_ALL_SECTIONS);
		expect(out.deckChaptersDue).toEqual(["2", "3"]);
	});

	it("splits proposed ids from the AWAITING YOUR REVIEW trailer", () => {
		const out = parseDueOutput(DUE_ALL_SECTIONS);
		expect(out.proposed).toEqual(["virtuoso-pilot-004", "virtuoso-pilot-007-firecrawl-developer-index"]);
	});

	it("captures TRANSFER CHECKS DUE ids without polluting itemsDue", () => {
		const out = parseDueOutput(DUE_ALL_SECTIONS);
		expect(out.transfersDue).toEqual(["virtuoso-pilot-005"]);
		expect(out.itemsDue).not.toContain("virtuoso-pilot-005");
	});

	it("handles the live degenerate case: deck due, nothing else", () => {
		// Byte-format of the actual CLI output on 2026-08-22.
		const live = `BOOK DECK DUE (NLP & LLMs, Zong/Zhao/Ma):
  Ch 2 — Basics of Neural Network (ready to introduce; previous chapters repped)
AWAITING YOUR REVIEW: 4 proposed item(s) — virtuoso-pilot-003, virtuoso-pilot-004, virtuoso-pilot-005, virtuoso-pilot-007-firecrawl-developer-index
`;
		const out = parseDueOutput(live);
		expect(out.itemsDue).toEqual([]);
		expect(out.deckChaptersDue).toEqual(["2"]);
	});

	it("returns empty arrays for a 'Nothing due.' output", () => {
		const out = parseDueOutput("Nothing due. Next scheduled: 2026-08-25\n");
		expect(out.itemsDue).toEqual([]);
		expect(out.deckChaptersDue).toEqual([]);
		expect(out.transfersDue).toEqual([]);
		expect(out.proposed).toEqual([]);
	});
});

// ---------- frontmatter / fmKey ----------

const ITEM_FIXTURE = `---
schema: virtuoso-learning-item/0.1
item_id: virtuoso-pilot-003
item_type: practice
status: active
review_state: proposed
privacy: private-local
practice_mode: prompt
exercise_type: explain
created_date: 2026-08-16
---

# Explain the scheduling ownership boundary from memory

Body text.
`;

describe("frontmatter + fmKey (NB-1)", () => {
	it("parses the canonical snake_case keys of a real virtuoso-learning-item", () => {
		const fm = frontmatter(ITEM_FIXTURE);
		expect(fm["schema"]).toBe("virtuoso-learning-item/0.1");
		expect(fmKey(fm, "item_id")).toBe("virtuoso-pilot-003");
		expect(fmKey(fm, "review_state")).toBe("proposed");
		expect(fmKey(fm, "exercise_type")).toBe("explain");
		expect(fmKey(fm, "practice_mode")).toBe("prompt");
		expect(fmKey(fm, "created_date")).toBe("2026-08-16");
	});

	it("NB-1 regression: underscore key is canonical and must match directly", () => {
		const fm = frontmatter(ITEM_FIXTURE);
		// The old code read fm["item-id"] and fm["review-state"] — both absent
		// in real notes. The canonical reads must now succeed:
		expect(fm["item_id"]).toBe("virtuoso-pilot-003");
		expect(fm["review_state"]).toBe("proposed");
	});

	it("tolerates the legacy hyphenated spelling as a fallback alias", () => {
		const legacy = `---
schema: virtuoso-learning-item/0.1
item-id: virtuoso-pilot-legacy
review-state: proposed
---
body`;
		const fm = frontmatter(legacy);
		expect(fmKey(fm, "item_id")).toBe("virtuoso-pilot-legacy");
		expect(fmKey(fm, "review_state")).toBe("proposed");
	});

	it("prefers the canonical spelling when both are present", () => {
		const both = `---
item_id: canonical-id
item-id: legacy-id
---`;
		const fm = frontmatter(both);
		expect(fmKey(fm, "item_id")).toBe("canonical-id");
	});

	it("returns an empty map when there is no frontmatter block", () => {
		expect(frontmatter("just body text")).toEqual({});
		expect(fmKey({}, "item_id")).toBe("");
	});

	it("strips surrounding quotes from values", () => {
		const fm = frontmatter('---\ntitle: "Quoted Title"\nnote: \'single\'\n---');
		expect(fm["title"]).toBe("Quoted Title");
		expect(fm["note"]).toBe("single");
	});
});