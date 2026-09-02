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
- Before answer reveal, item cards show the explicit focus, any explicit
  project identifiers, and the latest scheduler rationale when available.
  Project identifiers come only from `project_id` frontmatter or linked
  transfer events. The plugin never infers them from paths or note names.
- The optional context lookup reads `next --json`, `attempts --json`, and
  `transfer list --json`. The exact selection reason takes precedence for
  the selected item; its latest scheduler rationale is the fallback. Missing
  or malformed context does not block the practice session.

That single frontmatter flip is the **entire write surface**. Scheduling,
intervals, and evidence stay with the Virtuoso CLI; flashcards stay with the
Obsidian Spaced Repetition plugin. This plugin never touches schedule state.
That boundary comes from the 2026-07-24 architecture decision.

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

The plugin source is public inside this repository. An Obsidian Community
listing requires a dedicated public plugin repository with `manifest.json` at
its root and a GitHub Release containing `main.js`, `manifest.json`, and
`versions.json`.
