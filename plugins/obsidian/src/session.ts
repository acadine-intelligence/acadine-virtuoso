import type {
	ReviewAttemptRequest,
	ReviewAttemptResultPayload,
	ReviewItemPayload,
	ReviewItemSnapshot,
	ReviewQueueItem,
	ReviewQueuePayload,
	ReviewRecovery,
	ReviewSkipRequest,
	ReviewSkipResultPayload,
} from "./contracts";

export interface ReviewClient {
	due(): Promise<ReviewQueuePayload>;
	load(itemId: string): Promise<ReviewItemPayload>;
	record(request: ReviewAttemptRequest): Promise<ReviewAttemptResultPayload>;
	skip(request: ReviewSkipRequest): Promise<ReviewSkipResultPayload>;
}

export class ReviewClientError extends Error {
	constructor(
		message: string,
		readonly code: string,
		readonly recovery: ReviewRecovery,
	) {
		super(message);
		this.name = "ReviewClientError";
	}
}

export type ReviewPhase =
	| "idle"
	| "loading"
	| "empty"
	| "prompt"
	| "support"
	| "retry"
	| "grade"
	| "complete";

export interface ReviewSessionError {
	message: string;
	code: string;
	recovery: ReviewRecovery | "retry-next-card";
}

export interface ReviewCardContext {
	focus: string;
	projectIds: string[];
	selectionReason: string;
}

export interface ReviewSessionState {
	phase: ReviewPhase;
	item: ReviewItemSnapshot | null;
	context: ReviewCardContext | null;
	position: number;
	total: number;
	initialResponse: string;
	retry: { response: string; latency_ms: number } | null;
	hintUsed: boolean;
	openNotes: boolean;
	inFlight: boolean;
	error: ReviewSessionError | null;
	lastResult: ReviewAttemptResultPayload | ReviewSkipResultPayload | null;
}

interface ReviewSessionOptions {
	now?: () => Date;
	newSubmissionId?: () => string;
}

export class ReviewSessionController {
	readonly state: ReviewSessionState = {
		phase: "idle",
		item: null,
		context: null,
		position: 0,
		total: 0,
		initialResponse: "",
		retry: null,
		hintUsed: false,
		openNotes: false,
		inFlight: false,
		error: null,
		lastResult: null,
	};

	private readonly now: () => Date;
	private readonly newSubmissionId: () => string;
	private queue: ReviewQueueItem[] = [];
	private index = 0;
	private startedAt: Date | null = null;
	private initialAnsweredAt: Date | null = null;
	private retryStartedAt: Date | null = null;
	private submissionId = "";
	private pendingAttempt: ReviewAttemptRequest | null = null;
	private pendingSkip: ReviewSkipRequest | null = null;
	private advancePending = false;

	constructor(
		private readonly client: ReviewClient,
		options: ReviewSessionOptions = {},
	) {
		this.now = options.now ?? (() => new Date());
		this.newSubmissionId =
			options.newSubmissionId ??
			(() => globalThis.crypto.randomUUID().replace(/-/g, ""));
	}

	async start(): Promise<void> {
		this.state.phase = "loading";
		this.state.error = null;
		this.state.context = null;
		this.advancePending = false;
		try {
			const queue = await this.client.due();
			this.queue = queue.items;
			this.index = 0;
			this.state.total = this.queue.length;
			if (this.queue.length === 0) {
				this.state.phase = "empty";
				return;
			}
			await this.loadCurrent();
		} catch (error) {
			this.fail(error);
		}
	}

	setOpenNotes(open: boolean): void {
		if (this.state.item !== null && !this.state.inFlight && this.state.error === null) {
			this.state.openNotes = open;
		}
	}

	submitInitial(response: string): boolean {
		if (this.state.phase !== "prompt" || this.state.inFlight || this.state.error !== null) {
			return false;
		}
		this.state.initialResponse = response;
		this.initialAnsweredAt = this.now();
		this.state.phase = "support";
		return true;
	}

	beginRetry(): boolean {
		if (
			this.state.phase !== "support" ||
			this.state.retry !== null ||
			this.state.hintUsed ||
			this.state.error !== null ||
			this.state.inFlight
		) {
			return false;
		}
		this.retryStartedAt = this.now();
		this.state.phase = "retry";
		return true;
	}

