/**
 * Parser test pack (NB-6): deck card splitting, `due` output section parsing,
 * and frontmatter key handling including the item_id fix (NB-1).
 *
 * Fixtures mirror the real generators:
 * - virtuoso.py cmd_due() output format (verified against live CLI 2026-08-22)
 * - deck note structure (`### Ch N — Title` sections with `- Q:`/`- A:` lines)
 * - virtuoso-learning-item frontmatter (snake_case keys, canonical)
 */
import { describe, expect, it } from "vitest";
import {
	cardContextRows,
	deckChapterCards,
	fmKey,
	frontmatter,
	isLearningItemFrontmatter,
	learningItemId,
	parseDueOutput,
	parseNextRationale,
	parsePracticeRationales,
	parseSchedulerRationales,
	parseTransferProjects,
} from "../parsing";

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

describe("parseSchedulerRationales", () => {
	it("uses the latest supplied scheduler rationale for each item", () => {
		expect(
			parseSchedulerRationales(
				JSON.stringify({
					proposals: [
						{ item_id: "item-a", rationale: "Earlier scheduling evidence." },
						{ item_id: "item-b", rationale: "Only scheduling evidence." },
						{ item_id: "item-a", rationale: "Latest scheduling evidence." },
						{
							item_id: "item-a",
							learning_context: "project-transfer",
							rationale: "Unrelated scheduling evidence.",
						},
					],
				}),
			),
		).toEqual({
			"item-a": "Latest scheduling evidence.",
			"item-b": "Only scheduling evidence.",
		});
	});

	it("treats missing or malformed optional CLI output as no rationale", () => {
		for (const stdout of ["", "{bad json", "{}", '{"proposals":null}']) {
			expect(parseSchedulerRationales(stdout)).toEqual({});
		}
	});

	it("rejects rationale records without safe product item ids", () => {
		expect(
			parseSchedulerRationales(
				JSON.stringify({
					proposals: [
						{ item_id: "__proto__", rationale: "Unsafe." },
						{ item_id: "/private/item", rationale: "Unsafe." },
						{ item_id: "safe-item", rationale: "Explicit evidence." },
					],
				}),
			),
		).toEqual({ "safe-item": "Explicit evidence." });
	});
});

describe("parseNextRationale", () => {
	it("preserves the CLI selection reason for the selected item", () => {
		expect(
			parseNextRationale(
				JSON.stringify({
					item_id: "fresh-item",
					focus: "agentic-engineering",
					rationale: "Selected a new item in deterministic item-id order.",
				}),
			),
		).toEqual({
			"fresh-item": "Selected a new item in deterministic item-id order.",
		});
	});
});

describe("parsePracticeRationales", () => {
	it("uses the exact selection reason with scheduler rationale as fallback", () => {
		expect(
			parsePracticeRationales(
				JSON.stringify({
					proposals: [
						{ item_id: "selected-item", rationale: "Scheduler evidence." },
						{ item_id: "other-item", rationale: "Other scheduler evidence." },
					],
				}),
				JSON.stringify({
					item_id: "selected-item",
					rationale: "Selected because it is the earliest due item.",
				}),
			),
		).toEqual({
			"selected-item": "Selected because it is the earliest due item.",
			"other-item": "Other scheduler evidence.",
		});
	});
});

describe("parseTransferProjects", () => {
	it("collects only explicit product project ids from linked transfer events", () => {
		expect(
			parseTransferProjects(
				JSON.stringify({
					events: [
						{ item_id: "item-a", project_id: "project-one" },
						{ item_id: "item-a", project_id: "project-one" },
						{ item_id: "item-a", project_id: "/Users/private/project" },
						{ item_id: "item-a", project_id: "project-two" },
						{ item_id: "item-b", project_id: "project-three" },
					],
				}),
			),
		).toEqual({
			"item-a": ["project-one", "project-two"],
			"item-b": ["project-three"],
		});
	});

	it("treats unavailable transfer output as no project linkage", () => {
		for (const stdout of ["", "{bad json", "{}", '{"events":null}']) {
			expect(parseTransferProjects(stdout)).toEqual({});
		}
	});

	it("rejects transfer records without safe product item ids", () => {
		expect(
			parseTransferProjects(
				JSON.stringify({
					events: [
						{ item_id: "__proto__", project_id: "project-one" },
						{ item_id: "/private/item", project_id: "project-one" },
						{ item_id: "safe-item", project_id: "project-one" },
					],
				}),
			),
		).toEqual({ "safe-item": ["project-one"] });
	});
});

describe("cardContextRows", () => {
	it("keeps explicit focus, project linkage, and the full why-now text", () => {
		const longRationale = `Latest scheduler evidence: ${"measured recall and retained support context. ".repeat(20)}`;
		expect(
			cardContextRows({
				focus: "agentic-engineering",
				projectId: "current-project",
				linkedProjectIds: ["current-project", "/private/note", "earlier-project"],
				whyNow: longRationale,
			}),
		).toEqual([
			{ label: "Focus", text: "agentic-engineering" },
			{ label: "Projects", text: "current-project, earlier-project" },
			{ label: "Why now", text: longRationale },
		]);

		expect(cardContextRows({ focus: "learning-science" })).toEqual([
			{ label: "Focus", text: "learning-science" },
		]);
	});
});

describe("core item context linkage", () => {
	it("joins CLI context to the same item through the public item id field", () => {
		const fm = frontmatter(`---
schema: virtuoso/item@0.1
id: "scheduler-boundary"
title: "Explain the scheduler boundary"
focus: "agentic-engineering"
project_id: "current-project"
---`);
		const id = learningItemId(fm);
		const rationales = parseSchedulerRationales(
			JSON.stringify({
				proposals: [{ item_id: id, rationale: "The latest proposal is due." }],
			}),
		);
		const projects = parseTransferProjects(
			JSON.stringify({
				events: [{ item_id: id, project_id: "transfer-project" }],
			}),
		);

		expect(id).toBe("scheduler-boundary");
		expect(
			cardContextRows({
				focus: fmKey(fm, "focus"),
				projectId: fmKey(fm, "project_id"),
				linkedProjectIds: projects[id],
				whyNow: rationales[id],
			}),
		).toEqual([
			{ label: "Focus", text: "agentic-engineering" },
			{ label: "Projects", text: "current-project, transfer-project" },
			{ label: "Why now", text: "The latest proposal is due." },
		]);
	});

	it("recognizes public core and legacy adapter learning-item schemas", () => {
		expect(isLearningItemFrontmatter({ schema: "virtuoso/item@0.1" })).toBe(true);
		expect(isLearningItemFrontmatter({ schema: "virtuoso-learning-item/0.1" })).toBe(true);
		expect(isLearningItemFrontmatter({ schema: "unrelated/item@0.1" })).toBe(false);
	});
});
