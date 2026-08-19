# Acadine Virtuoso — agent execution contract

## Reality rule
This project is not complete because code exists, a page renders, or a demo was shown. `product.json` and Build OS verification evidence define its actual state.

## Canonical entry
1. Read `product.json`.
2. Set `BUILDOS_HOME` to the local Build OS checkout (default: `$HOME/projects/acadine-build-os`).
3. Run `python3 "$BUILDOS_HOME/scripts/buildos.py" check . --target architecture` before substantial implementation.
4. Work in a dedicated Git branch/worktree after a reviewed baseline.
5. Implement vertical, user-visible slices with tests written first.
6. Run all declared commands with `python3 "$BUILDOS_HOME/scripts/buildos.py" verify .`.
7. Run an independent review of the exact Git commit, record its JSON verdict with `python3 "$BUILDOS_HOME/scripts/buildos.py" review . --file /path/to/review.json`, and execute a representative end-to-end user journey; use authenticated browser evidence only when `experience.surfaces` declares a visual interface.

## Stop conditions
- Do not build when value, research, product, experience, or architecture gates are blocked.
- Do not use placeholders, invented evidence, generic demo data, or claims unsupported by a real run.
- Do not call a demo `released`.
- Do not push, publish, spend money, send messages, or use private data externally without explicit approval.

## Product boundary
Kind: `standalone`. Define the Hermes/product ownership boundary in `docs/04-architecture.md` and `product.json` before integration work.
