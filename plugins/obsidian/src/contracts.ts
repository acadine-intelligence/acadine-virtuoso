export class ContractError extends Error {
	readonly code = "schema-failure";
	readonly recovery = "check-settings";

	constructor(message: string) {
		super(message);
		this.name = "ContractError";
	}
}

export interface ReviewQueueItem {
	item_id: string;
	content_hash: string;
	focus: string;
	project_ids: string[];
	selection_reason: string;
	status: "due" | "new";
	due_at: string | null;
}

export interface ReviewQueuePayload {
	schema: "virtuoso/review-queue@0.1";
	items: ReviewQueueItem[];
}

export interface ReviewItemSnapshot {
	item_id: string;
	title: string;
	focus: string;
	content_hash: string;
	prompt: string;
	answer: string;
	hint: string | null;
	follow_up: string | null;
	learning_context: string;
}

export interface ReviewItemPayload {
	schema: "virtuoso/review-item@0.1";
	item: ReviewItemSnapshot;
}

export interface ReviewAttemptRequest {
	schema: "virtuoso/review-attempt@0.1";
	submission_id: string;
	item_id: string;
	item_content_hash: string;
	started_at: string;
	initial_answered_at: string;
	completed_at: string;
	initial_response: string;
	retry: { response: string; latency_ms: number } | null;
	hint_used: boolean;
	answer_revealed: true;
	result: "demonstrated" | "partial" | "not-demonstrated";
	confidence: number;
	open_notes: boolean;
}

export interface ReviewAttemptResultPayload {
	schema: "virtuoso/review-attempt-result@0.1";
	attempt: {
		event_id: string;
		item_id: string;
		item_content_hash: string;
		result: "demonstrated" | "partial" | "not-demonstrated";
		confidence: number;
		initial_latency_ms: number;
		administered: false;
		occurred_at: string;
	};
	proposal: {
		proposal_id: string;
		algorithm: string;
		algorithm_version: string;
		due_at: string;
	};
}

export interface ReviewSkipRequest {
	schema: "virtuoso/review-skip@0.1";
	submission_id: string;
	item_id: string;
	item_content_hash: string;
	occurred_at: string;
	surface: "obsidian-plugin";
}

export interface ReviewSkipResultPayload {
	schema: "virtuoso/review-skip-result@0.1";
	skip: {
		event_id: string;
		item_id: string;
		item_content_hash: string;
		occurred_at: string;
		surface: "obsidian-plugin";
	};
}

export type ReviewErrorCode =
	| "invalid-request"
	| "stale-content"
	| "record-failed"
	| "skip-failed"
	| "already-recorded"
	| "workspace-busy"
	| "workspace-error";

export type ReviewRecovery =
	| "check-contract"
	| "reload-item"
	| "retry-submit"
	| "advance-card"
	| "check-settings";

export interface ReviewErrorPayload {
	schema: "virtuoso/review-error@0.1";
	error: {
		code: ReviewErrorCode;
		message: string;
		recovery: ReviewRecovery;
	};
}

function object(value: unknown, label: string): Record<string, unknown> {
	if (typeof value !== "object" || value === null || Array.isArray(value)) {
		throw new ContractError(`${label} must be an object`);
	}
	return value as Record<string, unknown>;
}

function exactFields(
	value: Record<string, unknown>,
	fields: readonly string[],
	label: string,
): void {
	const actual = Object.keys(value).sort();
	const expected = [...fields].sort();
	if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index])) {
		throw new ContractError(`${label} fields do not match the contract`);
	}
}

function text(value: unknown, label: string): string {
	if (typeof value !== "string") throw new ContractError(`${label} must be a string`);
	return value;
}

function projectIds(value: unknown, label: string): string[] {
	if (!Array.isArray(value)) throw new ContractError(`${label} must be an array`);
	return value.map((entry, index) => {
		const result = text(entry, `${label} ${index}`);
		if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(result)) {
			throw new ContractError(`${label} ${index} must be a product id`);
		}
		return result;
	});
}

function nullableText(value: unknown, label: string): string | null {
	if (value === null) return null;
	return text(value, label);
}

function hash(value: unknown, label: string): string {
	const result = text(value, label);
	if (!/^[0-9a-f]{64}$/.test(result)) {
		throw new ContractError(`${label} must be a lowercase SHA-256 value`);
	}
	return result;
}

