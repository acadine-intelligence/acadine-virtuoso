# Release notes

Virtuoso has no published package or GitHub Release yet. These notes record public repository milestones and the checks that accompanied them.

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

The local `v0.2.0` tag records an earlier plugin milestone. The tag was never pushed and no GitHub Release exists for it. The current public version policy supersedes that local tag and reports `0.1.0`.

- Commit `2ba23cc` added a full-screen card session inside Obsidian.
- Commit `e77a0bd` hardened parsing and limited each card to one scheduler write per session.
- The plugin kept scheduling and evidence writes in the external CLI.

At that revision, TypeScript type checking passed, 23 plugin tests passed, the plugin build completed, and the Python suite passed 158 tests plus 78 subtests.

Issue #6 later connected this interface to the supported public Virtuoso CLI without requiring a live agent session.
