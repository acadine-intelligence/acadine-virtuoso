# Server and MCP design

Status: proposal, 2026-10-09; review findings applied 2026-10-18. Tracking issue: #69. The "Core changes" section is built (migration 19, `practice --administer --graded-by`, the surface allow lists, and the 0.2 query schemas). Migration 19 also carries the attempt `client` column and the pending-attempt hint, recording time and expiry rules, so the server needs no further migration for phase 1. The server itself is not built yet. Beyond those core changes, this record does not expand the interfaces in `12-cli-reference.md`.

## Decision

Virtuoso gets one open-source server, `virtuoso serve`. It exposes a fixed set of learning operations over the Model Context Protocol (MCP) and over plain HTTP JSON. The same code runs in two ways:

- **Self-hosted.** A learner runs the server against their own workspace, on their own machine or host.
- **Hosted.** An operator runs the same server for many learners. Each learner gets an isolated workspace. The operator supplies sign-in and account management in front of the server.

The server is MIT-licensed like the rest of the repository. The CLI stays the reference behavior. The standalone CLI path and its runtime dependencies do not change. CLI JSON contracts change only where the core changes listed under "Core changes" add fields. Each changed output that carries a schema id gets a version bump.

## Why now

`14-api-consideration.md` deferred an HTTP daemon (its option C) until a consumer appeared that the CLI cannot serve, and deferred MCP (option D) with no trigger. Two such consumers now exist, and both need MCP over a network:

1. **Remote chat and voice clients.** A learner practising out loud in a chat assistant, or in an assistant's voice mode, has no terminal and no local process to spawn. The client can only reach a network MCP server.
2. **Agents on other machines.** An agent harness that runs away from the workspace needs a network contract with authentication, not a subprocess.

An agent that can run a shell on the workspace machine (for example Pi, Claude Code, Codex or Hermes) keeps using the CLI with `--json` directly. That path needs no server and stays the reference integration. The server is for clients that cannot spawn a process.

The Hermes Desktop adapter (`20-hermes-desktop.md`) already proves the pattern: fixed operations, typed JSON envelopes, size and time limits, context identity, and idempotent writes. This design generalizes that adapter into a core server any client can use.

## Shape

```text
MCP client (chat app, voice mode, agent harness)      HTTP client
        |  stdio or streamable HTTP                        |  POST /v1/ops/<name>
        v                                                  v
   virtuoso.server: one operation registry, two bindings (MCP tools, HTTP routes)
        |
        |-> workspace resolver (principal -> one workspace root)
        v
   ReviewService, PracticeService, WorkspaceService, queries   (existing core services)
        |
        v
   Markdown items + SQLite evidence (one workspace per learner)
```

One operation registry defines every operation once: name, input schema, output schema, and whether it reads or writes. The MCP binding and the HTTP binding are generated from that registry, so the two surfaces cannot drift.

The server calls the Python services in process. It does not spawn the CLI per call. The CLI and the server share the same services, validation, writer lock, and fail-closed errors.

### Packaging

- New module `virtuoso.server`, behind an optional extra: `pip install "acadine-virtuoso[server]"` or `uvx --from "acadine-virtuoso[server]" virtuoso serve`.
- The extra adds the official MCP Python SDK and an ASGI server, with pinned versions. The base install keeps its current two dependencies.
- `virtuoso serve` without the extra exits with a plain error that names the install command.

### Transports

| Transport | Use | Default auth |
|---|---|---|
| `--transport stdio` | A local app that speaks MCP but has no shell tool spawns the server, and harness evals use it to test the MCP contract. | Process boundary. No token. |
| `--transport http` | Remote clients, hosted deployment, voice clients. | Bearer token or operator gateway, on every bind. |

`http` serves MCP streamable HTTP at `/mcp` and the HTTP JSON binding at `/v1/ops/<name>`. It binds `127.0.0.1` by default. Every HTTP bind requires an authentication mode, including loopback: a local endpoint is reachable from any browser tab through DNS rebinding or a cross-origin request. The server also validates `Host` against the configured bind names, rejects any request whose `Origin` header is present and not on a configured allow list (non-browser MCP clients send no `Origin`, and that is allowed), and answers no CORS preflight. A request that fails these checks gets 403 and writes nothing.

## Operations, phase 1

