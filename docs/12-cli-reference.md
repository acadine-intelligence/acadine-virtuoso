# CLI reference

Complete reference for the `virtuoso` command-line interface. Every command, flag, choice set, output shape and exit code documented here is derived from `src/virtuoso/cli.py`; if this document and the code disagree, the code is right and this document has a bug.

## Invocation

```
virtuoso --workspace PATH <command> [subcommand] [flags]
```

- `--workspace PATH` is required for every command except `--version`, and always comes before the command. It selects the learner workspace directory created by `init`.
- `--version` prints the package version (`0.1.0.dev0`) and exits 0; no workspace needed.
- Most commands accept `--json`. With `--json`, stdout is a single JSON object (pretty-printed, sorted keys). Without it, output is human-readable `key: value` lines. Agents and scripts should always use `--json`.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 2 | Domain error (`WorkspaceError`, `PracticeError`, `ModuleError`): stderr carries `Error: <plain actionable message>`; also returned for argparse usage errors and any command path that did not produce output |

There are no silent partial failures: commands either complete and return 0 or fail closed with 2 and no state change.

## Workspace

### `init`

Create a simple-mode workspace at `--workspace`.

```
virtuoso --workspace PATH init [--json]
```

JSON output: `{"status": "initialized", "workspace": "<path>"}`. Fails with exit 2 if the workspace already exists or any ancestor is a symlink.

### `doctor`

Check workspace health: database integrity, item freshness, source-link staleness, migration version.

```
virtuoso --workspace PATH doctor [--json]
```

JSON output keys: `status` (`healthy` or `needs-attention`), `database`, `items`, `attempts`, `proposals`, `transfer_events`, `stale_items`, `stale_source_links`, `workspace_schema`, `workload`. The `workload` object (`due_now`, `scheduled_total`, `new_items`) answers "how much is left today": items whose latest proposal is due, items with any schedule, and items never attempted (the new pool `next` draws from). Read-only: never mutates evidence and never silently repairs missing schema objects.

## Items

### `add`

Add one active-recall item. Writes human-owned Markdown under `workspace/items/` and indexes it.

```
virtuoso --workspace PATH add --id ID --title TITLE --focus FOCUS \
  --prompt PROMPT --answer ANSWER [--hint HINT] [--follow-up FOLLOW_UP] [--json]
```

Retire an item when it no longer earns its place: removed from selection and workload counts, Markdown and evidence untouched, reversible only by direct database edit.

```
virtuoso --workspace PATH retire --id ID [--json]
```

JSON output keys: `item_id`, `status` (`retired` on first call, `already-retired` after).

- `--id`: unique lowercase-dash identifier; unsafe or duplicate ids are rejected.
- `--focus`: the learning context / track tag (e.g. `ml-deep-learning`). Selection and scheduling are scoped by context.
- `--prompt`: the retrieval challenge shown before any answer.
- `--answer`: the reference answer, hidden until reveal.
- `--hint`: optional nudge shown only on request.
- `--follow-up`: optional smaller challenge offered after a non-demonstrated result.

JSON output: `{"item_id", "title", "focus", "path"}`.

### `next`

Recommend the next item to practice. Deterministic and explained.

```
virtuoso --workspace PATH next [--focus FOCUS] [--json]
```

JSON output: `{"item_id", "title", "focus", "prompt", "rationale", "alternatives", "uncertainty"}`. The `prompt` is included so a session can start immediately; the answer is never included. `rationale` states why this item was selected; `alternatives` lists the remaining candidates in order. `--focus` restricts the candidate set to one focus track (e.g. `next --focus languages-go`); the rationale names the scope, and a focus with no due or new items fails closed with `Error: no learning item is due in focus '<name>'`.

### `practice`

Run one interactive active-recall session for an item.

```
virtuoso --workspace PATH practice --item ID [--agent-help none|light|substantial|unknown]
```

Interactive protocol (stdout prompts, stdin answers, in order):

1. `Notes open? [y/N]:`: `y` or `n` (default n).
2. The challenge title and prompt are shown. The answer is not.
3. `Your recall:`: free-text attempt. The time to first answer is recorded as `initial_latency_ms`.
4. `Next [retry / hint / reveal]:`: repeats until `reveal` (or a `hint` followed by reveal):
   - `retry`: another unaided attempt, recorded as `retry-unaided`.
   - `hint`: shows the hint (if any), then `Response after hint:`.
   - `reveal`: shows the reference answer (recorded as `worked-feedback`).
