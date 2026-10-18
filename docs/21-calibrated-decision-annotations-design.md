# 21. Calibrated decision annotations

Status: V1 shipped in #67; V3 to V7 proposed. Tracked by issue #65, with credentials in #68. Each slice ships as its own reviewed PR. `12-cli-reference.md` is the authority for shipped commands and fields; this record explains the design and the slices that remain.

Related: design 19 (prerequisite navigation and diagnostics, issues #58 and #59) is adjacent and stays independent.

## 1. What a calibrated decision model is here

A calibrated decision model answers typed questions about supplied text with a probability: a yes/no answer (`noul`), one label from a list (`choice`), or a position on a rubric (`score`). Such models answer grounded fact questions well. Opinion and verdict questions must never decide anything alone. The rule this design follows: **the model supplies evidence, policy lives in code.**

Three Virtuoso rules bound the design:

1. **Learning evidence is what the learner did.** The README says Virtuoso "refuses to infer competence from completion counts, streaks, or AI-generated answers." A model answer is never an attempt result, never a scheduler input, and never a mastery claim. It is an annotation beside the evidence.
2. **Local-first, no telemetry.** A hosted model is a remote API, so item text would leave the machine. The core cannot depend on it. A remote call needs explicit per-workspace consent, and a local backend stays an equal-status alternative.
3. **No model-generated content in the core.** This is the principle behind design 19's non-goal of no model-generated prerequisite maps or probes. A decision model generates no learning content. It only judges text that a human or an authored source supplied.

So the feature is an **annotation layer**: append-only typed judgments about existing records, produced by a swappable backend through the trusted-module boundary, read by digests and review surfaces, and ignored by `next`, `compose` and every scheduler.

## 2. What the repo had before V1

| Need | Existed | Gap |
|---|---|---|
| Honest recall evidence | `practice` (timed, answer hidden), `--administer`, self-grade, confidence 1 to 5, support actions, agent-help level | Nothing checks whether the self-grade matches the response. |
| Item authoring | `add`, curriculum candidates, human `accept` / `edit` / `skip` / `reject` | Draft validation is structural only. Nothing flags a leaked answer, an unanswerable prompt, or a near-duplicate. |
| Agent-administered sessions | `docs/13` protocol; the agent transcribes the learner's chat answer | The agent decides alone whether a chat message is an answer, a request to learn first, or a skip. |
| Transfer checks | `transfer check complete --teach-back --assistance` | Teach-back quality is unexamined. |
| Extension boundary | `virtuoso/module@0.1` scheduler modules with receipts and fail-closed checks | No annotator kind. |

A typical young workspace holds many items and few attempts. That is enough data for item-level checks and far too little for a grading check, which must run in shadow and accumulate.

## 3. Shipped in V1 (#67)

### 3.1 Annotation record

Migration 17 adds the append-only `annotations` table. Schema `virtuoso/annotation@0.1` carries `annotation_id`, `subject_kind`, `subject_id`, `subject_hash`, `question_set_id`, `question_set_hash`, `module_id`, `module_version`, `module_network`, `module_receipt_id`, `answers`, `occurred_at`, `surface`, `stale` and `claims_mastery: false`.

- V1 supports `subject_kind: item` only. The request projection deliberately excludes attempts, schedules and paths.
- Rows reject update and deletion. A re-run appends.
- Staleness compares against the indexed item hash. `annotate list`, `queries history --item` and `doctor` report stale annotations. Stale annotations never change the health status.
- `queries history --item` lists annotations under their own key, apart from attempts.

### 3.2 Annotator module kind

An annotator is a module with category `annotator` under `virtuoso/module@0.1`. Its manifest reads exactly `annotation.request`, returns `annotation-answers`, and declares `network: none` or `network: remote`. The same fail-closed checks as scheduler modules apply: manifest identity, no shell indirection, timeout, output bounds, no private-state paths, and no descendants. The core imports no SDK.

Commands:

```
virtuoso --workspace PATH annotate item --item ITEM --module MODULE_ID [--question-set ID] [--json]
virtuoso --workspace PATH annotate list [--item ITEM] [--json]
virtuoso --workspace PATH annotate questions [--json]
virtuoso --workspace PATH annotate allow-remote --yes | --revoke [--json]
```

A `network: remote` module exits 2 before it runs unless the workspace has consented with `annotate allow-remote --yes`. Consent is stored in `virtuoso.json` under `annotator.allow_remote`. The public default is off.

`examples/modules/rule-annotator/` is a dependency-free local annotator that demonstrates the contract.

### 3.3 Bundled question set

V1 bundles one question set in code, `item_quality_v1`, for items:

- `noul` `atomic`: the prompt tests exactly one idea.
- `noul` `answer_supports_prompt`: the answer fully resolves the prompt without adding new claims.
- `choice` `difficulty`: easy, moderate or hard for a learner who has met the focus once.
- `noul` `hint_leaks_answer`: the hint gives away the answer.

The hash of the question set's canonical JSON travels with every annotation, so a wording change never masquerades as the same question.

This replaces the earlier plan for versioned prose files (`questions/item-quality.md`) holding `answer_in_prompt`, `prompt_answerable_from_answer`, `yes_no_prompt`, `answer_is_definition_only` and `near_duplicate_of`. Bundling in code keeps the hash stable and the contract testable. Later question sets follow the same pattern: a new set id, never an edit to a shipped one. The leaked-answer and near-duplicate checks move to V6.

## 4. Remaining slices, each one PR

Order: V6, then V7, then V4. V3 and V5 wait until a workspace holds enough graded attempts to judge them.

### V6. Intake triage on capture (absorbs the earlier V2)

When a topic, link, note or curriculum candidate arrives, an agent today guesses its focus and entry mode, and a candidate gets structural checks only.

Questions over the captured text plus the workspace's focus list and existing item prompts:

- `choice` `focus` over the workspace's focus ids plus `none-of-these`.
- `choice` `entry_mode`: `recall-ready`, `learn-first`, or `wish-only` (no item yet, park in the backlog).
- `choice` `covered_by` over the focus's existing item ids plus `none` (near-duplicate check).
- `noul` `answer_in_prompt` when the capture already carries a prompt and answer.

Policy in code: a proposal row (focus, entry mode, covered-by, flags) attached to the candidate. The human still accepts, edits or skips. Nothing enters the item store on a model answer. Subject kind `candidate` arrives here, with its own projection and tests.

### V7. Focus ranking across the backlog and the item store (ranking over the backlog from #56)

The learner asks what deserves attention this week, and much of the answer is not yet in the item store.

- `score` `goal_relevance` 0 to 4 against the workspace's stated goals, with the goal named in a sibling `choice`.
- `noul` `prerequisite_missing`: the topic depends on something the workspace shows no evidence for.

Policy in code: rank by a fixed formula over the score with the yes/no answer as a tie-breaker, print the top five with probabilities and named reasons, and keep everything below the line visible. The learner confirms or reorders. The confirmed order is a `LearnerDecision`, the same record the compose step already writes. Nothing is scheduled by this.

### V4. Administered-session intent (agent side, no core change)

Free-text replies such as "hmm, I think I need to see this first" can be read as a recall answer one time and as a request to learn first another time. One `choice` `intent` over `answer`, `learn_first`, `go_more_fundamental`, `skip`, `stop` and `other`, with a confidence floor of 0.8, removes the guess. Below the floor the agent asks one plain question. This lands in an agent-side session skill and is listed here so the plan is complete.

### Later

- **V3 self-grade check.** An optional human-authored `facts:` list in item Markdown, one `noul` per fact over the response, run only after the self-grade is recorded, reported only in a per-focus digest. It never changes the attempt, the proposal or the due date. Subject kind `attempt` arrives here.
- **V5 teach-back digest.** `noul` questions over a transfer-check teach-back. Digest only.

### Tracers before anything gates a surface

- **T1, item quality.** Run `item_quality_v1` over a real workspace's items. A human spot-checks the flagged rows and a random sample of unflagged rows, and records precision on flags.
- **T2, self-grade agreement.** Shadow on every new attempt once V3 exists. Reviewed after 30 annotated attempts. Until then the digest is labelled `descriptive`.

## 5. What this excludes

- A decision model never grades, schedules, unlocks or places. No mastery inference from a model.
- No model-written items, hints or learning units through this path.
- No remote call without workspace consent. The public default is off.
- No telemetry. Annotations stay in the local SQLite like every other record.
- Credential handling for remote annotators is issue #68 and is not designed here.

## 6. Decisions

1. **Boundary.** Annotator is a trusted module kind. It reuses the fail-closed contract and keeps the core SDK-free. Agent-side only was rejected because it leaves no receipts and feeds no digests. A core dependency was rejected on the local-first rule.
2. **Remote consent.** A workspace may run a remote module after `annotate allow-remote --yes`. Item text then leaves the machine for that workspace only. `--revoke` withdraws consent. The public default is off.
3. **First tracer.** T1 item quality, because item data exists before attempt data does.
4. **Self-grade exposure.** The V3 check runs only after the self-grade is recorded and appears only in a digest. Showing coverage at reveal time would anchor the grade and taint the evidence.
5. **Order against #58 and #59.** Slices land independently. V1 touched `annotations.py`, `modules.py`, `workspace.py` (migration 17) and `cli.py`; design 19 works on the prerequisite map and probe tables.
6. **Question sets in code.** Shipped sets are frozen. A change ships as a new set id.
