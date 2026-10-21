# Rule annotator example

This dependency-free example demonstrates Virtuoso's annotator module contract. It answers the bundled `item_quality_v1` and `intake_triage_v1` question sets with fixed heuristics. It calls no model and opens no network connection, so its manifest declares `network: none`. The answers show the contract; they do not establish item quality or readiness.

Copy this directory into the workspace and apply the required private modes:

```bash
mkdir -p workspace/modules
cp -R examples/modules/rule-annotator workspace/modules/rule-annotator
chmod 700 workspace/modules workspace/modules/rule-annotator
chmod 600 workspace/modules/rule-annotator/virtuoso.module.json
```

If `python3` does not identify the intended interpreter, replace the first manifest `command.argv` value with the explicit absolute path printed by:

```bash
python3 -c 'import sys; print(sys.executable)'
```

Run it against one item and read the result:

```bash
virtuoso --workspace workspace annotate item --item <item-id> --module rule-annotator --json
virtuoso --workspace workspace annotate list --item <item-id>
```

Run triage on a review candidate:

```bash
virtuoso --workspace workspace annotate candidate --candidate <candidate-id> --module rule-annotator --json
virtuoso --workspace workspace candidate show --id <candidate-id>
virtuoso --workspace workspace annotate list --candidate <candidate-id> --json
```

The triage rules preserve a proposed focus if it is among the supplied options, otherwise choose `none-of-these`. A prompt and answer produce `recall-ready`; otherwise the result is `wish-only`. `covered_by` chooses the first active item whose prompt matches exactly, or `none`. `answer_in_prompt` is 0.9 when the candidate answer occurs in its prompt, ignoring case, and 0.1 otherwise. When there is no prompt and answer pair, the request omits that question and the module omits its answer. These are illustrative rules, not a calibrated assessment; the module never chooses `learn-first`.

For items, the module receives the title, focus, prompt, answer, hint, follow-up, learning context and entry mode. For candidates it receives title, kind, rationale, proposed prompt, answer, hint and focus when present, focus and active item options, existing items' ids, titles and prompts, and whether the leaked-answer question applies. It never receives attempts, schedules, file paths or existing items' answers. Every request carries its question set. Virtuoso records the answers, module receipt, subject hash and static question set hash. A changed option list makes candidate annotations stale. Candidate displays label the latest triage as a model proposal; only the human's `candidate decide` can accept, edit, skip or reject. Selection, composition and every scheduler ignore the result.

A module that sends the subject to a remote model must declare `network: remote`. Virtuoso refuses to run it until the learner records consent for that workspace with `virtuoso annotate allow-remote --yes`.
