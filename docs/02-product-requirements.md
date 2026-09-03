# Product requirements

## V0 job

When a learner begins focused practice, Virtuoso presents one relevant prompt before any answer and measures the retrieval attempt. Bounded support is provided only when requested. Virtuoso records attributable evidence and proposes the next review without claiming mastery.

## Functional requirements

- Initialize an isolated local workspace with Markdown items and SQLite state.
- Add manually authored items containing prompt, answer, optional hint, and optional follow-up.
- Select a due item deterministically from simple-mode focus and scheduler state.
- Keep the answer hidden until an explicit attempt.
- Measure elapsed recall time in the program; never accept a user-supplied latency as authoritative in interactive mode.
- Support retry unaided, hint, worked answer/feedback, and smaller follow-up challenge.
- Record result, latency, confidence, notes state, and each support action as append-only evidence.
- Use `py-fsrs` for the first atomic-recall scheduler and persist its serialized state separately by scheduler id.
- Record scheduler algorithm, package/version, configuration, context, inputs, proposal, and reason.
- Load external command modules only from declared manifests and validate all responses before core state changes.
- Provide machine-readable JSON output for inspection and harness compatibility.
- Work without Obsidian or Hermes.

## Safety and truth requirements

- XP and completion cannot promote capability state.
- Retrieval can establish retrieved evidence at most; project transfer requires a separate evidence event.
- Project opportunities remain proposals until the learner accepts them.
- Virtuoso sends modules no database path or arbitrary workspace projection. Modules are trusted local executables. Calling code must pass `allow_trusted=True`; there is no public CLI command for module execution. They are not OS-sandboxed processes and may independently access files allowed to the invoking user.
- No personal learning data is committed to this repository.
- Missing or stale inputs are reported as uncertainty.

## Deferred modules

Direction-led planning, virtue compass, skill trees, project transfer, two-way Obsidian synchronization, scheduled focus proposals, XP, computational exercise runners, external-resource adapters, scheduler comparison, and meta-scheduler policy optimization are planned after the first slice survives real use.
