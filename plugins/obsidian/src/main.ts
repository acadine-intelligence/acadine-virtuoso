import { App, Modal, Notice, Plugin, PluginSettingTab, Setting, TFile, Vault } from "obsidian";
import { spawn } from "child_process";
import { deckChapterCards, fmKey, frontmatter, parseDueOutput } from "./parsing";
import { GradeGate } from "./grading";

/**
 * Virtuoso for Obsidian.
 *
 * Ownership boundaries (2026-07-24 architecture decision, extended 2026-08-22):
 * - The Virtuoso CLI owns scheduling and the evidence ledger. This plugin NEVER
 *   computes intervals itself — every grade shells out to the CLI, so there is
 *   exactly one scheduler and one ledger writer.
 * - Obsidian SR keeps flashcard + whole-note schedules (inline comments / sr-*).
 * - Project priority belongs to the project system.
 *
 * Commands:
 * - "Open review queue"          — accept/reject proposed learning items.
 * - "Cycle today's cards"        — hotkey-driven rep session over everything
 *                                  due today (CLI items + book deck chapters).
 *                                  One card at a time: prompt -> reveal ->
 *                                  grade (again/hard/good/easy) or skip.
 */

type Rating = "again" | "hard" | "good" | "easy";

interface VirtuosoItem {
	file: TFile;
	itemId: string;
	title: string;
	exerciseType: string;
	practiceMode: string;
	createdDate: string;
}

interface DueCard {
	source: "item" | "deck";
	id: string;
	/** Deck cards only: chapter number this card belongs to ("2" for "deck-ch2"). */
	chapter?: string;
	title: string;
	promptLines: string[];
	detail?: string;
}

interface VirtuosoSettings {
	itemsDir: string;
	cliPath: string;
	deckPath: string;
}

const DEFAULT_SETTINGS: VirtuosoSettings = {
	itemsDir: "07-learning/virtuoso/items",
	cliPath: "~/.virtuoso/scheduler.py",
	deckPath: "07-learning/deck.md",
};

class ReviewQueueModal extends Modal {
	private items: VirtuosoItem[];
	private decide: (item: VirtuosoItem, verdict: "accepted" | "rejected") => Promise<void>;

	constructor(
		app: App,
		items: VirtuosoItem[],
		decide: (item: VirtuosoItem, verdict: "accepted" | "rejected") => Promise<void>,
	) {
		super(app);
		this.items = items;
		this.decide = decide;
	}

	onOpen() {
		const { contentEl } = this;
		contentEl.createEl("h2", { text: "Virtuoso review queue" });
		if (this.items.length === 0) {
			contentEl.createEl("p", { text: "No proposed learning items. Queue clear." });
			return;
		}
		for (const item of this.items) {
			const row = contentEl.createDiv();
			row.style.marginBottom = "0.75em";
			row.createEl("strong", { text: item.title });
			row.createEl("small", {
				text: ` · ${item.exerciseType} / ${item.practiceMode} · ${item.createdDate}`,
			});
			const buttons = row.createDiv();
			buttons
				.createEl("button", { text: "Accept" })
				.addEventListener("click", () => {
					void this.decide(item, "accepted").then(() => void this.close());
				});
			buttons
				.createEl("button", { text: "Reject" })
				.addEventListener("click", () => {
					void this.decide(item, "rejected").then(() => void this.close());
				});
		}
	}

	onClose() {
		this.contentEl.empty();
	}
}

/**
 * Full-viewport rep session. One due card at a time:
 * prompt shown first, Space/Enter reveals the answer, then grade or skip.
 */
class RepSessionModal extends Modal {
	private app2: App;
	private cards: DueCard[];
	private grade: (card: DueCard, rating: Rating | "skip") => Promise<string | null>;
	private index = 0;
	private revealed = false;
	private statusEl!: HTMLElement;
	private bodyEl!: HTMLElement;
	/** One scheduler call per card per session; one chapter grade per session. See grading.ts. */
	private gate = new GradeGate();

	constructor(
		app: App,
		cards: DueCard[],
		grade: (card: DueCard, rating: Rating | "skip") => Promise<string | null>,
	) {
		super(app);
		this.app2 = app;
		this.cards = cards;
		this.grade = grade;
	}

