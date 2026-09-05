# Contributor and user-facing acknowledgements

Virtuoso builds on open-source work. Thank you.

## FSRS and py-fsrs

The default scheduler is [FSRS](https://github.com/open-spaced-repetition/fsrs4anki) (Free Spaced Repetition Scheduler), used through the [`fsrs`](https://github.com/open-spaced-repetition/py-fsrs) Python package by the Open Spaced Repetition project (MIT licence, copyright 2022 Open Spaced Repetition). Every review proposal records the algorithm name, installed package version, configuration, and previous and proposed card state, so scheduling is auditable rather than opaque.

The project's research notes also draw on the FSRS algorithm papers and Anki's public documentation of its SM-2-derived and FSRS schedulers when weighing design trade-offs.

## SuperMemo 2

The second built-in scheduler, `sm2`, implements the SuperMemo 2 algorithm published by Piotr Wozniak in 1990 (easiness factor, repetition count, and the 1-day, 6-day, then interval-times-easiness ladder). Virtuoso's implementation in `src/virtuoso/schedulers.py` was written from the published description of the algorithm and does not copy code from SuperMemo, Anki, or any other implementation. SuperMemo is a trademark of SuperMemo World; Virtuoso is not affiliated with or endorsed by SuperMemo World.

Any future built-in algorithm adds its entry here in the same change that adds the code.

## Tooling

- [Obsidian](https://obsidian.md) hosts the optional review plugin. The plugin speaks only to the local CLI.
- Python, SQLite, and the Python packaging ecosystem carry the local-first runtime.

## Licence

Virtuoso is MIT-licensed. See [LICENSE](LICENSE). Third-party package licences are declared in their distributions and honoured by that licence.
