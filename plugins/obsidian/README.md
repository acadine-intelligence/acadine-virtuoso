# Virtuoso Obsidian plugin

Human review queue for Virtuoso learning items, inside Obsidian.

## What it does (only this)

- Ribbon icon + command "Open review queue"
- Lists notes in `07-learning/virtuoso/items/` with
  `schema: virtuoso-learning-item*` and `review_state: proposed`
  (snake_case keys are canonical; legacy hyphenated spellings are tolerated)
- Accept / Reject buttons flip `review_state` to `accepted` / `rejected`
- Command "Cycle today's cards" (cmd+alt+N): rep session over CLI items and
  book deck chapters due today. One scheduler call per card per session; a
  chapter grades at most once per session (first card's rating). Parsing
  lives in `src/parsing.ts`, session grading rules in `src/grading.ts`;
  both are pure modules covered by `npm run test` (vitest).

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
ln -s /path/to/acadine-virtuoso/plugins/obsidian \
      /path/to/vault/.obsidian/plugins/virtuoso
```

Then enable "Virtuoso" in Obsidian's Community Plugins (restricted mode off).

## Publishing note

At a future public cut this directory extracts to its own repo — Obsidian
community listing requires `manifest.json` at a public repo root with a GitHub
release containing `main.js`, `manifest.json`, `versions.json`.