	onOpen() {
		this.modalEl.addClass("virtuoso-rep-modal");
		this.modalEl.style.width = "min(760px, 92vw)";
		this.contentEl.createEl("h2", { text: "Virtuoso — today's reps" });
		this.statusEl = this.contentEl.createDiv({ cls: "virtuoso-rep-status" });
		this.bodyEl = this.contentEl.createDiv({ cls: "virtuoso-rep-body" });

		const keyHandler = (evt: KeyboardEvent) => {
			if (evt.key === "Escape") return; // default modal close
			if (!this.revealed && (evt.key === " " || evt.key === "Enter")) {
				evt.preventDefault();
				this.revealed = true;
				this.render();
				return;
			}
			if (this.revealed) {
				const map: Record<string, Rating> = { "1": "again", "2": "hard", "3": "good", "4": "easy" };
				const r = map[evt.key];
				if (r) {
					evt.preventDefault();
					void this.submit(r);
				} else if (evt.key.toLowerCase() === "s") {
					evt.preventDefault();
					void this.submit("skip");
				}
			}
		};
		this.scope.register([], "Space", () => keyHandler(new KeyboardEvent("keydown", { key: " " })));
		this.scope.register([], "Enter", () => keyHandler(new KeyboardEvent("keydown", { key: "Enter" })));
		for (const k of ["1", "2", "3", "4"]) {
			this.scope.register([], k, () => keyHandler(new KeyboardEvent("keydown", { key: k })));
		}
		this.scope.register([], "s", () => keyHandler(new KeyboardEvent("keydown", { key: "s" })));

		this.render();
	}

	private render() {
		const { bodyEl, statusEl } = this;
		bodyEl.empty();
		if (this.index >= this.cards.length) {
			statusEl.setText("Session complete.");
			bodyEl.createEl("p", { text: "All of today's cards handled. Evidence is in the ledger." });
			return;
		}
		const card = this.cards[this.index];
		const gradedHint =
			card.source === "deck" && card.chapter && this.gate.isChapterGraded(card.chapter)
				? "  ·  chapter already graded — practice only"
				: "";
		statusEl.setText(`Card ${this.index + 1} of ${this.cards.length}${gradedHint}${this.revealed ? "" : "  ·  recall first, then Space to reveal"}`);
		bodyEl.createEl("h3", { text: card.title });
		for (let i = 0; i < card.promptLines.length; i++) {
			bodyEl.createEl("p", { text: card.promptLines[i] });
			if (!this.revealed && i === 0 && card.promptLines.length > 1) break; // multi-part: reveal part by part
		}
		if (this.revealed) {
			if (card.detail) {
				const d = bodyEl.createEl("p");
				d.style.color = "var(--text-muted)";
				d.setText(card.detail);
			}
			const btns = bodyEl.createDiv({ cls: "virtuoso-rep-buttons" });
			const mk = (label: string, r: Rating | "skip") => {
				const b = btns.createEl("button", { text: label });
				b.addEventListener("click", () => void this.submit(r));
				return b;
			};
			mk("Again (1)", "again").style.marginRight = "0.5em";
			mk("Hard (2)", "hard").style.marginRight = "0.5em";
			mk("Good (3)", "good").style.marginRight = "0.5em";
			mk("Easy (4)", "easy").style.marginRight = "0.5em";
			mk("Skip (s)", "skip");
		}
	}

	private async submit(rating: Rating | "skip") {
		const card = this.cards[this.index];
		if (rating !== "skip") {
			// NB-3 + NB-2 gate: true only for the first answer of a card whose
			// chapter has not been graded yet (deck cards); item cards have no
			// chapter, so only the per-card rule applies.
			if (this.gate.consume(card.id, card.chapter)) {
				const err = await this.grade(card, rating);
				if (err) new Notice(`Virtuoso CLI: ${err}`, 6000);
			} else {
				new Notice("Practice retry — already graded this session.", 2500);
			}
		}
		this.revealed = false;
		if (rating !== "again") this.index++; // "again" re-queues the same card within the session
		else this.cards.push(card); // retry later in this session
		this.render();
	}

	onClose() {
		this.contentEl.empty();
	}
}

export default class VirtuosoPlugin extends Plugin {
	settings: VirtuosoSettings = DEFAULT_SETTINGS;

