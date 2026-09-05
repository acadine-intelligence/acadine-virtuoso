# Architecture

## System context

Virtuoso is a standalone Python 3.11 CLI and importable library. A learner points it at one local workspace. The core works offline and does not require Obsidian, Hermes, a model, or a server.

```text
learner / harness
      |
      v
cli.py -> workspace, learning, practice, candidate, review, query, and search services
             |-> Markdown item files
             |-> SQLite study/recall evidence, scheduler state, and derived search index
             |-> built-in scheduler backends (FSRS default, SM-2)
             `-> external command module runner

optional local interfaces: Obsidian plugin and Hermes plugin
```

## Current Python modules

The package is flat. These files are the implemented boundaries:

- `__init__.py`: installed package version lookup.
- `candidates.py`: source-backed candidate generation and review decisions.
- `cli.py`: argument parsing, prompts, JSON output, and exit codes.
- `errors.py`: the shared public error family.
- `learning.py`: the bounded interactive learning step and completion decision.
- `learning_state.py`: shared typed learn-or-practice projection for current item versions.
- `modules.py`: trusted external command manifests and execution.
- `practice.py`: active-recall sessions and scheduler proposals through the configured backend.
- `queries.py`: read-only evidence and workload projections.
- `review.py`: versioned review contracts for local interfaces.
- `schedulers.py`: the scheduler backend protocol and the built-in FSRS and SM-2 backends.
- `search.py`: lexical and caller-supplied-vector retrieval.
- `workload.py`: shared current-schedule workload projection.
- `workspace.py`: workspace files, SQLite schema, sources, evidence, and transfers.

## Target design

The future package design may separate domain and application layers. It may also isolate infrastructure. These layers do not exist as Python packages in v0.1.0. A later split must preserve the current CLI behavior, storage ownership, and versioned JSON contracts.

## Obsidian review boundary

The optional Obsidian plugin is a local interface over the installed CLI. In this path, offline means Obsidian, the local CLI, and the local workspace can run without a live agent, server, or network.

The plugin settings hold the CLI executable path and workspace path. `review due` omits learn-first items that still need study. `review due` and `review load` return versioned practice snapshots. The plugin keeps the active snapshot in memory for the open session. It has no durable review cache. `review record` validates the snapshot hash and writes the measured direct attempt, scheduler proposal, and scheduler state through the core transaction. `review skip` validates the same hash, appends one skip event, and leaves scheduler state unchanged.

The plugin does not open SQLite or calculate an interval. It blocks another submission while a write runs. Process, schema, workspace, and stale-content errors retain the current card and carry a recovery action.

The review slice excludes agent enrichment. `plugins/obsidian/src/enrichment.ts` defines the additive boundary for a later explicit action. Its pure guard accepts only enrichment-owned fields. It rejects schedule, evidence, hash, result, and other core-owned fields. The current review interface does not call the guard or expose an enrichment action.

## Storage and synchronization

Markdown owns item prose, including a learn-first item's learning unit. SQLite stores the derived item metadata and append-only study event. Each study event binds the exact item and learning-unit hashes. A matching completion changes the typed next action from learn to practice. It creates no FSRS state. A future sync adapter compares owned fields and hashes; it never applies generic last-write-wins. Conflicting learner-authored edits become explicit conflict records.

The candidate pipeline separates the source index from approved content import. A normal source scan stores metadata only. `candidate generate --adapter curriculum` reads the exact selected note under explicit command scope. The built-in versioned adapter accepts `virtuoso/item@0.1` notes and `virtuoso/curriculum@0.1` notes with typed practice blocks. It stores candidate fields, adapter version and the exact source hash for human review. Accepting or editing an import writes one workspace Markdown item, its SQLite item row, its source link and the append-only decision in one transaction. A failed validation or database write removes any file created by that transaction. Skip and reject write only the decision. Source notes are never written.

Historical due values remain candidate metadata. Import creates no scheduler state, attempt or transfer event. A delta run validates changed content before updating the source index, preserves old proposals and decisions, and produces no output or database write for an unchanged snapshot.

Recall attempts, project-transfer events, and delayed transfer checks stay separate. A transfer event binds to the exact learning-item hash and records project, use case, outcome, independence, optional artifact reference, reflection, and a delayed-check date. One manually authored delayed check may inherit that date and append a pre-attempt prediction followed by an immutable completion containing the independent attempt, assistance attribution, scorer-bound acceptance evidence, teach-back, outcome, and optional inert artifact reference. The event, check, prediction, and completion rows reject direct update or deletion and fix `claims_mastery` to false. Their UTC chronology is causal: check creation cannot precede its source event; late check creation is allowed but cannot be backdated; prediction cannot precede either the inherited due time or check creation; and completion cannot precede check creation or prediction. The check queue is chronological capability evidence only: it never reads or writes recall attempts, scheduler state/proposals, or project selection/priority. Capability views may later interpret repeated evidence, but cannot rewrite these source records.

SQLite migrations run in transactions and fail closed. Migration 13 adds item learning metadata and the append-only study-event ledger. Migration 14 adds append-only session composition proposals and learner decisions. Migration 15 adds append-only benchmark runs, observations, rerun links, and proposal markers. Migration 16 adds the append-only scheduler-switch ledger. Existing items become recall-first and existing workspaces gain empty composition, benchmark, and switch tables without invented study, proposal, decision, benchmark, or switch evidence. The current migrations are additive or reconstruct constrained tables without inventing evidence; automatic backup and restore are not implemented. Operators must make a consistent local backup before a future destructive migration. Runtime databases, WAL files, logs, and learner workspaces are ignored by Git.

## Scheduler portfolio

`schedulers.py` defines one `SchedulerBackend` protocol: validate the algorithm's own configuration keys, turn one attempt plus the previous stored state into a proposal (`proposed_state_json`, `due_at`, rationale), and read the due time back out of a stored state. Two built-ins implement it: `fsrs` (the `fsrs` package, version `6.3.2`, the default) and `sm2` (SuperMemo 2 written from the published 1990 description, version `sm2-1990/1`). `practice.py` asks the workspace for the configured backend and never imports an algorithm directly. The workspace validates every proposal the same way before storing it, including that the proposed state's own due time matches `due_at`.

Scheduler state is keyed by item, algorithm, and learning context, so two algorithms never overwrite each other. Changing `scheduler.algorithm` in `virtuoso.json` while another algorithm still holds state in that context fails closed in every reader and writer until `scheduler switch` records the change in the append-only `scheduler_switches` table (migration 16) and rewrites the configuration in the same transaction. The only switch mode is `fresh`: no memory parameters are converted between algorithms, and the previous algorithm's state and proposals stay as history.

Every proposal records its algorithm, version, configuration, context, input attempt, and due result. Delayed transfer checks do not use this scheduler portfolio: their inherited date is an evidence-inspection boundary; it does not schedule memory. External schedulers through the `scheduler` module category are designed in `18-scheduler-portfolio-design.md` and not yet wired to practice.

FSRS supports an optional `minimum_interval_days` scheduling preference. Zero or omission preserves upstream timing. A positive value clamps the due time after FSRS runs, without changing its returned memory parameters. The card and proposal carry the same effective due time; the rationale preserves the original due time. Only this preference may change across existing FSRS state without an incompatibility error. The workspace rechecks the configured minimum inside the attempt transaction and rejects a stale or too-short proposal. `scheduler configure` replaces the configuration file under the same writer lock while leaving existing due dates and evidence untouched.

The future meta-scheduler compares outcomes within comparable contexts and chooses only among learner-approved policies. It does not invent intervals or infer competence.

## Extension protocol

V0 supports external command modules only. A `virtuoso.module.json` manifest declares:

- `schema: virtuoso/module@0.1`
- stable id, version, and category
- executable argv with no shell expansion
- protocol version and timeout
- requested read projections and output capability

Virtuoso sends one bounded JSON object on stdin and expects one typed JSON object on stdout. External modules are trusted local executables; this boundary is not an OS sandbox. They run with the invoking user's permissions. Calling code must opt in for each run with `allow_trusted=True`. There is no public CLI command for module execution and no consent dialog. The runner rejects shell and command-wrapper indirection, uses `shell=False`, a sanitized environment, bounded temporary-file output capture, nested projection validation, per-result required fields, and load-time manifest hashing. V0 grants no descendant-process capability: on supported POSIX systems the module starts with a zero process limit, the runner terminates its process group after success or failure, and execution fails closed where that limit is unavailable. Modules receive no database path, and only core code may accept and persist their proposals; users must review a module because these controls do not prevent the executable itself from accessing other user-readable files.

Initial categories are scheduler, practice-format, source-adapter, scoring-signal, and output-adapter. In-process third-party plugins remain out of scope until the protocol and trust model have survived dogfooding.

## Hermes boundary

Hermes can invoke the CLI, schedule proposal runs, test adapters, and provide an explicitly approved read-only context projection. Virtuoso owns learning state and learner decisions. Hermes does not silently commit focus, mutate projects, or become required for the core journey.

## Failure behavior

Malformed Markdown, unknown schema, failed migration, incompatible module, timeout, malformed module response, stale item version, or unavailable scheduler fails clearly and leaves prior state intact. `doctor` reports recovery steps without changing learning evidence.
