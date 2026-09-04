# Domain model

## Owned records

`Workspace`
: Root containing configuration, authored Markdown, and runtime state.

`LearningItem`
: Human-authored focus, entry mode, optional learning unit, prompt, answer, hint, follow-up, context, and immutable item version. Markdown owns its prose. Existing v0.1 items are recall-first. A v0.2 learn-first item requires learning prose before recall.

`NextAction`
: Versioned selection output typed as `learn` or `practice`, bound to the current item and learning-unit hashes and accompanied by a plain reason.

`StudyEvent`
: Append-only exposure activity for one exact learn-first item and learning-unit version. It records completion time and surface. It creates no recall, schedule, transfer, capability, or mastery evidence.

`PracticeChallenge`
: A versioned projection of one item shown to the learner. It contains no answer until the attempt boundary is crossed.

`Attempt`
: Append-only evidence with prompt version, start/end timestamps, elapsed milliseconds, result, confidence, open-notes state, and support actions.

`ReviewSkip`
: Append-only evidence that a learner skipped one exact item version through a named interface. It does not create an attempt or scheduler proposal.

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

`FocusProposal`
: One evidence-aware session proposal: primary item, action, cited source events and item hashes, skipped material with traceable reasons, alternatives, uncertainty, and rationale. A proposal never becomes a commitment by itself.

`LearnerDecision`
: Append-only learner accept, change, or reject of one `FocusProposal`. It binds the chosen item to its current content hash at decide time and creates no attempt, scheduler, capability, or mastery evidence.

`BenchmarkRun`
: Append-only benchmark evidence: source reference, source hash, tested commit, harness and version, model identifier, prompt hash, tool permissions, environment, and normalized observations. The benchmarked system owns the artifact; Virtuoso stores the import.

`BenchmarkRerun`
: One run linked to a baseline with stored comparability warnings and per-criterion metric changes. It creates no capability or mastery evidence, and a passing rerun promotes nothing.

## Ownership

Markdown owns learner prose. SQLite owns study events, attempts, scheduler state, proposals, module receipts, XP events, and sync history. Project systems own commitments and milestones. Obsidian edits Markdown; Hermes may invoke contracts and supply approved context. No owner writes another owner's native state.
