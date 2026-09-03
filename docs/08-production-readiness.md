# Production readiness

Virtuoso is not released. The public source baseline has passed the checks recorded in `docs/16-verification-history.md`; the release requirements below remain open.

## Installation and first run

The first slice targets Python 3.11 and a project-local `.venv`. Installation must use tracked `pyproject.toml` and pinned runtime requirements, then prove import, CLI entry point, clean workspace initialization, and the synthetic hero journey.

## Runtime and health

There is no server or port. Each command is a bounded local process. `doctor` will verify workspace schema, SQLite integrity, Markdown ownership, installed scheduler version, and declared module manifests. Diagnostic output must exclude private content and credentials.

## Data lifecycle

SQLite migrations run transactionally. Before any future destructive migration, Virtuoso must make a consistent local backup. Markdown stays user-owned. Attempts are append-only; corrections add events rather than deleting observations. Export, restore, retention, and deletion are release work and remain unverified.

## Release evidence

Required before release: clean-environment installation, public CI, reproducible repository checks, representative journeys, exact-commit independent review, migration and restore proof, user acceptance, known limitations, and an approved distribution decision.

The manual `.github/workflows/release.yml` path reuses required CI, builds the fixed v0.1.0 assets, verifies their checksums and archive boundaries, then creates a draft GitHub Release. It rejects any ref except `main` and stops if the tag or release exists. A maintainer still decides whether to publish the draft.

## Distribution boundary

The source is available from the public GitHub repository and installs into a local project environment. No package registry release or hosted service exists. Runtime workspace data remains local unless a user explicitly moves it.
