# Delivery contract: active recall, source index, and project-transfer checks

## Observable outcomes

This iteration is complete only when all of the following work from a clean local checkout:

1. Python 3.11 bootstrap installs the pinned project and FSRS dependency into `.venv`.
2. `virtuoso --workspace PATH init` creates one isolated workspace with configuration, Markdown items directory, and migrated SQLite state.
3. `virtuoso --workspace PATH add ...` creates a valid manually authored recall item without exposing private fixture data.
4. `virtuoso --workspace PATH practice --item ID` shows the prompt before the answer, measures initial recall with a monotonic clock, supports retry then hint then worked answer, and records the result, confidence, notes state, response text, and support sequence.
5. The completed attempt appends immutable evidence and an FSRS next-review proposal that names algorithm `fsrs`, installed version, configuration, learning context, source event, and due time.
6. `virtuoso --workspace PATH attempts --json` returns the recorded attempt and proposal without claiming mastery.
7. The extension boundary validates and invokes one explicitly approved trusted local executable through `virtuoso/module@0.1`, while tests prove unknown fields/schema, missing consent, shell command, malformed output, timeout, oversized output, private-state projections, and manifest identity drift fail closed. This boundary is not an OS sandbox.
8. `virtuoso --workspace PATH doctor --json` reports a healthy workspace after the journey and reports actionable errors for missing or damaged state.
9. `virtuoso --workspace PATH source add ...` connects an existing Markdown folder or Obsidian vault as an explicit read-only source without making Obsidian or Hermes a runtime dependency.
10. `source scan` indexes only bounded note metadata, hashes, and wikilinks; it never writes to source files or copies note bodies into SQLite, and symlink escapes fail closed.
11. `source link` binds a manually authored learning item to the exact indexed source-note hash, while `doctor` reports a linked note that changed or disappeared.
12. `transfer record` appends one project-application event bound to the exact learning-item hash with outcome, independence, artifact reference, reflection, and a seven-day delayed-check date; it cannot claim mastery.
13. `transfer list` returns project evidence without merging it into recall attempts, scheduler state, XP, or capability claims.
14. `transfer check create` links one immutable, manually authored changed/novel challenge, acceptance criteria, and scorer to an existing transfer event and inherits its validated due date.
15. `transfer check due` lists pending and started incomplete checks chronologically without writing state or becoming a scheduler/project-priority recommendation.
16. `transfer check begin` appends one prediction at or after the due time before the learner attempts the changed challenge or requests help.
17. `transfer check complete` appends one independent attempt with assistance attribution, scorer-bound acceptance evidence, teach-back, outcome, optional inert artifact reference, timestamps, and `claims_mastery: false`; it cannot update scheduler, project selection, or capability state.
18. The full unit/integration suite, compile check, Build OS verification, representative CLI journeys, exact-commit independent review, and clean-worktree check pass.

## Automated verification

The implementation must declare and execute:

- Python compile check for `src` and `tests`.
- Complete `unittest` suite.
- Build/install smoke check in the project-local virtual environment.
- Deterministic synthetic CLI journey.
- Module-boundary security tests.
- Project-transfer attribution and stale-item tests.
- Build OS `verify` against the exact commit.

The CLI has no visual surface, network service, browser console, or responsive layout in this slice. Accessibility is verified through keyboard-only prompts, no color dependency, stable plain text, and JSON output tests.

## Representative user journey

Actor: a new local learner using synthetic data.

Input: initialize `/tmp/virtuoso-journey`, add a question about the testing effect, attempt once without help, reveal and grade it, inspect the attempt, and run doctor.

Observable result: answer remains hidden until the learner chooses reveal; the output shows measured latency; SQLite contains one attempt and one FSRS proposal with attributable metadata; doctor is healthy; the repository remains free of runtime data.

A second synthetic journey records a retry and hint before a failed grade, proving the support ladder and attribution do not become a competence claim.

## Independent review

A non-implementing reviewer inspects the exact Git commit for:

- product and delivery-contract fit;
- answer-hiding and timing logic;
- learning-evidence attribution;
- FSRS integration and scheduler-state isolation;
- SQLite transactions and migration safety;
- module process security, timeout, output bounds, and schema validation;
- privacy, path handling, and absence of personal data;
- maintainability and test gaps.

Any critical/high finding or contract failure returns the slice to implementation.

## Evidence packet

Evidence must bind to the exact commit and retain command argv, cwd, exit code, full local logs, bounded JSON excerpts, module receipts, and known limitations. Runtime workspaces remain outside Git. No remote or public push is part of this contract.