5. `Result [demonstrated / partial / not-demonstrated]:`: self-grading. A blank recall cannot be recorded as `demonstrated`; the session fails closed instead.
6. `Confidence [1-5]:`
7. If the result was not `demonstrated` and the item has a follow-up: `Follow-up response:`.

On completion the attempt (with full assistance attribution) and an FSRS 6.3.2 scheduling proposal are persisted atomically, and the next review time is printed. `--agent-help` must honestly record any agent assistance used during the attempt.

### `practice --administer`

Record one agent-administered attempt non-interactively. Use this when the learner answered outside the terminal (chat, voice, another tool) and an agent transcribes the answer and grade. Never pipe scripted stdin into interactive `practice` for this purpose: that records a fabricated near-zero latency and pollutes the evidence.

```
virtuoso --workspace PATH practice --item ID --administer \
  --response TEXT --result demonstrated|partial|not-demonstrated \
  --confidence 1-5 [--agent-help none|light|substantial|unknown] [--json]
```

Contract:

- `--response`, `--result` and `--confidence` are required with `--administer`, and rejected without it.
- `initial_latency_ms` is stored as NULL/unknown. The tool did not measure the answer, so no latency exists; 0 ms is never written and no timing row is created.
- The attempt row carries `administered = 1`, so administered and direct interactive attempts stay distinguishable in every query.
- `--agent-help` defaults to `substantial` in this mode (the agent mediated the whole exchange). Pass a different level only when it honestly applies.
- A blank `--response` cannot be graded `demonstrated`; the command fails closed.
- The same FSRS proposal flow runs as for interactive attempts; the proposal rationale states that latency was unmeasured.

JSON output: `{"event_id", "item_id", "result", "confidence", "agent_help", "administered": true, "initial_latency_ms": null, "occurred_at", "proposal_due_at", "proposal_algorithm"}`.

### `attempts`

Show recorded evidence and scheduler proposals.

```
virtuoso --workspace PATH attempts [--json]
```

JSON output: `{"attempts": [...], "proposals": [...]}`. Attempts carry `result`, `confidence`, `agent_help`, `open_notes`, `initial_latency_ms`, `started_at`/`completed_at`, `administered` (0 direct, 1 agent-administered; administered rows have NULL latency and timing), and `support_json` (the ordered support actions). Proposals carry `algorithm`, `algorithm_version` (`6.3.2`), `learning_context`, `due_at` and `rationale`.

## Sources (read-only Markdown/Obsidian)

Sources are external Markdown roots (e.g. an Obsidian vault folder) that Virtuoso indexes read-only: metadata, wikilinks and content hashes, never note bodies.

### `source add`

```
virtuoso --workspace PATH source add --id ID --kind markdown|obsidian --path ROOT [--json]
```

Connects a read-only source. Rejects symlinked roots, missing roots, duplicates, and roots that overlap the workspace. JSON output: `{"source_id", "kind", "root", "read_only": true}`.

### `source scan`

```
virtuoso --workspace PATH source scan --id SOURCE [--json]
```

Indexes (or re-indexes) the source. JSON output: `{"receipt_id", "source_id", "indexed", "removed", "skipped", "total_bytes", "occurred_at"}`. Bounded: file-count and byte limits fail closed without partial updates; symlinks are never followed.

### `source list` / `source notes`

```
virtuoso --workspace PATH source list [--json]
virtuoso --workspace PATH source notes --id SOURCE [--json]
```

`list` shows connected sources. `notes` lists indexed document metadata: `relative_path`, `title`, `content_hash`, `wikilinks`, `modified_ns`, `byte_size`. Note bodies are never returned.

### `source link`

```
virtuoso --workspace PATH source link --id ID --path PATH --item ITEM [--json]
```

Links a learning item to an indexed source note, binding the item to the note's current content hash. Later `doctor` runs flag the link as stale if the note changes or is replaced by a symlink.

### `source relink`

```
virtuoso --workspace PATH source relink --id ID --path PATH --item ITEM [--json]
```

Rebinds an existing stale item-source link to the note's current content hash. This is the conscious recovery path after a note edit: `source link` refuses a pair that already exists, so editing a linked note otherwise leaves the link permanently stale. `relink` refuses a link that is not stale and a note that is not indexed (scan first). It never edits the source.

