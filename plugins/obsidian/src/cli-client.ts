import { spawn } from "child_process";
import {
	ContractError,
	parseReviewAttemptResult,
	parseReviewError,
	parseReviewItem,
	parseReviewQueue,
	parseReviewSkipResult,
	type ReviewAttemptRequest,
	type ReviewAttemptResultPayload,
	type ReviewItemPayload,
	type ReviewQueuePayload,
	type ReviewRecovery,
	type ReviewSkipRequest,
	type ReviewSkipResultPayload,
} from "./contracts";
import { ReviewClientError, type ReviewClient } from "./session";

export interface ProcessInvocation {
	executable: string;
	args: string[];
	stdin: string | null;
}

export interface ProcessResult {
	exitCode: number;
	stdout: string;
	stderr: string;
}

export interface ProcessRunner {
	run(invocation: ProcessInvocation): Promise<ProcessResult>;
}

const MAX_OUTPUT_BYTES = 1_000_000;

export class SpawnProcessRunner implements ProcessRunner {
	run(invocation: ProcessInvocation): Promise<ProcessResult> {
		return new Promise((resolve, reject) => {
			const processHandle = spawn(invocation.executable, invocation.args, {
				shell: false,
				timeout: 15_000,
				windowsHide: true,
			});
			let stdout = "";
			let stderr = "";
			let settled = false;
			const fail = (error: Error) => {
				if (settled) return;
				settled = true;
				reject(error);
			};
			const append = (current: string, chunk: unknown): string => {
				const next = current + String(chunk);
				if (Buffer.byteLength(next, "utf8") > MAX_OUTPUT_BYTES) {
					processHandle.kill();
					throw new Error("Virtuoso CLI output exceeded the one-megabyte limit");
				}
				return next;
			};
			processHandle.stdout.on("data", (chunk: unknown) => {
				try {
					stdout = append(stdout, chunk);
				} catch (error) {
					fail(error instanceof Error ? error : new Error(String(error)));
				}
			});
			processHandle.stderr.on("data", (chunk: unknown) => {
				try {
					stderr = append(stderr, chunk);
				} catch (error) {
					fail(error instanceof Error ? error : new Error(String(error)));
				}
			});
			processHandle.on("error", fail);
			processHandle.on("close", (code: number | null) => {
				if (settled) return;
				settled = true;
				resolve({ exitCode: code ?? -1, stdout, stderr });
			});
			if (invocation.stdin === null) processHandle.stdin.end();
			else processHandle.stdin.end(invocation.stdin, "utf8");
		});
	}
}

function expandHome(value: string): string {
	if (value === "~") return process.env.HOME ?? value;
	if (value.startsWith("~/")) return `${process.env.HOME ?? ""}/${value.slice(2)}`;
	return value;
}

export class VirtuosoCliClient implements ReviewClient {
	private readonly executable: string;
	private readonly workspace: string;

	constructor(
		executable: string,
		workspace: string,
		private readonly runner: ProcessRunner = new SpawnProcessRunner(),
	) {
		this.executable = expandHome(executable.trim());
		this.workspace = expandHome(workspace.trim());
	}

	async due(): Promise<ReviewQueuePayload> {
		return this.invoke(
			["review", "due", "--json"],
			null,
			parseReviewQueue,
			"check-settings",
		);
	}

	async load(itemId: string): Promise<ReviewItemPayload> {
		return this.invoke(
			["review", "load", "--item", itemId, "--json"],
			null,
			parseReviewItem,
			"check-settings",
		);
	}

	async record(request: ReviewAttemptRequest): Promise<ReviewAttemptResultPayload> {
		return this.invoke(
			["review", "record", "--json"],
			JSON.stringify(request),
			parseReviewAttemptResult,
			"retry-submit",
		);
	}

	async skip(request: ReviewSkipRequest): Promise<ReviewSkipResultPayload> {
		return this.invoke(
			["review", "skip", "--json"],
			JSON.stringify(request),
			parseReviewSkipResult,
			"retry-submit",
		);
	}

	private async invoke<T>(
		contractArgs: string[],
		stdin: string | null,
		parse: (value: unknown) => T,
		processRecovery: ReviewRecovery,
	): Promise<T> {
		if (!this.executable || !this.workspace) {
			throw new ReviewClientError(
				"Set both the Virtuoso executable and workspace path.",
				"setup-required",
				"check-settings",
			);
		}
		let result: ProcessResult;
		try {
			result = await this.runner.run({
				executable: this.executable,
				args: ["--workspace", this.workspace, ...contractArgs],
				stdin,
			});
		} catch (error) {
			throw new ReviewClientError(
				error instanceof Error ? error.message : String(error),
				"process-failure",
				processRecovery,
			);
		}
		if (result.exitCode !== 0) {
			try {
				const payload = parseReviewError(JSON.parse(result.stderr));
				throw new ReviewClientError(
					payload.error.message,
					payload.error.code,
					payload.error.recovery,
				);
			} catch (error) {
				if (error instanceof ReviewClientError) throw error;
				throw new ReviewClientError(
					result.stderr.trim() || `Virtuoso CLI exited with code ${result.exitCode}`,
					"process-failure",
					processRecovery,
				);
			}
		}
		try {
			return parse(JSON.parse(result.stdout));
		} catch (error) {
			if (error instanceof ContractError || error instanceof SyntaxError) {
				throw new ReviewClientError(
					error.message,
					"schema-failure",
					"check-settings",
				);
			}
			throw error;
		}
	}
}
