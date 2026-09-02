import { describe, expect, it } from "vitest";
import type {
	ReviewAttemptRequest,
	ReviewAttemptResultPayload,
	ReviewItemPayload,
	ReviewQueuePayload,
	ReviewSkipRequest,
	ReviewSkipResultPayload,
} from "../contracts";
import {
	ReviewClientError,
	ReviewSessionController,
	type ReviewClient,
} from "../session";

const HASH = "a".repeat(64);

class FakeClient implements ReviewClient {
	recorded: ReviewAttemptRequest[] = [];
	skipped: ReviewSkipRequest[] = [];
	recordFailures = 0;
	skipFailures = 0;
	recordError: ReviewClientError | null = null;
	recordWait: Promise<void> | null = null;
	queueHash = HASH;
	itemHash = HASH;
	includeSecond = false;
	nextLoadFailures = 0;

	async due(): Promise<ReviewQueuePayload> {
		const items = [
			{
				item_id: "testing-effect",
				content_hash: this.queueHash,
				focus: "learning-science",
				project_ids: ["context-project"],
				selection_reason: "Selected a new item in deterministic item-id order.",
				status: "new" as const,
				due_at: null,
			},
		];
		if (this.includeSecond) {
			items.push({
				item_id: "second-item",
				content_hash: HASH,
				focus: "learning-science",
				project_ids: [],
				selection_reason: "Selected a new item in deterministic item-id order.",
				status: "new",
				due_at: null,
			});
		}
		return {
			schema: "virtuoso/review-queue@0.1",
			items,
		};
	}

	async load(itemId: string): Promise<ReviewItemPayload> {
		if (itemId === "second-item" && this.nextLoadFailures > 0) {
			this.nextLoadFailures -= 1;
			throw new ReviewClientError(
				"The CLI process exited while loading the next card.",
				"process-failure",
				"retry-submit",
			);
		}
		return {
			schema: "virtuoso/review-item@0.1",
			item: {
				item_id: itemId,
				title:
					itemId === "second-item" ? "Explain spaced practice" : "Explain the testing effect",
				focus: "learning-science",
				content_hash: itemId === "second-item" ? HASH : this.itemHash,
				prompt:
					itemId === "second-item"
						? "Why does spacing improve recall?"
						: "Why does retrieval improve recall?",
				answer: "Retrieval changes memory.",
				hint: "Compare retrieval with rereading.",
				follow_up: null,
				learning_context: "atomic-recall",
			},
		};
	}

	async record(request: ReviewAttemptRequest): Promise<ReviewAttemptResultPayload> {
		this.recorded.push(request);
		if (this.recordError !== null) {
			const error = this.recordError;
			this.recordError = null;
			throw error;
		}
		if (this.recordFailures > 0) {
			this.recordFailures -= 1;
			throw new ReviewClientError(
				"The CLI process exited before confirming the write.",
				"process-failure",
				"retry-submit",
			);
		}
		if (this.recordWait !== null) await this.recordWait;
		return {
			schema: "virtuoso/review-attempt-result@0.1",
			attempt: {
				event_id: `attempt-${request.submission_id}`,
				item_id: request.item_id,
				item_content_hash: request.item_content_hash,
				result: request.result,
				confidence: request.confidence,
				initial_latency_ms: 1250,
				administered: false,
				occurred_at: request.completed_at,
			},
			proposal: {
				proposal_id: "proposal-1",
				algorithm: "fsrs",
				algorithm_version: "6.3.2",
				due_at: "2026-09-03T12:00:05.000Z",
			},
		};
	}

	async skip(request: ReviewSkipRequest): Promise<ReviewSkipResultPayload> {
		this.skipped.push(request);
		if (this.skipFailures > 0) {
			this.skipFailures -= 1;
			throw new ReviewClientError(
				"The CLI process exited before confirming the skip.",
				"process-failure",
				"retry-submit",
			);
		}
		return {
			schema: "virtuoso/review-skip-result@0.1",
			skip: {
				event_id: `skip-${request.submission_id}`,
				item_id: request.item_id,
				item_content_hash: request.item_content_hash,
				occurred_at: request.occurred_at,
				surface: "obsidian-plugin",
			},
		};
	}
}

function scriptedClock(values: string[]): () => Date {
	const queue = values.map((value) => new Date(value));
	return () => {
		const next = queue.shift();
		if (!next) throw new Error("clock exhausted");
		return next;
	};
}

