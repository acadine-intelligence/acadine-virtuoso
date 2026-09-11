# Hermes Desktop with a remote workspace

Virtuoso adds a learning and practice page to Hermes Desktop. The interface uses the current Hermes connection. The Python adapter runs the installed Virtuoso CLI on that connection's backend. The workspace stays on the backend machine.

For a MacBook connected to another Mac, the MacBook needs the desktop module only. The backend Mac needs the Python adapter and Virtuoso CLI. The MacBook does not need a local learner database or a local Virtuoso installation. A local-only Hermes setup can keep both components on one machine.

The CLI remains the only scheduler and evidence writer. This optional interface does not change the standalone product boundary. It sends no answers to a model or the agent chat. Unfinished responses live in interface memory. Completed evidence lives in the backend workspace.

## Requirements

Use Hermes Desktop with the native plugin SDK, `ctx.rest`, and Git plugin installation. The connected backend must support enabled plugin API routes. See the [official SDK guide](https://hermes-agent.nousresearch.com/docs/developer-guide/desktop-plugin-sdk). Older Desktop and backend installations can need separate updates.

Install the Virtuoso CLI and adapter from the same reviewed repository revision. A previous `v0.1.0` release can lack the JSON study commands even though its version string matches the development version. Use commit identity when testing an unreleased change.

## Install from Git

In Hermes Desktop on the interface machine, select the intended backend connection. Open Capabilities → Plugins → Install from Git. Enter:

```text
acadine-intelligence/acadine-virtuoso/plugins/hermes
```

Choose the desktop and agent components in the confirmation dialog. Hermes installs the interface locally and the agent component on the selected backend. Confirm each component's enablement. The desktop component declares plugin ID `virtuoso` and starts disabled.

[Install in Hermes](hermes://plugin/install?repo=acadine-intelligence/acadine-virtuoso/plugins/hermes)

If your Markdown viewer does not open custom links, run this on the MacBook:

```bash
open 'hermes://plugin/install?repo=acadine-intelligence/acadine-virtuoso/plugins/hermes'
```

The link opens a confirmation dialog. It does not install code automatically. Git installation uses the repository's default branch unless the installed Hermes version explicitly offers component-specific revision selection. Do not assume that an agent-side commit pin also pins the desktop copy.

Some Hermes versions name the desktop folder `hermes`, from the repository subdirectory. The module still registers as `virtuoso`. Keep one desktop copy. Remove an earlier copy before changing installation methods.

## Configure the backend

Run these steps on the machine that holds the Virtuoso workspace. For the CLI installation, use the [installation guide](18-installation.md). Its isolated tool environment is separate from Hermes' Python environment.

```bash
# In the reviewed Virtuoso checkout on the backend:
uv tool install --python 3.11 .
command -v virtuoso
hermes plugins enable virtuoso
hermes config set plugins.entries.virtuoso.settings.executable "$(command -v virtuoso)"
```

Retain the existing workspace setting if the agent adapter already uses the intended workspace. Otherwise set it to the absolute path of an existing Virtuoso workspace:

```bash
hermes config set plugins.entries.virtuoso.settings.workspace /absolute/path/to/your-workspace
```

These are backend paths. A desktop app can inherit a different `PATH` from Terminal, so use the explicit executable setting. Do not install Virtuoso into Hermes' Python environment.

Restart the Hermes process serving this Desktop connection after adding the Python API. The routes mount at startup. Reconnecting to a persistent backend process alone does not load new routes. This does not require restarting an unrelated messaging gateway.

On the MacBook, enable Virtuoso in the desktop plugin list. Open the Virtuoso sidebar page or run Open Virtuoso from the command palette. If the module does not appear, run Reload desktop plugins.

## Install an exact development revision

Before a pull request merges, the default-branch install dialog cannot fetch its interface reliably. Use a full reviewed commit SHA for both components.

On the backend:

```bash
REVISION=REPLACE_WITH_FULL_REVIEWED_COMMIT
hermes plugins install acadine-intelligence/acadine-virtuoso/plugins/hermes --ref "$REVISION"
```

Build the CLI from that same checkout revision with the installation command above. An existing plugin installation may require replacement. Back up its local changes before explicitly choosing the installer's replacement option.

On the MacBook, in a source clone checked out at that same commit:

```bash
mkdir -p ~/.hermes/desktop-plugins/virtuoso
cp plugins/hermes/desktop/plugin.js ~/.hermes/desktop-plugins/virtuoso/plugin.js
```

This is a plain ESM file. The MacBook needs no npm build. Use one interface installation method at a time to avoid duplicate plugin IDs.

## Try the full journey

Start with a separate synthetic workspace on the backend. Keep your existing workspace setting available so you can restore it afterwards.

1. Initialize the synthetic workspace with `virtuoso --workspace PATH init`.
2. Add a learn-first item through the CLI with a learning unit and a recall prompt.
3. Set the adapter's workspace setting to that synthetic workspace.
4. Open Virtuoso in Desktop and select Start next action.
5. Stop the study once. The workspace should have no study event.
6. Start again and choose Finish study. The CLI should store one study event with no attempt or schedule.
7. Start the next action. Type a response before revealing the answer.
8. Record the result with your confidence and notes setting.
9. Inspect `virtuoso --workspace PATH attempts --json` on the backend.
10. Reload the interface. The completed records should remain on the backend.

The interface supports one unaided retry and an optional hint. A blank recall cannot be marked demonstrated. A skip records a skip event and leaves the due date unchanged, so the CLI may offer the same item again.

The interface uses the standard built-in scheduler path. It never grants permission to execute a trusted external scheduler. Use the CLI's explicit permission flow for a workspace configured with an external scheduler.

## Connection and recovery

The backend issues a context identifier tied to its current process, configured paths, and workspace database identity. Item reads and writes must present that context. A changed backend, workspace setting, or replaced database rejects the old operation. The interface clears unfinished state when its connection context changes.

A failed write retains its request and submission ID in interface memory. Retry same submission resends those exact values. An already-recorded review returns a distinct confirmation rather than creating another attempt. Study completion returns the original event for the same item and learning-unit hashes. Nothing retries a write automatically.

Closing or reloading the page clears unfinished responses and pending request IDs. If a connection fails after a write, inspect the backend evidence before starting another attempt. The plugin does not promise offline queuing or recovery of an unfinished response after an app restart.

Missing backend routes, a disabled plugin, an unavailable executable, and an incorrect workspace show setup errors. The interface never initializes a workspace. Application errors use a typed JSON envelope. All plugin API responses disable HTTP caching. The backend accepts fixed operations only, with JSON bodies limited to 64 KiB, a 30-second subprocess limit, and a combined output limit of one megabyte.

## Upgrade or remove

Back up the workspace before a CLI version change. Update the CLI and both plugin components to the same reviewed revision. Restart the serving backend when its Python code changes. The desktop file can reload without restarting the app.

Disable the desktop component to hide the page. Remove the backend adapter with `hermes plugins remove virtuoso` on the backend when it is no longer wanted. Remove the corresponding desktop copy through the interface plugin controls or from its installation folder. Plugin removal leaves the Virtuoso CLI and learner workspace in place.

## Verification boundary

The repository includes real CLI/API tests and a browser fixture that runs the shipped module against a synthetic backend workspace. The fixture supplies a small SDK host and React components for automated interaction tests. It does not certify the appearance or connection configuration of every installed Hermes Desktop version. Complete the final MacBook-to-backend check in the actual app before using a personal workspace.
