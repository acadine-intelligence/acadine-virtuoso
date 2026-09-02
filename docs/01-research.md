# Research baseline

## Direct evidence

The foundation of effective learning is based on principles around spaced repetition, active recall and the testing effect. Recall latency should fall over time. Practice should connect to tests, computational exercises, and authentic project use. Virtue and destiny structures are optional; simple bottom-up learning is first-class.

The current local deployment has five pilot learning items and an SM-2-lite path-bound script. It proves there is real material and a scheduling need, but it does not run the full recall journey, isolate per-workspace data, or expose a safe extension contract.

## External references

- Open Spaced Repetition's `py-fsrs` provides an established FSRS implementation, serializable card state, UTC scheduling, and optional parameter optimization. Virtuoso will integrate it rather than recreate FSRS: https://github.com/open-spaced-repetition/py-fsrs
- Anki's official FAQ confirms modern Anki offers both its SM-2-derived scheduler and FSRS. This supports a scheduler-portfolio design rather than a single hard-coded algorithm: https://faqs.ankiweb.net/what-spaced-repetition-algorithm
- Math Academy and LeetCode are interaction references for adaptive challenge and computational application. They are not dependencies or sources of private learner state.

## Alternatives

Anki and Obsidian are strong for atomic cards, but do not own Virtuoso's project-transfer and assistance-attribution loop. A full LMS would add curriculum and account scope before the selection/evidence model is proven. Manual project learning preserves authenticity but makes retrieval timing and comparable evidence expensive.

## Decision

Use Python 3.11, SQLite, user-owned Markdown, and `py-fsrs` for the first atomic-recall scheduler. Keep algorithm state isolated so later contexts can choose another established scheduler. Third-party v0 modules run out of process through a versioned JSON contract; no in-process plugin loading yet.
