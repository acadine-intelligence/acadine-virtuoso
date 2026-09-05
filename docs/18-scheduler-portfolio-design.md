# Scheduler portfolio design

Status: sections 1, 2, 4, and 5 are implemented (built-in portfolio, `scheduler switch`, fail-closed guard, attribution). Section 3 (external schedulers through the module boundary) remains a proposal. Tracks issue #47.

## Problem

Virtuoso records honest scheduling evidence (algorithm, version, configuration, previous and proposed state, rationale) but only one algorithm can produce it. `practice.py` builds FSRS proposals directly, `workspace.py` rejects any `scheduler.algorithm` other than `fsrs`, and the storage layer special-cases FSRS when it validates a proposal's due timestamp. A learner who wants a different algorithm, or who wants to run their own, has to fork.

The extension boundary already names `scheduler` as a module category and defines a `scheduler-proposal` result, so the shape of "bring your own algorithm" exists on paper. It has never been wired to practice.

## Goals

1. A learner selects a scheduler in `virtuoso.json` with no code change.
2. A learner can run their own scheduler as a trusted local module under the existing `virtuoso/module@0.1` boundary.
3. Every proposal keeps carrying `algorithm`, `algorithm_version`, `configuration`, previous and proposed state, `due_at`, and a rationale, whatever produced it.
4. Changing algorithm never mixes state and never happens silently.
5. FSRS stays the default. Existing workspaces keep working unchanged.

## Non-goals

- No network, cloud, or telemetry. Schedulers run locally.
- No automatic conversion of memory state between algorithms. A stability value from FSRS has no honest meaning to SM-2.
- No scoring of which algorithm is better. That is a separate evidence question and belongs with the benchmark work, once two algorithms can run.

## Design

### 1. One internal backend protocol

New module `src/virtuoso/schedulers.py` defines the contract every built-in implements:

```
class SchedulerBackend(Protocol):
    name: str                 # stable id, e.g. "fsrs", "sm2"
    version: str              # what gets stored as algorithm_version

    def validate_configuration(self, raw: Mapping[str, object]) -> dict[str, object]:
        """Return the normalized configuration or raise SchedulerConfigurationError."""

    def propose(
        self,
        *,
        previous_state: str | None,   # JSON text as stored, or None for a new item
        attempt: AttemptFacts,        # result, confidence, occurred_at, latency_ms | None, administered
        configuration: dict[str, object],
    ) -> SchedulerOutcome:            # proposed_state (JSON text), due_at (aware), rationale
```

`practice.py` stops importing `fsrs` and asks the registry for the configured backend. The current FSRS code moves behind this protocol unchanged in behaviour: same rating map (demonstrated → Good, partial → Hard, not-demonstrated → Again), same configuration keys, same version and configuration mismatch guards, same rationale text. The existing test suite is the regression net for this move.

The storage-layer check that the proposed state's `due` field matches `due_at` becomes a backend method (`due_from_state`) so the workspace validates every algorithm the same way instead of special-casing FSRS.

### 2. Built-in portfolio: FSRS and SM-2

Two built-ins ship in this slice.

- `fsrs` (default): unchanged. Attribution stays as documented in `17-acknowledgements.md`.
- `sm2`: the SuperMemo SM-2 algorithm as published by Piotr Wozniak (1990). Implemented from the published description, not copied from any existing implementation, so the MIT licence holds. State: `easiness` (start 2.5, floor 1.3), `repetitions`, `interval_days`, `due`. Intervals: 1 day, then 6 days, then previous interval × easiness. A failed response resets repetitions to zero without touching easiness. Response quality map: demonstrated → 4, partial → 3, not-demonstrated → 1. Configuration: `first_interval_days` (default 1), `second_interval_days` (default 6), `minimum_easiness` (default 1.3). Version string: `sm2-1990/1` (algorithm/implementation revision).

Why SM-2 and not a fixed ladder: SM-2 is what most learners already recognise from Anki and older tools, so it is the algorithm people will actually want to compare FSRS against. A fixed-interval ladder is more useful as a teaching example of a module, and that is where it goes (section 3).

Why not more: each built-in is a permanent maintenance and attribution commitment. Two is enough to prove the protocol and give a real choice.

### 3. External schedulers through the module boundary

`scheduler.algorithm` accepts `module:<module-id>`. Practice then invokes the module through `ModuleRunner` with `allow_trusted=True`, exactly as the other categories do today, and treats the result as a `SchedulerOutcome`.

