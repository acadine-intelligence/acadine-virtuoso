import { App, Modal, Notice, Plugin, PluginSettingTab, Setting, TFile, Vault } from "obsidian";
import { VirtuosoCliClient } from "./cli-client";
import {
	fmKey,
	frontmatter,
	isLearningItemFrontmatter,
	learningItemId,
} from "./parsing";
import { ReviewSessionController } from "./session";

/**
 * Virtuoso for Obsidian.
 *
 * The installed Virtuoso CLI is the only scheduler and evidence writer. This
 * plugin holds one review snapshot in memory, displays the practice flow, and
 * submits hash-bound decisions through versioned JSON CLI contracts. It never
 * calculates an interval or opens SQLite.
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
	cliPath: string;
	workspacePath: string;
}

const DEFAULT_SETTINGS: VirtuosoSettings = {
	itemsDir: "07-learning/virtuoso/items",
	cliPath: "",
	workspacePath: "",
};

class ReviewQueueModal extends Modal {
	constructor(
		app: App,
		private readonly items: VirtuosoItem[],
		private readonly decide: (
			item: VirtuosoItem,
			verdict: "accepted" | "rejected",
		) => Promise<void>,
	) {
		super(app);
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

class ReviewSessionModal extends Modal {
	private statusEl!: HTMLElement;
	private bodyEl!: HTMLElement;
	private selectedResult: "demonstrated" | "partial" | "not-demonstrated" =
		"partial";
	private selectedConfidence = 3;

	constructor(
		app: App,
		private readonly controller: ReviewSessionController,
		private readonly openSettings: () => void,
	) {
		super(app);
	}

	onOpen() {
		this.modalEl.addClass("virtuoso-review-modal");
		this.modalEl.style.width = "min(760px, 92vw)";
		this.contentEl.createEl("h2", { text: "Virtuoso review" });
		this.statusEl = this.contentEl.createDiv({ cls: "virtuoso-review-status" });
		this.bodyEl = this.contentEl.createDiv({ cls: "virtuoso-review-body" });
		this.perform(() => this.controller.start());
	}

	private perform(action: () => Promise<unknown>): void {
		const pending = action();
		this.render();
		void pending.finally(() => this.render());
	}

	private render(): void {
		const state = this.controller.state;
		this.bodyEl.empty();
		this.renderError();
		if (state.phase === "loading" || state.phase === "idle") {
			this.statusEl.setText(
				state.error ? "Review stopped. Use the recovery action." : "Loading the local review queue.",
			);
			return;
		}
		if (state.phase === "empty") {
			this.statusEl.setText("Review queue clear.");
			this.bodyEl.createEl("p", { text: "No due or new items." });
			return;
		}
		if (state.phase === "complete") {
			this.statusEl.setText("Session complete.");
			this.bodyEl.createEl("p", {
				text: "The CLI recorded each decision in the local workspace.",
			});
			return;
		}

		this.statusEl.setText(
			`Card ${state.position} of ${state.total}${state.inFlight ? " · saving" : ""}`,
		);
		const item = state.item;
		if (item === null) return;
		if (state.error !== null) return;
		this.bodyEl.createEl("h3", { text: item.title });
		this.bodyEl.createEl("p", { text: item.prompt });

		if (state.phase === "prompt") this.renderPrompt();
		else if (state.phase === "support") this.renderSupport();
		else if (state.phase === "retry") this.renderRetry();
		else if (state.phase === "grade") this.renderGrade();
	}

	private renderError(): void {
		const error = this.controller.state.error;
		if (error === null) return;
		const panel = this.bodyEl.createDiv({ cls: "virtuoso-review-error" });
		panel.createEl("p", { text: error.message });
		const label =
			error.recovery === "reload-item"
				? "Reload item"
				: error.recovery === "retry-submit"
					? "Retry submission"
					: error.recovery === "advance-card"
						? "Continue to next card"
						: error.recovery === "retry-next-card"
							? "Retry next card"
							: "Open settings";
		const button = panel.createEl("button", { text: label });
		button.disabled = this.controller.state.inFlight;
		button.addEventListener("click", () => {
			if (error.recovery === "reload-item") {
				this.perform(() => this.controller.reloadCurrent());
				return;
			}
			if (error.recovery === "retry-submit") {
				this.perform(() => this.controller.retrySubmission());
				return;
			}
			if (error.recovery === "advance-card") {
				this.perform(() => this.controller.acknowledgeRecorded());
				return;
			}
			if (error.recovery === "retry-next-card") {
				this.perform(() => this.controller.retryAdvance());
				return;
			}
			this.openSettings();
		});
	}

	private renderPrompt(): void {
		const state = this.controller.state;
		const notesLabel = this.bodyEl.createEl("label");
		const notes = notesLabel.createEl("input", { type: "checkbox" });
		notes.checked = state.openNotes;
		notes.disabled = state.inFlight;
		notes.addEventListener("change", () => this.controller.setOpenNotes(notes.checked));
		notesLabel.appendText(" Notes are open");

		const response = this.bodyEl.createEl("textarea");
		response.placeholder = "Type your first recall before using help.";
		response.rows = 6;
		response.disabled = state.inFlight;
		const submit = this.bodyEl.createEl("button", { text: "Submit initial response" });
		submit.disabled = state.inFlight;
		submit.addEventListener("click", () => {
			this.controller.submitInitial(response.value);
			this.render();
		});
		this.renderSkipButton();
	}

	private renderSupport(): void {
		const state = this.controller.state;
		this.bodyEl.createEl("p", {
			text: `Initial response: ${state.initialResponse || "(blank)"}`,
		});
		if (state.hintUsed && state.item?.hint) {
			this.bodyEl.createEl("p", { text: `Hint: ${state.item.hint}` });
		}
		if (state.retry === null && !state.hintUsed) {
			const retry = this.bodyEl.createEl("button", { text: "Retry unaided" });
			retry.disabled = state.inFlight;
			retry.addEventListener("click", () => {
				this.controller.beginRetry();
				this.render();
			});
		}
		if (state.item?.hint && !state.hintUsed) {
			const hint = this.bodyEl.createEl("button", { text: "Show hint" });
			hint.disabled = state.inFlight;
			hint.addEventListener("click", () => {
				this.controller.useHint();
				this.render();
			});
		}
		const reveal = this.bodyEl.createEl("button", { text: "Reveal answer" });
		reveal.disabled = state.inFlight;
		reveal.addEventListener("click", () => {
			this.controller.reveal();
			this.render();
		});
		this.renderSkipButton();
	}

	private renderRetry(): void {
		const state = this.controller.state;
		const response = this.bodyEl.createEl("textarea");
		response.placeholder = "Try once more without the hint or answer.";
		response.rows = 6;
		response.disabled = state.inFlight;
		const submit = this.bodyEl.createEl("button", { text: "Submit unaided retry" });
		submit.disabled = state.inFlight;
		submit.addEventListener("click", () => {
			this.controller.submitRetry(response.value);
			this.render();
		});
		this.renderSkipButton();
	}

	private renderGrade(): void {
		const state = this.controller.state;
		this.bodyEl.createEl("h4", { text: "Answer" });
		this.bodyEl.createEl("p", { text: state.item?.answer ?? "" });

		const resultLabel = this.bodyEl.createEl("label", { text: "Result " });
		const result = resultLabel.createEl("select");
		for (const [value, label] of [
			["demonstrated", "Demonstrated"],
			["partial", "Partial"],
			["not-demonstrated", "Not demonstrated"],
		] as const) {
			const option = result.createEl("option", { text: label });
			option.value = value;
		}
		result.value = this.selectedResult;
		result.disabled = state.inFlight;
		result.addEventListener("change", () => {
			this.selectedResult = result.value as typeof this.selectedResult;
		});

		const confidenceLabel = this.bodyEl.createEl("label", { text: " Confidence " });
		const confidence = confidenceLabel.createEl("select");
		for (let value = 1; value <= 5; value += 1) {
			const option = confidence.createEl("option", { text: String(value) });
			option.value = String(value);
		}
		confidence.value = String(this.selectedConfidence);
		confidence.disabled = state.inFlight;
		confidence.addEventListener("change", () => {
			this.selectedConfidence = Number(confidence.value);
		});

		const grade = this.bodyEl.createEl("button", { text: "Record grade" });
		grade.disabled = state.inFlight;
		grade.addEventListener("click", () => {
			this.selectedResult = result.value as typeof this.selectedResult;
			this.selectedConfidence = Number(confidence.value);
			this.perform(() =>
				this.controller.grade(this.selectedResult, this.selectedConfidence),
			);
		});
		this.renderSkipButton();
	}

	private renderSkipButton(): void {
		const skip = this.bodyEl.createEl("button", { text: "Skip" });
		skip.disabled = this.controller.state.inFlight;
		skip.addEventListener("click", () => {
			this.perform(() => this.controller.skip());
		});
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
				name: "Start offline review",
				callback: () => this.openReviewSession(),
			});
			this.addSettingTab(new VirtuosoSettingTab(this.app, this));
		} catch (error) {
			console.error("Virtuoso plugin failed to load:", error);
			new Notice(
				`Virtuoso failed to load: ${error instanceof Error ? error.message : String(error)}. ` +
					"Check restricted mode and the plugin settings.",
				10000,
			);
			throw error;
		}
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
				isLearningItemFrontmatter(fm) &&
				fmKey(fm, "review_state") === "proposed"
			) {
				items.push({
					file: child,
					itemId: learningItemId(fm) || child.basename,
					title: fmKey(fm, "title") || child.basename,
					exerciseType: fmKey(fm, "exercise_type") || "?",
					practiceMode: fmKey(fm, "practice_mode") || "?",
					createdDate: fmKey(fm, "created_date") || "",
				});
			}
		}
		return items;
	}

	private async decide(item: VirtuosoItem, verdict: "accepted" | "rejected") {
		const vault = this.app.vault as Vault;
		const raw = await vault.read(item.file);
		const line = /(^review_state:.*$)|(^review-state:.*$)/m;
		if (!line.test(raw)) return;
		await vault.modify(item.file, raw.replace(line, `review_state: ${verdict}`));
		new Notice(`Virtuoso: "${item.title}" ${verdict}`);
	}

	private async openReviewQueue() {
		const proposed = await this.loadProposed();
		new ReviewQueueModal(this.app, proposed, (item, verdict) =>
			this.decide(item, verdict),
		).open();
	}

	private openReviewSession(): void {
		const client = new VirtuosoCliClient(
			this.settings.cliPath,
			this.settings.workspacePath,
		);
		new ReviewSessionModal(
			this.app,
			new ReviewSessionController(client),
			() => this.openPluginSettings(),
		).open();
	}

	private openPluginSettings(): void {
		const app = this.app as App & {
			setting?: { open(): void; openTabById(id: string): void };
		};
		if (!app.setting) {
			new Notice("Open Settings, then select Virtuoso.");
			return;
		}
		app.setting.open();
		app.setting.openTabById(this.manifest.id);
	}
}

class VirtuosoSettingTab extends PluginSettingTab {
	constructor(app: App, private readonly plugin: VirtuosoPlugin) {
		super(app, plugin);
	}

	display() {
		const { containerEl } = this;
		containerEl.empty();
		new Setting(containerEl)
			.setName("Virtuoso executable")
			.setDesc("Absolute path to the installed virtuoso executable")
			.addText((text) =>
				text
					.setPlaceholder("/path/to/acadine-virtuoso/.venv/bin/virtuoso")
					.setValue(this.plugin.settings.cliPath)
					.onChange((value) => {
						void (async () => {
							this.plugin.settings.cliPath = value.trim();
							await this.plugin.saveSettings();
						})();
					}),
			);
		new Setting(containerEl)
			.setName("Virtuoso workspace")
			.setDesc("Absolute path to the local workspace created by virtuoso init")
			.addText((text) =>
				text
					.setPlaceholder("/path/to/virtuoso-workspace")
					.setValue(this.plugin.settings.workspacePath)
					.onChange((value) => {
						void (async () => {
							this.plugin.settings.workspacePath = value.trim();
							await this.plugin.saveSettings();
						})();
					}),
			);
		new Setting(containerEl)
			.setName("Proposal items directory")
			.setDesc("Vault path for optional proposed-item review")
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
