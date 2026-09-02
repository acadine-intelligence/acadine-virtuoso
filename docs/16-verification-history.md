# Verification history

This file records checks against public repository revisions. GitHub Actions is the public check surface. Maintainer-only checks are supplemental and do not replace a reproducible public run.

Passing software checks establishes that the tested behavior works at that revision. It does not establish learner capability, product adoption, or release readiness.

## Public baseline, 2026-09-02

Revision: `3f5ae658b20d43ec7dc8df4fc874fa41dd54042f`

Included work:

- PR #11: administered practice, skip history, legacy workspace detection, and plugin load errors
- PR #12: read-only SQLite analytics
- PR #14: public README
- PR #15: lexical and embedding-based retrieval on `main`

Checks run from a clean worktree:

- Python 3.11 package installation: passed
- Python compile check: passed
- Python unit and journey suite: 190 tests passed
- Installed CLI smoke check: passed
- Obsidian plugin dependency installation: passed
- Obsidian TypeScript check: passed
- Obsidian plugin suite: 23 tests passed
- Obsidian plugin build: passed
- Build OS architecture check: passed in the maintainer environment
- Build OS verification: passed in the maintainer environment

A synthetic CLI journey also completed workspace initialization, item creation, next-item selection, lexical retrieval, read-only workload analytics, and a healthy `doctor` check.

## Historical issue checks

PR #11 closed issues #2, #4, #8, and #9. Their issue bodies record the public symptom, resolved behavior, test scope, and merge commit. Private workspace names and learner content are excluded.

PR #13 contains the original RAG implementation history. It targeted an earlier feature branch, so PR #15 reapplied the same feature to `main` and verified the integrated result.

## Reproduce the current checks

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m compileall -q src tests
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/virtuoso --help

cd plugins/obsidian
npm ci
npm run typecheck
npm test
npm run build
```

The workflow at `.github/workflows/ci.yml` runs these public checks for each pull request and each push to `main`.

## Recording future results

For each material revision, record the exact commit, command, outcome, and public CI URL. Keep full private learner data in its owning workspace. Public history may state that maintainer dogfood occurred and must omit private responses, paths, item names, and local inventory.