Request (stdin), category `scheduler`:

```
{
  "schema": "virtuoso/module-request@0.1",
  "category": "scheduler",
  "item_id": "...",
  "learning_context": "atomic-recall",
  "attempt": {"result": "partial", "confidence": 3, "occurred_at": "...", "latency_ms": 4210, "administered": false},
  "previous_state": {...} | null,
  "configuration": {...}
}
```

Result (stdout), type `scheduler-proposal`, extended with one required field:

```
{
  "due_at": "...", "algorithm": "<module-id>", "algorithm_version": "...",
  "learning_context": "atomic-recall", "configuration": {...},
  "proposed_state": {...},          # new, required
  "rationale": "..."
}
```

`proposed_state` is required because a scheduler without state cannot be re-run honestly. The result type has no external consumers yet (the category was never reachable from practice), so this is added to `scheduler-proposal` now rather than versioned. The change is recorded in the delivery contract and release notes.

Core validates before storing: `algorithm` equals the configured module id, `learning_context` matches, `due_at` is timezone-aware and not before `occurred_at`, `proposed_state` is an object whose `due` matches `due_at`. Modules never receive a database path and never write the database. That is unchanged.

A worked example ships under `examples/modules/fixed-ladder/`: a fixed-interval ladder (1, 3, 7, 14, 30 days; failure resets) in one dependency-free Python file with its manifest. It doubles as a baseline for future algorithm comparisons.

### 4. Switching algorithms fails closed

Scheduler state is keyed by `(item_id, algorithm, learning_context)`, so two algorithms never overwrite each other. The risk is silence: a learner edits `virtuoso.json`, and every item quietly restarts as new under the other algorithm.

Rule: if any `scheduler_state` row exists for algorithm A in the configured context and the configuration now names B, then `practice`, `review record`, `next`, `review due`, and `doctor` fail with:

```
scheduler algorithm changed from fsrs to sm2 without a recorded switch;
run: virtuoso scheduler switch --to sm2
```

`virtuoso scheduler switch --to <algorithm> [--json]` validates the target (built-in or module manifest), writes the configuration, and records one append-only row in a new `scheduler_switches` table: from, to, context, mode, item count with prior state, timestamp. Mode in this slice is `fresh` only: B sees every item as new at its first attempt; A's state and proposals stay as history and remain queryable. `review due` and workload read only the configured algorithm. The switch is reported in `doctor`.

`switch` is the one new command. A `carry-due` mode (seed B with only A's current due date, no invented memory parameters) is a possible later addition and is deliberately out of this slice.

### 5. Attribution

`17-acknowledgements.md` gains an SM-2 entry naming Wozniak and SuperMemo, and states that the implementation is written from the published algorithm. Any future built-in adds an entry in the same change that adds the code.

## Delivery plan

Two pull requests, each independently reviewable and green:

- PR A: backend protocol, FSRS behind it, SM-2 built-in, configuration validation, `scheduler switch`, fail-closed guard, tests, docs, attribution.
- PR B: `module:<id>` path, `proposed_state` in the module contract, fixed-ladder example module, end-to-end tests, docs.

## Acceptance

- [ ] `virtuoso init` still produces an FSRS workspace; every existing test passes without modification to fixtures.
- [ ] A workspace with `scheduler.algorithm: sm2` completes attempt → proposal → due → workload with no code change; proposals record `sm2`, its version, and its configuration.
- [ ] Backend contract tests run against every built-in: deterministic output for identical input, `due_at` never before `occurred_at`, state round-trips, `due_from_state(proposed_state) == due_at`.
- [ ] Editing `scheduler.algorithm` on a workspace with existing state makes the five commands above fail closed with the switch instruction; `scheduler switch` clears it and records the row.
- [ ] The example module produces proposals that core accepts; a module result missing `proposed_state`, or with a `due_at` before the attempt, is rejected and nothing is stored.
- [ ] Database bytes are unchanged when a module run is rejected.
- [ ] CLI reference, architecture, delivery contract, release notes, and acknowledgements are updated in the same PRs as the code.

## Open decisions for the maintainer

1. Second built-in: SM-2 as proposed, or none (module-only) to keep the core smaller.
2. Response-quality map for SM-2 (4 / 3 / 1). Confidence is stored as evidence but does not change the schedule, matching how FSRS is driven today.
3. Whether `scheduler switch` should also be the only way to change `desired_retention` or other configuration, or whether configuration edits keep today's behaviour (mismatch guard at the next attempt).
