import { describe, expect, it } from "vitest";
import type { ReviewAttemptRequest, ReviewSkipRequest } from "../contracts";
import {
	ProcessRunnerError,
	SpawnProcessRunner,
	VirtuosoCliClient,
	type ProcessInvocation,
	type ProcessResult,
	type ProcessRunner,
} from "../cli-client";

const HASH = "a".repeat(64);

class FakeRunner implements ProcessRunner {
	calls: ProcessInvocation[] = [];
	results: ProcessResult[] = [];
	error: Error | null = null;

	async run(invocation: ProcessInvocation): Promise<ProcessResult> {
		this.calls.push(invocation);
		if (this.error !== null) throw this.error;
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
							focus: "learning-science",
							project_ids: ["context-project"],
							selection_reason: "Selected a new item in deterministic item-id order.",
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

	it("checks the installed CLI version and workspace doctor status", async () => {
		const runner = new FakeRunner();
		runner.results.push(
			{
				exitCode: 0,
				stdout: "0.1.0.dev0\n",
				stderr: "",
			},
			{
				exitCode: 0,
				stdout: JSON.stringify({
					status: "healthy",
					workspace_schema: "virtuoso/workspace@0.1",
					database: "ok",
					items: 2,
				}),
				stderr: "",
			},
		);
		const client = new VirtuosoCliClient(
			"/usr/local/bin/virtuoso",
			"/tmp/virtuoso-workspace",
			runner,
		);

		await expect(client.checkSetup()).resolves.toEqual({
			version: "0.1.0.dev0",
			workspaceStatus: "healthy",
			workspaceSchema: "virtuoso/workspace@0.1",
			database: "ok",
		});
		expect(runner.calls).toEqual([
			{
				executable: "/usr/local/bin/virtuoso",
				args: ["--version"],
				stdin: null,
			},
			{
				executable: "/usr/local/bin/virtuoso",
				args: ["--workspace", "/tmp/virtuoso-workspace", "doctor", "--json"],
				stdin: null,
			},
		]);
	});

	it("fails the setup check on an unsupported workspace schema", async () => {
		const runner = new FakeRunner();
		runner.results.push(
			{ exitCode: 0, stdout: "0.1.0.dev0\n", stderr: "" },
			{
				exitCode: 0,
				stdout: JSON.stringify({
					status: "healthy",
					workspace_schema: "virtuoso/workspace@9.9",
					database: "ok",
				}),
				stderr: "",
			},
		);
		const client = new VirtuosoCliClient(
			"/usr/local/bin/virtuoso",
			"/tmp/virtuoso-workspace",
			runner,
		);

		await expect(client.checkSetup()).rejects.toMatchObject({
			code: "schema-failure",
			recovery: "check-settings",
		});
	});

	it("reports a CLI timeout separately", async () => {
		const runner = new SpawnProcessRunner(20);

		await expect(
			runner.run({
				executable: process.execPath,
				args: ["-e", "setTimeout(() => undefined, 1000)"],
				stdin: null,
			}),
		).rejects.toMatchObject({
			kind: "timeout",
			message: "Virtuoso CLI timed out after 20 ms",
		});
	});

	it("reports a CLI spawn failure separately", async () => {
		const runner = new SpawnProcessRunner(1000);

		await expect(
			runner.run({
				executable: "/path/that-does-not-exist/virtuoso",
				args: [],
				stdin: null,
			}),
		).rejects.toMatchObject({
			kind: "spawn",
		});
	});

	it("maps a runner timeout to a typed review error", async () => {
		const runner = new FakeRunner();
		runner.error = new ProcessRunnerError("Virtuoso CLI timed out after 20 ms", "timeout");
		const client = new VirtuosoCliClient(
			"/usr/local/bin/virtuoso",
			"/tmp/virtuoso-workspace",
			runner,
		);

		await expect(client.record(attemptRequest)).rejects.toMatchObject({
			code: "process-timeout",
			message: "Virtuoso CLI timed out after 20 ms",
			recovery: "retry-submit",
		});
	});

	it("maps a runner spawn failure to a typed review error", async () => {
		const runner = new FakeRunner();
		runner.error = new ProcessRunnerError("Virtuoso CLI could not start: ENOENT", "spawn");
		const client = new VirtuosoCliClient(
			"/usr/local/bin/virtuoso",
			"/tmp/virtuoso-workspace",
			runner,
		);

		await expect(client.due()).rejects.toMatchObject({
			code: "process-spawn-failed",
			message: "Virtuoso CLI could not start: ENOENT",
			recovery: "check-settings",
		});
	});

	it("preserves stderr from an untyped nonzero CLI exit", async () => {
		const runner = new FakeRunner();
		runner.results.push({
			exitCode: 9,
			stdout: "",
			stderr: "workspace database is locked",
		});
		const client = new VirtuosoCliClient(
			"/usr/local/bin/virtuoso",
			"/tmp/virtuoso-workspace",
			runner,
		);

		await expect(client.record(attemptRequest)).rejects.toMatchObject({
			code: "process-exit",
			message: "workspace database is locked",
			recovery: "retry-submit",
		});
	});
});
