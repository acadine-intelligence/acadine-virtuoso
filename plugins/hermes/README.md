# virtuoso Hermes plugin

Hermes agent tools and a native Desktop page wrapping the installed `virtuoso`
CLI (`acadine-virtuoso` package). The plugin invokes the CLI with JSON contracts.
It holds no scheduling logic. The CLI remains the canonical interface.

The Desktop page supports learn-first study and measured direct recall. The
interface runs on the Desktop machine. Its Python API and CLI run on the
connected backend, where the workspace stays. See the [installation guide](../../docs/20-hermes-desktop.md).

## Tools

- `virtuoso_due`: what is due now (typed next learning/practice action + delayed transfer checks)
- `virtuoso_next`: single recommended learning or practice action
- `virtuoso_transfer_record`: record a real-project transfer event with an optional artifact reference
- `virtuoso_status`: workspace health

The tools check the configured executable with `shutil.which()`. Without an
executable setting they look for `virtuoso` on the backend PATH. If it is
unavailable, the toolset does not appear in agent schemas.

## Install

```bash
uv tool install --python 3.11 /path/to/acadine-virtuoso
hermes plugins install acadine-intelligence/acadine-virtuoso/plugins/hermes
hermes plugins enable virtuoso
```

This installs the agent component on the backend. For the desktop component,
use Install from Git on the interface machine or copy the reviewed
`desktop/plugin.js` as described in the guide. Backend enablement is separate
from the desktop enable switch. The backend configuration contains:

```yaml
plugins:
  enabled:
    - virtuoso
```

## Configuration

Workspace override via plugin settings:

```yaml
plugins:
  entries:
    virtuoso:
      settings:
        executable: /absolute/path/to/virtuoso
        workspace: /path/to/your-virtuoso-workspace
```

The agent tools default to `~/.virtuoso/workspace`. The Desktop API requires an
explicit workspace setting. Paths belong to the backend machine. Keep the CLI
in its own environment rather than installing it into Hermes' Python environment.

The plugin and Python package follow the same `0.1.0` release version.

## Result and argument contract

Every handler returns one JSON object. Success uses `{"success": true,
"data": ...}`. Failure uses `{"success": false, "error": "..."}`.
`virtuoso_due` succeeds only when both its next-item and delayed-check calls
succeed. Its failure adds `component` with `recommended_next` or
`transfer_checks_due`. A timeout, spawn failure, non-zero exit, empty output,
malformed JSON, or non-object JSON fails closed.

Failure output omits the subprocess argv. This keeps workspace paths and
transfer prose out of wrapper-generated diagnostics. The underlying CLI error
message remains available.

Item and project identifiers use lowercase words or numbers separated by
single dashes. Focus is an optional free-text track name. If supplied, focus
must be non-empty. The wrapper passes every dynamic value as `--flag=value`,
so a valid free-text value that starts with `-` remains data.

## Design notes

- Scheduling ownership stays split per the 2026-07-24 decision: Virtuoso owns
  learning items, Obsidian SR owns flashcards, the project system owns priority.
- Agent tools do not expose a rating shortcut. The Desktop page records an
  explicit human response through the CLI's measured direct-review contract.
- `dashboard/plugin_api.py` exports the scoped REST router. Hermes supplies its
  HTTP authentication and plugin enablement checks. The plugin starts no server.
- `desktop/plugin.js` is the shipped, uncompiled ESM module. npm dependencies
  support tests only and are unnecessary on the interface machine.
