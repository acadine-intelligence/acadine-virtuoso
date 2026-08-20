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
- `application`: initialize, add item, select, practise, record recall and project-transfer events, inspect evidence.
- `infrastructure`: Markdown, SQLite, FSRS serialization, clock, external process runner.
- `cli`: argument parsing, prompts, JSON output, exit codes.

## Storage and synchronization

Markdown owns item prose. SQLite owns derived and append-only state. Every item version is content-hashed. A future sync adapter compares owned fields and hashes; it never applies generic last-write-wins. Conflicting learner-authored edits become explicit conflict records.

Recall attempts and project-transfer events stay separate. A transfer event binds to the exact learning-item hash and records project, use case, outcome, independence, optional artifact reference, reflection, and a delayed-check date. Its schema fixes `claims_mastery` to false. Capability views may later interpret repeated evidence, but cannot rewrite the source events.

SQLite migrations run in transactions. Before any destructive migration, Virtuoso creates a SQLite backup. Runtime databases, WAL files, logs, and learner workspaces are ignored by Git.

## Scheduler portfolio

Each scheduler implements one typed port and stores state under its own algorithm id. The first adapter is `fsrs@6.3.2` for atomic recall. Every proposal records its package version, configuration, context, input attempt, and due result. Later schedulers may serve computational exercises, explanation, writing, and transfer checks without overwriting FSRS state.

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
