# Contributing to Virtuoso

Virtuoso accepts focused changes that preserve local ownership of learning data and keep the command-line interface reproducible from a public clone.

## Before you start

- Read `README.md`, the relevant public documentation, and the full issue thread.
- Check open pull requests and local branches for overlapping work.
- Use a dedicated branch or worktree from current `main`.
- Keep private learner content, credentials, local paths, and runtime databases outside the repository.

## Python setup and checks

Python 3.11 or newer is required. Install uv 0.10.10 for parity with CI. `.python-version` selects Python 3.11.15; uv can download it. CI bootstraps uv with `python -m pip install --only-binary=:all: --require-hashes -r ci/bootstrap.txt` after setting up Python. A normal contributor can install uv through its official installer.

```bash
uv sync --locked --group build
uv run --locked python -m compileall -q src tests scripts
uv run --locked python -m unittest discover -s tests -v
uv run --locked virtuoso --help
```

`--locked` refuses stale dependency metadata. When intentionally changing dependencies, run `uv lock` and review the lockfile diff. Keep the build backend version in `[build-system]` and the `build` dependency group equal.

Build the distributions with the locked backend, then test installation outside the checkout:

```bash
uv sync --locked --group build
uv build --no-build-isolation --out-dir dist/python
uv export --locked --no-emit-project --group build --format requirements-txt --output-file dist/install-requirements.txt
uv run --locked python scripts/check_distributions.py
uv run --locked python -m unittest discover -s tests/integration -v
```

The installation check creates a fresh environment for each distribution. It installs hash-locked dependencies first, then installs the artifact with dependency resolution and network access disabled. It checks the package resources and runs an administered practice journey with `doctor`. Integration controls remove a packaged resource and require rejection. CI installs the same distributions on macOS and Linux with Python 3.11 through 3.14. Later Python releases are unverified until added to the matrix.

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
