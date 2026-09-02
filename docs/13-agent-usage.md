# Agent usage guide

How an AI agent (Hermes, Pi, Claude Code, Codex, or any harness) should drive Virtuoso. No MCP server, no plugin: the CLI with `--json` is the integration surface. This document is written to be pasted into an agent's context or linked from an `AGENTS.md`.

## The five rules

1. **Always pass `--json`.** Human output is `key: value` lines; JSON output is the stable machine contract. Parse stdout, never scrape the human format.
2. **Check the exit code.** 0 means success; 2 means a domain error. On 2, read stderr (`Error: ...`), which is written to be actionable, and either fix the input or surface it to the human. Never retry the same failing input unchanged.
3. **Never fabricate evidence.** Attempts, transfer events and check completions are append-only records of what a real learner did. An agent may administer a session and transcribe the learner's answers; it may not invent results, and `--agent-help` / `--assistance` must honestly reflect any help the agent gave. When an agent runs a smoke test itself, record it as `substantial`, not `none`.
4. **Respect the boundaries.** Structural candidates remain proposal-only. Curriculum import candidates may create an item only after a human chooses `accept` or `edit`. `skip` and `reject` create no item. Sources stay read-only. A historical due value never becomes scheduler state, and an import never creates attempt or transfer evidence.
5. **Never edit the SQLite database directly.** All state changes go through the CLI (or the Python services behind it). The schema is fail-closed: tampering is detected on next open and the workspace refuses to start.

## Natural language to command mapping

Translate learner intent into commands like this:

| The human says... | Run |
|---|---|
| "Set up my learning workspace" | `virtuoso --workspace PATH init --json` |
| "What should I practice?" / "quiz me" / "what's next" | `next --json`: show the prompt, hide everything else |
| "Quiz me on <track>" / "today is a Go day" | `next --focus <track> --json`: selection scoped to one focus; a track with no items returns exit 2 with a clear error |
| "Add this as a practice item" | `add --id ... --title ... --focus ... --prompt ... --answer ... [--hint ...] [--follow-up ...] --json` |
| "Let's practice X" / "test me on X" | `practice --item X --agent-help <honest level>` (interactive; see the session protocol below) |
| "Quiz me in chat" / learner answered out-of-band | `practice --item X --administer --response "..." --result ... --confidence N --json` (agent transcribes; latency stored as unknown) |
| "How is my learning going?" / "show my evidence" | `attempts --json` and/or `doctor --json` |
| "Is my workspace healthy?" | `doctor --json` |
| "Connect my Obsidian vault / notes folder" | `source add --id ... --kind obsidian|markdown --path ROOT --json` |
| "Re-index my notes" | `source scan --id ... --json` |
| "What notes do you see?" | `source notes --id ... --json` |
| "Link this item to that note" | `source link --id ... --path ... --item ... --json` |
| "What does this note suggest working on?" | `candidate generate --source ... --path ... --json` then `candidate list --current-only --json` |
| "Import the practice items declared in this note" | `candidate generate --source ... --path ... --adapter curriculum --dry-run --json`; show the proposals before a decision |
| "Accept, edit, or skip this import" | `candidate decide --id ... --decision accept|edit|skip ... --json`; use edit flags only after the human supplies the changed fields |
| "Check that curriculum note for changes" | `candidate delta --source ... --path ... --json`; empty output means no change and needs no notification |
| "Show me the proposals" | `candidate list --json` (optionally `--kind atomic-note|link|practice`) |
| "I used this in a real project" | `transfer record --item ... --project ... --use-case ... --outcome ... --independence ... --json` |
| "What's due for a transfer check?" | `transfer check due --json` |
| "Start my transfer check" | `transfer check begin --check ... --prediction "..." --json`: record the prediction BEFORE any help |
| "Finish my transfer check" | `transfer check complete --check ... --attempt ... --assistance ... --acceptance-evidence ... --teach-back ... --outcome ... --json` |

## Running a practice session from an agent

`practice` is interactive on stdin/stdout. An agent administering a session (e.g. a morning pulse in a chat) has two honest options:

**Option A: relay to the human (preferred).** Tell the human to run the command in their terminal, or relay prompts and answers through the chat. The learner's own answers and self-grading are the evidence.

**Option B: administered mode (`--administer`).** When the learner answers through the agent (chat, voice) instead of the terminal, run one non-interactive command after the exchange:

```
practice --item X --administer --response "<learner's transcribed answer>" \
  --result <graded outcome> --confidence <1-5> [--agent-help <level>] --json
```

The attempt is marked `administered`, latency is stored as NULL/unknown (the tool measured nothing), and `--agent-help` defaults to `substantial`. Ask the learner for their answer and confidence BEFORE revealing the reference answer, exactly as the interactive protocol would. Do not pipe scripted stdin into interactive `practice`: that fabricates a near-zero latency measurement and pollutes the evidence.

**Driving the interactive protocol directly (rarely appropriate).** Feed stdin lines in protocol order and read stdout — only sensible when a human is typing at a relayed live terminal, because the measured latency must belong to the learner. The sequence is:

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

**Morning pulse.** `next --json` → present only the prompt and title → after the human answers in chat, record it with `practice --administer` (or relay an interactive session) → close with the printed next-review time. Optionally `transfer check due --json` first, since due checks outrank routine review.

**Capture a concept.** After a work session, the agent drafts an item (prompt/answer/hint/follow-up) from the material and calls `add --json`. The human reviews the Markdown file in `workspace/items/`; items are human-owned prose.

**Connect knowledge.** Run `source scan --id ... --json` after source changes. Use the default `candidate generate` for metadata-only suggestions. Use `--adapter curriculum --dry-run` when the human selects a note that declares complete practice items. Present every proposal. Record the human's `accept`, `edit`, `skip`, or `reject` decision through `candidate decide`. A scheduled `candidate delta` run should send nothing when stdout is empty.

**Record application.** After real project work using a practiced concept: `transfer record` with honest `--independence`. When the delayed check comes due: `begin` (prediction first), the human attempts the challenge, then `complete` with honest `--assistance`.

## Failure handling for agents

- `Error: no learning item with id: ...` → list or re-check the id; do not guess.
- `Error: item is stale because its Markdown changed` → the item file was edited; do not practice it until the workspace is re-synced; surface to the human.
- `Error: scheduler state changed during practice` → concurrent modification; re-run `next` and start a fresh session.
- `Error: source ...` on scans or links → path or staleness problem; run `doctor --json` and surface the stale entry.
- Empty `checks` from `transfer check due --json` is success, not an error.
