# Release notes

Virtuoso has no published package or GitHub Release yet. These notes record public repository milestones and the checks that accompanied them.

## Unreleased: reviewed source import

Date: 2026-09-02

The public candidate queue now handles structural suggestions and declared practice imports through one review flow. `candidate generate --adapter curriculum` reads one explicitly selected `virtuoso/curriculum@0.1` or `virtuoso/item@0.1` note. `--dry-run` validates and reports proposals without candidate writes.

`candidate decide` now records `accept`, `edit`, `skip`, or `reject`. Accept and edit create the workspace Markdown item and exact source link through one protected write path. Skip and reject create no item. Historical due metadata stays in the proposal and does not seed scheduler state or create learning evidence.

`candidate delta` is suitable for scheduled checks. It returns no output and makes no database write when the source snapshot is unchanged. Concurrent checks of the same new hash store and report one run. A changed source creates a new candidate run while prior proposals and decisions remain available. The tracked public vault fixture covers the complete path and confirms that source notes stay byte-identical.

Migration 11 adds the decision action, reviewed item payload and materialized item reference. Existing accept and reject decisions migrate without a fabricated item reference.

## Unreleased public baseline, 2026-09-02

Commit `3f5ae658b20d43ec7dc8df4fc874fa41dd54042f` combines the current public CLI and documentation.

### Product changes

- PR #11 added agent-administered practice, skip-event history, legacy workspace detection, and visible Obsidian plugin load failures.
- PR #12 added read-only analytics for focus performance, item history, workload, and stale source links.
- PR #14 replaced the builder-oriented README with a public product guide.
- PR #15 placed lexical and embedding-based retrieval on `main`. It recovered the RAG change after PR #13 had merged into an earlier feature branch.

### Public state

The source checkout supports local installation and the documented CLI journey. The repository remains pre-release. Package registry publication, upgrade tooling, and a public adoption result remain pending.

Verification details live in `docs/16-verification-history.md`.

## Obsidian plugin milestone, 2026-08-22

The local `v0.2.0` tag records an earlier plugin milestone. The tag was never pushed and no GitHub Release exists for it. The Python package still reports `0.1.0.dev0`.

### Product changes

- Commit `2ba23cc` added a full-screen card session inside Obsidian.
- Commit `e77a0bd` hardened parsing and limited each card to one scheduler write per session.
- The plugin kept scheduling and evidence writes in the external CLI.

### Historical checks

- TypeScript type checking passed.
- All 23 plugin tests passed.
- The plugin build completed.
- The Python suite at that revision passed 158 tests plus 78 subtests.

Issue #6 tracks the remaining work to connect this interface to the supported public Virtuoso CLI without requiring a live agent session.
