# Architecture

## System context

Virtuoso is a standalone Python 3.11 CLI and importable library. A learner points it at one local workspace. The core works offline and does not require Obsidian, Hermes, a model, or a server.

```text
learner / harness
      |
      v
CLI -> application services -> domain ports
                     |-> Markdown item repository
                     |-> SQLite evidence and scheduler store
                     |-> built-in FSRS adapter
                     `-> external command module runner

optional read projections: Obsidian vault, Hermes context, project systems
```

## Package boundaries

- `domain`: immutable values, evidence semantics, scheduler and module contracts. No filesystem or SQLite imports.
- `application`: initialize, add item, select, practise, record recall and project-transfer events, and append delayed transfer-check predictions/completions.
- `infrastructure`: Markdown, SQLite, FSRS serialization, clock, external process runner.
- `cli`: argument parsing, prompts, JSON output, exit codes.

## Storage and synchronization

Markdown owns item prose. SQLite owns derived and append-only state. Every item version is content-hashed. A future sync adapter compares owned fields and hashes; it never applies generic last-write-wins. Conflicting learner-authored edits become explicit conflict records.

Recall attempts, project-transfer events, and delayed transfer checks stay separate. A transfer event binds to the exact learning-item hash and records project, use case, outcome, independence, optional artifact reference, reflection, and a delayed-check date. One manually authored delayed check may inherit that date and append a pre-attempt prediction followed by an immutable completion containing the independent attempt, assistance attribution, scorer-bound acceptance evidence, teach-back, outcome, and optional inert artifact reference. The event, check, prediction, and completion rows reject direct update or deletion and fix `claims_mastery` to false. Their UTC chronology is causal: check creation cannot precede its source event; late check creation is allowed but cannot be backdated; prediction cannot precede either the inherited due time or check creation; and completion cannot precede check creation or prediction. The check queue is chronological capability evidence only: it never reads or writes recall attempts, scheduler state/proposals, or project selection/priority. Capability views may later interpret repeated evidence, but cannot rewrite these source records.

SQLite migrations run in transactions. Before any destructive migration, Virtuoso creates a SQLite backup. Runtime databases, WAL files, logs, and learner workspaces are ignored by Git.

## Scheduler portfolio

Each scheduler implements one typed port and stores state under its own algorithm id. The first adapter is `fsrs@6.3.2` for atomic recall. Every proposal records its package version, configuration, context, input attempt, and due result. Delayed transfer checks do not use this scheduler portfolio: their inherited date is an evidence-inspection boundary, not a memory schedule. Later schedulers may serve computational exercises, explanation, and writing without overwriting FSRS state.

The future meta-scheduler compares outcomes within comparable contexts and chooses only among learner-approved policies. It does not invent intervals or infer competence.

## Extension protocol

V0 supports external command modules only. A `virtuoso.module.json` manifest declares:

- `schema: virtuoso/module@0.1`
- stable id, version, and category
- executable argv with no shell expansion
- protocol version and timeout
- requested read projections and output capability

Virtuoso sends one bounded JSON object on stdin and expects one typed JSON object on stdout. External modules are trusted local executables, not an OS sandbox: they run with the invoking user’s permissions and require explicit consent. The runner uses `shell=False`, a sanitized environment, temporary-file output capture, a process-group timeout, strict schemas, and load-time manifest hashing. Modules receive no database path, and only core code may accept and persist their proposals; users must review a module because the operating system does not prevent it from accessing other user-readable files.

Initial categories are scheduler, practice-format, source-adapter, scoring-signal, and output-adapter. In-process third-party plugins remain out of scope until the protocol and trust model have survived dogfooding.

## Hermes boundary

Hermes can invoke the CLI, schedule proposal runs, test adapters, and provide an explicitly approved read-only context projection. Virtuoso owns learning state and learner decisions. Hermes does not silently commit focus, mutate projects, or become required for the core journey.

## Failure behavior

Malformed Markdown, unknown schema, failed migration, incompatible module, timeout, malformed module response, stale item version, or unavailable scheduler fails clearly and leaves prior state intact. `doctor` reports recovery steps without changing learning evidence.
