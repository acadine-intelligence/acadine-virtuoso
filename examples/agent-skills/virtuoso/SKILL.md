---
name: virtuoso
description: Use when the user wants to learn, review or be quizzed on what they are building, or asks "what should I practise next". Drives the local Virtuoso CLI to capture concepts from the current project as recall items and to run honest recall sessions in chat.
compatibility: Requires the `virtuoso` CLI on PATH and a workspace path in VIRTUOSO_WORKSPACE.
---

# Virtuoso

Virtuoso is a local learning tool. Items are Markdown the human owns. Evidence (attempts, study events) is append-only SQLite that only the CLI writes. Your job is to help the human understand the work you do together, so they can explain how and why it works.

Always run `virtuoso --workspace "$VIRTUOSO_WORKSPACE" <command> --json`. Parse stdout. Exit 2 is a domain error: read stderr, fix the input or tell the human. Never retry the same failing input unchanged. Never touch the SQLite file.

## Capture: after real work

When you finish a change the human should be able to explain, propose one to three items. Each item tests one idea from this project, in the human's words where possible.

1. Show the drafts (id, title, focus, prompt, answer, optional hint) and ask which to keep.
2. For each kept draft: `add --id ID --title T --focus F --prompt P --answer A [--hint H] --json`.
3. If the human says the material is new to them, add `--entry-mode learn-first --learning-unit "short explanation"`.

Good prompts ask why or how ("Why does the worker retry with jitter instead of a fixed delay?"). Avoid yes/no prompts and prompts that contain the answer. Use the project name as the focus, lowercase with hyphens.

## Recall: "quiz me" or "what next"

1. Run `next --json` (add `--focus F` to stay in one project). It returns `action`, `item_id`, `title` and `prompt`, never the answer. Branch on `action`.
2. `learn`: run `review study-load --item ITEM --json` and show `item.learning_unit`. Wait. Only when the human says they finished, pipe this request to `review study-record --json`, copying `item.content_hash` into `item_content_hash` and `item.learning_unit_hash` into `learning_unit_hash`:
   `{"schema":"virtuoso/study-completion@0.1","item_id":ITEM,"item_content_hash":HASH,"learning_unit_hash":HASH,"completed":true,"surface":"cli"}`
3. `practice`: show only the title and prompt. Do not read the item file, guess or hint at the answer. Ask for their answer and a confidence from 1 to 5.
4. Only after you have both, run `review load --item ITEM --json` and show `item.answer`. Ask them to grade themselves: `demonstrated`, `partial` or `not-demonstrated`. Suggest a grade only if asked, and say it is your suggestion.
5. Record it: `practice --item ITEM --administer --response '<their exact words>' --result <grade> --confidence N --json`. Single-quote the response so the shell stores it unchanged, and write each `'` inside it as `'\''`. Add `--agent-help light` only if you did nothing beyond relaying the prompt and the answer.
6. Show the stored record and the next due date. Offer one more item or stop.

## Honesty rules

- Without `--agent-help`, an administered attempt records `substantial`, because you ran the exchange. Never pass `none`.
- Never invent a response, grade or confidence.
- If the human declines before seeing the prompt, record nothing. If they saw the prompt and give up, ask for their confidence and record it as `not-demonstrated` with their words or an empty response. If they decline to give a confidence, record 1 and tell them you did.
- A blank answer is never `demonstrated`.
- Do not pipe scripted input into interactive `practice`; it fakes latency.

## Useful reads

- `queries workload --json`: due counts per focus.
- `queries history --item ITEM --json`: one item's evidence.
- `doctor --json`: workspace health.

## Workspace portability

When the human asks to move their workspace, use `export workspace --out FILE --json` with an archive path outside the workspace. It includes their Markdown, configuration, modules and evidence. Restore it with `import workspace --archive FILE --into DIR --json`; this command needs no `--workspace`, and `DIR` must be absent or empty. Check the returned `doctor.status`. Import never runs module code. Both commands return domain errors on stderr in the existing JSON error envelope.