Phase 1 covers the daily loop only: choose the next action, study, recall, record, and inspect.

| Operation | Kind | Core call |
|---|---|---|
| `next_action` | read | `select_next` plus the learning state: returns `learn` or `practice`, item id, title, and focus. Never the answer. |
| `study_load` | read | The learning unit for a learn-first item, without prompt or answer. |
| `study_record` | write | Records study completion only after the learner says they finished. A repeat for the same item and learning-unit hashes returns the stored completion instead of the core's "already completed" error, so a client retry is safe. |
| `recall_start` | write | Opens a recall attempt. Returns the prompt, hint availability, and an attempt token. Never the answer. |
| `recall_respond` | write | Stores the learner's transcribed response against the attempt token. Required before reveal. |
| `recall_reveal` | read | Returns the reference answer, and only after `recall_respond` for the same token. |
| `recall_record` | write | Records result, confidence, `agent_help`, and who graded. A learner-graded result writes the attempt, scheduler proposal, and state in one transaction. An agent-graded result writes the attempt only. Idempotent on `submission_id`. |
| `recall_skip` | write | Appends a skip event. The due date stays unchanged. |
| `workload` | read | Due counts per focus, the same projection as `queries workload`. |
| `history` | read | Evidence for one item, the same projection as `queries history` (`virtuoso/item-history@0.2` after the core changes below). That projection carries result, confidence, `agent_help`, timing and annotations. It never carries the prompt, the reference answer or a stored response, so it cannot leak the answer for a due item. A test pins this. |

### The answer stays hidden

The current review contract returns the answer in the item snapshot and trusts the interface to hide it. A chat model is not a trustworthy interface for that rule: once the answer is in its context, it can leak it. So the server splits recall into `recall_start`, `recall_respond`, `recall_reveal`, and `recall_record`. The server holds the open attempt and refuses to reveal the answer before a response exists. A blank response cannot be graded `demonstrated`, matching the CLI.

The open attempt lives in the workspace database as a pending row with an expiry. The pending-attempt table is new. A restarted server can still finish or expire it. The row also stores the surface, the client name, when a hint was shown (`hint_shown_at`) and when the attempt was recorded (`recorded_at`), so `hint_used` survives a restart. The database enforces the expiry: a response, a hint or a recording time after `expires_at` is rejected, so an expired attempt records nothing even if the server misses the expiry. The recorded attempt must match the row's item version, response, surface and client.

### Evidence honesty

- `surface` records the client, for example `mcp` or `http`, plus the client name the caller supplies. The name is stored in the attempt's `client` column, which is `null` when no name is given and for CLI attempts.
- Latency is stored as unknown. The server cannot measure when a learner started thinking, and a client clock is a claim, not a measurement. This matches `practice --administer`.
- `agent_help` is required on `recall_record` and has no default of `none`. It is the existing attempt field with the existing values (`none`, `light`, `substantial`, `unknown`). The tool description tells the model to report its own help honestly.
- `graded_by` is required: `learner` or `agent`. It is a new attempt column and needs a migration in phase 1.
- `learner` means the learner chose the grade, even when an agent relayed it. This is what `practice --administer` records today, and it keeps feeding the scheduler on both the CLI and the server.
- An agent-graded result is stored as evidence only. It does not feed the scheduler, does not change a due date, and is excluded from the attempt rows that composition reads to choose the next target. Phase 1 adds `--graded-by` to `practice --administer` with the same rule, defaulting to `learner`, so the CLI and the server agree. That changes only after a tracer shows agent grades agree with learner grades on the same attempts, and after a maintainer decision recorded here. This follows the README rule against inferring competence from AI-generated output.
- Every write returns the stored record, so the client can show the learner exactly what was saved.

### Core changes

Phase 1 changes the core in at least these places. The phase 1 implementation PR fixes the exact seams and lists any further change in its description:

