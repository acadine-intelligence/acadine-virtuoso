# Rule annotator example

This dependency-free example demonstrates Virtuoso's annotator module contract. It answers the bundled `item_quality_v1` question set from the item text with fixed heuristics. It calls no model and opens no network connection, so its manifest declares `network: none`. The answers show the contract; they do not judge items well.

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

What the module receives: the item title, focus, prompt, answer, hint, follow-up, learning context and entry mode, plus the question set. It never receives attempts, schedules, or file paths. What Virtuoso records: the answers, the module receipt, the item hash and the question set hash. Selection, composition and every scheduler ignore the result.

A module that sends the subject to a remote model must declare `network: remote`. Virtuoso refuses to run it until the learner records consent for that workspace with `virtuoso annotate allow-remote --yes`.
