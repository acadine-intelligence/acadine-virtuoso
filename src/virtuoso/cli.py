from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
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

    source = commands.add_parser(
        "source", help="connect and inspect read-only Markdown or Obsidian sources"
    )
    source_commands = source.add_subparsers(dest="source_command", required=True)
    source_add = source_commands.add_parser("add", help="connect a read-only source")
    source_add.add_argument("--id", required=True)
    source_add.add_argument("--kind", choices=("markdown", "obsidian"), required=True)
    source_add.add_argument("--path", type=Path, required=True)
    source_add.add_argument("--json", action="store_true")
    source_list = source_commands.add_parser("list", help="list connected sources")
    source_list.add_argument("--json", action="store_true")
    source_scan = source_commands.add_parser("scan", help="index source metadata")
    source_scan.add_argument("--id", required=True)
    source_scan.add_argument("--json", action="store_true")
    source_link = source_commands.add_parser(
        "link", help="link a learning item to an indexed source note"
    )
    source_link.add_argument("--id", required=True)
    source_link.add_argument("--path", required=True)
    source_link.add_argument("--item", required=True)
    source_link.add_argument("--json", action="store_true")
    source_notes = source_commands.add_parser("notes", help="list indexed note metadata")
    source_notes.add_argument("--id", required=True)
    source_notes.add_argument("--json", action="store_true")

    transfer = commands.add_parser(
        "transfer", help="record and inspect real project application evidence"
    )
    transfer_commands = transfer.add_subparsers(dest="transfer_command", required=True)
    transfer_record = transfer_commands.add_parser(
        "record", help="record an attributed project transfer event"
    )
    transfer_record.add_argument("--item", required=True)
    transfer_record.add_argument("--project", required=True)
    transfer_record.add_argument("--use-case", required=True)
    transfer_record.add_argument(
        "--outcome", choices=("successful", "partial", "unsuccessful"), required=True
    )
    transfer_record.add_argument(
        "--independence",
        choices=("independent", "guided", "agent-produced", "unknown"),
        required=True,
    )
    transfer_record.add_argument("--artifact")
    transfer_record.add_argument("--reflection")
    transfer_record.add_argument("--json", action="store_true")
    transfer_list = transfer_commands.add_parser(
        "list", help="list project transfer evidence"
    )
    transfer_list.add_argument("--json", action="store_true")
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
        if args.command == "transfer":
            if args.transfer_command == "record":
                event = workspace.record_transfer(
                    item_id=args.item,
                    project_id=args.project,
                    use_case=args.use_case,
                    outcome=args.outcome,
                    independence=args.independence,
                    artifact_reference=args.artifact,
                    reflection=args.reflection,
                )
                _emit(asdict(event), as_json=args.json)
                return 0
            if args.transfer_command == "list":
                _emit(
                    {"events": [asdict(event) for event in workspace.list_transfer_events()]},
                    as_json=args.json,
                )
                return 0
        if args.command == "source":
            if args.source_command == "add":
                source = workspace.add_source(
                    source_id=args.id, kind=args.kind, root=args.path
                )
                _emit(
                    {
                        "source_id": source.source_id,
                        "kind": source.kind,
                        "root": str(source.root),
                        "read_only": source.read_only,
                    },
                    as_json=args.json,
                )
                return 0
            if args.source_command == "list":
                _emit(
                    {
                        "sources": [
                            {
                                "source_id": source.source_id,
                                "kind": source.kind,
                                "root": str(source.root),
                                "read_only": source.read_only,
                            }
                            for source in workspace.list_sources()
                        ]
                    },
                    as_json=args.json,
                )
                return 0
            if args.source_command == "scan":
                receipt = workspace.scan_source(args.id)
                _emit(
                    {
                        "receipt_id": receipt.receipt_id,
                        "source_id": receipt.source_id,
                        "indexed": receipt.indexed,
                        "removed": receipt.removed,
                        "skipped": receipt.skipped,
                        "total_bytes": receipt.total_bytes,
                        "occurred_at": receipt.occurred_at,
                    },
                    as_json=args.json,
                )
                return 0
            if args.source_command == "link":
                link = workspace.link_item_source(
                    item_id=args.item,
                    source_id=args.id,
                    relative_path=args.path,
                )
                _emit(link, as_json=args.json)
                return 0
            if args.source_command == "notes":
                _emit(
                    {
                        "source_id": args.id,
                        "documents": [
                            {
                                "relative_path": document.relative_path,
                                "title": document.title,
                                "content_hash": document.content_hash,
                                "wikilinks": list(document.wikilinks),
                                "modified_ns": document.modified_ns,
                                "byte_size": document.byte_size,
                            }
                            for document in workspace.list_source_documents(args.id)
                        ],
                    },
                    as_json=args.json,
                )
                return 0
    except (WorkspaceError, PracticeError, ModuleError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
