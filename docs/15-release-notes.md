# Release notes

## v0.2.0 — Obsidian rep sessions (cmd+alt+N) and hardened parsing

Date: 2026-08-22 (Africa/Johannesburg)
Scope: two commits on `main` since `ea411aa` (origin/main): `2ba23cc` then `e77a0bd`.
State: consolidated locally, nothing pushed. Push and any org move stay Jonathan's gate.
SDLC chain: implementation → review t_7daa61da (CHANGES_NEEDED) → fix lane t_9dc44a77 → re-review t_4a45f350 (APPROVED, 0 blocking / 3 low nonblocking) → this release lane t_1f6b974f.

### 2ba23cc — due-card cycling session (cmd+alt+N)

New "Cycle today's cards" command: a full rep session over CLI-due items and deck chapters, one card at a time. Prompt first, Space reveals, grade again/hard/good/easy or skip; "again" requeues in-session for practice.

- All grades shell out to `virtuoso.py` (`deck-rep` / `review`): the plugin stays schedule-free and the ledger keeps a single writer.
- Q/A split for deck cards so answers stay hidden until reveal.
- Review-queue command retained; settings gain a scheduler CLI path.

### e77a0bd — NB fixes + parser/grading test pack

Fixes the four findings from the first review (t_7daa61da), verified behaviorally in re-review t_4a45f350:

- **NB-1** Canonical snake_case frontmatter keys (`item_id`, `review_state`, ...) are now primary in `loadProposed`, `loadDueCards` and `decide()`; hyphenated spellings tolerated as legacy aliases via `fmKey()`. Due loader resolves real vault notes (verified: 5/5 items, 4 proposed visible).
- **NB-2** One chapter grade per session. The first rating on any card of a chapter fires the chapter `deck-rep`; later cards render practice-only. `GradeGate` (`src/grading.ts`) is the pure gate, unit-tested with a counting double.
- **NB-3** One scheduler call per card per session. "Again" requeues the card in-session, but only the first answer ever reaches the CLI.
- **NB-4 (opportunistic)** Item lookup builds one `item_id -> note` map per folder pass instead of O(items x due) vault reads.
- **NB-6** Vitest test pack, 23 tests: deck card splitting, due-output section parsing (REVIEWS DUE / BOOK DECK DUE split, bracketed ids), frontmatter key handling including the `item_id` regression, and GradeGate NB-2/NB-3 semantics. Parsing extracted to `src/parsing.ts` (pure, no Obsidian imports).

### Acceptance gates (this lane)

- `npm run typecheck` — tsc clean (exit 0)
- `npm run test` — vitest 23/23 (parsing 17 + grading 6, exit 0)
- `npm run build` — esbuild `main.js` 16.8kb (exit 0)
- `.venv/bin/python -m pytest tests/ -q` — 158 passed + 78 subtests (exit 0)

### Known non-blocking findings (carried from re-review t_4a45f350)

- R2-1 (low): unused `section` helper, `parsing.ts:96`
- R2-2 (low): proposed-id split depends on the em dash, `parsing.ts:114-116` (current CLI format always emits it)
- R2-3 (low): hardcoded deck path `main.ts:325` mirrors the `virtuoso.py:32` CLI constant

None gating; fix-forward in a future lane.

### Next

Push `main` and tag `v0.2.0` to origin, and any acadine-intelligence org move: Jonathan decides.
