# Agent usage guide

How an AI agent (Hermes, Pi, Claude Code, Codex, or any harness) should drive Virtuoso. No MCP server, no plugin: the CLI with `--json` is the integration surface. This document is written to be pasted into an agent's context or linked from an `AGENTS.md`.

## The five rules

1. **Always pass `--json`.** Human output is `key: value` lines; JSON output is the stable machine contract. Parse stdout, never scrape the human format.
2. **Check the exit code.** 0 means success; 2 means a domain error. On 2, read stderr (`Error: ...`), which is written to be actionable, and either fix the input or surface it to the human. Never retry the same failing input unchanged.
3. **Never fabricate evidence.** Attempts, transfer events and check completions are append-only records of what a real learner did. An agent may administer a session and transcribe the learner's answers; it may not invent results, and `--agent-help` / `--assistance` must honestly reflect any help the agent gave. When an agent runs a smoke test itself, record it as `substantial`, not `none`.
4. **Respect the boundaries.** Candidates are proposal-only — there is no apply command, and acting on one is a decision made outside Virtuoso. Sources are read-only. Scheduling and selection changes come from recorded evidence, never from an agent editing the database.
5. **Never edit the SQLite database directly.** All state changes go through the CLI (or the Python services behind it). The schema is fail-closed: tampering is detected on next open and the workspace refuses to start.

## Natural language to command mapping

Translate learner intent into commands like this:

| The human says... | Run |
|---|---|
| "Set up my learning workspace" | `virtuoso --workspace PATH init --json` |
| "What should I practice?" / "quiz me" / "what's next" | `next --json` — show the prompt, hide everything else |
| "Quiz me on <track>" / "today is a Go day" | `next --focus <track> --json` — selection scoped to one focus; a track with no items returns exit 2 with a clear error |
| "Add this as a practice item" | `add --id ... --title ... --focus ... --prompt ... --answer ... [--hint ...] [--follow-up ...] --json` |
| "Let's practice X" / "test me on X" | `practice --item X --agent-help <honest level>` (interactive — see the session protocol below) |
| "How is my learning going?" / "show my evidence" | `attempts --json` and/or `doctor --json` |
| "Is my workspace healthy?" | `doctor --json` |
| "Connect my Obsidian vault / notes folder" | `source add --id ... --kind obsidian|markdown --path ROOT --json` |
| "Re-index my notes" | `source scan --id ... --json` |
| "What notes do you see?" | `source notes --id ... --json` |
| "Link this item to that note" | `source link --id ... --path ... --item ... --json` |
| "What does this note suggest working on?" | `candidate generate --source ... --path ... --json` then `candidate list --current-only --json` |
| "Show me the proposals" | `candidate list --json` (optionally `--kind atomic-note|link|practice`) |
| "I used this in a real project" | `transfer record --item ... --project ... --use-case ... --outcome ... --independence ... --json` |
| "What's due for a transfer check?" | `transfer check due --json` |
| "Start my transfer check" | `transfer check begin --check ... --prediction "..." --json` — record the prediction BEFORE any help |
| "Finish my transfer check" | `transfer check complete --check ... --attempt ... --assistance ... --acceptance-evidence ... --teach-back ... --outcome ... --json` |

## Running a practice session from an agent

`practice` is interactive on stdin/stdout. An agent administering a session (e.g. a morning pulse in a chat) has two honest options:

**Option A — relay to the human (preferred).** Tell the human to run the command in their terminal, or relay prompts and answers through the chat. The learner's own answers and self-grading are the evidence.

**Option B — drive the protocol programmatically.** Feed stdin lines in protocol order and read stdout. The sequence is:

```
stdin:  <y|n>                     # Notes open?
stdout: Challenge + prompt shown
stdin:  <free-text recall>        # timed — answer BEFORE any reveal
stdout: Initial recall time reported
stdin:  retry | hint | reveal     # repeatable until reveal; hint adds "Response after hint:"
stdout: reference answer shown
stdin:  demonstrated | partial | not-demonstrated
stdin:  <1-5>                     # confidence
[if not demonstrated and item has a follow-up]
stdin:  <follow-up response>
stdout: Evidence line + next review proposal
```

Rules for scripted sessions: the recall answer must come before any `reveal`; a blank recall may not be graded `demonstrated` (the CLI rejects it); `--agent-help` must reflect the agent's actual contribution; latency is measured by the tool, not claimed by the agent.

## Standard agent workflows

**Morning pulse.** `next --json` → present only the prompt and title → after the human answers, administer `practice` for that item → close with the printed next-review time. Optionally `transfer check due --json` first, since due checks outrank routine review.

**Capture a concept.** After a work session, the agent drafts an item (prompt/answer/hint/follow-up) from the material and calls `add --json`. The human reviews the Markdown file in `workspace/items/` — items are human-owned prose.

**Connect knowledge.** `source scan --id ... --json` after vault changes; `candidate generate` on notes under active study; present candidates as suggestions for the human to accept, modify or reject outside the tool.

**Record application.** After real project work using a practiced concept: `transfer record` with honest `--independence`. When the delayed check comes due: `begin` (prediction first), the human attempts the challenge, then `complete` with honest `--assistance`.

## Failure handling for agents

- `Error: no learning item with id: ...` → list or re-check the id; do not guess.
- `Error: item is stale because its Markdown changed` → the item file was edited; do not practice it until the workspace is re-synced; surface to the human.
- `Error: scheduler state changed during practice` → concurrent modification; re-run `next` and start a fresh session.
- `Error: source ...` on scans or links → path or staleness problem; run `doctor --json` and surface the stale entry.
- Empty `checks` from `transfer check due --json` is success, not an error.
