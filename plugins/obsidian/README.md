# Virtuoso Obsidian plugin

Human review queue for Virtuoso learning items, inside Obsidian.

## What it does (only this)

- Ribbon icon + command "Open review queue"
- Lists notes in `07-learning/virtuoso/items/` with
  `schema: virtuoso-learning-item*` and `review-state: proposed`
- Accept / Reject buttons flip `review-state` to `accepted` / `rejected`

That single frontmatter flip is the **entire write surface**. Scheduling,
intervals, and evidence stay with the Virtuoso CLI; flashcards stay with the
Obsidian Spaced Repetition plugin. This plugin never touches schedule state —
that boundary is the 2026-07-24 architecture decision.

## Build

```bash
npm install
npm run typecheck
npm run build   # emits main.js for release
```

## Install (dev)

Symlink or copy this directory into your vault:

```bash
ln -s ~/projects/acadine-virtuoso/plugins/obsidian \
      ~/vaults/acadine-core/.obsidian/plugins/virtuoso
```

Then enable "Virtuoso" in Obsidian's Community Plugins (restricted mode off).

## Publishing note

At a future public cut this directory extracts to its own repo — Obsidian
community listing requires `manifest.json` at a public repo root with a GitHub
release containing `main.js`, `manifest.json`, `versions.json`.
