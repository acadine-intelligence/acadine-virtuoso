import { describe, expect, it } from "vitest";
import type { ReviewAttemptRequest, ReviewSkipRequest } from "../contracts";
import {
	VirtuosoCliClient,
	type ProcessInvocation,
	type ProcessResult,
	type ProcessRunner,
} from "../cli-client";

const HASH = "a".repeat(64);

class FakeRunner implements ProcessRunner {
	calls: ProcessInvocation[] = [];
	results: ProcessResult[] = [];

	async run(invocation: ProcessInvocation): Promise<ProcessResult> {
		this.calls.push(invocation);
		const result = this.results.shift();
		if (!result) throw new Error("runner result exhausted");
		return result;
	}
}

const attemptRequest: ReviewAttemptRequest = {
	schema: "virtuoso/review-attempt@0.1",
	submission_id: "0123456789abcdef0123456789abcdef",
	item_id: "testing-effect",
	item_content_hash: HASH,
	started_at: "2026-09-02T12:00:00.000Z",
	initial_answered_at: "2026-09-02T12:00:01.000Z",
	completed_at: "2026-09-02T12:00:02.000Z",
	initial_response: "A measured response.",
	retry: null,
	hint_used: false,
	answer_revealed: true,
	result: "partial",
	confidence: 3,
	open_notes: false,
};

const skipRequest: ReviewSkipRequest = {
	schema: "virtuoso/review-skip@0.1",
	submission_id: "11111111111111111111111111111111",
	item_id: "testing-effect",
	item_content_hash: HASH,
	occurred_at: "2026-09-02T12:00:03.000Z",
	surface: "obsidian-plugin",
};

describe("VirtuosoCliClient", () => {
	it("uses the configured executable and workspace for every versioned contract", async () => {
		const runner = new FakeRunner();
		runner.results.push(
			{
				exitCode: 0,
				stdout: JSON.stringify({
					schema: "virtuoso/review-queue@0.1",
					items: [
						{
							item_id: "testing-effect",
							content_hash: HASH,
							status: "new",
							due_at: null,
						},
					],
				}),
				stderr: "",
			},
			{
				exitCode: 0,
				stdout: JSON.stringify({
					schema: "virtuoso/review-item@0.1",
					item: {
						item_id: "testing-effect",
						title: "Explain the testing effect",
						focus: "learning-science",
						content_hash: HASH,
						prompt: "Why does retrieval improve recall?",
						answer: "Retrieval changes memory.",
						hint: null,
						follow_up: null,
						learning_context: "atomic-recall",
					},
				}),
				stderr: "",
			},
			{
				exitCode: 0,
				stdout: JSON.stringify({
					schema: "virtuoso/review-attempt-result@0.1",
					attempt: {
						event_id: `attempt-${attemptRequest.submission_id}`,
						item_id: "testing-effect",
						item_content_hash: HASH,
						result: "partial",
						confidence: 3,
						initial_latency_ms: 1000,
						administered: false,
						occurred_at: attemptRequest.completed_at,
					},
					proposal: {
						proposal_id: "proposal-1",
						algorithm: "fsrs",
						algorithm_version: "6.3.2",
						due_at: "2026-09-03T12:00:02.000Z",
					},
				}),
				stderr: "",
			},
			{
				exitCode: 0,
				stdout: JSON.stringify({
					schema: "virtuoso/review-skip-result@0.1",
					skip: {
						event_id: `skip-${skipRequest.submission_id}`,
						item_id: "testing-effect",
						item_content_hash: HASH,
						occurred_at: skipRequest.occurred_at,
						surface: "obsidian-plugin",
					},
				}),
				stderr: "",
			},
		);
		const client = new VirtuosoCliClient(
			"/usr/local/bin/virtuoso",
			"/tmp/virtuoso-workspace",
			runner,
		);

		expect((await client.due()).items).toHaveLength(1);
		expect((await client.load("testing-effect")).item.item_id).toBe("testing-effect");
		expect((await client.record(attemptRequest)).attempt.administered).toBe(false);
		expect((await client.skip(skipRequest)).skip.surface).toBe("obsidian-plugin");

		expect(runner.calls).toEqual([
			{
				executable: "/usr/local/bin/virtuoso",
				args: ["--workspace", "/tmp/virtuoso-workspace", "review", "due", "--json"],
				stdin: null,
			},
			{
				executable: "/usr/local/bin/virtuoso",
				args: [
					"--workspace",
					"/tmp/virtuoso-workspace",
					"review",
					"load",
					"--item",
					"testing-effect",
					"--json",
				],
				stdin: null,
			},
			{
				executable: "/usr/local/bin/virtuoso",
				args: ["--workspace", "/tmp/virtuoso-workspace", "review", "record", "--json"],
				stdin: JSON.stringify(attemptRequest),
			},
			{
				executable: "/usr/local/bin/virtuoso",
				args: ["--workspace", "/tmp/virtuoso-workspace", "review", "skip", "--json"],
				stdin: JSON.stringify(skipRequest),
			},
		]);
	});
});