	async onload() {
		try {
			await this.loadSettings();
			this.addRibbonIcon("list-checks", "Virtuoso review queue", () =>
				void this.openReviewQueue(),
			);
			this.addCommand({
				id: "virtuoso-review-queue",
				name: "Open review queue",
				callback: () => void this.openReviewQueue(),
			});
			this.addCommand({
				id: "virtuoso-cycle-due",
				name: "Cycle today's cards",
				callback: () => void this.openRepSession(),
			});
			this.addSettingTab(new VirtuosoSettingTab(this.app, this));
		} catch (err) {
			// Issue #9: a plugin that fails to load disappears silently — Obsidian
			// restricted mode or a corrupt data.json shows the user nothing. Surface
			// the failure path we own; the console keeps the full stack.
			console.error("Virtuoso plugin failed to load:", err);
			new Notice(
				`Virtuoso failed to load: ${err instanceof Error ? err.message : String(err)}. ` +
					"If the plugin stays inactive, check restricted-mode (Community plugins " +
					"toggle) and the plugin's data.json.",
				10000,
			);
			throw err;
		}
	}

	private async loadSettings() {
		this.settings = Object.assign({}, DEFAULT_SETTINGS, await this.loadData());
	}

	async saveSettings() {
		await this.saveData(this.settings);
	}

	private cli(): string {
		return this.settings.cliPath.replace(/^~(?=\/|$)/, (process.env.HOME ?? ""));
	}

	/** Run a read-only CLI command and capture stdout. */
	private cliRun(args: string[]): Promise<{ ok: boolean; stdout: string }> {
		return new Promise((resolve) => {
			const proc = spawn("python3", [this.cli(), ...args], { timeout: 15000 });
			let stdout = "";
			proc.stdout.on("data", (d: unknown) => (stdout += String(d)));
			proc.on("error", () => resolve({ ok: false, stdout }));
			proc.on("close", (code: number | null) => resolve({ ok: code === 0, stdout }));
		});
	}

	private async loadProposed(): Promise<VirtuosoItem[]> {
		const vault = this.app.vault as Vault;
		const folder = vault.getAbstractFileByPath(this.settings.itemsDir);
		if (!folder || !("children" in folder)) return [];
		const items: VirtuosoItem[] = [];
		for (const child of (folder as unknown as { children: TFile[] }).children) {
			if (!(child instanceof TFile) || child.extension !== "md") continue;
			const raw = await vault.read(child);
			const fm = frontmatter(raw);
			if (
				fm["schema"]?.startsWith("virtuoso-learning-item") &&
				fmKey(fm, "review_state") === "proposed"
			) {
				items.push({
					file: child,
					itemId: fmKey(fm, "item_id") || child.basename,
					title: fmKey(fm, "title") || child.basename,
					exerciseType: fmKey(fm, "exercise_type") || "?",
					practiceMode: fmKey(fm, "practice_mode") || "?",
					createdDate: fmKey(fm, "created_date") || "",
				});
			}
		}
		return items;
	}

	/** The human-review write: flip review_state on a proposal. */
	private async decide(item: VirtuosoItem, verdict: "accepted" | "rejected") {
		const vault = this.app.vault as Vault;
		const raw = await vault.read(item.file);
		// Canonical key is review_state (underscore); tolerate the legacy
		// hyphenated spelling from older drafts.
		const line = /(^review_state:.*$)|(^review-state:.*$)/m;
		if (!line.test(raw)) return;
		await vault.modify(item.file, raw.replace(line, `review_state: ${verdict}`));
		new Notice(`Virtuoso: "${item.title}" ${verdict}`);
	}

	private async openReviewQueue() {
		const proposed = await this.loadProposed();
		new ReviewQueueModal(this.app, proposed, (item, verdict) => this.decide(item, verdict)).open();
	}

