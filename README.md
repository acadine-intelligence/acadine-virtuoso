# Acadine Virtuoso

Virtuoso is a local-first CLI mental gym. It presents a prompt before feedback, times active recall, records what help was used, and asks FSRS 6.3.2 for an attributable next-review proposal. Markdown remains human-owned; SQLite holds derived evidence and scheduler state.

The current slices cover simple-mode active recall, a read-only Markdown/Obsidian source index, attributed project-transfer events, and manually authored delayed capability checks with pre-attempt predictions and append-only completion evidence. Hermes and Obsidian remain optional, and the core CLI works without either. A connected source stores only its path-scoped note metadata, hashes, and wikilinks in SQLite; source prose stays in its owning vault. Direction-led pathways, generated exercises, two-way synchronization, XP, automated scoring, and meta-scheduling remain later modules.

## Install

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -e .
```

## First practice journey

```bash
WORKSPACE=/tmp/virtuoso-learner
.venv/bin/virtuoso --workspace "$WORKSPACE" init
.venv/bin/virtuoso --workspace "$WORKSPACE" add \
  --id testing-effect \
  --title "Explain the testing effect" \
  --focus learning-science \
  --prompt "Why does active recall improve memory?" \
  --answer "Retrieval changes memory and strengthens later access." \
  --hint "Compare retrieval with rereading." \
  --follow-up "Give one coding example."
.venv/bin/virtuoso --workspace "$WORKSPACE" next --json
.venv/bin/virtuoso --workspace "$WORKSPACE" practice --item testing-effect
.venv/bin/virtuoso --workspace "$WORKSPACE" attempts --json
.venv/bin/virtuoso --workspace "$WORKSPACE" doctor --json
```

Virtuoso does not infer competence from XP, completion, a test, or an agent-produced answer. Attempts retain actual start and completion times, result, initial latency, confidence, open-notes state, response, help attribution, worked-answer reveal, and timed follow-up support.

## Read-only Markdown and Obsidian sources

```bash
VAULT=/path/to/your/vault
.venv/bin/virtuoso --workspace "$WORKSPACE" source add \
  --id personal-vault --kind obsidian --path "$VAULT" --json
.venv/bin/virtuoso --workspace "$WORKSPACE" source scan \
  --id personal-vault --json
.venv/bin/virtuoso --workspace "$WORKSPACE" source notes \
  --id personal-vault --json
.venv/bin/virtuoso --workspace "$WORKSPACE" source link \
  --id personal-vault --path "Learning/Testing Effect.md" \
  --item testing-effect --json
```

Scanning never writes to the source. It indexes relative paths, titles, content hashes, byte sizes, modification times, and Obsidian wikilinks. It does not copy note bodies into Virtuoso state. Markdown symlinks fail closed, scans have file and byte limits, deleted note metadata is removed transactionally, and `doctor` reports linked notes that changed or disappeared.

## Project-transfer evidence

```bash
.venv/bin/virtuoso --workspace "$WORKSPACE" transfer record \
  --item testing-effect \
  --project virtuoso-cli \
  --use-case "Applied retrieval practice to a real CLI journey." \
  --outcome successful \
  --independence guided \
  --artifact git:abc123 \
  --reflection "One design hint was used." \
  --json
.venv/bin/virtuoso --workspace "$WORKSPACE" transfer list --json
```

Transfer events are append-only and bound to the exact learning-item hash. Outcome, independence, artifact reference, and reflection remain separate. Each event proposes a seven-day delayed check and explicitly records that it does not claim mastery. Transfer events, checks, predictions, and completions all reject direct update or deletion.

Create a check only after authoring the changed or novel context, challenge, acceptance criteria, and scorer. Its UTC creation time cannot precede the source transfer event. Late creation after the inherited due time is allowed, but cannot be backdated. The due command is a chronological capability-evidence queue, not a scheduler or project-priority recommendation:

```bash
EVENT=transfer-00000000000000000000000000000000 # use an id from transfer list
.venv/bin/virtuoso --workspace "$WORKSPACE" transfer check create \
  --event "$EVENT" \
  --context-kind changed \
  --context "The same distinction in a changed research policy." \
  --prompt "Classify two artifacts and propose one falsifiable refresh rule." \
  --acceptance-criteria "Classify both artifacts and state one testable cadence rule." \
  --scorer-kind human \
  --scorer-reference reviewer-jonathan \
  --json
.venv/bin/virtuoso --workspace "$WORKSPACE" transfer check due --json
```

At or after both the inherited due time and check creation time, **before attempting the challenge or requesting help**, record the prediction. Completion cannot precede either check creation or that prediction. Then complete the changed task and append the independent attempt, assistance attribution, scorer-bound acceptance evidence, teach-back, outcome, and optional opaque artifact reference:

```bash
CHECK=transfer-check-00000000000000000000000000000000 # use an id from check create/due
.venv/bin/virtuoso --workspace "$WORKSPACE" transfer check begin \
  --check "$CHECK" \
  --prediction "I expect the distinction to transfer, but cadence selection may be weak." \
  --json
.venv/bin/virtuoso --workspace "$WORKSPACE" transfer check complete \
  --check "$CHECK" \
  --attempt "My independent classification and cadence rule." \
  --assistance none \
  --acceptance-evidence "The configured criteria were met." \
  --teach-back "Retrievability stayed separate from project urgency." \
  --outcome successful \
  --artifact git:abc123 \
  --json
```

These three check records are raw capability evidence only. They never update recall attempts, FSRS/scheduler state, project selection or priority, or a capability/mastery label. References remain inert strings and are not opened, fetched, executed, or resolved.

The research basis and limits behind active recall, spacing, latency, transfer, and future meta-scheduling are in `docs/10-learning-research.md`.

The CLI contract and integration guidance live in `docs/12-cli-reference.md` (every command, flag, JSON shape and exit code), `docs/13-agent-usage.md` (how agents drive the CLI, including a natural-language to command mapping), and `docs/14-api-consideration.md` (why the CLI is the API for now).

## Extension boundary

External v0 modules use `virtuoso/module@0.1` manifests and bounded JSON over stdin/stdout. They are trusted local executables, not sandboxed plugins: a module runs with the invoking user’s OS permissions and therefore must be reviewed before use. Virtuoso requires explicit per-call consent, rejects shell and command-wrapper indirection by declared name, resolved executable identity, and script-interpreter ancestry, invokes argv with `shell=False`, sends only declared projections whose supplied fields pass the protocol’s nested type checks, and requires each result kind’s declared fields. Output is captured in bounded temporary files, and the manifest hash is captured when loaded. V0 grants no descendant-process capability: on supported POSIX systems the child starts with a zero process limit and the runner always terminates its process group; module execution fails closed when that OS limit is unavailable. Core code alone decides whether to accept a returned proposal.

Initial categories are scheduler, practice-format, source-adapter, scoring-signal, and output-adapter. In-process third-party plugins are deliberately out of scope.

## Verify

```bash
.venv/bin/python -m compileall -q src tests
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/virtuoso --help
python3 "$HOME/projects/acadine-build-os/scripts/buildos.py" verify .
```

`product.json` and `docs/07-delivery-contract.md` define the current completion boundary. The initial GitHub repository is private; no public release is part of this slice.
