from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from .modules import ModuleError
from .practice import PracticeError, PracticeIO, PracticeService
from .workspace import WorkspaceError, WorkspaceService


class ConsoleIO(PracticeIO):
    def write(self, text: str) -> None:
        print(text)

    def ask(self, prompt: str) -> str:
        return input(prompt)


def _emit(payload: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    for key, value in payload.items():
        print(f"{key}: {value}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="virtuoso", description="Local-first active-recall practice"
    )
    parser.add_argument("--workspace", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init", help="create a simple-mode workspace")
    init.add_argument("--json", action="store_true")

    add = commands.add_parser("add", help="add one active-recall item")
    add.add_argument("--id", required=True)
    add.add_argument("--title", required=True)
    add.add_argument("--focus", required=True)
    add.add_argument("--prompt", required=True)
    add.add_argument("--answer", required=True)
    add.add_argument("--hint")
    add.add_argument("--follow-up")
    add.add_argument("--json", action="store_true")

    next_command = commands.add_parser("next", help="recommend the next item")
    next_command.add_argument("--json", action="store_true")

    practice = commands.add_parser("practice", help="run an active-recall session")
    practice.add_argument("--item", required=True)
    practice.add_argument(
        "--agent-help",
        choices=("none", "light", "substantial", "unknown"),
        default="none",
    )

    attempts = commands.add_parser("attempts", help="show evidence and proposals")
    attempts.add_argument("--json", action="store_true")

    doctor = commands.add_parser("doctor", help="check workspace health")
    doctor.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "init":
            workspace = WorkspaceService.init(args.workspace)
            _emit(
                {"status": "initialized", "workspace": str(workspace.root)},
                as_json=args.json,
            )
            return 0

        workspace = WorkspaceService.open(args.workspace)
        if args.command == "add":
            item = workspace.add_item(
                item_id=args.id,
                title=args.title,
                focus=args.focus,
                prompt=args.prompt,
                answer=args.answer,
                hint=args.hint,
                follow_up=args.follow_up,
            )
            _emit(
                {
                    "item_id": item.item_id,
                    "title": item.title,
                    "focus": item.focus,
                    "path": str(item.path),
                },
                as_json=args.json,
            )
            return 0
        if args.command == "next":
            selection = workspace.select_next(datetime.now(timezone.utc))
            _emit(
                {
                    "item_id": selection.item.item_id,
                    "title": selection.item.title,
                    "focus": selection.item.focus,
                    "prompt": selection.item.prompt,
                    "rationale": selection.rationale,
                    "alternatives": list(selection.alternatives),
                    "uncertainty": selection.uncertainty,
                },
                as_json=args.json,
            )
            return 0
        if args.command == "practice":
            PracticeService(workspace).run(
                item_id=args.item,
                io=ConsoleIO(),
                agent_help=args.agent_help,
            )
            return 0
        if args.command == "attempts":
            _emit(
                {
                    "attempts": workspace.list_attempts(),
                    "proposals": workspace.list_proposals(),
                },
                as_json=args.json,
            )
            return 0
        if args.command == "doctor":
            _emit(workspace.doctor(), as_json=args.json)
            return 0
    except (WorkspaceError, PracticeError, ModuleError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