describe("ReviewSessionController", () => {
	it("runs prompt, typed response, retry, hint, reveal, grade, and completion", async () => {
		const client = new FakeClient();
		const controller = new ReviewSessionController(client, {
			now: scriptedClock([
				"2026-09-02T12:00:00.000Z",
				"2026-09-02T12:00:01.250Z",
				"2026-09-02T12:00:02.000Z",
				"2026-09-02T12:00:02.750Z",
				"2026-09-02T12:00:05.000Z",
			]),
			newSubmissionId: () => "0123456789abcdef0123456789abcdef",
		});

		await controller.start();
		expect(controller.state.phase).toBe("prompt");
		expect(controller.state.item?.answer).toBe("Retrieval changes memory.");
		expect(controller.state.context).toEqual({
			focus: "learning-science",
			projectIds: ["context-project"],
			selectionReason: "Selected a new item in deterministic item-id order.",
		});
		controller.setOpenNotes(true);
		controller.submitInitial("Retrieval strengthens later access paths.");
		expect(controller.state.phase).toBe("support");
		controller.beginRetry();
		expect(controller.state.phase).toBe("retry");
		controller.submitRetry("It changes memory through retrieval.");
		controller.useHint();
		expect(controller.state.hintUsed).toBe(true);
		controller.reveal();
		expect(controller.state.phase).toBe("grade");

		await controller.grade("demonstrated", 4);

		expect(controller.state.phase).toBe("complete");
		expect(client.recorded).toEqual([
			{
				schema: "virtuoso/review-attempt@0.1",
				submission_id: "0123456789abcdef0123456789abcdef",
				item_id: "testing-effect",
				item_content_hash: HASH,
				started_at: "2026-09-02T12:00:00.000Z",
				initial_answered_at: "2026-09-02T12:00:01.250Z",
				completed_at: "2026-09-02T12:00:05.000Z",
				initial_response: "Retrieval strengthens later access paths.",
				retry: {
					response: "It changes memory through retrieval.",
					latency_ms: 750,
				},
				hint_used: true,
				answer_revealed: true,
				result: "demonstrated",
				confidence: 4,
				open_notes: true,
			},
		]);
		expect(client.skipped).toEqual([]);
	});

	it("rejects an unaided retry after the hint is shown", async () => {
		const client = new FakeClient();
		const controller = new ReviewSessionController(client, {
			now: scriptedClock([
				"2026-09-02T12:00:00.000Z",
				"2026-09-02T12:00:01.000Z",
				"2026-09-02T12:00:02.000Z",
			]),
			newSubmissionId: () => "88888888888888888888888888888888",
		});
		await controller.start();
		controller.submitInitial("A measured response.");

		expect(controller.useHint()).toBe(true);
		expect(controller.beginRetry()).toBe(false);
		expect(controller.state.phase).toBe("support");
		expect(controller.state.retry).toBeNull();
		expect(controller.submitRetry("A response written after the hint.")).toBe(false);
	});

	it("keeps the card open after a CLI failure and retries the exact request", async () => {
		const client = new FakeClient();
		client.recordFailures = 1;
		const controller = new ReviewSessionController(client, {
			now: scriptedClock([
				"2026-09-02T12:00:00.000Z",
				"2026-09-02T12:00:01.000Z",
				"2026-09-02T12:00:02.000Z",
				"2026-09-02T12:00:03.000Z",
			]),
			newSubmissionId: () => "11111111111111111111111111111111",
		});
		await controller.start();
		controller.submitInitial("A measured response.");
		controller.reveal();

		expect(await controller.grade("partial", 3)).toBe(false);
		expect(controller.state.phase).toBe("grade");
		expect(controller.state.inFlight).toBe(false);
		expect(controller.state.error).toEqual({
			message: "The CLI process exited before confirming the write.",
			code: "process-failure",
			recovery: "retry-submit",
		});

		expect(await controller.grade("partial", 3)).toBe(false);
		expect(await controller.skip()).toBe(false);
		expect(await controller.retrySubmission()).toBe(true);
		expect(controller.state.phase).toBe("complete");
		expect(client.recorded).toHaveLength(2);
		expect(client.recorded[1]).toEqual(client.recorded[0]);
	});

	it("keeps a stale card open and reloads a fresh content snapshot", async () => {
		const client = new FakeClient();
		client.recordError = new ReviewClientError(
			"The item changed during review.",
			"stale-content",
			"reload-item",
		);
		const ids = [
			"22222222222222222222222222222222",
			"33333333333333333333333333333333",
		];
		const controller = new ReviewSessionController(client, {
			now: scriptedClock([
				"2026-09-02T12:00:00.000Z",
				"2026-09-02T12:00:01.000Z",
				"2026-09-02T12:00:02.000Z",
				"2026-09-02T12:00:03.000Z",
			]),
			newSubmissionId: () => ids.shift() ?? "",
		});
		await controller.start();
		controller.submitInitial("Response to the original prompt.");
		controller.reveal();
		expect(await controller.grade("partial", 2)).toBe(false);
		expect(controller.state.phase).toBe("grade");
		expect(controller.state.item?.content_hash).toBe(HASH);
		expect(controller.state.error?.recovery).toBe("reload-item");
		expect(await controller.grade("partial", 2)).toBe(false);
		expect(await controller.skip()).toBe(false);

		client.queueHash = "b".repeat(64);
		client.itemHash = "b".repeat(64);
		expect(await controller.reloadCurrent()).toBe(true);

		expect(controller.state.phase).toBe("prompt");
		expect(controller.state.item?.content_hash).toBe("b".repeat(64));
		expect(controller.state.initialResponse).toBe("");
		expect(controller.state.error).toBeNull();
	});

	it("retries the exact skip request through the recovery action", async () => {
		const client = new FakeClient();
		client.skipFailures = 1;
		const controller = new ReviewSessionController(client, {
			now: scriptedClock([
				"2026-09-02T12:00:00.000Z",
				"2026-09-02T12:00:01.000Z",
			]),
			newSubmissionId: () => "dddddddddddddddddddddddddddddddd",
		});
		await controller.start();

		expect(await controller.skip()).toBe(false);
		expect(controller.state.error?.recovery).toBe("retry-submit");
		expect(controller.submitInitial("A response outside recovery.")).toBe(false);
		expect(await controller.skip()).toBe(false);
		expect(await controller.retrySubmission()).toBe(true);

		expect(controller.state.phase).toBe("complete");
		expect(client.skipped).toHaveLength(2);
		expect(client.skipped[1]).toEqual(client.skipped[0]);
	});

	it("retries next-card loading after a recorded grade without another write", async () => {
		const client = new FakeClient();
		client.includeSecond = true;
		client.nextLoadFailures = 1;
		const ids = [
			"99999999999999999999999999999999",
			"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
		];
		const controller = new ReviewSessionController(client, {
			now: scriptedClock([
				"2026-09-02T12:00:00.000Z",
				"2026-09-02T12:00:01.000Z",
				"2026-09-02T12:00:02.000Z",
				"2026-09-02T12:00:03.000Z",
			]),
			newSubmissionId: () => ids.shift() ?? "",
		});
		await controller.start();
		controller.submitInitial("A measured response.");
		controller.reveal();

		expect(await controller.grade("partial", 3)).toBe(false);
		expect(client.recorded).toHaveLength(1);
		expect(controller.state.item?.item_id).toBe("testing-effect");
		expect(controller.state.position).toBe(1);
		expect(controller.state.error?.recovery).toBe("retry-next-card");
		expect(await controller.grade("partial", 3)).toBe(false);
		expect(await controller.skip()).toBe(false);

		expect(await controller.retryAdvance()).toBe(true);
		expect(client.recorded).toHaveLength(1);
		expect(client.skipped).toHaveLength(0);
		expect(controller.state.item?.item_id).toBe("second-item");
		expect(controller.state.position).toBe(2);
		expect(controller.state.phase).toBe("prompt");
		expect(controller.state.error).toBeNull();
	});

	it("keeps next-card recovery retryable after another load failure", async () => {
		const client = new FakeClient();
		client.includeSecond = true;
		client.nextLoadFailures = 2;
		const ids = [
			"eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
			"ffffffffffffffffffffffffffffffff",
		];
		const controller = new ReviewSessionController(client, {
			now: scriptedClock([
				"2026-09-02T12:00:00.000Z",
				"2026-09-02T12:00:01.000Z",
				"2026-09-02T12:00:02.000Z",
				"2026-09-02T12:00:03.000Z",
			]),
			newSubmissionId: () => ids.shift() ?? "",
		});
		await controller.start();
		controller.submitInitial("A measured response.");
		controller.reveal();

		expect(await controller.grade("partial", 3)).toBe(false);
		expect(await controller.retryAdvance()).toBe(false);
		expect(controller.state.error?.recovery).toBe("retry-next-card");
		expect(controller.state.position).toBe(1);
		expect(client.recorded).toHaveLength(1);

		expect(await controller.retryAdvance()).toBe(true);
		expect(controller.state.item?.item_id).toBe("second-item");
		expect(controller.state.position).toBe(2);
		expect(client.recorded).toHaveLength(1);
	});

	it("retries next-card loading after a recorded skip without another write", async () => {
		const client = new FakeClient();
		client.includeSecond = true;
		client.nextLoadFailures = 1;
		const ids = [
			"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
			"cccccccccccccccccccccccccccccccc",
		];
		const controller = new ReviewSessionController(client, {
			now: scriptedClock([
				"2026-09-02T12:00:00.000Z",
				"2026-09-02T12:00:01.000Z",
				"2026-09-02T12:00:02.000Z",
				"2026-09-02T12:00:03.000Z",
			]),
			newSubmissionId: () => ids.shift() ?? "",
		});
		await controller.start();

		expect(await controller.skip()).toBe(false);
		expect(client.skipped).toHaveLength(1);
		expect(controller.state.item?.item_id).toBe("testing-effect");
		expect(controller.state.position).toBe(1);
		expect(controller.state.error?.recovery).toBe("retry-next-card");
		expect(await controller.skip()).toBe(false);

		expect(await controller.retryAdvance()).toBe(true);
		expect(client.skipped).toHaveLength(1);
		expect(client.recorded).toHaveLength(0);
		expect(controller.state.item?.item_id).toBe("second-item");
		expect(controller.state.position).toBe(2);
		expect(controller.state.phase).toBe("prompt");
		expect(controller.state.error).toBeNull();
	});

	it("blocks repeated grading while in flight and after success", async () => {
		const client = new FakeClient();
		let release: () => void = () => undefined;
		client.recordWait = new Promise<void>((resolve) => {
			release = resolve;
		});
		const controller = new ReviewSessionController(client, {
			now: scriptedClock([
				"2026-09-02T12:00:00.000Z",
				"2026-09-02T12:00:01.000Z",
				"2026-09-02T12:00:02.000Z",
			]),
			newSubmissionId: () => "44444444444444444444444444444444",
		});
		await controller.start();
		controller.submitInitial("A measured response.");
		controller.reveal();

		const first = controller.grade("partial", 3);
		await Promise.resolve();
		expect(controller.state.inFlight).toBe(true);
		expect(await controller.grade("partial", 3)).toBe(false);
		expect(client.recorded).toHaveLength(1);

		release();
		expect(await first).toBe(true);
		expect(await controller.grade("partial", 3)).toBe(false);
		expect(client.recorded).toHaveLength(1);
	});

	it("records a hash-bound skip and advances without grading", async () => {
		const client = new FakeClient();
		const controller = new ReviewSessionController(client, {
			now: scriptedClock([
				"2026-09-02T12:00:00.000Z",
				"2026-09-02T12:00:01.000Z",
			]),
			newSubmissionId: () => "55555555555555555555555555555555",
		});
		await controller.start();

		expect(await controller.skip()).toBe(true);

		expect(controller.state.phase).toBe("complete");
		expect(client.recorded).toEqual([]);
		expect(client.skipped).toEqual([
			{
				schema: "virtuoso/review-skip@0.1",
				submission_id: "55555555555555555555555555555555",
				item_id: "testing-effect",
				item_content_hash: HASH,
				occurred_at: "2026-09-02T12:00:01.000Z",
				surface: "obsidian-plugin",
			},
		]);
	});

	it("recovers when item content changes between queue and load", async () => {
		const client = new FakeClient();
		client.itemHash = "b".repeat(64);
		const controller = new ReviewSessionController(client, {
			now: scriptedClock(["2026-09-02T12:00:00.000Z"]),
			newSubmissionId: () => "66666666666666666666666666666666",
		});

		await controller.start();
		expect(controller.state.item).toBeNull();
		expect(controller.state.error?.recovery).toBe("reload-item");

		client.queueHash = "b".repeat(64);
		expect(await controller.reloadCurrent()).toBe(true);
		expect(controller.state.phase).toBe("prompt");
		expect(controller.state.item?.content_hash).toBe("b".repeat(64));
	});

	it("advances only after the CLI confirms an earlier submission was recorded", async () => {
		const client = new FakeClient();
		client.recordError = new ReviewClientError(
			"This review submission was already recorded.",
			"already-recorded",
			"advance-card",
		);
		const controller = new ReviewSessionController(client, {
			now: scriptedClock([
				"2026-09-02T12:00:00.000Z",
				"2026-09-02T12:00:01.000Z",
				"2026-09-02T12:00:02.000Z",
			]),
			newSubmissionId: () => "77777777777777777777777777777777",
		});
		await controller.start();
		controller.submitInitial("A measured response.");
		controller.reveal();
		expect(await controller.grade("partial", 3)).toBe(false);
		expect(controller.state.phase).toBe("grade");
		expect(controller.state.error?.recovery).toBe("advance-card");
		expect(await controller.grade("partial", 3)).toBe(false);
		expect(await controller.skip()).toBe(false);
		expect(client.recorded).toHaveLength(1);

		expect(await controller.acknowledgeRecorded()).toBe(true);
		expect(controller.state.phase).toBe("complete");
	});
});
