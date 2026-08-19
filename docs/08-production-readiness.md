# Production readiness

Virtuoso is not released. This file records the intended checks without claiming they have passed.

## Installation and first run

The first slice targets Python 3.11 and a project-local `.venv`. Installation must use tracked `pyproject.toml` and pinned runtime requirements, then prove import, CLI entry point, clean workspace initialization, and the synthetic hero journey.

## Runtime and health

There is no server or port. Each command is a bounded local process. `doctor` will verify workspace schema, SQLite integrity, Markdown ownership, installed scheduler version, and declared module manifests. Diagnostic output must exclude private content and credentials.

## Data lifecycle

SQLite migrations run transactionally. Before any future destructive migration, Virtuoso must make a consistent local backup. Markdown stays user-owned. Attempts are append-only; corrections add events rather than deleting observations. Export, restore, retention, and deletion are release work and remain unverified.

## Release evidence

Required before release: clean-environment install, all Build OS verification commands, representative journeys, exact-commit independent review, migration/backup/restore proof, user acceptance, known limitations, and an approved distribution decision.

## Distribution boundary

Current distribution is local-only. No Git remote, package publication, public repository, hosted service, or external data transfer is authorized by this iteration.
