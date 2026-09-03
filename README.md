# Virtuoso

[![CI](https://github.com/acadine-intelligence/acadine-virtuoso/actions/workflows/ci.yml/badge.svg)](https://github.com/acadine-intelligence/acadine-virtuoso/actions/workflows/ci.yml)

A local-first command-line tool for deliberate practice. Virtuoso shows you a prompt before any answer, times your recall, records what help you used, and asks the FSRS spaced-repetition algorithm for a transparent next-review proposal you can inspect and override.

Your material stays yours: items are plain Markdown you can read and edit; SQLite holds derived evidence and scheduler state on your own disk. No account, no cloud, no telemetry.

## Why

Most learning tools measure activity. Virtuoso measures attempts: what you recalled, how long it took, what help you used, and whether you could apply it later in a real project. It refuses to infer competence from completion counts, streaks, or AI-generated answers. Virtuoso records the context needed to interpret each attempt as evidence.

## Install

Requires Python 3.11+.

```bash
git clone https://github.com/acadine-intelligence/acadine-virtuoso.git
cd acadine-virtuoso
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

### Read-only sources

Connect any Markdown folder or Obsidian vault as a source. A normal source scan stores paths, titles, hashes and wikilinks. It does not copy note prose into SQLite or write to the source.

### Reviewed practice import

A selected note can declare complete practice items with the public `virtuoso/curriculum@0.1` format. Run `candidate generate --adapter curriculum` to place those items in the review queue. Use `--dry-run` to inspect the exact proposals without a database write. Each proposal keeps the adapter version and exact source hash.

`candidate decide --decision accept` creates the proposed Markdown item and its source link. `edit` creates the reviewed version. `skip` records the choice without creating an item. Historical due values stay in the proposal as context; they never seed FSRS state or create attempt evidence.

For scheduled checks, `candidate delta` writes a new run only when the selected note changes. An unchanged run exits successfully with no output, so cron does not send empty notifications. The source remains byte-for-byte unchanged throughout the import.

### Project transfer evidence

When you apply something you learned to real work, record the outcome, independence, artifact reference and your reflection. A delayed capability check follows days later, with a pre-attempt prediction and append-only completion evidence. These records never create a mastery claim.

### Retrieval

Lexical full-text search works across all items. Word stemming lets "goroutine" find "goroutines". An embedding table supports cosine kNN. Virtuoso never calls an embedding API itself. You compute vectors with your chosen tool and store them locally. The retrieved items can feed a tutor agent, a session composer or your own prompt.

### Analytics

Read-only queries report per-focus performance, item history, due workload by focus and stale source links. Every query opens the database in read-only mode.

## The Obsidian plugin (optional)

The plugin runs the full local review flow inside Obsidian. Set the installed Virtuoso executable and workspace paths, then run `Virtuoso: Start offline review`. You can type the first response, take one unaided retry, show a hint, reveal the answer, record result and confidence, mark notes as open, or skip.

Offline here means Obsidian, the installed CLI, and the local workspace work without a live agent, server, or network. The plugin keeps only the open session snapshot in memory. Every grade and skip goes through a versioned JSON CLI contract with a content hash. The CLI remains the only scheduler and evidence writer. See `plugins/obsidian/README.md` for setup and recovery steps.

## Extension boundary

External modules use a JSON-over-stdin/stdout protocol with no shell indirection, bounded output, and fail-closed process limits. Calling code must opt in for each run with `allow_trusted=True`. There is no public CLI command for module execution and no consent dialog. Initial categories: scheduler, practice-format, source-adapter, scoring-signal, output-adapter. Modules are trusted local executables and should be reviewed before use.

## What Virtuoso does not do

- Virtuoso has no XP, streaks, leaderboards or moral scoring.
- Virtuoso uses no cloud service, account or telemetry.
- Virtuoso does not invent practice content automatically. Structural candidates contain no drafted answer. Curriculum import accepts only complete items that the selected source note declares, then waits for a human decision.
- A single event never creates a mastery claim.
- The CLI runs without Obsidian, an agent or a model.

## Documentation

- [Documentation index](docs/README.md): current guides and supporting records
- `docs/12-cli-reference.md`: every command, flag, JSON shape and exit code
- `docs/13-agent-usage.md`: how agents drive the CLI
- `docs/10-learning-research.md`: the research basis and its limits
- `docs/03-domain-model.md`: who owns which state and why
- `docs/15-release-notes.md`: public change history
- `docs/16-verification-history.md`: reproducible checks and historical results
- [CONTRIBUTING.md](CONTRIBUTING.md): public setup, checks, and pull request workflow
- [LICENSE](LICENSE): MIT licence terms

## Status

Virtuoso is early and under active dogfood. `product.json` records its current completion and adoption state. This README documents the implemented behavior. A maintainer can manually run the release workflow after required CI to prepare a draft `v0.1.0` GitHub Release. The workflow does not publish to a package registry or deploy the product.

## Verify

```bash
.venv/bin/python -m compileall -q src tests
.venv/bin/python -m unittest discover -s tests
.venv/bin/virtuoso --help
```
