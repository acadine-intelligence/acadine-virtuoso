import { App, Modal, Notice, Plugin, PluginSettingTab, Setting, TFile, Vault } from "obsidian";

/**
 * Virtuoso review queue.
 *
 * Ownership boundaries (2026-07-24 architecture decision):
 * - The Virtuoso CLI owns scheduling and evidence (SQLite + ledger).
 *   This plugin NEVER writes schedule state — it only flips
 *   `review-state: proposed` to `accepted`/`rejected`, which is the human
 *   decision the CLI already reads.
 * - Flashcard intervals belong to the Obsidian SR plugin (inline comments).
 * - Project priority belongs to the project system.
 */

interface VirtuosoItem {
	file: TFile;
	itemId: string;
	title: string;
	exerciseType: string;
	practiceMode: string;
	createdDate: string;
}

interface VirtuosoSettings {
	itemsDir: string;
}

const DEFAULT_SETTINGS: VirtuosoSettings = {
	itemsDir: "07-learning/virtuoso/items",
};

const FM_PATTERN = /^---\n([\s\S]*?)\n---/;

function frontmatter(raw: string): Record<string, string> {
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

export default class VirtuosoPlugin extends Plugin {
	settings: VirtuosoSettings = DEFAULT_SETTINGS;

	async onload() {
		await this.loadSettings();
		this.addRibbonIcon("list-checks", "Virtuoso review queue", () =>
			void this.openReviewQueue(),
		);
		this.addCommand({
			id: "virtuoso-review-queue",
			name: "Open review queue",
			callback: () => void this.openReviewQueue(),
		});
		this.addSettingTab(new VirtuosoSettingTab(this.app, this));
	}

	private async loadSettings() {
		this.settings = Object.assign({}, DEFAULT_SETTINGS, await this.loadData());
	}

	async saveSettings() {
		await this.saveData(this.settings);
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
				fm["review-state"] === "proposed"
			) {
				items.push({
					file: child,
					itemId: fm["item-id"] ?? child.basename,
					title: fm["title"] ?? child.basename,
					exerciseType: fm["exercise-type"] ?? "?",
					practiceMode: fm["practice-mode"] ?? "?",
					createdDate: fm["created-date"] ?? "",
				});
			}
		}
		return items;
	}

	/** The only write this plugin performs: the human review decision. */
	private async decide(item: VirtuosoItem, verdict: "accepted" | "rejected") {
		const vault = this.app.vault as Vault;
		const raw = await vault.read(item.file);
		if (!/^review-state:.*$/m.test(raw)) return;
		await vault.modify(item.file, raw.replace(/^review-state:.*$/m, `review-state: ${verdict}`));
		new Notice(`Virtuoso: "${item.title}" ${verdict}`);
	}

	private async openReviewQueue() {
		const proposed = await this.loadProposed();
		new ReviewQueueModal(this.app, proposed, (item, verdict) => this.decide(item, verdict)).open();
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
	}
}
