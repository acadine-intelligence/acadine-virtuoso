# Virtuoso agent skill

`virtuoso/SKILL.md` teaches a shell-capable coding agent two journeys:

- **Capture.** After real work, the agent drafts one to three recall items about what you built, you pick which to keep, and the agent adds them.
- **Recall.** The agent asks the next due prompt in chat, keeps the answer hidden until you respond, and records your own grade with `practice --administer`.

The skill uses the [Agent Skills](https://agentskills.io) format, so the same folder works in any harness that loads it. It calls the CLI with `--json` and needs no server.

## Setup

1. Install the CLI and create a workspace. See `docs/18-installation.md`.
2. Export the workspace path in the shell your agent uses:

   ```
   export VIRTUOSO_WORKSPACE=/path/to/your/workspace
   ```

3. Load the skill in your agent.

   | Agent | How |
   |---|---|
   | Pi | `pi --skill examples/agent-skills/virtuoso`, or copy the folder to `~/.pi/agent/skills/` |
   | Claude Code | Copy the folder to `~/.claude/skills/` or `.claude/skills/` in your project |
   | Other harnesses | Load the folder wherever the harness reads Agent Skills |

## Try it

In a project you just worked on, ask the agent:

```
Capture what I should be able to explain about this change into Virtuoso.
```

Later, in any session:

```
Quiz me.
```

`queries history --item ITEM --json` shows what was recorded. Every attempt is marked `administered`, with latency stored as unknown and the agent's help level as reported.

`tests/test_agent_skill.py` reads the commands from the skill and parses each one against the current CLI with abbreviated flags turned off. It checks every taught choice value, and runs the taught journey (next, reveal, study record, a give-up attempt) against a temporary workspace.
