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

/** Resolve the legacy adapter key and the public core learning-item key. */
export function learningItemId(fm: Record<string, string>): string {
	return fmKey(fm, "item_id") || fmKey(fm, "id");
}

/** Accept the public core schema and the legacy Obsidian adapter schema. */
export function isLearningItemFrontmatter(fm: Record<string, string>): boolean {
	const schema = fm["schema"] ?? "";
	return schema.startsWith("virtuoso/item@") || schema.startsWith("virtuoso-learning-item/");
}

const PRODUCT_ID = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

function productId(value: unknown): string | null {
	return typeof value === "string" && PRODUCT_ID.test(value) ? value : null;
}

function jsonObject(stdout: string): Record<string, unknown> | null {
	let payload: unknown;
	try {
		payload = JSON.parse(stdout);
	} catch {
		return null;
	}
	if (!payload || typeof payload !== "object" || Array.isArray(payload)) return null;
	return payload as Record<string, unknown>;
}

function jsonArrayField(stdout: string, field: string): unknown[] {
	const value = jsonObject(stdout)?.[field];
	return Array.isArray(value) ? value : [];
}

/** Read each item's latest scheduler rationale from `attempts --json`. */
export function parseSchedulerRationales(stdout: string): Record<string, string> {
	const rationales: Record<string, string> = {};
	for (const proposal of jsonArrayField(stdout, "proposals")) {
		if (!proposal || typeof proposal !== "object") continue;
		const { item_id: itemId, learning_context: learningContext, rationale } = proposal as {
			item_id?: unknown;
			learning_context?: unknown;
			rationale?: unknown;
		};
		const safeItemId = productId(itemId);
		if (!safeItemId) continue;
		if (learningContext !== undefined && learningContext !== "atomic-recall") continue;
		if (typeof rationale !== "string" || !rationale.trim()) continue;
		rationales[safeItemId] = rationale.trim();
	}
	return rationales;
}

/** Read the selected item's exact reason from `next --json`. */
export function parseNextRationale(stdout: string): Record<string, string> {
	const payload = jsonObject(stdout);
	const itemId = productId(payload?.["item_id"]);
	const rationale = payload?.["rationale"];
	if (!itemId || typeof rationale !== "string" || !rationale.trim()) return {};
	return { [itemId]: rationale.trim() };
}

/** Prefer the current selection reason and retain proposal reasons for other cards. */
export function parsePracticeRationales(
	attemptsStdout: string,
	nextStdout: string,
): Record<string, string> {
	return {
		...parseSchedulerRationales(attemptsStdout),
		...parseNextRationale(nextStdout),
	};
}

/** Read explicit item-to-project links from `transfer list --json`. */
export function parseTransferProjects(stdout: string): Record<string, string[]> {
	const projects: Record<string, string[]> = {};
	for (const event of jsonArrayField(stdout, "events")) {
		if (!event || typeof event !== "object") continue;
		const { item_id: itemId, project_id: projectId } = event as {
			item_id?: unknown;
			project_id?: unknown;
		};
		const safeItemId = productId(itemId);
		const safeProjectId = productId(projectId);
		if (!safeItemId || !safeProjectId) continue;
		if (!Object.prototype.hasOwnProperty.call(projects, safeItemId)) projects[safeItemId] = [];
		if (!projects[safeItemId].includes(safeProjectId)) projects[safeItemId].push(safeProjectId);
	}
	return projects;
}

export interface CardContext {
	focus?: string;
	projectId?: string;
	linkedProjectIds?: readonly string[];
	whyNow?: string;
}

export interface CardContextRow {
	label: string;
	text: string;
}

/** Build safe, explicit context rows for display before answer reveal. */
export function cardContextRows(context: CardContext): CardContextRow[] {
	const rows: CardContextRow[] = [];
	if (context.focus?.trim()) rows.push({ label: "Focus", text: context.focus.trim() });

	const projects: string[] = [];
	for (const candidate of [context.projectId, ...(context.linkedProjectIds ?? [])]) {
		const safeProjectId = productId(candidate);
		if (!safeProjectId) continue;
		if (!projects.includes(safeProjectId)) projects.push(safeProjectId);
	}
	if (projects.length > 0) {
		rows.push({
			label: projects.length === 1 ? "Project" : "Projects",
			text: projects.join(", "),
		});
	}

	if (context.whyNow?.trim()) rows.push({ label: "Why now", text: context.whyNow });
	return rows;
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
