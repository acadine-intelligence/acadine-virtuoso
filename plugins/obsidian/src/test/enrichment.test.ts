import { describe, expect, it } from "vitest";
import { applyEnrichmentAdditions, EnrichmentBoundaryError } from "../enrichment";

describe("additive enrichment boundary", () => {
	it("adds only enrichment-owned fields and preserves schedule and evidence", () => {
		const schedule = { due_at: "2026-09-03T12:00:00Z", interval_days: 1 };
		const evidence = { attempts: [{ event_id: "attempt-1", result: "partial" }] };
		const before = {
			item_id: "testing-effect",
			content_hash: "a".repeat(64),
			schedule,
			evidence,
			enrichment: { summary: "Existing learner-approved context." },
		};

		const after = applyEnrichmentAdditions(before, {
			examples: ["Use retrieval before reading the answer."],
			connections: ["testing-effect"],
		});

		expect(after).not.toBe(before);
		expect(after.schedule).toBe(schedule);
		expect(after.evidence).toBe(evidence);
		expect(before.enrichment).toEqual({ summary: "Existing learner-approved context." });
		expect(after.enrichment).toEqual({
			summary: "Existing learner-approved context.",
			examples: ["Use retrieval before reading the answer."],
			connections: ["testing-effect"],
		});

		for (const protectedField of [
			"schedule",
			"scheduler_state",
			"due_at",
			"evidence",
			"attempts",
			"result",
		]) {
			expect(() =>
				applyEnrichmentAdditions(before, { [protectedField]: "rewrite" }),
			).toThrow(EnrichmentBoundaryError);
		}
		expect(() =>
			applyEnrichmentAdditions(before, { summary: "Replace existing context." }),
		).toThrow(/additive/);
	});
});
