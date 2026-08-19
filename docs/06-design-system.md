# CLI design system

Virtuoso uses terminal-native semantics rather than a decorative visual brand.

## State language

- `Challenge`: the current prompt.
- `Attempt`: what the learner did before help.
- `Support used`: retries, hints, feedback, notes, or agent help.
- `Evidence`: the recorded observation.
- `Next review proposal`: scheduler output, not a commitment or competence claim.

## Color

Color is optional and never the only signal. Cyan marks the current challenge, green a demonstrated result, amber partial/help-used context, red a failed or blocked operation, and muted text provenance. `NO_COLOR` disables all styling.

## Layout

Readable at 80 columns. One blank line around the prompt. Evidence uses aligned labels. Long prose wraps naturally. JSON mode is stable and unstyled.

## Accessibility

Every action has a keyboard path and numbered/plain-text fallback. Prompts do not time out. The timer measures recall but never forces a rushed response. Screen readers receive the same labels and order as sighted users.
