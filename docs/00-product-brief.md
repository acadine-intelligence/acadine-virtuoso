# Product brief

Acadine Virtuoso is a local-first active-recall CLI. It turns user-owned Markdown and current work into one worthwhile challenge, records what the attempt demonstrated, and proposes when and how to practise next.

The first use case is maintainer dogfood. The initial slice stays small: initialize a workspace, add a manually authored recall item, attempt it before seeing the answer, record latency and assistance, and obtain a transparent FSRS-backed next-review proposal.

## Outcome

The learner retrieves important ideas reliably, retrieves them faster over time, and transfers them into real projects without mistaking activity, XP, tests, or agent output for independent capability.

## Hero workflow

1. `virtuoso init` creates a local Markdown and SQLite workspace.
2. `virtuoso add` creates one active-recall item.
3. `virtuoso practice` presents the prompt with the answer hidden and starts the timer.
4. The learner attempts recall, then may retry, request a hint, or reveal worked feedback.
5. Virtuoso records correctness, latency, confidence, open-notes state, and help used.
6. The chosen scheduler proposes the next review and explains its algorithm, configuration, and context.
7. The learner can inspect the append-only attempt record.

## Boundary

Simple mode requires no identity model, virtue compass, roadmap, skill tree, Obsidian, Hermes, or cloud account. Direction-led planning, project transfer, integrations, and meta-scheduling are later modules over the same evidence and extension contracts.
