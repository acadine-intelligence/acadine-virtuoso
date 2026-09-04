# Contributor and user-facing acknowledgements

Virtuoso builds on open-source work. Thank you.

## FSRS and py-fsrs

The built-in scheduler is [FSRS](https://github.com/open-spaced-repetition/fsrs4anki) (Free Spaced Repetition Scheduler), used through the [`fsrs`](https://github.com/open-spaced-repetition/py-fsrs) Python package by the Open Spaced Repetition project (MIT licence, copyright 2022 Open Spaced Repetition). Every review proposal records the algorithm name, installed package version, configuration, and previous and proposed card state, so scheduling is auditable rather than opaque.

The project's research notes also draw on the FSRS algorithm papers and Anki's public documentation of its SM-2-derived and FSRS schedulers when weighing design trade-offs.

## Tooling

- [Obsidian](https://obsidian.md) hosts the optional review plugin. The plugin speaks only to the local CLI.
- Python, SQLite, and the Python packaging ecosystem carry the local-first runtime.

## Licence

Virtuoso is MIT-licensed. See [LICENSE](LICENSE). Third-party package licences are declared in their distributions and honoured by that licence.
