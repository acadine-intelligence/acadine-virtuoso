# Delivery contract: typed learning, evidence-ranked composition, active recall, source index, and project-transfer checks

## Observable outcomes

This iteration is complete only when all of the following work from a clean local checkout:

1. Python 3.11 bootstrap installs the pinned project and FSRS dependency into `.venv`.
2. `virtuoso --workspace PATH init` creates one isolated workspace with configuration, Markdown items directory, and migrated SQLite state.
3. `virtuoso --workspace PATH add ...` creates a valid manually authored recall-first or learn-first item without exposing private fixture data.
4. `virtuoso --workspace PATH next --json` returns a typed `learn` or `practice` action with exact current hashes and no answer content.
5. `virtuoso --workspace PATH compose --json` returns a `virtuoso/focus-proposal@0.1` with one primary challenge, cited source event ids, item hashes, skipped material with traceable reasons, alternatives, uncertainty, and rationale. A partial or assisted attempt targets the observed gap before falling back to the deterministic selection; missing evidence falls back with explicit uncertainty. `compose decide` records one append-only `virtuoso/learner-decision@0.1` per proposal with hash revalidation, and creates no attempt, scheduler, capability, or mastery evidence.
6. `virtuoso --workspace PATH learn --item ID` shows only the current learning unit and appends one study event after explicit completion. Stopping or failing writes nothing. Study creates no attempt, scheduler, transfer, capability, or mastery evidence.
7. Every direct, administered, and review practice writer rejects a learn-first item until a matching current study event exists.
8. `virtuoso --workspace PATH practice --item ID` shows the prompt before the answer, measures initial recall with a monotonic clock, supports retry then hint then worked answer, times a failed/partial follow-up response, and records actual attempt start/completion times, result, confidence, notes state, response text, answer reveal, and support attribution.
9. The completed attempt appends immutable evidence and a next-review proposal from the configured built-in scheduler (`fsrs` by default, `sm2` optional) that names the algorithm, its version, configuration, learning context, source event, and due time. Changing the algorithm on a workspace with recorded state fails closed until `scheduler switch` records the change.
10. `virtuoso --workspace PATH attempts --json` returns separate study, attempt, skip, and proposal evidence without claiming mastery.
11. The extension boundary validates and invokes one trusted local executable through `virtuoso/module@0.1` only when calling code passes `allow_trusted=True`. Tests prove that unknown fields/schema, missing calling-code opt-in, shell or command-wrapper indirection, malformed or incomplete typed output, timeout, oversized output, nested private-state projections, descendant spawning, and manifest identity drift fail closed. There is no public CLI command for module execution and no consent dialog. Descendant spawning is denied with a POSIX process limit and execution fails closed when that control is unavailable. The trusted executable still has the invoking user's file permissions, so this boundary is not an OS sandbox.
12. `virtuoso --workspace PATH doctor --json` reports a healthy workspace after the journey and explains active learn-versus-practice counts.
13. `virtuoso --workspace PATH source add ...` connects an existing Markdown folder or Obsidian vault as an explicit read-only source without making Obsidian or Hermes a runtime dependency.
14. `source scan` indexes only bounded note metadata, hashes, and wikilinks; it never writes to source files or copies note bodies into SQLite, and symlink escapes fail closed.
15. `source link` binds a manually authored learning item to the exact indexed source-note hash, while `doctor` reports a linked note that changed or disappeared.
16. `transfer record` appends one project-application event bound to the exact learning-item hash with outcome, independence, artifact reference, reflection, and a seven-day delayed-check date; it cannot claim mastery.
17. `transfer list` returns project evidence without merging it into recall attempts, scheduler state, XP, or capability claims.
18. `transfer check create` links one immutable, manually authored changed/novel challenge, acceptance criteria, and scorer to an existing transfer event and inherits its validated due date. Check creation cannot precede the source event. A check may be created after its due date, but its UTC creation timestamp cannot be backdated.
19. `transfer check due` lists pending and started incomplete checks chronologically without writing state or becoming a scheduler/project-priority recommendation.
20. `transfer check begin` appends one prediction at or after both the inherited due time and check creation time, before the learner attempts the changed challenge or requests help.
21. `transfer check complete` appends one independent attempt no earlier than both check creation and its required prediction, with assistance attribution, scorer-bound acceptance evidence, teach-back, outcome, optional inert artifact reference, timestamps, and `claims_mastery: false`; it cannot update scheduler, project selection, or capability state.
22. The full unit/integration suite, compile check, public repository checks, representative CLI journeys, exact-commit independent review, and clean-worktree check pass.

## Automated verification

The implementation must declare and execute:

- Python compile check for `src` and `tests`.
- Complete `unittest` suite.
- Build/install smoke check in the project-local virtual environment.
- Deterministic synthetic CLI journey.
- Module-boundary security tests.
- Project-transfer attribution and stale-item tests.
- GitHub Actions checks against the exact commit.

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
- FSRS integration, the scheduler backend protocol, and scheduler-state isolation;
- SQLite transactions and migration safety;
- module process security, timeout, output bounds, and schema validation;
- privacy, path handling, and absence of personal data;
- maintainability and test gaps.

Any critical/high finding or contract failure returns the slice to implementation.

## Evidence packet

Evidence must bind to the exact commit and retain command argv, cwd, exit code, full local logs, bounded JSON excerpts, module receipts, and known limitations. Runtime workspaces remain outside Git. No remote or public push is part of this contract.