- One migration adds the pending-attempt table and three attempt columns: `graded_by`, `surface` and `client`. Before it, attempts had no `surface` column; skips, study events, annotations and composition decisions did. The same migration makes attempts append-only: an update or deletion is rejected, so a stored grade cannot move from `agent` to `learner`.
- The attempt write path gains a mode that records an agent-graded attempt without a scheduler proposal or state change. Today `WorkspaceService.record_attempt` requires both, and `ReviewService.record` and `practice --administer` reach it through `PracticeService`, so the change spans `PracticeService` and `WorkspaceService.record_attempt`. If `recall_record` reuses the review request contract, that contract gains `graded_by` and a new schema version.
- Composition ignores agent-graded attempts when it chooses the next target.
- `practice --administer` gains `--graded-by learner|agent`, default `learner`.
- The study surface allow list (`hermes-desktop`, `obsidian-plugin`, `cli`) and the skip surface allow list (`obsidian-plugin`, `hermes-desktop`) add `mcp` and `http`.
- `attempts --json` (which has no schema id) and the new `export workspace` carry the new columns. `queries history` adds `graded_by` to each attempt, which bumps the schema to `virtuoso/item-history@0.2`. `queries workload` counts due items from scheduler state, so agent-graded attempts never change it. `queries focus` keeps its outcome counts and mean confidence over learner-graded attempts only and adds an `agent_graded` count, which bumps the schema to `virtuoso/focus-performance@0.2`. `attempts`, `administered` and `mean_latency_ms` are learner-only too, so `attempts` stays the sum of the outcome counts.

### Not exposed

These stay CLI-only, on every transport: workspace `init`, item `retire`, source connection and scans, candidate import decisions, scheduler configuration and switches, trusted modules and external schedulers, and annotator configuration. Phase 2 may add `item_add`, because capturing a concept from a conversation is a core journey, but only for the single-item form with human-owned Markdown.

The server never passes `allow_trusted=True`. A workspace configured with an external scheduler rejects remote recall writes with an error that names the CLI path.

## Authentication and tenancy

The server has one open interface for identity: a **workspace resolver**. It turns an authenticated principal into exactly one workspace root, or refuses.

| Mode | Principal | Resolver |
|---|---|---|
| stdio | The local process owner. | The fixed `--workspace` path. |
| http, single learner | Holder of `VIRTUOSO_SERVER_TOKEN`. | The fixed `--workspace` path. |
| http, operator | A subject asserted by a trusted gateway. | One directory per subject under `--workspaces-root`, created on first use only when the operator enables provisioning. The directory name is the hex SHA-256 of the NFC-normalized issuer and subject pair, never the raw subject. |

Single-learner mode is the self-hosted default. The token comes from an environment variable or a file, is compared in constant time, and is never logged.

Operator mode expects an authentication gateway in front of the server. The gateway implements OAuth 2.1 for MCP clients and forwards a signed principal assertion. The server verifies that assertion with a configured public key, rejects unsigned or expired assertions, and rejects any request that arrives without one. A hosted operator may write their own gateway. The repository ships the verifier and the resolver, not an account system, a billing system, or a sign-in page.

Hashing the subject means a subject such as `../x`, a path separator, or unusual Unicode cannot escape the root or collide with another subject. The resolver also checks that the resolved path is a direct child of `--workspaces-root`.

Each principal maps to its own workspace root, its own SQLite database, and its own writer lock. No operation accepts a workspace path or an item from another principal. A resolver error never reveals whether another principal exists.

## Hosted data

Hosting moves learner prose and evidence off the learner's machine. That is a real change to the product's local-first promise, so the hosted mode carries these obligations:

- **Opt-in.** The standalone CLI never contacts a server. Self-hosting remains a complete option.
- **Export.** `virtuoso export workspace --out FILE`, a new subcommand beside `export obsidian`, writes the complete workspace (Markdown, database, configuration) as one archive. The hosted operator exposes the same export to the learner. An exported workspace opens with the local CLI unchanged.
- **Import.** A learner can move a local workspace to a hosted one, and back, without losing evidence.
- **Deletion.** The operator deletes a learner's workspace on request, including backups within a stated period. The repository cannot enforce this. It is an operator obligation, and a self-hoster owns it for their own backups.
- **Client models see returned text.** Whatever a tool returns (prompts, responses, answers) reaches the client's model provider under the learner's own account with that provider. The server sends nothing to any model itself. The documentation states this plainly.

## Environments

| Environment | Purpose | Data |
|---|---|---|
| Local development | Unit and contract tests, `codex exec` and other harness evals over stdio. | Synthetic temporary workspaces only. |
| Self-hosted | A learner's own server. | The learner's workspace. |
| Staging | A separate hosted instance for testers and feedback. Testers file issues labelled `from-usage`. | Synthetic or tester-owned workspaces. Never a maintainer's personal workspace. |
| Production hosted | Operator-run service for learners. | Per-learner workspaces under the hosted data rules. |

