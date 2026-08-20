# Acadine Virtuoso

Virtuoso is a local-first CLI mental gym. It presents a prompt before feedback, times active recall, records what help was used, and asks FSRS 6.3.2 for an attributable next-review proposal. Markdown remains human-owned; SQLite holds derived evidence and scheduler state.

The current slice is simple mode. Hermes and Obsidian are optional, and the core CLI works without either. Direction-led pathways, project-transfer exercises, synchronization, XP, and meta-scheduling remain later modules.

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

## Extension boundary

External v0 modules use `virtuoso/module@0.1` manifests and bounded JSON over stdin/stdout. Modules declare one category, explicit read projections, and a typed return. Virtuoso invokes argv directly without a shell, rejects writes to core state, enforces a timeout and output limits, validates the result, and records a hash-bound receipt before core-owned code acts on it.

Initial categories are scheduler, practice-format, source-adapter, scoring-signal, and output-adapter. In-process third-party plugins are deliberately out of scope.

## Verify

```bash
.venv/bin/python -m compileall -q src tests
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/virtuoso --help
python3 "$HOME/projects/acadine-build-os/scripts/buildos.py" verify .
```

`product.json` and `docs/07-delivery-contract.md` define the current completion boundary. No remote repository or public release is part of this slice.
