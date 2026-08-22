/**
 * GradeGate: one scheduler call per card per session, one chapter grade per
 * session. Test double counts calls — this is the NB-2/NB-3 acceptance proof.
 */
import { describe, expect, it } from "vitest";
import { GradeGate } from "../grading";

describe("GradeGate (NB-2 + NB-3)", () => {
	it("passes the first answer of a card to the scheduler", () => {
		const g = new GradeGate();
		expect(g.consume("item-a", undefined)).toBe(true);
	});

	it("NB-3: an in-session retry of the same card never re-grades", () => {
		const g = new GradeGate();
		expect(g.consume("item-a", undefined)).toBe(true); // first answer
		expect(g.consume("item-a", undefined)).toBe(false); // 'again' re-queue
		expect(g.consume("item-a", undefined)).toBe(false); // and again
	});

	it("NB-2: N cards of one chapter emit exactly one scheduler call", () => {
		const g = new GradeGate();
		let calls = 0;
		const fire = (cardId: string, ch: string) => {
			if (g.consume(cardId, ch)) calls++;
		};
		// 4-card chapter, realistic session: some again-requeues mixed in.
		fire("deck-ch2-0", "2");
		fire("deck-ch2-1", "2");
		fire("deck-ch2-0", "2"); // retry of card 0 (answer 'again' earlier)
		fire("deck-ch2-2", "2");
		fire("deck-ch2-3", "2");
		fire("deck-ch2-1", "2"); // retry of card 1
		expect(calls).toBe(1);
	});

	it("different chapters grade independently", () => {
		const g = new GradeGate();
		expect(g.consume("deck-ch2-0", "2")).toBe(true);
		expect(g.consume("deck-ch5-0", "5")).toBe(true);
		expect(g.consume("deck-ch2-4", "2")).toBe(false);
	});

	it("isChapterGraded reports the practice-only hint state", () => {
		const g = new GradeGate();
		expect(g.isChapterGraded("2")).toBe(false);
		g.consume("deck-ch2-0", "2");
		expect(g.isChapterGraded("2")).toBe(true);
		expect(g.isChapterGraded("5")).toBe(false);
	});

	it("item and deck ids never collide: same id with/without chapter", () => {
		const g = new GradeGate();
		expect(g.consume("deck-ch2-0", "2")).toBe(true);
		expect(g.consume("deck-ch2-0", undefined)).toBe(false); // same card id — still one grade
	});
});
