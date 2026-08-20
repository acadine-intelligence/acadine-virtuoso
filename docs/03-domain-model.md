# Domain model

## Owned records

`Workspace`
: Root containing configuration, authored Markdown, and runtime state.

`LearningItem`
: Human-authored prompt, answer, hint, follow-up, focus, context, and immutable item version. Markdown owns its prose.

`PracticeChallenge`
: A versioned projection of one item shown to the learner. It contains no answer until the attempt boundary is crossed.

`Attempt`
: Append-only evidence with prompt version, start/end timestamps, elapsed milliseconds, result, confidence, open-notes state, and support actions.

`SupportAction`
: Retry, hint, worked feedback, or follow-up challenge. Assistance is evidence context. It is not a penalty hidden inside a score.

`SchedulerState`
: Algorithm-specific serialized state, isolated by item, algorithm id, and learning context.

`SchedulerProposal`
: Algorithm, version, configuration, context, input event, proposed due time, and reason. It is not a competence claim.

`CapabilityView`
: Derived interpretation over evidence. V0 records retrieval evidence but does not autonomously promote transfer or mastery.

`ModuleManifest`
: External command, protocol version, category, declared capabilities, bounded read projections, and response schema.

`ModuleReceipt`
: Invocation metadata, validated response hash, duration, exit status, and error. It excludes secrets and private prompt content unless explicitly required by the module projection.

`FocusProposal` and `LearnerDecision`
: Future selection output and the explicit accept/change/reject event. A proposal never becomes a commitment by itself.

## Ownership

Markdown owns learner prose. SQLite owns attempts, scheduler state, proposals, module receipts, XP events, and sync history. Project systems own commitments and milestones. Obsidian edits Markdown; Hermes may invoke contracts and supply approved context. No owner writes another owner's native state.
