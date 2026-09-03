# CLI reference

Complete reference for the `virtuoso` command-line interface. Every command, flag, choice set, output shape and exit code documented here is derived from `src/virtuoso/cli.py`; if this document and the code disagree, the code is right and this document has a bug.

## Invocation

```
virtuoso --workspace PATH <command> [subcommand] [flags]
```

- `--workspace PATH` is required for every command except `--version`, and always comes before the command. It selects the learner workspace directory created by `init`.
- `--version` prints the package version (`0.1.0.dev0`) and exits 0; no workspace needed.
- Most commands accept `--json`. On success, `--json` makes stdout a single JSON object (pretty-printed, sorted keys). Without it, output is human-readable `key: value` lines. Agents and scripts should always use `--json`.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 2 | Domain or contract error in the `VirtuosoError` family (`WorkspaceError`, `PracticeError`, `ModuleError`, `SearchError`, `QueryError`, or `ReviewError`). Most commands keep stdout empty and write `Error: <plain actionable message>` to stderr. `review ... --json` writes a `virtuoso/review-error@0.1` object with a recovery value. SQLite errors use `Error: database unavailable: <message>`. Argparse usage errors and command paths that produce no result also return 2. |

Commands either complete and return 0 or return 2 without a partial state change.

### Search input behavior

`search lex --query` treats the query as plain text. Whitespace separates required terms, and every term is escaped before FTS5 receives it. Apostrophes, plus signs, hyphens, quotes, leading minus signs, column-like text, and words such as `NOT` have no FTS operator meaning. Porter stemming still applies. Snippets come from the column that matched, including titles.

`search embed` and `search sem` require a JSON array containing finite numbers. Invalid JSON, a non-array value, a non-numeric value, a zero vector, or a dimension mismatch follows the same exit-2 stderr contract. Error output stays out of stdout even when `--json` is present.

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

### `review` JSON contracts

The Obsidian plugin and other local interfaces use this versioned contract. Always pass `--json`. The CLI remains the only scheduler and evidence writer.

List due and new items without answer content:

```
virtuoso --workspace PATH review due --json
```

Output schema: `virtuoso/review-queue@0.1`. Each item has `item_id`, `content_hash`, `focus`, `project_ids`, `selection_reason`, `status` (`due` or `new`), and `due_at`. The selection reason uses the same scheduler rule as `next`. Project IDs come from explicit transfer records. New items use `null` for `due_at`. Future items stay outside the queue.

Load one content snapshot:

```
virtuoso --workspace PATH review load --item ID --json
```

Output schema: `virtuoso/review-item@0.1`. The `item` object contains `item_id`, `title`, `focus`, `content_hash`, `prompt`, `answer`, optional `hint`, optional `follow_up`, and `learning_context`. An interface must keep the answer hidden until the learner asks to reveal it.

Record one measured direct attempt by sending a JSON object on stdin:

```
printf '%s' "$REQUEST_JSON" | \
  virtuoso --workspace PATH review record --json
```

Request schema: `virtuoso/review-attempt@0.1`. It requires these exact fields:

- `submission_id`: 32 lowercase hexadecimal characters. Retrying uses the same value.
- `item_id` and `item_content_hash`: the identity returned by `review load`.
- `started_at`, `initial_answered_at`, and `completed_at`: timezone-aware timestamps measured by the interface. The CLI derives `initial_latency_ms` from the first two timestamps.
- `initial_response`: the typed first response.
- `retry`: `null` or one object with `response` and measured `latency_ms`.
- `hint_used` and `answer_revealed`: assistance facts. Grading requires `answer_revealed: true`.
- `result`: `demonstrated`, `partial`, or `not-demonstrated`.
- `confidence`: integer 1 through 5.
- `open_notes`: whether notes were open during recall.

Output schema: `virtuoso/review-attempt-result@0.1`. The attempt carries `administered: false`, measured latency, result, and item hash. The proposal carries the FSRS algorithm, version, and due time. Core code writes the attempt, proposal, and scheduler state in one SQLite transaction.

Record a skip by sending a JSON object on stdin:

```
printf '%s' "$SKIP_JSON" | \
  virtuoso --workspace PATH review skip --json
```

Request schema: `virtuoso/review-skip@0.1`. It requires `submission_id`, `item_id`, `item_content_hash`, timezone-aware `occurred_at`, and `surface: "obsidian-plugin"`. Output schema: `virtuoso/review-skip-result@0.1`. The CLI appends the skip event and leaves scheduler state unchanged.

Both write commands validate the current item content hash during request handling and again inside the SQLite transaction before commit. A changed item fails before the CLI commits an attempt, proposal, scheduler transition, or skip. Malformed input and unknown schemas also fail before a write.

JSON failures use `virtuoso/review-error@0.1` on stderr:

