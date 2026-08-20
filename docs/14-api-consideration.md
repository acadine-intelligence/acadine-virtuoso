# API consideration

Should Virtuoso expose an API beyond the CLI? A decision note, not a commitment. Status: thinking, 2026-08-20; no API is built and none is owed by the current delivery contract.

## Surfaces that already exist

1. **The CLI with `--json`** — stable JSON output, documented exit codes, plain-language stderr errors. This is the current integration contract (see `docs/12-cli-reference.md` and `docs/13-agent-usage.md`).
2. **The Python services** — `WorkspaceService`, `PracticeService`, `CandidateService` are importable, typed, and tested. Any Python process can embed Virtuoso directly.
3. **The module contract** (`virtuoso/module@0.1`) — the extension surface: external local executables exchanging bounded typed JSON with consent, permissions, timeouts and receipts. This is how third-party capability is meant to enter.

## Options

**A. Keep the CLI as the API (status quo).** The `--json` surface already serves agents well: every agent harness can spawn a process and parse JSON. Zero new attack surface, zero daemon lifecycle, and the exit-code/error contract is already fail-closed. Cost: process spawn per call, no streaming, no shared state between concurrent consumers beyond the database's own locking.

**B. Formal Python SDK.** Publish the services as the supported library interface with versioned schemas. Cheap — they exist — but only serves Python consumers, and every other harness still needs the CLI.

**C. Local HTTP daemon.** A localhost-only server wrapping the same services. Enables long-lived consumers (a future UI, a menu-bar app, watch-mode dashboards) and streaming sessions. Costs a lifecycle to manage, an auth story even on localhost, and a second contract to keep honest. This is the first option that adds real capability (streaming, subscriptions) rather than repackaging existing ones.

**D. MCP server.** Exposes Virtuoso as MCP tools for MCP-capable harnesses. Deliberately deferred: it duplicates the CLI contract inside a protocol, serves only MCP-speaking harnesses, and the project's harness-neutral stance argues for the boring universal surface (a CLI) first. If an MCP adapter is ever wanted, it should be a thin module over the same services — and could itself ship as a `virtuoso/module@0.1` extension rather than core code.

## Recommendation

Stay on A, keep B informal (import at your own risk until a versioning policy exists), and treat C as the only future step that needs a trigger. The trigger: a second consumer class that the CLI genuinely cannot serve — a visual interface needing streaming, or multiple concurrent consumers contending on process spawns. When that consumer is real and named, design the HTTP surface against the same service layer and the same fail-closed evidence rules, with the CLI contract as its reference behavior.

Do not build an API to make the tool feel more complete. The CLI-as-API is a deliberate position: one contract, one behavior, every harness already speaks subprocess.

## What would change the decision

- A visual interface enters `experience.surfaces` in `product.json` and needs streaming sessions.
- A second harness needs concurrent access that database locking alone does not serve well.
- Evidence from dogfooding that process-spawn ergonomics are the actual friction for agents (measure first).
