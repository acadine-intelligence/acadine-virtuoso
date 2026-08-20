# Acadine Virtuoso

Virtuoso is a local-first CLI mental gym. It presents a prompt before feedback, times active recall, records what help was used, and asks FSRS 6.3.2 for an attributable next-review proposal. Markdown remains human-owned; SQLite holds derived evidence and scheduler state.

The current slices cover simple-mode active recall, a read-only Markdown/Obsidian source index, and attributed project-transfer evidence. Hermes and Obsidian remain optional, and the core CLI works without either. A connected source stores only its path-scoped note metadata, hashes, and wikilinks in SQLite; source prose stays in its owning vault. Direction-led pathways, generated exercises, two-way synchronization, XP, and meta-scheduling remain later modules.

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

Virtuoso does not infer competence from XP, completion, a test, or an agent-produced answer. Attempts retain result, latency, confidence, open-notes state, response, help attribution, and support sequence.

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

Transfer events are append-only and bound to the exact learning-item hash. Outcome, independence, artifact reference, and reflection remain separate. Each event proposes a seven-day delayed check and explicitly records that it does not claim mastery.

The research basis and limits behind active recall, spacing, latency, transfer, and future meta-scheduling are in `docs/10-learning-research.md`.

## Extension boundary

External v0 modules use `virtuoso/module@0.1` manifests and bounded JSON over stdin/stdout. They are trusted local executables, not sandboxed plugins: a module runs with the invoking user’s OS permissions and therefore must be reviewed before use. Virtuoso requires explicit per-call consent, invokes argv without a shell, sends only declared projections, stores output in temporary files rather than memory, kills the process group on timeout, validates exact request/result shapes, and records the manifest hash captured when it was loaded. Core code alone decides whether to accept a returned proposal.

Initial categories are scheduler, practice-format, source-adapter, scoring-signal, and output-adapter. In-process third-party plugins are deliberately out of scope.

## Verify

```bash
.venv/bin/python -m compileall -q src tests
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/virtuoso --help
python3 "$HOME/projects/acadine-build-os/scripts/buildos.py" verify .
```

`product.json` and `docs/07-delivery-contract.md` define the current completion boundary. No remote repository or public release is part of this slice.