	submitRetry(response: string): boolean {
		if (
			this.state.phase !== "retry" ||
			this.retryStartedAt === null ||
			this.state.error !== null ||
			this.state.inFlight
		) {
			return false;
		}
		const completed = this.now();
		this.state.retry = {
			response,
			latency_ms: Math.max(0, completed.getTime() - this.retryStartedAt.getTime()),
		};
		this.retryStartedAt = null;
		this.state.phase = "support";
		return true;
	}

	useHint(): boolean {
		if (
			this.state.phase !== "support" ||
			this.state.item?.hint === null ||
			this.state.hintUsed ||
			this.state.error !== null ||
			this.state.inFlight
		) {
			return false;
		}
		this.state.hintUsed = true;
		return true;
	}

	reveal(): boolean {
		if (this.state.phase !== "support" || this.state.inFlight || this.state.error !== null) {
			return false;
		}
		this.state.phase = "grade";
		return true;
	}

	async reloadCurrent(): Promise<boolean> {
		if (this.state.inFlight || this.advancePending) return false;
		const currentId = this.state.item?.item_id ?? this.queue[this.index]?.item_id;
		if (!currentId) return false;
		this.state.inFlight = true;
		try {
			const queue = await this.client.due();
			const index = queue.items.findIndex((item) => item.item_id === currentId);
			if (index < 0) {
				throw new ReviewClientError(
					"The current item is no longer in the due or new queue.",
					"item-unavailable",
					"check-settings",
				);
			}
			this.queue = queue.items;
			this.state.total = this.queue.length;
			await this.loadAt(index);
			return true;
		} catch (error) {
			this.fail(error);
			return false;
		} finally {
			this.state.inFlight = false;
		}
	}

	async acknowledgeRecorded(): Promise<boolean> {
		if (
			this.state.inFlight ||
			this.state.item === null ||
			this.state.error?.recovery !== "advance-card"
		) {
			return false;
		}
		this.state.inFlight = true;
		this.state.error = null;
		this.pendingAttempt = null;
		this.pendingSkip = null;
		this.advancePending = true;
		try {
			await this.advance();
			return true;
		} catch (error) {
			this.failAdvance(error);
			return false;
		} finally {
			this.state.inFlight = false;
		}
	}

	async skip(): Promise<boolean> {
		if (
			this.state.item === null ||
			this.state.inFlight ||
			this.state.phase === "idle" ||
			this.state.phase === "loading" ||
			this.state.phase === "empty" ||
			this.state.phase === "complete" ||
			this.state.error !== null ||
			this.advancePending ||
			this.pendingAttempt !== null
		) {
			return false;
		}
		if (this.pendingSkip === null) {
			this.pendingSkip = {
				schema: "virtuoso/review-skip@0.1",
				submission_id: this.submissionId,
				item_id: this.state.item.item_id,
				item_content_hash: this.state.item.content_hash,
				occurred_at: this.now().toISOString(),
				surface: "obsidian-plugin",
			};
		}
		this.state.inFlight = true;
		this.state.error = null;
		try {
			this.state.lastResult = await this.client.skip(this.pendingSkip);
			this.pendingSkip = null;
			this.advancePending = true;
			try {
				await this.advance();
				return true;
			} catch (error) {
				this.failAdvance(error);
				return false;
			}
		} catch (error) {
			this.fail(error);
			return false;
		} finally {
			this.state.inFlight = false;
		}
	}