	/** Build today's card list from the CLI's own due output (items + deck). */
	private async loadDueCards(): Promise<DueCard[]> {
		const vault = this.app.vault as Vault;
		const cards: DueCard[] = [];

		// Deck chapters due, straight from the CLI verdict.
		const due = await this.cliRun(["due"]);
		const parsed = parseDueOutput(due.stdout);
		if (parsed.deckChaptersDue.length > 0) {
			const deckIds = new Set(parsed.deckChaptersDue);
			const deckFile = vault.getAbstractFileByPath(this.settings.deckPath);
			if (deckFile instanceof TFile) {
				const raw = await vault.read(deckFile);
				for (const chap of deckChapterCards(raw)) {
					if (!deckIds.has(chap.ch)) continue;
					for (let i = 0; i < chap.qa.length; i++) {
						const card = chap.qa[i];
						cards.push({
							source: "deck",
							id: `deck-ch${chap.ch}-${i}`,
							chapter: chap.ch,
							title: `Book deck · Ch ${chap.ch} — ${chap.title} (${i + 1}/${chap.qa.length})`,
							promptLines: [card.q],
							detail: card.a || "(no answer key in deck note — check the source)",
						});
					}
				}
			}
		}

		// Virtuoso items due per the CLI, rendered from their notes.
		if (parsed.itemsDue.length > 0) {
			const folder = vault.getAbstractFileByPath(this.settings.itemsDir);
			if (folder && "children" in folder) {
				// One vault read per note: build an item_id -> note lookup first (was: one
				// full folder rescan per due id — O(items x due) reads).
				const byItemId = new Map<string, { fm: Record<string, string>; body: string; basename: string }>();
				for (const child of (folder as unknown as { children: TFile[] }).children) {
					if (!(child instanceof TFile) || child.extension !== "md") continue;
					const raw = await vault.read(child);
					const fm = frontmatter(raw);
					if (!fm["schema"]?.startsWith("virtuoso-learning-item")) continue;
					const id = fmKey(fm, "item_id");
					if (id) {
						const body = raw.slice(raw.indexOf("---", 4) + 4);
						byItemId.set(id, { fm, body, basename: child.basename });
					}
				}
				for (const itemId of parsed.itemsDue) {
					const hit = byItemId.get(itemId);
					if (!hit) continue;
					const lines = hit.body.split("\n").map((l) => l.trim()).filter((l) => l && !l.startsWith("#"));
					cards.push({
						source: "item",
						id: itemId,
						title: fmKey(hit.fm, "title") || hit.basename,
						promptLines: lines,
					});
				}
			}
		}
		return cards;
	}

	private async openRepSession() {
		const cards = await this.loadDueCards();
		if (cards.length === 0) {
			new Notice("Virtuoso: nothing due today.");
			return;
		}
		new RepSessionModal(this.app, cards, (card, rating) => this.gradeCard(card, rating)).open();
	}

	/** Grade through the CLI only — the plugin never computes an interval. */
	private async gradeCard(card: DueCard, rating: Rating | "skip"): Promise<string | null> {
		if (rating === "skip") {
			// Issue #4: a skip must leave a trace. Record it in the CLI's
			// evidence ledger (schedule untouched); a failure surfaces as a
			// Notice rather than vanishing.
			if (card.source !== "item") return null;
			const res = await this.cliRun(["skip", card.id, "--surface", "obsidian-plugin"]);
			if (!res.ok) new Notice(`Virtuoso skip record failed: ${res.stdout.trim()}`, 6000);
			return null;
		}
		const args =
			card.source === "deck" && card.chapter
				? ["deck-rep", card.chapter, rating]
				: ["review", card.id, rating];
		const res = await this.cliRun(args);
		if (!res.ok) return res.stdout.trim() || "command failed";
		new Notice(res.stdout.trim(), 3500);
		return null;
	}
}

class VirtuosoSettingTab extends PluginSettingTab {
	private plugin: VirtuosoPlugin;

	constructor(app: App, plugin: VirtuosoPlugin) {
		super(app, plugin);
		this.plugin = plugin;
	}

	display() {
		const { containerEl } = this;
		containerEl.empty();
		new Setting(containerEl)
			.setName("Items directory")
			.setDesc("Vault path holding virtuoso-learning-item notes")
			.addText((text) =>
				text
					.setPlaceholder(DEFAULT_SETTINGS.itemsDir)
					.setValue(this.plugin.settings.itemsDir)
					.onChange((value) => {
						void (async () => {
							this.plugin.settings.itemsDir = value || DEFAULT_SETTINGS.itemsDir;
							await this.plugin.saveSettings();
						})();
					}),
			);
		new Setting(containerEl)
			.setName("Scheduler CLI path")
			.setDesc("Path to virtuoso.py — all grades run through it")
			.addText((text) =>
				text
					.setPlaceholder(DEFAULT_SETTINGS.cliPath)
					.setValue(this.plugin.settings.cliPath)
					.onChange((value) => {
						void (async () => {
							this.plugin.settings.cliPath = value || DEFAULT_SETTINGS.cliPath;
							await this.plugin.saveSettings();
						})();
					}),
			);
		new Setting(containerEl)
			.setName("Deck note path")
			.setDesc("Vault path to the spaced-repetition deck note")
			.addText((text) =>
				text
					.setPlaceholder(DEFAULT_SETTINGS.deckPath)
					.setValue(this.plugin.settings.deckPath)
					.onChange((value) => {
						void (async () => {
							this.plugin.settings.deckPath = value || DEFAULT_SETTINGS.deckPath;
							await this.plugin.saveSettings();
						})();
					}),
			);
	}
}
