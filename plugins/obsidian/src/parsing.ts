/**
 * Pure parsing helpers for the Virtuoso Obsidian plugin.
 *
 * No Obsidian API imports here — every function is plain string-in /
 * data-out so it can be unit-tested under Node (see src/test/parsing.test.ts).
 * main.ts keeps all vault, CLI and UI concerns; parsing lives here.
 *
 * Frontmatter key policy (NB-1 fix, 2026-08-22): virtuoso-learning-item
 * notes use snake_case keys (`item_id`, `review_state`, `exercise_type`,
 * `practice_mode`, `created_date`). Those are canonical. Hyphenated
 * spellings are tolerated as legacy aliases where they occurred in older
 * drafts, but underscore matches win.
 */

export const FM_PATTERN = /^---\n([\s\S]*?)\n---/;

/** Parse the leading YAML frontmatter block into a flat key/value map. */
export function frontmatter(raw: string): Record<string, string> {
	const m = raw.match(FM_PATTERN);
	if (!m) return {};
	const out: Record<string, string> = {};
	for (const line of m[1].split("\n")) {
		const idx = line.indexOf(":");
		if (idx === -1) continue;
		const key = line.slice(0, idx).trim();
		const val = line.slice(idx + 1).trim().replace(/^["']|["']$/g, "");
		out[key] = val;
	}
	return out;
}

/**
 * Read a frontmatter key with the canonical snake_case spelling first and
 * any number of legacy hyphenated aliases as fallbacks. Returns "" when no
 * spelling is present.
 */
export function fmKey(fm: Record<string, string>, canonical: string): string {
	if (fm[canonical] !== undefined) return fm[canonical];
	const alias = canonical.replace(/_/g, "-");
	return fm[alias] ?? "";
}

export interface QA {
	q: string;
	a: string;
}

export interface ChapterCards {
	ch: string;
	title: string;
	qa: QA[];
}

/** Extract `### Ch N — Title` sections from the deck note as Q/A pairs. */
export function deckChapterCards(raw: string): ChapterCards[] {
	const out: ChapterCards[] = [];
	let current: ChapterCards | null = null;
	for (const line of raw.split("\n")) {
		const h = line.match(/^### Ch (\d+) — (.+)$/);
		if (h) {
			current = { ch: h[1], title: h[2].trim(), qa: [] };
			out.push(current);
			continue;
		}
		if (current && /^## /.test(line)) current = null; // next top section ends the deck
		if (current) {
			const q = line.match(/^- Q: (.+)$/);
			const a = line.match(/^- A: (.+)$/);
			if (q) {
				const full = q[1].trim();
				const split = full.match(/^(.*?)\s+A:\s+(.*)$/s);
				if (split) current.qa.push({ q: split[1].trim(), a: split[2].trim() });
				else current.qa.push({ q: full, a: "" });
			} else if (a && current.qa.length > 0 && !current.qa[current.qa.length - 1].a) {
				current.qa[current.qa.length - 1].a = a[1].trim();
			}
		}
	}
	return out;
}

/**
 * Split the CLI's `due` output into its sections.
 *
 * Known sections (in the order cmd_due emits them): TRANSFER CHECKS DUE,
 * REVIEWS DUE, BOOK DECK DUE, AWAITING YOUR REVIEW. A missing section
 * yields an empty string, never undefined — callers can always `.matchAll`
 * the result.
 */
export function parseDueOutput(stdout: string): {
	itemsDue: string[];
	deckChaptersDue: string[];
	transfersDue: string[];
	proposed: string[];
} {
	const section = (name: string): string => stdout.split(name)[1] ?? "";

	// Each later known section header terminates the previous section's text.
	const itemSection = (stdout.split("REVIEWS DUE:")[1] ?? "").split("BOOK DECK DUE")[0].split("AWAITING YOUR REVIEW")[0];
	const deckSection = (stdout.split("BOOK DECK DUE")[1] ?? "").split("AWAITING YOUR REVIEW")[0];
	const transferSection = (stdout.split("TRANSFER CHECKS DUE:")[1] ?? "").split("REVIEWS DUE:")[0].split("BOOK DECK DUE")[0].split("AWAITING YOUR REVIEW")[0];
	const proposedSection = stdout.split("AWAITING YOUR REVIEW:")[1] ?? "";

	const itemsDue: string[] = [];
	for (const m of itemSection.matchAll(/\[([^\]]+)\]/g)) itemsDue.push(m[1]);

	const deckChaptersDue: string[] = [];
	for (const m of deckSection.matchAll(/^  Ch (\d+) —/gm)) deckChaptersDue.push(m[1]);

	const transfersDue: string[] = [];
	for (const m of transferSection.matchAll(/\[([^\]]+)\]/g)) transfersDue.push(m[1]);

	const proposed: string[] = [];
	// "4 proposed item(s) — id1, id2, id3": bare comma-separated ids after the dash.
	const rest = proposedSection.replace(/^[^—]*—\s*/, "");
	for (const tok of rest.split(",").map((t) => t.trim()).filter(Boolean)) proposed.push(tok);

	return { itemsDue, deckChaptersDue, transfersDue, proposed };
}