Staging and production run the same released image. A change reaches production only after it passes staging.

## Limits and failure behavior

The server reuses the Hermes adapter limits as starting values: JSON bodies up to 64 KiB, a 30-second operation limit, and a one-megabyte response limit. Every error uses the existing `virtuoso/review-error@0.1` style envelope with a stable code and a plain recovery action. Domain errors map to MCP tool errors (`isError: true`) and to HTTP 4xx. Server faults map to HTTP 5xx and never leave a partial write. A stale item, a changed scheduler state, or an expired attempt returns its own code so a client can recover without guessing.

## Phases

1. **Server core.** Operation registry, both bindings, stdio and token-authenticated HTTP with `Host` and `Origin` checks, single-learner token mode, the core changes listed above, phase 1 operations, the split recall contract, and `export workspace`. Evaluated with real MCP clients over stdio against synthetic workspaces.
2. **Hosted staging.** Operator mode, signed-assertion verifier, per-principal resolver (acceptance: subjects such as `../x`, `a/b` and Unicode lookalikes map to distinct directories inside `--workspaces-root`, with a test proving no escape and no collision), import, a container image, and a staging deployment for testers. Deployment and hosting spend need maintainer approval.
3. **Chat and voice clients.** A client package (skills plus the MCP connection) for chat assistants, tested in developer mode first. The first test answers one open question: whether the target assistant's voice mode calls MCP tools at all.
4. **Public listing.** Store submission and public hosted sign-up. Needs maintainer approval.

## Acceptance for phase 1

- A fresh install without the extra keeps the current CLI behavior and dependency set. A test pins this.
- `virtuoso serve --transport stdio` completes the hero workflow with an MCP client against a synthetic workspace: next action, study, recall start, respond, reveal, record, and history.
- `recall_reveal` before `recall_respond` fails and writes nothing.
- A replayed `recall_record` with the same `submission_id` returns the original record and writes nothing new.
- The MCP and HTTP bindings return identical records for the same operation sequence.
- HTTP on any address, loopback included, refuses to start without an authentication mode. A wrong or missing token gets 401 and writes nothing.
- A request with a foreign `Host` or `Origin`, or a CORS preflight, gets 403 and writes nothing.
- An agent-graded `recall_record` stores the attempt and leaves the scheduler state and due date unchanged.
- `practice --administer --graded-by agent` does the same on the CLI. Without the flag, the attempt records `graded_by: learner` and feeds the scheduler as today.
- `next` returns the same output before and after an agent-graded attempt is recorded. `compose` returns the same content; its proposal id and timestamp come from the clock.
- `queries history` returns `virtuoso/item-history@0.2` with `graded_by` on each attempt. `queries focus` returns `virtuoso/focus-performance@0.2`, whose outcome counts exclude agent-graded attempts and whose `agent_graded` count includes them.
- Study and skip writes from the `mcp` and `http` surfaces are accepted, and an unknown surface is still rejected.
- `history` output for a due item contains no prompt, reference answer or response text.
- No operation accepts a path, a module id, or `allow_trusted`.
- Every write's stored evidence shows `surface`, the client name, unknown latency, the reported `agent_help`, and `graded_by`.
- An exported workspace opens and passes `doctor` with the CLI on a clean machine.

## Open questions

- Whether a target chat assistant's voice mode calls MCP tools. Phase 3 answers this with a developer-mode test before any client package work.
- How the hint is released. A hint comes after `recall_start` and before `recall_reveal`, and the current contract records `hint_used`, so the split contract probably needs a `recall_hint` operation between them. The storage is settled: the pending row's `hint_shown_at` is set once, while the row is open or responded and before it expires.

## Relationship to earlier decisions

This record replaces the recommendation in `14-api-consideration.md`. It keeps that note's conditions: the server is a thin layer over the same services, uses the same fail-closed evidence rules, and treats the CLI contract as reference behavior. Option D there suggested shipping MCP as a `virtuoso/module@0.1` extension. That does not fit, because modules are executables the core calls, while a server is a process that calls the core. The server lives in core behind an optional extra instead.