function integer(value: unknown, label: string, minimum: number, maximum?: number): number {
	if (
		typeof value !== "number" ||
		!Number.isInteger(value) ||
		value < minimum ||
		(maximum !== undefined && value > maximum)
	) {
		throw new ContractError(`${label} must be an integer in range`);
	}
	return value;
}

function timestamp(value: unknown, label: string): string {
	const result = text(value, label);
	if (
		Number.isNaN(Date.parse(result)) ||
		!/Z$|[+-]\d{2}:\d{2}$/.test(result)
	) {
		throw new ContractError(`${label} must be an ISO timestamp with a timezone`);
	}
	return result;
}

export function parseReviewQueue(value: unknown): ReviewQueuePayload {
	const root = object(value, "review queue response");
	exactFields(root, ["schema", "items"], "review queue response");
	if (root.schema !== "virtuoso/review-queue@0.1") {
		throw new ContractError(`unsupported review queue schema: ${String(root.schema)}`);
	}
	if (!Array.isArray(root.items)) {
		throw new ContractError("review queue items must be an array");
	}
	const items = root.items.map((entry, index): ReviewQueueItem => {
		const item = object(entry, `review queue item ${index}`);
		exactFields(
			item,
			[
				"item_id",
				"content_hash",
				"focus",
				"project_ids",
				"selection_reason",
				"status",
				"due_at",
			],
			`review queue item ${index}`,
		);
		const status = item.status;
		if (status !== "due" && status !== "new") {
			throw new ContractError(`review queue item ${index} has an invalid status`);
		}
		const dueAt = nullableText(item.due_at, `review queue item ${index} due_at`);
		if ((status === "new" && dueAt !== null) || (status === "due" && dueAt === null)) {
			throw new ContractError(`review queue item ${index} has an inconsistent due_at`);
		}
		if (dueAt !== null) timestamp(dueAt, `review queue item ${index} due_at`);
		return {
			item_id: text(item.item_id, `review queue item ${index} item_id`),
			content_hash: hash(item.content_hash, `review queue item ${index} content_hash`),
			focus: text(item.focus, `review queue item ${index} focus`),
			project_ids: projectIds(
				item.project_ids,
				`review queue item ${index} project_ids`,
			),
			selection_reason: text(
				item.selection_reason,
				`review queue item ${index} selection_reason`,
			),
			status,
			due_at: dueAt,
		};
	});
	return { schema: "virtuoso/review-queue@0.1", items };
}

export function parseReviewItem(value: unknown): ReviewItemPayload {
	const root = object(value, "review item response");
	exactFields(root, ["schema", "item"], "review item response");
	if (root.schema !== "virtuoso/review-item@0.1") {
		throw new ContractError(`unsupported review item schema: ${String(root.schema)}`);
	}
	const item = object(root.item, "review item");
	exactFields(
		item,
		[
			"item_id",
			"title",
			"focus",
			"content_hash",
			"prompt",
			"answer",
			"hint",
			"follow_up",
			"learning_context",
		],
		"review item",
	);
	return {
		schema: "virtuoso/review-item@0.1",
		item: {
			item_id: text(item.item_id, "review item item_id"),
			title: text(item.title, "review item title"),
			focus: text(item.focus, "review item focus"),
			content_hash: hash(item.content_hash, "review item content_hash"),
			prompt: text(item.prompt, "review item prompt"),
			answer: text(item.answer, "review item answer"),
			hint: nullableText(item.hint, "review item hint"),
			follow_up: nullableText(item.follow_up, "review item follow_up"),
			learning_context: text(item.learning_context, "review item learning_context"),
		},
	};
}

