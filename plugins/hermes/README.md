# virtuoso Hermes plugin

Hermes agent tools wrapping the installed `virtuoso` CLI (`acadine-virtuoso`
package). The plugin shells out with `--json`; it holds no scheduling logic of
its own. This is an optional adapter. The CLI remains the canonical interface.

## Tools

- `virtuoso_due`: what is due now (recommended next + delayed transfer checks)
- `virtuoso_next`: single recommended practice item
- `virtuoso_transfer_record`: record a real-project transfer event with an optional artifact reference
- `virtuoso_status`: workspace health

All tools are service-gated on `shutil.which("virtuoso")`: if the CLI is not
installed, the toolset never appears in agent schemas.

## Install

```bash
pip install -e /path/to/acadine-virtuoso   # provides the virtuoso CLI
cp -r plugins/hermes ~/.hermes/plugins/virtuoso
```

Then enable it in `config.yaml`:

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
        workspace: /path/to/your-virtuoso-workspace
```

Default workspace: `~/.virtuoso/workspace`. Override it with the `workspace:`
plugin setting.

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
- Review ratings flow through interactive `virtuoso practice` sessions (human
  answers first); this plugin deliberately does not expose a non-interactive
  rating shortcut.
