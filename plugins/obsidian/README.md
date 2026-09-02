# Virtuoso Obsidian plugin

This optional plugin runs a Virtuoso review inside Obsidian. It needs the installed local Virtuoso CLI and a local Virtuoso workspace. It does not need a live agent, server, account, or network connection.

## Set up from a clean checkout

Install the CLI and create a workspace first:

```bash
git clone https://github.com/acadine-intelligence/acadine-virtuoso.git
cd acadine-virtuoso
python3.11 -m venv .venv
.venv/bin/python -m pip install -e .

WORKSPACE="$HOME/my-practice"
.venv/bin/virtuoso --workspace "$WORKSPACE" init
```

Build the plugin:

```bash
cd plugins/obsidian
npm ci
npm run typecheck
npm test
npm run build
```

Copy or symlink this directory into the vault:

```bash
ln -s /path/to/acadine-virtuoso/plugins/obsidian \
  /path/to/vault/.obsidian/plugins/virtuoso
```

Enable Virtuoso under Community plugins in Obsidian. Open the Virtuoso settings and set both paths:

- Virtuoso executable: the absolute path to `.venv/bin/virtuoso`
- Virtuoso workspace: the absolute path to the workspace created by `virtuoso init`

The plugin stores these local paths in its ignored `data.json` file.

## Run a review

Run the command `Virtuoso: Start offline review`.

The plugin loads due and new items from the CLI. Each card supports this flow:

1. Read the prompt and type the first response.
2. Mark whether notes were open.
3. Take one unaided retry if useful.
4. Show the optional hint or reveal the answer.
5. Choose the result and confidence, then record the grade. You can also skip the card.

Before answer reveal, the card shows its focus, explicit project links, and selection reason from the public CLI contract. The plugin does not infer project links from paths or note names.

The plugin keeps the open card snapshot in memory only. It sends every grade and skip to the versioned JSON review contract. The CLI checks the item content hash before it writes and again inside the write transaction. A stale item, schema mismatch, timeout, spawn failure, or nonzero CLI exit keeps the card open and shows a recovery action. The error panel preserves CLI stderr when no typed error envelope is available. The plugin advances only when the success response matches the pending submission, item, content hash, grade, and timestamp. A second click cannot start another write while one is running. A failed write can retry the same request. If a write succeeds and the next card fails to load, the plugin retries only the load and never sends the recorded decision again.

A blank unaided response cannot be marked Demonstrated. The grade controls stay open so the learner can choose Partial or Not demonstrated.

## Ownership boundary

The CLI is the only scheduler and evidence writer. It records a measured plugin attempt with `administered: false`, which stays distinct from an agent-administered attempt with unknown latency. The plugin does not calculate intervals, open SQLite, or keep a durable review cache.

Agent enrichment is outside this review command. Future enrichment can add only its owned Markdown fields through the pure guard in `src/enrichment.ts`. The guard rejects scheduler, attempt, grade, hash, and other evidence fields. Enrichment has no path to the review write contracts.

## Review proposed item notes

The command `Virtuoso: Open review queue` lists notes from the configured proposal items directory when they use a `virtuoso-learning-item` schema and have `review_state: proposed`. Accept and Reject change only the human-owned `review_state` frontmatter field.

## Test and package

```bash
npm run typecheck
npm test
npm run build
```

The plugin source is public in this repository. An Obsidian Community listing needs a dedicated public plugin repository with `manifest.json` at its root. The GitHub release must contain `main.js`, `manifest.json`, and `versions.json`.
