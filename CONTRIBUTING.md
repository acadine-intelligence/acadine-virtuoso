# Contributing to Virtuoso

Virtuoso accepts focused changes that preserve local ownership of learning data and keep the command-line interface reproducible from a public clone.

## Before you start

- Read `README.md`, the relevant public documentation, and the full issue thread.
- Check open pull requests and local branches for overlapping work.
- Use a dedicated branch or worktree from current `main`.
- Keep private learner content, credentials, local paths, and runtime databases outside the repository.

## Python setup and checks

Python 3.11 or newer is required. Confirm that `python` resolves to a supported version, then run the same commands as public CI:

```bash
python --version
python -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m compileall -q src tests
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/virtuoso --help
```

Use synthetic temporary workspaces for tests. Do not point tests at a personal Virtuoso workspace or Obsidian vault.

## Obsidian plugin checks

Node.js 22 is the public CI version. Run:

```bash
cd plugins/obsidian
npm ci
npm run typecheck
npm test
npm run build
```

Do not commit `node_modules`, generated local settings, or a locally built `main.js` unless a release issue explicitly requires it.

## Working agreement

- Write a failing regression test before fixing a bug.
- Keep the CLI as the only scheduler and evidence writer.
- Preserve the local-first, offline core and its read-only source boundary.
- Use complete synthetic fixtures. Never copy private learner data into a test.
- Keep each pull request tied to one issue and one coherent failure class.
- Run every affected Python and plugin check before pushing.

## Prepare the pull request

After the applicable checks pass, commit the intended files on a focused branch:

```bash
git switch -c docs/issue-123-short-name
git add --patch
git commit -m "docs: describe the change"
git push -u origin HEAD
gh pr create --draft --base main --fill
```

If you use a fork, push to the remote that points to your fork. If the GitHub CLI is unavailable, create a draft pull request from GitHub's compare page.

## Pull requests

Link the issue. State the observed behavior, expected behavior, implementation boundary, commands run, exact commit, and remaining risk. Material changes need an independent review of the exact commit. Keep the pull request as a draft until that review and public CI pass.

A passing test suite proves the tested software behavior. It does not prove learner capability, adoption, release readiness, or the safety of private data.

## Licence

Contributions accepted into this repository are distributed under the [MIT License](LICENSE).
