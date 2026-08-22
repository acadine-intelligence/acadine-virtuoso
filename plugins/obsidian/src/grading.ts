/**
 * Session grading gate — pure logic, no Obsidian imports.
 *
 * The scheduler (Virtuoso CLI) must hear each card exactly once per session:
 *
 * - NB-3: an "again" answer re-queues the card for in-session practice, but
 *   only the FIRST answer ever reaches the scheduler. A re-grade would write
 *   a second scheduler event (second interval reset) for one session.
 * - NB-2: the book deck is scheduled at chapter granularity. The first rating
 *   given to ANY card of a chapter grades the whole chapter; later cards of
 *   the same chapter are practice-only and emit nothing.
 *
 * RepSessionModal consults this gate before shelling out to the CLI.
 */
export class GradeGate {
	private gradedCards = new Set<string>();
	private gradedChapters = new Set<string>();

	/**
	 * Record an answer and report whether it must be sent to the scheduler.
	 * Call exactly once per non-skip answer. Returns true only for the first
	 * answer of a card that is also the first answer of its chapter (deck
	 * cards); item cards have no chapter, so only the per-card rule applies.
	 */
	consume(cardId: string, chapter: string | undefined): boolean {
		if (this.gradedCards.has(cardId)) return false; // NB-3: practice retry
		this.gradedCards.add(cardId);
		if (chapter === undefined) return true; // item card: first answer grades it
		if (this.gradedChapters.has(chapter)) return false; // NB-2: chapter already graded
		this.gradedChapters.add(chapter);
		return true;
	}

	/** True when a deck card of this chapter should render the "practice only" hint. */
	isChapterGraded(chapter: string): boolean {
		return this.gradedChapters.has(chapter);
	}
}