### `source unlink`

```
virtuoso --workspace PATH source unlink --id ID --path PATH --item ITEM [--json]
```

Removes one item-source link when its note moved away or was deleted — the recovery path after a vault rename, where `relink` cannot help because the old path no longer exists to rebind. Fails closed if no such link exists. The item, its Markdown, and any recorded evidence are untouched; only the link row goes. Pair it with `source link` at the new path to carry provenance across a rename.

## Candidates (metadata-only structural proposals)

Candidates are proposal-only records derived from source metadata: unresolved wikilinks, ambiguous links, and practice opportunities. There is no apply path: `candidate decide` records a human accept/reject decision as append-only evidence, and acting on an accepted proposal (drafting the note, answering the practice item) remains human work outside Virtuoso.

### `candidate generate`

```
virtuoso --workspace PATH candidate generate --source SOURCE --path RELATIVE_PATH [--limit N] [--json]
```

Generates candidates for one indexed note (default limit 20). Output is a run record (`virtuoso/review-candidate-run@0.1`) containing the created candidates. Generation is deterministic and idempotent for an unchanged snapshot; every candidate carries `claims_mastery: false`.

### `candidate list` / `candidate show` / `candidate decide`

```
virtuoso --workspace PATH candidate list [--source S] [--kind atomic-note|link|practice] [--run RUN] [--current-only] [--json]
virtuoso --workspace PATH candidate show --id CANDIDATE_ID [--json]
virtuoso --workspace PATH candidate decide --id CANDIDATE_ID --decision accept|reject [--note TEXT] [--json]
```

`list` filters candidates; `--current-only` excludes candidates superseded by newer runs. `show` prints one candidate's full proposal payload. `decide` records a human accept or reject as append-only evidence: the proposal row stays immutable, one decision per candidate, and nothing is auto-applied — accepted proposals still need human drafting (every proposal carries `requires_human_drafting` or `requires_human_answer`). After deciding, `review_state` reads `accepted` or `rejected` in list/show output.

## Transfer evidence (project application)

Transfer commands record real-world application of learned concepts: immutable, attributed events and delayed capability checks. They never change memory scheduling, never rank projects, and never claim mastery (`claims_mastery` is always false).

### `transfer record`

```
virtuoso --workspace PATH transfer record --item ITEM --project PROJECT \
  --use-case TEXT --outcome successful|partial|unsuccessful \
  --independence independent|guided|agent-produced|unknown \
  [--artifact REF] [--reflection TEXT] [--json]
```

Appends one transfer event: what was applied, where, the outcome, and how independently the learner did it. Output is the event record including `event_id` and the inherited `delayed_check_due_at`.

### `transfer list`

```
virtuoso --workspace PATH transfer list [--json]
```

Lists all transfer events (append-only; `{"events": [...]}`).

### `transfer check create`

```
virtuoso --workspace PATH transfer check create --event EVENT_ID \
  --context-kind changed|novel --context TEXT --prompt TEXT \
  --acceptance-criteria TEXT --scorer-kind self|human|tool|agent \
  --scorer-reference REF [--json]
```

Creates a delayed check against an existing transfer event: a concrete challenge in a changed or novel context, with acceptance criteria and a named scorer. The due date is inherited from the event; creation timestamps are validated against the event chronology.

### `transfer check due`

```
virtuoso --workspace PATH transfer check due [--as-of ISO_TIMESTAMP] [--json]
```

Lists incomplete checks whose evidence date is due (default: now). `--as-of` must be timezone-aware (`Z` or offset accepted).

### `transfer check begin`

```
virtuoso --workspace PATH transfer check begin --check CHECK_ID --prediction TEXT [--json]
```

Records a pre-attempt prediction. Must happen before attempting the challenge or requesting help; prediction-before-attempt is the integrity rule that keeps the evidence meaningful.

### `transfer check complete`

```
virtuoso --workspace PATH transfer check complete --check CHECK_ID --attempt TEXT \
  --assistance none|light|substantial|unknown [--assistance-detail TEXT] \
  --acceptance-evidence TEXT --teach-back TEXT \
  --outcome successful|partial|unsuccessful [--artifact REF] [--json]
```

Appends the attributed completion: what was attempted, how much help was used, the evidence against the acceptance criteria, and a teach-back. Single-insert and concurrency-safe; a completion without a prior prediction is rejected. Completion changes no capability or mastery state.