	async grade(
		result: "demonstrated" | "partial" | "not-demonstrated",
		confidence: number,
	): Promise<boolean> {
		if (
			this.state.phase !== "grade" ||
			this.state.inFlight ||
			this.state.item === null ||
			this.startedAt === null ||
			this.initialAnsweredAt === null ||
			this.state.error !== null ||
			this.advancePending ||
			!Number.isInteger(confidence) ||
			confidence < 1 ||
			confidence > 5
		) {
			return false;
		}
		if (this.pendingAttempt === null) {
			const completed = this.now();
			this.pendingAttempt = {
				schema: "virtuoso/review-attempt@0.1",
				submission_id: this.submissionId,
				item_id: this.state.item.item_id,
				item_content_hash: this.state.item.content_hash,
				started_at: this.startedAt.toISOString(),
				initial_answered_at: this.initialAnsweredAt.toISOString(),
				completed_at: completed.toISOString(),
				initial_response: this.state.initialResponse,
				retry: this.state.retry,
				hint_used: this.state.hintUsed,
				answer_revealed: true,
				result,
				confidence,
				open_notes: this.state.openNotes,
			};
		}
		this.state.inFlight = true;
		this.state.error = null;
		try {
			this.state.lastResult = await this.client.record(this.pendingAttempt);
			this.pendingAttempt = null;
			this.advancePending = true;
			try {
				await this.advance();
				return true;
			} catch (error) {
				this.failAdvance(error);
				return false;
			}
		} catch (error) {
			this.fail(error);
			return false;
		} finally {
			this.state.inFlight = false;
		}
	}

	async retrySubmission(): Promise<boolean> {
		if (
			this.state.inFlight ||
			this.state.error?.recovery !== "retry-submit" ||
			(this.pendingAttempt === null) === (this.pendingSkip === null)
		) {
			return false;
		}
		this.state.inFlight = true;
		this.state.error = null;
		try {
			if (this.pendingAttempt !== null) {
				this.state.lastResult = await this.client.record(this.pendingAttempt);
				this.pendingAttempt = null;
			} else if (this.pendingSkip !== null) {
				this.state.lastResult = await this.client.skip(this.pendingSkip);
				this.pendingSkip = null;
			}
			this.advancePending = true;
			try {
				await this.advance();
				return true;
			} catch (error) {
				this.failAdvance(error);
				return false;
			}
		} catch (error) {
			this.fail(error);
			return false;
		} finally {
			this.state.inFlight = false;
		}
	}

	async retryAdvance(): Promise<boolean> {
		if (this.state.inFlight || !this.advancePending || this.state.item === null) {
			return false;
		}
		this.state.inFlight = true;
		this.state.error = null;
		try {
			await this.advance();
			return true;
		} catch (error) {
			this.failAdvance(error);
			return false;
		} finally {
			this.state.inFlight = false;
		}
	}

	private async loadCurrent(): Promise<void> {
		await this.loadAt(this.index);
	}

	private async loadAt(index: number): Promise<void> {
		const expected = this.queue[index];
		const loaded = await this.client.load(expected.item_id);
		if (loaded.item.content_hash !== expected.content_hash) {
			throw new ReviewClientError(
				"The item changed while the review session loaded.",
				"stale-content",
				"reload-item",
			);
		}
		this.index = index;
		this.state.item = loaded.item;
		this.state.context = {
			focus: loaded.item.focus,
			projectIds: [...expected.project_ids],
			selectionReason: expected.selection_reason,
		};
		this.state.position = index + 1;
		this.state.initialResponse = "";
		this.state.retry = null;
		this.state.hintUsed = false;
		this.state.openNotes = false;
		this.state.error = null;
		this.state.lastResult = null;
		this.startedAt = this.now();
		this.initialAnsweredAt = null;
		this.retryStartedAt = null;
		this.submissionId = this.newSubmissionId();
		this.pendingAttempt = null;
		this.pendingSkip = null;
		this.advancePending = false;
		this.state.phase = "prompt";
	}

	private async advance(): Promise<void> {
		const nextIndex = this.index + 1;
		if (nextIndex >= this.queue.length) {
			this.advancePending = false;
			this.state.phase = "complete";
			return;
		}
		await this.loadAt(nextIndex);
	}

	private failAdvance(error: unknown): void {
		const detail = error instanceof Error ? error.message : String(error);
		this.state.error = {
			message: `The review was recorded, but the next card could not load. ${detail}`,
			code: "next-card-load-failed",
			recovery: "retry-next-card",
		};
	}

	private fail(error: unknown): void {
		if (error instanceof ReviewClientError) {
			this.state.error = {
				message: error.message,
				code: error.code,
				recovery: error.recovery,
			};
		} else {
			this.state.error = {
				message: error instanceof Error ? error.message : String(error),
				code: "process-failure",
				recovery: "check-settings",
			};
		}
	}
}
