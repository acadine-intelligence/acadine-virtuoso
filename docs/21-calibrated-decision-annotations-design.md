# 21. Calibrated decision annotations (Jev / RLCD) in Virtuoso

Status: accepted design, tracked by issue #65. Nothing here is implemented yet. Each slice ships as its own reviewed PR.

Related: design 19 (prerequisite navigation and diagnostics, issues #58 and #59) is adjacent and stays independent. The maintainer's private evaluation of a calibrated-decision API on non-Virtuoso data informed the accuracy figures below; that evaluation is not part of this repository.

## 1. What Jev is in this repo, and what it is not

Jev (TypeSafe) answers typed questions about supplied text with a calibrated probability: Noul (yes/no probability), Choice (one label from a list), Score (a position on a rubric). 300 to 500 ms, about $0.001 per call. Tracers on Acadine data showed grounded fact questions at 0.988 to 1.000 accuracy and opinion or verdict questions that must never decide anything alone. Standing rule from both tracers: **the model supplies evidence, policy lives in code.**

Three Virtuoso rules bound the design harder than the RLCD rule does:

1. **Learning evidence is what the learner did.** README: Virtuoso "refuses to infer competence from completion counts, streaks, or AI-generated answers." Agent guide rule 3: never fabricate evidence. A Jev answer is therefore never an attempt result, never a scheduler input, never a mastery claim. It is an annotation beside the evidence.
2. **Local-first, no cloud, no telemetry.** Jev is a remote API. Item text and learner responses would leave the machine. The core cannot depend on it. Any remote call needs an explicit per-workspace opt-in and a local backend (openjev, or TypeSafe's open LLM adapter over a local model) as the equal-status alternative.
3. **No model-generated content in the core** (design 19 non-goals). Jev generates nothing, which is why it fits: it only judges text a human or an authored source supplied.

So the feature is not "Jev grades my practice". It is an **annotation layer**: append-only typed judgments about existing records, produced by a swappable backend through the existing trusted-module boundary, read by digests and review surfaces, ignored by `next`, `compose` and every scheduler.

## 2. What the repo already has

| Need | Exists today | Gap |
|---|---|---|
| Honest recall evidence | `practice` (timed, answer hidden), `--administer` (latency unknown), self-grade demonstrated / partial / not-demonstrated, confidence 1 to 5, support actions, agent-help level | Nothing checks whether the self-grade matches the response. A learner's grading calibration is invisible. |
| Item authoring | `add`, curriculum import via `candidate generate --adapter curriculum`, human `accept` / `edit` / `skip` / `reject` | Draft validation is structural only (fields, JSON, hashes). Nothing flags an unanswerable prompt, an answer leaked in the prompt, or a near-duplicate of an existing item. |
| Agent-administered sessions | `docs/13` protocol; the agent transcribes the learner's chat answer | The agent decides by itself whether a chat message is an answer, a request to learn first, a "go more fundamental", or a skip. Design 19 will add typed navigation signals but nothing classifies free text into them. |
| Transfer checks | `transfer check complete --acceptance-evidence --teach-back --assistance`, scorer kind self / human / tool / agent | Teach-back quality is unexamined. |
| Extension boundary | `virtuoso/module@0.1`: one trusted local executable, typed projection in, typed result out, receipts, fail-closed, scheduler kind only | No annotator kind; the boundary carries only `scheduler.request`. |
| Evidence in the maintainer's private workspace | About 200 items, under ten attempts with responses | Enough items for an item-quality tracer. Far too few attempts for a grading tracer; that one must run in shadow and accumulate. |

## 3. Design

### 3.1 Annotation record

New append-only table `annotations` (schema `virtuoso/annotation@0.1`), never read by selection or scheduling:

- `annotation_id` (deterministic from subject hash + question-set hash + backend id)
- `subject_kind` (`attempt`, `item`, `candidate`, `transfer-check-completion`) and `subject_id`
- `subject_hash` (the item hash or attempt row hash at annotation time; drift makes the annotation stale, same rule as study events)
- `question_set_id` and `question_set_hash` (the question file is versioned prose in the repo, one file per subject kind)
- `backend` (`module:<id>`), `backend_version`, `module_receipt_id`
- `answers` JSON: per question id, the typed value, probabilities, confidence where the primitive has one
- `created_at`, `surface`

Rules: one annotation never updates another; a re-run appends. `doctor` lists stale annotations. `queries history --item` shows them under a separate heading labelled "annotation, not evidence".

### 3.2 Annotator module kind

Extend `virtuoso/module@0.1` with kind `annotator`. Projection in: the subject fields the question set names (item prompt, reference answer, learner response, item list for duplicate checks). Result out: `answers` in the shape above. The same fail-closed checks as scheduler modules apply (manifest identity, no shell indirection, timeout, output bounds, no private-state paths, no descendants).

Backends are modules, so the core never imports an SDK:

- `jev` module (private workspace only): calls the maintainer's local wrapper for the TypeSafe API with a key held outside the repository. Requires `annotator configure --allow-remote true` on the workspace; the manifest declares `network: remote` and the CLI refuses to run it without the flag. This is the consent line.
- `openjev` module: local NLI cross-encoder, `network: none`. Blocked today on local GPU memory.
- `llm-adapter` module: TypeSafe's open System One adapter over a local model, `network: none`, for baselines.

The public repo ships the contract, the question files, the module manifest schema and an example module that answers from a fixture. Real backends stay outside the repo, like personal paths do today.

### 3.3 Question sets, one file each, thresholds beside them

**`questions/item-quality.md`** (subject: item or candidate draft)

- Noul `answer_in_prompt`: the prompt text contains or paraphrases the reference answer.
- Noul `prompt_answerable_from_answer`: a competent learner who knew the reference answer could produce it from this prompt alone.
- Noul `yes_no_prompt`: the prompt is answerable with yes or no.
- Noul `answer_is_definition_only`: the reference answer names the concept without stating a mechanism or example.
- Choice `near_duplicate_of` over the focus's item ids plus `none` (state carries the other prompts).

Policy in code: any Noul above 0.7 or a duplicate Choice above 0.7 with confidence above 0.6 adds a `quality_flags` list to the candidate or item in `candidate list` and `queries learning`. **Nothing is rejected.** The human still chooses accept / edit / skip.

**`questions/self-grade-check.md`** (subject: attempt; runs only after the self-grade is recorded)

- One Noul per authored rubric fact: "the response states that `<fact>`". Rubric facts are an optional `facts:` list in the item Markdown, authored by a human. Items without a facts list get one Noul, `response_matches_reference`, labelled weaker.
- Noul `response_blank_or_evasive`.

Policy in code: compute `rubric_coverage` (mean of fact Nouls). Agreement table: demonstrated expects coverage above 0.7, partial 0.3 to 0.7, not-demonstrated below 0.3. Disagreement is recorded as an annotation field, shown in a per-focus digest (`queries grading --json`: attempts, disagreement count, direction), and **never** changes the attempt, the proposal or the due date. Exposure rule: the check never runs before the self-grade exists, so it cannot anchor the learner.

**`questions/administered-intent.md`** (subject: a learner chat message during an administered session; agent side, Hermes skill, not core)

- Choice `intent` over `answer`, `learn_first`, `go_more_fundamental`, `skip`, `stop`, `other`.

Policy: confidence above 0.8 lets the agent proceed on that reading; below it the agent asks one plain question. This lands in the Hermes `spaced-repetition-session-protocol` skill and needs no core change; it is here so the plan is complete.

**`questions/teach-back.md`** (later): Nouls over the transfer-check teach-back (states the mechanism, states a limit, names where it applied). Digest only.

### 3.4 Tracers before anything gates a surface

- **T1, item quality.** Run `item-quality` over the maintainer's private items and every open curriculum candidate. The maintainer spot-checks the flagged rows and a random 20 unflagged. Record precision on flags and the undecided band. Cost under $0.05. This is the first tracer because the data exists today.
- **T2, self-grade agreement.** Shadow on every new attempt. Reviewed once 30 attempts carry annotations. Until then the digest prints with the label `descriptive`.

## 4. Slices, each one PR

- **V1 Annotation contract.** Table, migration, `annotator` module kind, `annotate --subject-kind --subject-id --module ID --json`, `annotator configure --allow-remote`, doctor and history surfaces, fixture module, tests for every fail-closed path. No question sets active.
- **V2 Item quality.** `questions/item-quality.md`, `quality_flags` on `candidate list` and `queries learning`, T1 run in the private workspace, results into the workspace backlog.
- **V3 Self-grade check.** `facts:` list in item Markdown (optional), `questions/self-grade-check.md`, post-grade shadow run, `queries grading`.
- **V4 Administered intent** (Hermes skill only).
- **V5 Teach-back digest.**

V1 and V2 fit one session together. They touch `modules.py`, `workspace.py` (migration), `candidates.py`, `cli.py`, `queries.py`; none of the files design 19 will touch (`learning_state.py`, `composition.py`, a new `prerequisites` module).

## 4b. Priority pick

The maintainer asked for the few pieces that most improve smoothness, speed, the automation of bringing items into the space, and deciding what deserves focus even when it is not in the database yet. Three picks, in build order. V3 (self-grade check) and V5 (teach-back) drop to later; the data for them is too thin to matter now.

### Pick 1. Intake triage on capture (new slice V6, absorbs V2)

Where: the moment a topic, URL, note or "add this to Virtuoso" line arrives (Discord drop, vault capture, curriculum note). Today an agent reads it and guesses the track and entry mode; a curriculum candidate gets structural checks only.

Questions over the captured text plus the workspace's focus list, existing item prompts and the north-star compass:

- Choice `focus` over the workspace's focus ids plus `none-of-these`.
- Choice `entry_mode`: `recall-ready` (the learner has already met this), `learn-first`, `wish-only` (no item yet, park in the backlog).
- Choice `covered_by` over the focus's existing item ids plus `none` (near-duplicate check, same as V2).
- Score `goal_relevance` 0 to 4 against the compass goals, with the goal named in a sibling Choice.
- The V2 item-quality Nouls when the capture already carries a prompt and answer.

Policy in code: a proposal row (focus, entry mode, covered-by, relevance, flags) attached to the candidate or the backlog row. The human still accepts, edits or skips; nothing enters the item store on a model answer. Effect: a drop becomes a one-decision review instead of a hand-classified capture.

### Pick 2. Focus ranking across the backlog and the item store (new slice V7, the ranking half of issue #56)

Where: the want-to-learn backlog (vault note today, wish store later), open curriculum candidates, and items with no attempt. The question the learner asks is "what deserves my attention this week", and most of the answer is not in the database yet.

State per row: the topic, the goal it claims to serve, the compass goals, and a work-signal summary (repos and vault folders touched in the last 14 days, listed as names, not contents).

- Score `goal_relevance` 0 to 4 and Score `work_pull` 0 to 4 (how strongly current work is touching this topic), each with the named goal or repo in a sibling Choice.
- Noul `prerequisite_missing`: the topic depends on something the workspace shows no evidence for.

Policy in code: rank by a fixed formula over the two Scores with the Noul as a tie-breaker, print the top five with the probabilities and the named reasons, keep everything below the line visible. The learner confirms or reorders; the confirmed order is a `LearnerDecision`, same record the compose step already writes. Cadence: weekly digest, and on demand in the pulse. Nothing is scheduled by this; it feeds session composition as one input.

### Pick 3. Administered-session intent (V4, unchanged)

The chat session is where smoothness is lost today: a message like "not sure, need lessons" was read as a recall answer once and as a jump to fundamentals another time. One Choice with a confidence floor of 0.8 removes the guess; below the floor the agent asks one plain question. Hermes skill only, no core change, one session.

### Order

V1 (contract) then V6 then V7 then V4. V6 and V7 share the state-building code (focus list, item prompts, compass, work signals) so they land as two PRs from one session. Tracer for V6 runs over the existing backlog rows and the last 20 vault captures with the maintainer's hand labels; tracer for V7 compares the ranked top five with the maintainer's own pick on the same week.

## 5. What this excludes

- Jev never grades, schedules, unlocks or places. No mastery inference from a model.
- No model-written items, hints or learning units through this path.
- No remote call without the workspace flag; the public default is off.
- No telemetry. Annotations stay in the local SQLite like every other record.
- No RL of any kind; the name RLCD refers to how TypeSafe trained their model.

## 6. Decisions

1. **Boundary.** Annotator is a trusted module kind. It reuses the fail-closed contract and keeps the core SDK-free. Agent-side only was rejected because it leaves no receipts and feeds no digests; a core dependency was rejected on the local-first rule.
2. **Remote consent.** A private workspace may run a remote module behind `annotator configure --allow-remote true`; item text and learner responses then leave the machine for that workspace only. The public default is off.
3. **First tracer.** T1 item quality, because the data exists.
4. **Self-grade exposure.** The check runs only after the self-grade is recorded and appears only in a digest. Showing rubric coverage at reveal time would anchor the grade and taint the evidence.
5. **Order against #58 and #59.** V1 lands now as an independent slice. The files differ.
