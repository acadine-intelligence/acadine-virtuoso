import { describe, expect, it } from "vitest";
import {
	ContractError,
	parseReviewAttemptResult,
	parseReviewError,
	parseReviewItem,
	parseReviewQueue,
	parseReviewSkipResult,
} from "../contracts";

const HASH = "a".repeat(64);

describe("review CLI contract validation", () => {
	it("accepts exact due/load payloads and rejects schema or field drift", () => {
		const queue = parseReviewQueue({
			schema: "virtuoso/review-queue@0.1",
			items: [
				{
					item_id: "testing-effect",
					content_hash: HASH,
					focus: "learning-science",
					project_ids: ["context-project"],
					selection_reason: "Selected a new item in deterministic item-id order.",
					status: "new",
					due_at: null,
				},
			],
		});
		expect(queue.items[0].item_id).toBe("testing-effect");
		expect(queue.items[0].focus).toBe("learning-science");
		expect(queue.items[0].project_ids).toEqual(["context-project"]);
		expect(queue.items[0].selection_reason).toBe(
			"Selected a new item in deterministic item-id order.",
		);

		const item = parseReviewItem({
			schema: "virtuoso/review-item@0.1",
			item: {
				item_id: "testing-effect",
				title: "Explain the testing effect",
				focus: "learning-science",
				content_hash: HASH,
				prompt: "Why does retrieval improve recall?",
				answer: "Retrieval changes memory.",
				hint: "Compare it with rereading.",
				follow_up: null,
				learning_context: "atomic-recall",
			},
		});
		expect(item.item.answer).toBe("Retrieval changes memory.");

		expect(() =>
			parseReviewQueue({
				schema: "virtuoso/review-queue@9.9",
				items: [],
			}),
		).toThrow(ContractError);
		expect(() =>
			parseReviewItem({
				schema: "virtuoso/review-item@0.1",
				item: {
					item_id: "testing-effect",
					title: "Title",
					focus: "focus",
					content_hash: HASH,
					prompt: "Prompt",
					answer: "Answer",
					hint: null,
					follow_up: null,
					learning_context: "atomic-recall",
					scheduler_state: {},
				},
			}),
		).toThrow(/fields/);
	});

	it("validates write results and typed recovery errors", () => {
		const attempt = parseReviewAttemptResult({
			schema: "virtuoso/review-attempt-result@0.1",
			attempt: {
				event_id: "attempt-0123456789abcdef0123456789abcdef",
				item_id: "testing-effect",
				item_content_hash: HASH,
				result: "partial",
				confidence: 3,
				initial_latency_ms: 1250,
				administered: false,
				occurred_at: "2026-09-02T12:00:05+00:00",
			},
			proposal: {
				proposal_id: "proposal-1",
				algorithm: "fsrs",
				algorithm_version: "6.3.2",
				due_at: "2026-09-03T12:00:05+00:00",
			},
		});
		expect(attempt.attempt.administered).toBe(false);

		const skip = parseReviewSkipResult({
			schema: "virtuoso/review-skip-result@0.1",
			skip: {
				event_id: "skip-0123456789abcdef0123456789abcdef",
				item_id: "testing-effect",
				item_content_hash: HASH,
				occurred_at: "2026-09-02T12:00:05+00:00",
				surface: "obsidian-plugin",
			},
		});
		expect(skip.skip.surface).toBe("obsidian-plugin");

		const error = parseReviewError({
			schema: "virtuoso/review-error@0.1",
			error: {
				code: "stale-content",
				message: "Reload the changed item.",
				recovery: "reload-item",
			},
		});
		expect(error.error.recovery).toBe("reload-item");
		expect(() =>
			parseReviewError({
				schema: "virtuoso/review-error@0.1",
				error: {
					code: "stale-content",
					message: "Reload the changed item.",
					recovery: "reload-item",
					details: "unexpected",
				},
			}),
		).toThrow(/fields/);
	});

	it("rejects unknown review error codes", () => {
		expect(() =>
			parseReviewError({
				schema: "virtuoso/review-error@0.1",
				error: {
					code: "unrelated-error",
					message: "An unrelated operation failed.",
					recovery: "advance-card",
				},
			}),
		).toThrow(/unknown error code/);
	});

	it("rejects a recovery action that does not match its error code", () => {
		expect(() =>
			parseReviewError({
				schema: "virtuoso/review-error@0.1",
				error: {
					code: "stale-content",
					message: "The item changed.",
					recovery: "advance-card",
				},
			}),
		).toThrow(/recovery action/);
	});
});