export function parseReviewAttemptResult(value: unknown): ReviewAttemptResultPayload {
	const root = object(value, "review attempt result");
	exactFields(root, ["schema", "attempt", "proposal"], "review attempt result");
	if (root.schema !== "virtuoso/review-attempt-result@0.1") {
		throw new ContractError(`unsupported review attempt result schema: ${String(root.schema)}`);
	}
	const attempt = object(root.attempt, "review attempt result attempt");
	exactFields(
		attempt,
		[
			"event_id",
			"item_id",
			"item_content_hash",
			"result",
			"confidence",
			"initial_latency_ms",
			"administered",
			"occurred_at",
		],
		"review attempt result attempt",
	);
	if (
		attempt.result !== "demonstrated" &&
		attempt.result !== "partial" &&
		attempt.result !== "not-demonstrated"
	) {
		throw new ContractError("review attempt result has an invalid result");
	}
	if (attempt.administered !== false) {
		throw new ContractError("review attempt result must identify a measured direct attempt");
	}
	const proposal = object(root.proposal, "review attempt result proposal");
	exactFields(
		proposal,
		["proposal_id", "algorithm", "algorithm_version", "due_at"],
		"review attempt result proposal",
	);
	return {
		schema: "virtuoso/review-attempt-result@0.1",
		attempt: {
			event_id: text(attempt.event_id, "review attempt event_id"),
			item_id: text(attempt.item_id, "review attempt item_id"),
			item_content_hash: hash(
				attempt.item_content_hash,
				"review attempt item_content_hash",
			),
			result: attempt.result,
			confidence: integer(attempt.confidence, "review attempt confidence", 1, 5),
			initial_latency_ms: integer(
				attempt.initial_latency_ms,
				"review attempt initial_latency_ms",
				0,
			),
			administered: false,
			occurred_at: timestamp(attempt.occurred_at, "review attempt occurred_at"),
		},
		proposal: {
			proposal_id: text(proposal.proposal_id, "review proposal proposal_id"),
			algorithm: text(proposal.algorithm, "review proposal algorithm"),
			algorithm_version: text(
				proposal.algorithm_version,
				"review proposal algorithm_version",
			),
			due_at: timestamp(proposal.due_at, "review proposal due_at"),
		},
	};
}

export function parseReviewSkipResult(value: unknown): ReviewSkipResultPayload {
	const root = object(value, "review skip result");
	exactFields(root, ["schema", "skip"], "review skip result");
	if (root.schema !== "virtuoso/review-skip-result@0.1") {
		throw new ContractError(`unsupported review skip result schema: ${String(root.schema)}`);
	}
	const skip = object(root.skip, "review skip result skip");
	exactFields(
		skip,
		["event_id", "item_id", "item_content_hash", "occurred_at", "surface"],
		"review skip result skip",
	);
	if (skip.surface !== "obsidian-plugin") {
		throw new ContractError("review skip result has an invalid surface");
	}
	return {
		schema: "virtuoso/review-skip-result@0.1",
		skip: {
			event_id: text(skip.event_id, "review skip event_id"),
			item_id: text(skip.item_id, "review skip item_id"),
			item_content_hash: hash(skip.item_content_hash, "review skip item_content_hash"),
			occurred_at: timestamp(skip.occurred_at, "review skip occurred_at"),
			surface: "obsidian-plugin",
		},
	};
}

const REVIEW_RECOVERY_BY_CODE: Record<ReviewErrorCode, ReviewRecovery> = {
	"invalid-request": "check-contract",
	"stale-content": "reload-item",
	"record-failed": "retry-submit",
	"skip-failed": "retry-submit",
	"already-recorded": "advance-card",
	"workspace-busy": "retry-submit",
	"workspace-error": "check-settings",
};

export function parseReviewError(value: unknown): ReviewErrorPayload {
	const root = object(value, "review error");
	exactFields(root, ["schema", "error"], "review error");
	if (root.schema !== "virtuoso/review-error@0.1") {
		throw new ContractError(`unsupported review error schema: ${String(root.schema)}`);
	}
	const error = object(root.error, "review error body");
	exactFields(error, ["code", "message", "recovery"], "review error body");
	const code = text(error.code, "review error code");
	if (!Object.prototype.hasOwnProperty.call(REVIEW_RECOVERY_BY_CODE, code)) {
		throw new ContractError(`review error has an unknown error code: ${code}`);
	}
	const typedCode = code as ReviewErrorCode;
	const expectedRecovery = REVIEW_RECOVERY_BY_CODE[typedCode];
	if (error.recovery !== expectedRecovery) {
		throw new ContractError(`review error recovery action does not match code: ${typedCode}`);
	}
	return {
		schema: "virtuoso/review-error@0.1",
		error: {
			code: typedCode,
			message: text(error.message, "review error message"),
			recovery: expectedRecovery,
		},
	};
}