```
{"schema":"virtuoso/review-error@0.1","error":{"code":"stale-content","message":"...","recovery":"reload-item"}}
```

CLI error codes are `invalid-request`, `stale-content`, `record-failed`, `skip-failed`, `already-recorded`, `workspace-busy`, and `workspace-error`. Each code has one fixed recovery value: `check-contract`, `reload-item`, `retry-submit`, `advance-card`, or `check-settings`. Exit code 2 still signals the failure. The interface must keep the current card open until the learner takes the recovery action.

### `attempts`

Show recorded evidence and scheduler proposals.

```
virtuoso --workspace PATH attempts [--json]
```

JSON output: `{"attempts": [...], "proposals": [...], "skips": [...]}`. Attempts carry `result`, `confidence`, `agent_help`, `open_notes`, `initial_latency_ms`, `started_at`/`completed_at`, `administered` (0 direct, 1 agent-administered; administered rows have NULL latency and timing), and `support_json` (the ordered support actions). Proposals carry `algorithm`, `algorithm_version` (`6.3.2`), `learning_context`, `due_at` and `rationale`. Skips carry the item hash, timestamp, and source surface. A skip never changes scheduler state.

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

Indexes (or re-indexes) the source. JSON output: `{"receipt_id", "source_id", "indexed", "removed", "skipped", "total_bytes", "occurred_at"}`. Bounded: file-count and byte limits fail closed without partial updates; symlinks are never followed. For an `obsidian` source, the scanner prunes directory components named `.obsidian` or `.trash`, counts their Markdown paths as `skipped`, and does not open those files or charge them to scan budgets. A generic `markdown` source does not apply these Obsidian-specific exclusions.

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

## Candidates and reviewed import

The candidate queue has two adapters. The default `structural` adapter uses indexed metadata to propose unresolved links, ambiguous links and answer-free practice. The `curriculum` adapter reads one explicitly selected note and proposes complete practice items. Both adapters bind each proposal to the exact source hash.

### `candidate generate`

```
virtuoso --workspace PATH candidate generate --source SOURCE --path RELATIVE_PATH \
  [--adapter structural|curriculum] [--limit N] [--dry-run] [--json]
```

Generation is deterministic for the same adapter, source snapshot and limit. `--dry-run` validates and reports the exact candidate set without writing candidate rows. Normal generation is idempotent and reuses the existing run for an unchanged snapshot.

The curriculum adapter accepts either a complete `virtuoso/item@0.1` note or a `virtuoso/curriculum@0.1` note containing one or more fenced `virtuoso-practice` JSON objects. Each object uses `virtuoso/practice-item@0.1` and declares `id`, `title`, `focus`, `prompt`, `answer`, nullable `hint`, nullable `follow_up`, `state: active`, and nullable `historical_due_at`. Unknown fields, duplicate ids, unsupported schemas and unsupported states fail before candidate rows are written.

The explicit `--adapter curriculum` flag authorizes Virtuoso to read the selected note body for that run. The source index remains metadata-only. Candidate rows retain the declared item fields because a human must review them. Virtuoso does not send the content anywhere.

### `candidate delta`

```
virtuoso --workspace PATH candidate delta --source SOURCE --path RELATIVE_PATH \
  [--limit N] [--json]
```

`delta` checks one curriculum note. It validates changed content before refreshing the source index. It writes a candidate run only for a new source hash. An unchanged source exits 0 with empty stdout and stderr. Concurrent checks of the same new hash store and report one run. Changed content creates a new run; older proposals and their decisions stay in history and report a changed source status.

### `candidate list`, `candidate show`, and `candidate decide`

```
virtuoso --workspace PATH candidate list [--source S] [--kind atomic-note|link|practice] [--run RUN] [--current-only] [--json]
virtuoso --workspace PATH candidate show --id CANDIDATE_ID [--json]
virtuoso --workspace PATH candidate decide --id CANDIDATE_ID \
  --decision accept|edit|skip|reject \
  [--item-id ID] [--title TEXT] [--focus TEXT] [--prompt TEXT] \
  [--answer TEXT] [--hint TEXT] [--follow-up TEXT] [--note TEXT] [--json]
```

`list` filters candidates. `--current-only` omits proposals whose source hash no longer matches. `show` prints one proposal and its decision state.

For a curriculum import candidate, `accept` creates the proposed Markdown item and source link in one transaction. `edit` applies the supplied item-field changes and creates that reviewed item. `skip` records the choice without creating an item. `reject` records a hard rejection. Accepted and edited imports create no attempt, transfer event, scheduler state or scheduler proposal. A historical due value stays in the immutable proposal and does not become a live due date.

Structural candidates keep their original proposal-only behavior. Accepting one records the decision without drafting an answer or creating an item.

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
