# Release notes

The first public release is [v0.1.0](releases/v0.1.0.md) on GitHub Releases. Sections marked "Unreleased" landed on `main` after that tag and will ship in the next release. These notes record public repository milestones and the checks that accompanied them.

## Unreleased Obsidian projection

`export obsidian --out DIR` writes one frontmatter-only Markdown stub per review-queue item into a folder outside the workspace, plus a manifest, so Obsidian Bases can show the workspace due queue with no second scheduler. Stubs carry item id, title, focus, status, next review, project ids, content hash, and generation time; no answer or hint content is exported. The command refuses targets inside the workspace, symlinks, and folders holding files it did not generate, and removes stubs for items that left the queue. Repeated runs at the same instant are byte-identical. Closes #45.

## Unreleased benchmark-directed focus

`benchmark import` stores a local benchmark artifact as append-only evidence: tested commit, harness and version, model, prompt hash, tool permissions, environment, and normalized observations against a versioned operating-level map. `benchmark propose` turns the earliest unproposed failed criterion into an evidence-cited FocusProposal with rerun condition. `benchmark decide` reuses the existing learner-decision contract. `benchmark rerun` links a rerun to its baseline and reports per-criterion metric change with specific comparability warnings; a passing rerun never promotes capability or mastery. `benchmark export` emits a redacted run with normalized fields only.

## Unreleased practice context display

Interactive CLI practice now shows `Focus:`, optional `Projects:`, and `Why now:` before the first interaction. Project identifiers come only from explicit transfer records. The reason mirrors the review queue's deterministic wording, or names an explicit item request. The context display reads state only and writes nothing. The Obsidian plugin already showed the same context through the review contracts.

## Unreleased session composition

`compose` returns an evidence-aware `virtuoso/focus-proposal@0.1`: one primary challenge with cited source events, item hashes, skipped material with traceable reasons, alternatives, uncertainty, and rationale. It targets the newest recorded gap before falling back to the deterministic due-then-new selection, and never exposes an answer before an attempt.

`compose decide` records one append-only `virtuoso/learner-decision@0.1` per proposal with hash revalidation at decide time. A decision creates no attempt, scheduler, capability, or mastery evidence; practice from a proposal uses the existing `practice --item ID` command with the decided item.

## Unreleased learn-first workflow

Learn-first items use `virtuoso/item@0.2` and carry a bounded learning unit before their prompt and hidden answer. `next --json` returns a typed `virtuoso/next-action@0.1` envelope. The local `learn` command records explicit completion as a hash-bound study event. Study does not start FSRS or create recall, transfer, capability, or mastery evidence. Existing `virtuoso/item@0.1` files remain recall-first.

The Obsidian review queue omits pending learn-first items. The learning step remains CLI-only in this slice.

## v0.1.0 release candidate

Date: 2026-09-03

The Python package and Hermes plugin now use version `0.1.0`. The Obsidian plugin uses the same version. `virtuoso --version` reads installed distribution metadata.

A manually triggered GitHub Actions workflow can prepare a draft `v0.1.0` GitHub Release after the required Python and Obsidian CI jobs pass. It builds and verifies these assets:

- `acadine_virtuoso-0.1.0-py3-none-any.whl`
- `acadine_virtuoso-0.1.0.tar.gz`
- `virtuoso-obsidian-0.1.0.zip`
- `main.js`
- `manifest.json`
- `versions.json`
- `SHA256SUMS`

The workflow fails when the tag or release already exists. It cannot run from a ref other than `main`. It creates a draft only. It does not publish to PyPI, deploy Virtuoso, or publish the GitHub Release.

The current command reference now covers the implemented analytics, retrieval, review, and plugin interfaces. The documentation index separates current guides from design and historical records.

## Reviewed source import milestone

Date: 2026-09-02

The public candidate queue handles structural suggestions and declared practice imports through one review flow. `candidate generate --adapter curriculum` reads one explicitly selected `virtuoso/curriculum@0.1` or `virtuoso/item@0.1` note. `--dry-run` validates and reports proposals without candidate writes.

`candidate decide` records `accept`, `edit`, `skip`, or `reject`. Accept and edit create the workspace Markdown item and exact source link through one protected write path. Skip and reject create no item. Historical due metadata stays in the proposal and does not seed scheduler state or create learning evidence.

`candidate delta` is suitable for scheduled checks. It returns no output and makes no database write when the source snapshot is unchanged. Concurrent checks of the same new hash store and report one run. A changed source creates a new candidate run while prior proposals and decisions remain available. The tracked public vault fixture covers the complete path and confirms that source notes stay byte-identical.

Migration 11 adds the decision action, reviewed item payload and materialized item reference. Existing accept and reject decisions migrate without a fabricated item reference.

## Public baseline, 2026-09-02

Commit `3f5ae658b20d43ec7dc8df4fc874fa41dd54042f` combines the public CLI and documentation at that milestone.

- PR #11 added agent-administered practice, skip-event history, legacy workspace detection, and visible Obsidian plugin load failures.
- PR #12 added read-only analytics for focus performance, item history, workload, and stale source links.
- PR #14 replaced the builder-oriented README with a public product guide.
- PR #15 placed lexical and embedding-based retrieval on `main`. It recovered the retrieval change after PR #13 had merged into an earlier feature branch.

Verification details live in `docs/16-verification-history.md`.

## Obsidian plugin milestone, 2026-08-22

A local `v0.2.0` tag once recorded an earlier plugin milestone. It was never
pushed and never had a GitHub Release; it was deleted from this clone on
2026-09-04 because its number collides with the public version sequence that
now starts at `0.1.0`. History for that milestone lives in the commits below
and in `docs/16-verification-history.md`.

- Commit `2ba23cc` added a full-screen card session inside Obsidian.
- Commit `e77a0bd` hardened parsing and limited each card to one scheduler write per session.
- The plugin kept scheduling and evidence writes in the external CLI.

At that revision, TypeScript type checking passed, 23 plugin tests passed, the plugin build completed, and the Python suite passed 158 tests plus 78 subtests.

Issue #6 later connected this interface to the supported public Virtuoso CLI without requiring a live agent session.
