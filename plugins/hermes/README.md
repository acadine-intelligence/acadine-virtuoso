# virtuoso Hermes plugin

Hermes agent tools wrapping the installed `virtuoso` CLI (`acadine-virtuoso`
package). The plugin shells out with `--json`; it holds no scheduling logic of
its own.

## Tools

- `virtuoso_due` — what is due now (recommended next + delayed transfer checks)
- `virtuoso_next` — single recommended practice item
- `virtuoso_transfer_record` — record a real-project transfer event (evidence)
- `virtuoso_status` — workspace health

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
        workspace: /path/to/virtuoso-workspace
```

Default workspace: `~/projects/virtuoso-workspace`.

## Design notes

- Scheduling ownership stays split per the 2026-07-24 decision: Virtuoso owns
  learning items, Obsidian SR owns flashcards, the project system owns priority.
- Review ratings flow through interactive `virtuoso practice` sessions (human
  answers first); this plugin deliberately does not expose a non-interactive
  rating shortcut.
