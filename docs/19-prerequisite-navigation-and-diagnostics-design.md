# Prerequisite navigation and diagnostic placement design

Status: proposal. Nothing in this document is implemented. Tracks issues #58 (prerequisite navigation) and #59 (periodic diagnostics). #59 is blocked by #58.

## Problem

Virtuoso knows whether an item needs study or recall. It does not know where a learner sits on a concept path, and it has no move that takes the learner one step toward fundamentals while keeping the original target in view. Two observed failures during maintainer dogfood:

1. A learner asked for more fundamental material and was dropped several levels in one jump, wasting a session segment. The intended move was one prerequisite edge.
2. Nothing periodically re-checks where the learner sits as learning proceeds. The only calibration signals today are recall attempts, which are scored, scheduled, and therefore the wrong instrument for placement.

The learner's reference model is Math Academy, where diagnostic assessment is part of the curriculum and continuously calibrates the system's model of the student. Virtuoso adopts the diagnostic layer without the curriculum or mastery gating.

## Goals

1. A learner can move exactly one prerequisite edge per request, in either direction, with the original target retained.
2. Diagnostic probes exist as a typed event that is never scored as recall and never touches the scheduler.
3. Each focus carries a provisional placement derived only from probe events and self-report signals, shown with its evidence and uncertainty.
4. Session composition reads placement as one input and the learner confirms it before practice follows it.
5. The default probe cadence is driven by activity in the focus, with an optional weekly check-in for learners who want a calendar trigger.
6. Workspaces without prerequisite maps or probe banks behave exactly as today.

## Non-goals

- No mastery scores, unlock gating, or progression thresholds. Those need placement evidence to exist first and get their own issue.
- No model-generated prerequisite maps or probes in the core. A tutor may propose content for later author review; the core registers only what a human authored.
- No global learner level. Placement is per focus and stays unknown when evidence is sparse.
- No psychometric interpretation. A probe response is a data point, not a trait.
- No calendar scheduling in the core beyond the optional weekly setting. External schedulers deliver digests; they do not run probes.

## Design

### 1. Prerequisite map

A focus may carry one authored map file, `prerequisites.md`, next to its items, declaring directed edges between existing item ids:

```
---
schema: virtuoso/prerequisite-map@0.1
focus: model-calibration
---
- calibration-reliability-diagram requires calibration-percent-as-frequency
- calibration-percent-as-frequency requires proportions-percent-per-hundred
- proportions-percent-per-hundred requires proportions-part-of-whole
```

The map lives in Markdown because it is learner-authored prose about the curriculum, and Markdown owns prose. SQLite indexes it with a content hash so route proposals can bind to the exact map version. Editing the map never rewrites item files or item hashes.

Validation on index rejects: self-edges, cycles, duplicate edges, and references to unknown or retired items. A map that fails validation is not indexed and the previous index stays in force. `doctor` reports the failure and the offending line.

### 2. Navigation request and decision

One new command family, `navigate`, with `--json` on every subcommand:

- `navigate down --item ID` proposes the single prerequisite the learner should move to. With one incoming edge the choice is determined. With several, the proposal orders candidates deterministically: no placement evidence first (unknown beats known), then item-id order. The learner picks.
- `navigate up --item ID` proposes the single dependent item, same tie rule.
- `navigate decide --proposal P --choice ITEM|stay --familiar` records the learner's move.

A `NavigationDecision` is append-only and records: the proposal id, the map hash, the origin item and hash, the chosen item and hash, direction, whether the learner marked the origin as familiar, surface, and time. Marking an item familiar is a navigation signal only. It creates no attempt, study event, capability record, or scheduler state.

The original target is the origin of the first decision in a chain. `navigate status --item ID` returns the chain so the learner can see the path back. Returning is a `navigate up` decision like any other.

### 3. Learning at the chosen item

A `navigate decide` that lands on a learn-first item whose study is incomplete surfaces `action: learn` through the existing `next` contract; the learner runs `learn --item` as today and completion writes the existing hash-bound `StudyEvent`. A landing on an item with a completed study event offers the learning unit read-only through `learn --item ID --revisit`, which writes nothing. A landing on a recall-first item with no learning unit returns an explicit `missing-material` result naming the item; the core creates no substitute prose.

### 4. Diagnostic probe

A probe is an ordinary authored item carrying `practice-format: diagnostic-probe` in its frontmatter. Probe items never enter `next`, `practice`, `review due`, or composition as practice candidates. They exist only to be administered by `probe`.

`probe run --focus F [--count N]` selects up to N probe items (default 3, maximum 5) for the focus, deterministically: items with no probe event first, then oldest probe event first, then item-id order. It shows each prompt without a timer, records the verbatim response, and asks for one self-report per set: too fundamental, about right, too hard, unsure. The interface sets an observed-correctness flag per item by comparing the response with the answer; the flag is evidence context, not a grade.

`DiagnosticProbeEvent` is append-only: item id, item hash, verbatim response, observed correctness, surface, time, and the set id. `DiagnosticProbeSet` groups the events with the self-report and records dismissal when the learner declines the set in one action. Dismissal writes only the set row with `dismissed: true`.

