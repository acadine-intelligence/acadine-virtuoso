# Virtuoso

A local-first command-line tool for deliberate practice. Virtuoso shows you a prompt before any answer, times your recall, records what help you used, and asks the FSRS spaced-repetition algorithm for a transparent next-review proposal you can inspect and override.

Your material stays yours: items are plain Markdown you can read and edit; SQLite holds derived evidence and scheduler state on your own disk. No account, no cloud, no telemetry.

## Why

Most learning tools measure activity. Virtuoso measures attempts: what you recalled, how long it took, what help you used, and whether you could apply it later in a real project. It refuses to infer competence from completion counts, streaks, or AI-generated answers. An attempt is evidence or it is nothing.

## Install

Requires Python 3.11+.

```bash
git clone <repo-url> && cd <repo-name>
python3.11 -m venv .venv
.venv/bin/python -m pip install -e .
```

## Five-minute tour

```bash
WORKSPACE=~/my-practice
.venv/bin/virtuoso --workspace "$WORKSPACE" init

# Add your first item: a question you want to be able to answer cold.
.venv/bin/virtuoso --workspace "$WORKSPACE" add \
  --id testing-effect \
  --title "Explain the testing effect" \
  --focus learning-science \
  --prompt "Why does active recall improve memory?" \
  --answer "Retrieval changes memory and strengthens later access." \
  --hint "Compare retrieval with rereading." \
  --follow-up "Give one coding example."

# What should I practice next?
.venv/bin/virtuoso --workspace "$WORKSPACE" next --json

# Practice: prompt first, recall timed, then reveal and grade honestly.
.venv/bin/virtuoso --workspace "$WORKSPACE" practice --item testing-effect

# Inspect the evidence and the scheduler's reasoning.
.venv/bin/virtuoso --workspace "$WORKSPACE" attempts --json
.venv/bin/virtuoso --workspace "$WORKSPACE" doctor --json
```

## What it records, honestly

Every attempt stores the actual start and completion times, your initial response verbatim, recall latency, the result you graded, your confidence, whether notes were open, and how much help was used (none, light, substantial). Blank recalls cannot be graded as demonstrated. Agent-relayed sessions are marked `administered` with unknown latency rather than a fabricated zero.

Scheduling is explainable: each attempt produces a scheduler proposal carrying the algorithm, version, configuration, previous state, proposed state, and a plain-language rationale. FSRS 6.3.2 is the built-in scheduler; the module protocol lets you swap it for your own.

## Beyond single items

**Read-only sources.** Connect any Markdown folder or Obsidian vault as a source. Virtuoso indexes paths, titles, hashes and wikilinks; it never writes to your notes and never copies their prose into its database.

**Project transfer evidence.** When you apply something you learned to real work, record it: outcome, independence, artifact reference, your reflection. A delayed capability check follows days later, with a pre-attempt prediction and append-only completion evidence. None of it ever claims mastery.

**Retrieval (RAG-ready).** Lexical full-text search over all items (word-stemmed, so "goroutine" finds "goroutines"), plus an embedding table with cosine kNN. Virtuoso never calls an embedding API itself: you compute vectors with any tool you like and store them. Find the items closest to a question, then feed them to a tutor agent, a session composer, or your own prompt.

**Analytics.** Read-only queries over your own database: per-focus performance, full item history, due workload by focus, and stale source links. Every query opens the database in read-only mode.

## The Obsidian plugin (optional)

A community plugin gives you a human review queue for proposed items and a full-viewport review session with a keyboard ladder (reveal, retry, hint, grade) inside Obsidian. The plugin never computes intervals itself; every grade goes through the CLI, so there is exactly one scheduler and one evidence ledger. Obsidian is entirely optional; the CLI works without it.

## Extension boundary

External modules use a JSON-over-stdin/stdout protocol with explicit per-call consent, no shell indirection, bounded output, and fail-closed process limits. Initial categories: scheduler, practice-format, source-adapter, scoring-signal, output-adapter. They are trusted local executables and should be reviewed before use.

## What Virtuoso does not do

- No XP, streaks, leaderboards, or moral scoring
- No cloud, accounts, or telemetry
- No automatic content generation: you author the items (a review-candidate pipeline can propose structural practice from your indexed notes, but acceptance stays human)
- No mastery claims from any single event
- Does not require Obsidian, any agent, or any model

## Documentation

- `docs/12-cli-reference.md` — every command, flag, JSON shape, exit code
- `docs/13-agent-usage.md` — how agents drive the CLI
- `docs/10-learning-research.md` — the research basis and its limits
- `docs/03-domain-model.md` — who owns which state and why

## Status

Early, dogfooded daily, honest about scope. The completion boundary lives in `product.json`; this README documents what exists today, not a roadmap promise.

## Verify

```bash
.venv/bin/python -m compileall -q src tests
.venv/bin/python -m unittest discover -s tests
.venv/bin/virtuoso --help
```
