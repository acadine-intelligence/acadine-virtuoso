# CLI reference

Complete reference for the `virtuoso` command-line interface. Every command, flag, choice set, output shape and exit code documented here is derived from `src/virtuoso/cli.py`; if this document and the code disagree, the code is right and this document has a bug.

## Invocation

```
virtuoso --workspace PATH <command> [subcommand] [flags]
```

- `--workspace PATH` is required for every command except `--version`, and always comes before the command. It selects the learner workspace directory created by `init`.
- `--version` prints the package version (`0.1.0`) and exits 0; no workspace is needed.
- Most commands accept `--json`. On success, `--json` makes stdout a single JSON object (pretty-printed, sorted keys). Without it, output is human-readable `key: value` lines. Agents and scripts should always use `--json`.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 2 | Domain or contract error in the `VirtuosoError` family (`WorkspaceError`, `LearningError`, `PracticeError`, `ModuleError`, `SearchError`, `QueryError`, or `ReviewError`). Most commands keep stdout empty and write `Error: <plain actionable message>` to stderr. `review ... --json` writes a `virtuoso/review-error@0.1` object with a recovery value. SQLite errors use `Error: database unavailable: <message>`. Argparse usage errors and command paths that produce no result also return 2. |

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

JSON output keys: `status` (`healthy` or `needs-attention`), `database`, `items`, `attempts`, `proposals`, `transfer_events`, `study_events`, `learning`, `scheduler`, `stale_items`, `stale_source_links`, `legacy_files`, `workspace_schema`, `workload`. Each `legacy_files` entry has `path`, `reason`. The `workload` object (`due_now`, `scheduled_total`, `new_items`) reports current scheduler state. The `learning` object reports `waiting_for_learning` and `ready_for_practice` across active items. The `scheduler` object reports the configured `algorithm`, `algorithm_version`, `learning_context`, `configuration`, `configuration_error` (the validation message when the `scheduler` block carries an unknown algorithm or another algorithm's keys, otherwise `null`), `unrecorded_switch_from` (an algorithm that still holds state after `virtuoso.json` was edited by hand, or `null`), `last_switch`, and `last_switch_matches_configuration` (`false` when the newest recorded switch names another algorithm than the file). A configuration error, an unrecorded switch, or a disagreeing ledger makes the status `needs-attention`; `doctor` reports these instead of failing. `study_events` counts all retained study history. The command is read-only. It never repairs missing schema objects or changes evidence.

### `scheduler`

Inspect the spaced-repetition algorithm or adopt another built-in one.

```
virtuoso --workspace PATH scheduler show [--json]
virtuoso --workspace PATH scheduler configure --minimum-interval-days DAYS [--json]
virtuoso --workspace PATH scheduler switch --to ALGORITHM [--json]
virtuoso --workspace PATH scheduler history [--json]
```

Built-in algorithms: `fsrs` (default, version `6.3.2`, configuration `desired_retention`, `enable_fuzzing`, and optional `minimum_interval_days`) and `sm2` (version `sm2-1990/1`, configuration `first_interval_days`, `second_interval_days`, `minimum_easiness`). `virtuoso.json` selects the algorithm under `scheduler.algorithm`; every other `scheduler` key except `context` belongs to that algorithm and is validated by it. Keys from another algorithm fail with exit 2.

`scheduler show` output schema: `virtuoso/scheduler-settings@0.1` with `algorithm`, `algorithm_version`, `learning_context`, `configuration`, and `built_in_algorithms`.

Changing `scheduler.algorithm` by hand on a workspace that already holds state for another algorithm in the same context fails closed: `practice`, `review record`, `review due`, `next`, `compose`, `queries workload`, and `scheduler show` exit 2 with `scheduler algorithm changed from A to B without a recorded switch; run: virtuoso scheduler switch --to B`, and `doctor` reports `needs-attention`. No evidence is written while the guard holds.

`scheduler switch --to ALGORITHM` validates the target, appends one `virtuoso/scheduler-switch@0.1` row (`switch_id`, `from_algorithm`, `to_algorithm`, `learning_context`, `mode`, `items_with_prior_state`, `occurred_at`), and rewrites the `scheduler` block of `virtuoso.json` with the target's default configuration. The row and the file change land together or not at all. Mode is `fresh`: the target sees every item as new at its first attempt; the previous algorithm's state and proposals stay as history and remain visible in `attempts`. No memory parameters are converted between algorithms. Switching to the algorithm the file already names fails with exit 2 unless `doctor` reports an unrecorded switch or a disagreeing ledger; the switch then records the algorithm that holds state, or the ledger's newest algorithm, as `from_algorithm` so the ledger is repaired honestly.

`scheduler history` output schema: `virtuoso/scheduler-history@0.1` with the chronological `switches` list. Switch rows reject update and deletion.

#### Minimum FSRS interval

`scheduler configure --minimum-interval-days DAYS` atomically updates only that setting in `virtuoso.json`. It accepts integers from 0 through 36500 and requires FSRS. Invalid input exits 2 without changing configuration or evidence. The JSON response uses `virtuoso/scheduler-settings@0.1` with `algorithm`, `algorithm_version`, `learning_context`, `configuration`, `built_in_algorithms`, and `existing_due_dates_changed: false`.

Set `1` for a minimum of 24 elapsed hours after an attempt. Set `7` for a week. Set `0` to keep FSRS's own timing, including minute-scale learning steps. Omission also means zero; existing configurations and proposal payloads keep their original shape until you set a minimum.

The FSRS backend computes the review first, including any fuzzing, then uses the later of its due time and `attempt.occurred_at + DAYS`. This includes learning and relearning after a failed attempt. The serialized card and proposal share that effective due time. A positive setting adds the configured minimum, original due time, and effective due time to the rationale. The memory parameters and rating mapping remain FSRS outputs.

Changing only the minimum preserves existing memory state; the next recorded attempt uses the new setting. Existing due dates and previous proposals remain unchanged, so an item already due can still appear immediately. Other scheduler configuration changes and version mismatches retain the existing incompatibility checks. If the minimum changes between proposal generation and recording, the write fails with a retry instruction. `scheduler history` continues to list algorithm switches only. Switching algorithms resets the target configuration to its defaults, including an omitted minimum for FSRS.

The minimum is a scheduling preference around FSRS. Delaying a review beyond its recommended time may reduce actual retention. SM-2 does not accept this setting. No database migration or retrospective rescheduling runs.

## Items

### `add`

Add one recall-first or learn-first item. Virtuoso writes human-owned Markdown under `workspace/items/` and indexes it.

```
virtuoso --workspace PATH add --id ID --title TITLE --focus FOCUS \
  --prompt PROMPT --answer ANSWER [--hint HINT] [--follow-up FOLLOW_UP] \
  [--entry-mode recall-first|learn-first] [--learning-unit TEXT] [--json]
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
- `--entry-mode`: defaults to `recall-first`. Use `learn-first` when the material is unfamiliar and needs explicit study before recall.
- `--learning-unit`: required with `learn-first` and rejected with `recall-first`. It contains the explanation and examples shown by `learn`.

Recall-first items retain `virtuoso/item@0.1`. Learn-first items use `virtuoso/item@0.2`, add `entry-mode: learn-first`, and require `# Learning unit`, `# Prompt`, and `# Answer` sections. The learning-unit hash covers the parsed, trimmed learning prose. JSON output: `{"item_id", "title", "focus", "path", "content_hash", "entry_mode", "learning_unit_hash"}`.

### `next`

Recommend the next learning or practice action. Selection is deterministic and explained.

```
virtuoso --workspace PATH next [--focus FOCUS] [--json]
```

Output schema: `virtuoso/next-action@0.1`. The flat envelope contains `action` (`learn` or `practice`), `item_id`, `title`, `focus`, `item_content_hash`, `learning_unit_hash`, `prompt`, `rationale`, `alternatives`, and `uncertainty`. A learn action returns `prompt: null`. A practice action returns the prompt so recall can begin. The answer and learning prose are never returned by `next`. `alternatives` retains the remaining item ids in rank order. `--focus` restricts the candidate set to one focus track. A focus with no due or new items fails closed with `Error: no learning item is due in focus '<name>'`.

### `compose`

Compose one evidence-aware practice proposal from current workspace evidence. The proposal identifies one primary challenge, cites the source events and item hashes used, explains skipped material, and states uncertainty. It never exposes the answer, hint, follow-up, or learning-unit prose before an attempt.

```
virtuoso --workspace PATH compose [--focus FOCUS] [--json]
```

Output schema: `virtuoso/focus-proposal@0.1`. The proposal contains `proposal_id`, `focus_scope`, `action` (`learn` or `practice`), `primary` (item id, title, focus, item content hash, learning unit hash, prompt for practice or null for learn), `source_event_ids`, `skipped` (each with `item_id`, `item_content_hash`, `reason`, `source_event_ids`), `alternatives`, `uncertainty`, `rationale`, and `occurred_at`.

Selection policy: a pending learn-first item (no matching current study event) produces a `learn` proposal in deterministic item-id order. Otherwise the composition reads current schedules and attempt evidence: it targets the newest recorded gap (non-demonstrated result or attributable assistance such as a hint, worked feedback, follow-up, or administered attempt) across due, new, and scheduled items; otherwise it falls back to the deterministic due-then-new order used by `next`. A demonstrated item is skipped only with a traceable reason and cited attempt. Missing evidence produces the deterministic selection with an explicit `uncertainty` note. The same workspace snapshot, clock, and request produce the same proposal.

Record the learner's decision before practicing from a proposal:

```
virtuoso --workspace PATH compose decide --id PROPOSAL_ID --decision accept|change|reject [--chosen-item ID] [--reason TEXT] [--json]
virtuoso --workspace PATH compose show --id PROPOSAL_ID [--json]
virtuoso --workspace PATH compose list [--status pending|decided|all] [--limit N] [--json]
```

`compose decide` output schema: `virtuoso/learner-decision@0.1`. `accept` uses the proposal primary; `change` requires a chosen active item from the same focus; `reject` takes no chosen item. One decision per proposal: a second decision fails with exit 2. At decide time the proposal's cited item hashes are revalidated against current workspace state; a stale hash fails closed and writes nothing. A decision appends only its own record: no attempt, scheduler, transfer, review-skip, capability, or mastery evidence. Practice from a proposal means invoking the existing `practice --item ID` with the decided item id.

### `benchmark`

Turn one failed benchmark criterion into human practice. The benchmarked system owns the artifact; Virtuoso stores the import as append-only evidence.

```
virtuoso --workspace PATH benchmark import --file ARTIFACT.json [--source-reference REF] [--json]
virtuoso --workspace PATH benchmark propose [--json]
virtuoso --workspace PATH benchmark rerun --file ARTIFACT.json --baseline RUN_ID [--json]
virtuoso --workspace PATH benchmark export --run-id RUN_ID [--json]
```

`benchmark import` output schema: `virtuoso/benchmark-run@0.1`. The artifact must carry `run_id`, `source_reference`, `tested_commit`, `harness`, `harness_version`, `model_id`, `prompt_hash`, `tool_permissions`, `environment`, `operating_level_map_version` (`opmap@1`), `occurred_at`, and normalized `observations` (each with `criterion`, `level`, `status`, `metric`, `value`). Malformed JSON, unknown schema, unknown operating level, duplicate `run_id`, and a changed source hash for the same `source_reference` fail closed without changing prior state. Local filesystem paths are rejected as `source_reference`.

`benchmark propose` composes one ordinary `FocusProposal` from the earliest failed observation not yet proposed (ordering: run `occurred_at`, then `criterion`, then `run_id`). The proposal adds a `benchmark` object naming the run, failed criterion, operating level, rerun condition, and cites `benchmark:<run_id>` in `source_event_ids`. The primary item is matched by focus equal to the criterion; a missing compatible item fails with guidance. When no valid benchmark run exists, ordinary composition still works.

`benchmark rerun` links the run to its baseline and reports per-criterion `metric` change. Matching requires the same criterion and metric; a missing counterpart reports `metric-missing`. Changed `tested_commit`, `harness`, `harness_version`, `model_id`, `prompt_hash`, `tool_permissions`, or `environment` each produce a specific comparability warning. The report carries `claims_mastery: false` always; a passing rerun never promotes capability or mastery.

`benchmark export` emits the run with normalized fields only and `redacted: true`. Private paths and learner content are excluded by construction; provenance is shared only when the user runs this export deliberately.

### `learn`

Read and explicitly finish one current learn-first item version.

```
virtuoso --workspace PATH learn --item ID
```

The command displays the exact learning unit. It does not show the recall prompt or answer and does not start a recall timer. At `Finish this learning step? [finish / stop]:`, `finish` appends one hash-bound study event. Read output carries `claims_mastery: false` as a constant semantic boundary. `stop` exits 0 without writing an event. EOF or keyboard interruption returns exit 2 without writing an event. A completed study event changes no attempt, scheduler, transfer, capability, or mastery state. Running `learn` again for the same exact version fails clearly. A changed item or learning-unit hash requires a new learning completion while preserving old history.

### `practice`

Run one interactive active-recall session for an item. A pending learn-first item is rejected before the prompt loop begins.

```
virtuoso --workspace PATH practice --item ID [--agent-help none|light|substantial|unknown]
```

Interactive protocol (stdout prompts, stdin answers, in order):

0. Context lines before the first interaction: `Focus: <focus>` (when the item has a focus), `Projects: <ids>` (only from explicit transfer records), and `Why now: <reason>` (when a reason is available). The reason mirrors the review queue rule when the item is the queue's first entry (`Selected the earliest due item; ties use item id.` or `Selected a new item in deterministic item-id order.`) and reads `Selected by explicit item request.` otherwise. Display only: no state changes.
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

On completion the attempt (with full assistance attribution) and a scheduling proposal from the configured algorithm (FSRS 6.3.2 by default; see `scheduler`) are persisted atomically, and the next review time is printed. `--agent-help` must honestly record any agent assistance used during the attempt.

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
- The same scheduler proposal flow runs as for interactive attempts; the proposal rationale states that latency was unmeasured.

JSON output: `{"event_id", "item_id", "result", "confidence", "agent_help", "administered": true, "initial_latency_ms": null, "occurred_at", "proposal_due_at", "proposal_algorithm"}`.

### `review` JSON contracts

The Obsidian plugin and other local interfaces use this versioned contract. Always pass `--json`. The CLI remains the only scheduler and evidence writer.

List due and new items without answer content:

```
virtuoso --workspace PATH review due --json
```

Output schema: `virtuoso/review-queue@0.1`. Each item has `item_id`, `content_hash`, `focus`, `project_ids`, `selection_reason`, `status` (`due` or `new`), and `due_at`. The selection reason uses the same scheduler rule as `next`. Project IDs come from explicit transfer records. New items use `null` for `due_at`. Future items and learn-first items waiting for study stay outside the queue.

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

Output schema: `virtuoso/review-attempt-result@0.1`. The `attempt` object contains `event_id`, `item_id`, `item_content_hash`, `result`, `confidence`, `initial_latency_ms`, `administered`, and `occurred_at`. The `proposal` object contains `proposal_id`, `algorithm`, `algorithm_version`, and `due_at`. Core code writes the attempt, proposal, and scheduler state in one SQLite transaction.

Record a skip by sending a JSON object on stdin:

```
printf '%s' "$SKIP_JSON" | \
  virtuoso --workspace PATH review skip --json
```

Request schema: `virtuoso/review-skip@0.1`. It requires `submission_id`, `item_id`, `item_content_hash`, timezone-aware `occurred_at`, and `surface: "obsidian-plugin"`. Output schema: `virtuoso/review-skip-result@0.1`. Its `skip` object contains `event_id`, `item_id`, `item_content_hash`, `occurred_at`, and `surface`. The CLI appends the skip event and leaves scheduler state unchanged.

Both write commands validate the current item content hash during request handling and again inside the SQLite transaction before commit. They reject a learn-first item that has no matching study event. A changed item fails before the CLI commits an attempt, proposal, scheduler transition, or skip. Malformed input and unknown schemas also fail before a write.

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

JSON output: `{"attempts": [...], "proposals": [...], "skips": [...], "study_events": [...]}`. Attempts carry `result`, `confidence`, `agent_help`, `open_notes`, `initial_latency_ms`, `started_at`/`completed_at`, `administered` (0 direct, 1 agent-administered; administered rows have NULL latency and timing), and `support_json` (the ordered support actions). Proposals carry `algorithm`, `algorithm_version` (`6.3.2` for `fsrs`, `sm2-1990/1` for `sm2`), `learning_context`, `configuration`, `due_at` and `rationale`. Proposals made under a previous algorithm stay listed after a `scheduler switch`. Skips carry the item hash, timestamp, and source surface. Study events carry the exact item and learning-unit hashes, occurrence time, and source surface. Study and skip events never change scheduler state.

## Read-only analytics

Every `queries` command opens SQLite in read-only mode and leaves workspace state unchanged. Use `--json` for the versioned response contract.

### `queries focus`

```
virtuoso --workspace PATH queries focus [--json]
```

Output schema: `virtuoso/focus-performance@0.1`. The top-level `focuses` array contains `focus`, `items`, `attempts`, `demonstrated`, `partial`, `not_demonstrated`, `administered`, `mean_confidence`, and `mean_latency_ms`. A mean is `null` when no qualifying value exists.

### `queries history`

```
virtuoso --workspace PATH queries history --item ITEM [--json]
```

Output schema: `virtuoso/item-history@0.1`. The response contains `item_id` and `attempts`. Each attempt contains `event_id`, `item_id`, `occurred_at`, `result`, `confidence`, `agent_help`, `administered`, and `latency_ms`. An administered attempt has `latency_ms: null`.

### `queries workload`

```
virtuoso --workspace PATH queries workload [--json]
```

Output schema: `virtuoso/workload-by-focus@0.1`. The top-level `focuses` array contains `focus`, `items`, `scheduled`, and `due_now`. Workload uses the configured scheduler algorithm and learning context. It counts only the proposal selected by current scheduler state, so proposal history cannot inflate the result.

### `queries learning`

```
virtuoso --workspace PATH queries learning [--json]
```

Output schema: `virtuoso/learning-state@0.1`. The top-level `items` array contains each active item's `item_id`, `focus`, `entry_mode`, `item_content_hash`, `learning_unit_hash`, typed `action`, `reason_code`, `rationale`, and matching `study_completed_at`. A study event counts only when both current hashes match. This command opens SQLite read-only.

### `queries stale-links`

```
virtuoso --workspace PATH queries stale-links [--json]
```

Output schema: `virtuoso/stale-links@0.1`. The top-level `links` array contains `item_id`, `source_id`, and `relative_path` for each linked source note whose current indexed hash no longer matches.

## Export (read-only projections)

Exports write derived views of workspace state to a folder you choose. They read the workspace and never write to its database. Every run rewrites the whole target folder, so treat the folder as generated output.

### `export obsidian`

```
virtuoso --workspace PATH export obsidian --out DIR [--json]
```

Writes one Markdown stub per item in the current review queue (the same items and order that `review due` returns) into `DIR`, plus a `.virtuoso-export.json` manifest. Obsidian Bases can index the stubs by folder and frontmatter, so a Base view shows the same due set as the CLI at the moment of export.

Each stub carries frontmatter only: `schema` (`virtuoso/obsidian-stub@0.1`), `item_id`, `title`, `focus`, `status` (`due` or `new`), `next_review` (ISO-8601 or `null`), `project_ids`, `schedule_owner` (`virtuoso-workspace`), `content_hash`, `generated_at`, and `generated: true`. The body is a fixed notice. No prompt, answer, hint, follow-up, or learning-unit prose leaves the workspace.

Safety rules, all fail closed:

- `DIR` must be outside the workspace and must not be a symlink.
- If `DIR` contains any file that this export did not generate, the command refuses and lists the foreign files. Choose an empty folder or remove them first.
- Stubs for items that left the queue (retired, or now waiting on a learn-first step) are deleted; `removed_count` reports how many.
- Running twice at the same instant against the same workspace is byte-identical. Only `generated_at` changes between runs.

Output schema: `virtuoso/obsidian-export@0.1` with `generated_at`, `output_dir`, `stub_count`, `due_count`, `new_count`, `removed_count`, and a `stubs` array of `item_id`, `status`, `due_at`, `focus`, `relative_path`.

Example Base view (add to your own `.base` file):

```yaml
views:
  - type: table
    name: Workspace Due Now
    filters:
      and:
        - file.inFolder("path/to/DIR")
        - schedule_owner == "virtuoso-workspace"
        - status == "due"
    order:
      - file.name
      - focus
      - next_review
```

The projection is a view, not a store. The workspace SQLite remains the only scheduler and evidence writer. Re-run the export after practice sessions, or on a schedule.

## Retrieval

The retrieval index is derived state in `.virtuoso/search.sqlite3`. Lexical commands refresh stale index content from active workspace items. Semantic commands use vectors supplied by the caller. Virtuoso does not call an embedding service.

### `search lex`

```
virtuoso --workspace PATH search lex --query TEXT [--limit N] [--json]
```

Output schema: `virtuoso/lexical-search@0.1`. The response contains `query` and `hits`. Each hit contains `item_id`, `score`, and `snippet`. The default limit is 10. The query follows the plain-text behavior described under exit codes.

### `search embed`

```
virtuoso --workspace PATH search embed --item ITEM --model MODEL --vector JSON [--json]
```

Output schema: `virtuoso/embed-upsert@0.1`. The response contains `item_id`, `model`, and `dim`. The vector must be a non-zero JSON array of finite numbers. A model keeps one vector dimension across all items. An unknown or retired item is rejected, and a failed write leaves the existing index unchanged.

### `search sem`

```
virtuoso --workspace PATH search sem --model MODEL --vector JSON [--limit N] [--json]
```

Output schema: `virtuoso/semantic-search@0.1`. The response contains `model` and `hits`. Each hit contains `item_id` and cosine `score`. The default limit is 10. Results include active items only, and the query vector must match the stored dimension for the selected model.

### `search status`

```
virtuoso --workspace PATH search status [--json]
```

Output schema: `virtuoso/search-status@0.1`. The response contains `index`, `exists`, `lexical_index_rows`, `active_items`, `lexical_fresh`, `fingerprint`, and `embedding_models`. Each embedding-model entry contains `model` and `vectors`. `fingerprint` is `null` before a lexical index fingerprint exists.

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