Neither table is read by `practice.py`, `schedulers.py`, `workload.py`, or the transfer code. That boundary is a test, not a convention.

### 5. Placement projection

`queries placement --focus F --json` is read-only and returns one of:

- `unknown` when the focus has no probe set, or every set was dismissed.
- A provisional placement: the deepest item on the map whose probe evidence is consistent with understanding, the cited probe event ids and self-report, the map hash, the count of activities since the last set, and `stale: true` once that count crosses the threshold.

The rule is deliberately simple. Walk the map from its roots. An item counts as consistent when its most recent probe event has observed correctness true and the set's self-report is not "too hard". The placement is the last consistent item before the first inconsistent one on each path; with several paths, report each. Self-report alone can move placement one edge: "too fundamental" after a set moves the provisional placement one edge up; "too hard" moves it one edge down. Both moves are recorded as `NavigationDecision` rows with `source: self-report`.

Activity counts, minutes, streaks, and item volume never enter the rule. The projection never reads attempts or study events except to count activity for staleness.

### 6. Cadence

Activity is the count of attempts plus study completions in the focus since its last probe set. When that count reaches the threshold (default 10, configurable per focus in `virtuoso.json` under `diagnostics.activity_threshold`), the next `next` or `compose` call for that focus returns `action: probe` before any practice action. An idle focus is never probed for being idle.

Evidence triggers return `action: probe` regardless of the count: a focus with a map and probe bank but no probe set, and a contradiction, which is any self-report that moved placement in the previous set.

Weekly check-in is a workspace setting, `diagnostics.weekly_checkin: false` by default. When true, a focus whose last probe set is older than seven days also returns `action: probe`, and `export digest --out PATH` writes a read-only placement digest per focus: placement, cited evidence, staleness, and what changed since the last digest. The digest writes nothing to the database.

`action: probe` is dismissible; a dismissed set satisfies the trigger until the next threshold crossing or contradiction, so a learner who declines is not nagged every session.

### 7. Composition

`compose` reads the placement projection when the focus has a map. The proposal gains a `placement` block: the provisional placement, its uncertainty, and whether it is stale. A practice proposal never selects an item deeper on the map than the placement plus one edge; the rationale says so. The learner's existing `compose decide` accepts or overrides. A workspace without maps sees no `placement` block and no behaviour change.

### 8. Migrations

Two additive migrations, numbers pre-assigned to avoid a parallel-lane collision:

- Migration 17 (#58): `prerequisite_maps` (focus, content hash, indexed time), `prerequisite_edges` (map hash, from item, to item), `navigation_proposals`, `navigation_decisions`.
- Migration 18 (#59): `diagnostic_probe_sets`, `diagnostic_probe_events`.

Both re-verified against `main` when the branch is cut. Existing workspaces gain empty tables and no invented rows.

## Delivery plan

Two pull requests, each independently green and reviewable:

- PR A (#58): map format and validation, index and `doctor` reporting, `navigate` command family, `NavigationDecision`, `learn --revisit`, `missing-material` result, migration 17, CLI reference, domain model, architecture, release notes, tests, one synthetic end-to-end journey.
- PR B (#59): `diagnostic-probe` practice format, `probe` command, probe tables and events, placement projection, cadence and `action: probe`, weekly setting and digest export, composition `placement` block, migration 18, docs, tests, one synthetic end-to-end journey covering threshold, contradiction, dismissal, and the weekly setting.

## Acceptance

- [ ] Every acceptance criterion on #58 and #59 maps to at least one test in the corresponding PR, and the mapping is listed in the PR body.
- [ ] A synthetic four-item map fixture drives both journeys; no private learner content enters the repository.
- [ ] `practice.py`, `schedulers.py`, `workload.py`, and transfer code have no import of the probe or navigation modules, enforced by a test.
- [ ] Database bytes are unchanged after a rejected map, a dismissed probe set, and a `learn --revisit`.
- [ ] A workspace with no map and no probe items passes the existing suite without fixture changes.

## Open decisions for the maintainer

1. Map location and format. Recommendation: one `prerequisites.md` per focus with the schema above, so the map stays next to the items it links and Markdown keeps ownership.
2. Placement rule. Recommendation: the deepest-consistent-item walk in section 5, with self-report moving one edge. It is explainable in one sentence and needs no parameters.
3. Probe as a practice format versus a separate item kind. Recommendation: practice format `diagnostic-probe` on ordinary items, so authoring, hashing, and source links reuse everything that exists.
4. Probe bank size and selection. Recommendation: default 3, maximum 5, never-probed first.
5. Activity threshold default. Recommendation: 10 attempts plus study completions per focus, configurable.
6. Whether `action: probe` may interrupt a `compose` proposal that has a benchmark failure pending. Recommendation: no; benchmark-directed practice keeps precedence, and the probe waits for the next call.
7. Whether "familiar" on a learn-first item should also allow skipping its study step. Recommendation: no; familiar is a navigation signal, and study completion stays an explicit act, so the study ledger keeps meaning exposure.
