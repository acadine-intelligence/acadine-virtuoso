# Product brief

Acadine Virtuoso is a local-first learning and active-recall CLI. It turns user-owned Markdown and current work into one worthwhile learning or practice action, records each activity under its correct evidence type, and proposes when to practise again after recall.

The first use case is maintainer dogfood. The initial path stays small: initialize a workspace, add a manually authored item, study first when the material is unfamiliar, attempt recall before seeing the answer, record latency and assistance, and obtain a transparent FSRS-backed next-review proposal.

## Outcome

The learner retrieves important ideas reliably, retrieves them faster over time, and transfers them into real projects without mistaking activity, XP, tests, or agent output for independent capability.

## Hero workflow

1. `virtuoso init` creates a local Markdown and SQLite workspace.
2. `virtuoso add` creates one recall-first or learn-first item.
3. `virtuoso next` returns a typed action and its reason.
4. A learn-first action shows the learning unit and records explicit study completion without starting a schedule.
5. A practice action presents the prompt with the answer hidden and starts the timer.
6. The learner attempts recall, then may retry, request a hint, or reveal worked feedback.
7. Virtuoso records correctness, latency, confidence, open-notes state, and help used.
8. The chosen scheduler proposes the next review and explains its algorithm, configuration, and context.
9. The learner can inspect separate append-only study and recall records.

## Boundary

Simple mode requires no identity model, virtue compass, roadmap, skill tree, Obsidian, Hermes, or cloud account. Direction-led planning, project transfer, integrations, and meta-scheduling are later modules over the same evidence and extension contracts.
