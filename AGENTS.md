# Virtuoso agent instructions

## Start here

1. Read `README.md`, `CONTRIBUTING.md`, `product.json`, and the relevant GitHub issue.
2. Compare the issue contract with current `main`. Record any conflict before changing code.
3. Check open pull requests and branches for overlapping work.
4. Create a dedicated branch or worktree from current `main`.
5. Write a failing regression test before changing behavior.
6. Use synthetic temporary workspaces. Keep private learner content, credentials, local paths, and runtime databases outside the repository.
7. Run every applicable command in `CONTRIBUTING.md` and `.github/workflows/ci.yml`.
8. Run a representative local journey for the changed surface.
9. Ask a reviewer who did not implement the change to inspect the exact commit.

The repository files and public CI define the reproducible workflow. Historical verification records describe earlier revisions and do not add current contributor requirements.

## Completion

A change is ready for human review when its issue acceptance cases pass, the applicable public checks pass, and the diff contains only intended files. Record the exact commit and any remaining risk. Keep a pull request as a draft until its required review and CI checks pass.

## Authority boundaries

- The CLI owns scheduler changes and learning evidence writes.
- Markdown owns learner-authored prose. SQLite owns derived state and append-only evidence.
- Optional Obsidian and Hermes adapters must preserve the standalone CLI path.
- External actions and use of private data require explicit approval.

## Product boundary

Virtuoso is a standalone local tool. `docs/04-architecture.md` and `product.json` define the ownership boundary between the core, adapters, and user-controlled workspaces.
