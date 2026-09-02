from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from .candidates import CandidateService
from .errors import VirtuosoError
from .practice import PracticeIO, PracticeService
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


def _parse_cli_timestamp(value: str, *, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise WorkspaceError(f"{field} must be a valid timezone-aware timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise WorkspaceError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _parser() -> argparse.ArgumentParser:
    from . import __version__

    parser = argparse.ArgumentParser(
        prog="virtuoso", description="Local-first active-recall practice"
    )
    parser.add_argument("--version", action="version", version=__version__)
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

    retire = commands.add_parser("retire", help="retire an item from selection")
    retire.add_argument("--id", required=True)
    retire.add_argument("--json", action="store_true")

    next_command = commands.add_parser("next", help="recommend the next item")
    next_command.add_argument("--focus", help="restrict selection to one focus track")
    next_command.add_argument("--json", action="store_true")

    practice = commands.add_parser("practice", help="run an active-recall session")
    practice.add_argument("--item", required=True)
    practice.add_argument(
        "--agent-help",
        choices=("none", "light", "substantial", "unknown"),
        default=None,
    )
    practice.add_argument(
        "--administer",
        action="store_true",
        help=(
            "record an agent-administered attempt non-interactively: the "
            "learner answered outside this terminal, an agent transcribes "
            "the response and grade, and latency is stored as unknown"
        ),
    )
    practice.add_argument(
        "--response", help="transcribed learner answer (requires --administer)"
    )
    practice.add_argument(
        "--result",
        choices=("demonstrated", "partial", "not-demonstrated"),
        help="graded outcome (requires --administer)",
    )
    practice.add_argument(
        "--confidence",
        type=int,
        help="learner confidence 1-5 (requires --administer)",
    )
    practice.add_argument("--json", action="store_true")

    attempts = commands.add_parser("attempts", help="show evidence and proposals")
    attempts.add_argument("--json", action="store_true")

    queries = commands.add_parser(
        "queries", help="read-only analytics over the workspace database"
    )
    query_commands = queries.add_subparsers(dest="query_command", required=True)
    query_focus = query_commands.add_parser(
        "focus", help="per-focus attempt outcomes"
    )
    query_focus.add_argument("--json", action="store_true")
    query_history = query_commands.add_parser(
        "history", help="attempt history for one item"
    )
    query_history.add_argument("--item", required=True)
    query_history.add_argument("--json", action="store_true")
    query_workload = query_commands.add_parser(
        "workload", help="due-now and scheduled counts per focus"
    )
    query_workload.add_argument("--json", action="store_true")
    query_stale = query_commands.add_parser(
        "stale-links", help="source links whose note changed or disappeared"
    )
    query_stale.add_argument("--json", action="store_true")

    doctor = commands.add_parser("doctor", help="check workspace health")
    doctor.add_argument("--json", action="store_true")

    search = commands.add_parser(
        "search", help="lexical (FTS5) and semantic (embedding kNN) retrieval"
    )
    search_commands = search.add_subparsers(dest="search_command", required=True)
    search_lexical = search_commands.add_parser("lex", help="word search over items")
    search_lexical.add_argument("--query", required=True)
    search_lexical.add_argument("--limit", type=int, default=10)
    search_lexical.add_argument("--json", action="store_true")
    search_embed = search_commands.add_parser(
        "embed", help="store or replace an embedding vector (JSON) for one item"
    )
    search_embed.add_argument("--item", required=True)
    search_embed.add_argument("--model", required=True)
    search_embed.add_argument("--vector", required=True)
    search_embed.add_argument("--json", action="store_true")
    search_semantic = search_commands.add_parser(
        "sem", help="cosine kNN over stored embeddings"
    )
    search_semantic.add_argument("--model", required=True)
    search_semantic.add_argument("--vector", required=True)
    search_semantic.add_argument("--limit", type=int, default=10)
    search_semantic.add_argument("--json", action="store_true")
    search_status_cmd = search_commands.add_parser(
        "status", help="index freshness and embedding inventory"
    )
    search_status_cmd.add_argument("--json", action="store_true")

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
    source_relink = source_commands.add_parser(
        "relink",
        help="consciously rebind a stale item-source link to the note's current hash",
    )
    source_relink.add_argument("--id", required=True)
    source_relink.add_argument("--path", required=True)
    source_relink.add_argument("--item", required=True)
    source_relink.add_argument("--json", action="store_true")
    source_unlink = source_commands.add_parser(
        "unlink", help="remove a dead item-source link after a rename or delete"
    )
    source_unlink.add_argument("--id", required=True)
    source_unlink.add_argument("--path", required=True)
    source_unlink.add_argument("--item", required=True)
    source_unlink.add_argument("--json", action="store_true")
    source_notes = source_commands.add_parser("notes", help="list indexed note metadata")
    source_notes.add_argument("--id", required=True)
    source_notes.add_argument("--json", action="store_true")

    candidate = commands.add_parser(
        "candidate", help="generate and decide source-backed review candidates"
    )
    candidate_commands = candidate.add_subparsers(
        dest="candidate_command", required=True
    )
    candidate_generate = candidate_commands.add_parser(
        "generate", help="generate candidates for one indexed note"
    )
    candidate_generate.add_argument("--source", required=True)
    candidate_generate.add_argument("--path", required=True)
    candidate_generate.add_argument(
        "--adapter",
        choices=("structural", "curriculum"),
        default="structural",
        help="select metadata-only structural or explicit curriculum import proposals",
    )
    candidate_generate.add_argument("--limit", type=int, default=20)
    candidate_generate.add_argument(
        "--dry-run",
        action="store_true",
        help="validate and report candidates without writing the workspace database",
    )
    candidate_generate.add_argument("--json", action="store_true")
    candidate_delta = candidate_commands.add_parser(
        "delta",
        help="scan one curriculum note and write candidates only when it changed",
    )
    candidate_delta.add_argument("--source", required=True)
    candidate_delta.add_argument("--path", required=True)
    candidate_delta.add_argument("--limit", type=int, default=20)
    candidate_delta.add_argument("--json", action="store_true")
    candidate_list = candidate_commands.add_parser(
        "list", help="list review candidates"
    )
    candidate_list.add_argument("--source")
    candidate_list.add_argument(
        "--kind", choices=("atomic-note", "link", "practice")
    )
    candidate_list.add_argument("--run")
    candidate_list.add_argument("--current-only", action="store_true")
    candidate_list.add_argument("--json", action="store_true")
    candidate_show = candidate_commands.add_parser(
        "show", help="show one candidate in full"
    )
    candidate_show.add_argument("--id", required=True)
    candidate_show.add_argument("--json", action="store_true")

    candidate_decide = candidate_commands.add_parser(
        "decide", help="record a human review decision"
    )
    candidate_decide.add_argument("--id", required=True)
    candidate_decide.add_argument(
        "--decision",
        required=True,
        choices=["accept", "edit", "skip", "reject"],
    )
    candidate_decide.add_argument("--item-id")
    candidate_decide.add_argument("--title")
    candidate_decide.add_argument("--focus")
    candidate_decide.add_argument("--prompt")
    candidate_decide.add_argument("--answer")
    candidate_decide.add_argument("--hint")
    candidate_decide.add_argument("--follow-up")
    candidate_decide.add_argument("--note")
    candidate_decide.add_argument("--json", action="store_true")

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

    transfer_check = transfer_commands.add_parser(
        "check", help="manage delayed capability-evidence checks"
    )
    transfer_check_commands = transfer_check.add_subparsers(
        dest="transfer_check_command", required=True
    )
    transfer_check_create = transfer_check_commands.add_parser(
        "create", help="create a delayed check for an existing transfer event"
    )
    transfer_check_create.add_argument("--event", required=True)
    transfer_check_create.add_argument(
        "--context-kind", choices=("changed", "novel"), required=True
    )
    transfer_check_create.add_argument("--context", required=True)
    transfer_check_create.add_argument("--prompt", required=True)
    transfer_check_create.add_argument("--acceptance-criteria", required=True)
    transfer_check_create.add_argument(
        "--scorer-kind", choices=("self", "human", "tool", "agent"), required=True
    )
    transfer_check_create.add_argument("--scorer-reference", required=True)
    transfer_check_create.add_argument("--json", action="store_true")

    transfer_check_due = transfer_check_commands.add_parser(
        "due", help="list incomplete delayed checks whose evidence date is due"
    )
    transfer_check_due.add_argument("--as-of")
    transfer_check_due.add_argument("--json", action="store_true")

    transfer_check_begin = transfer_check_commands.add_parser(
        "begin",
        help="record a prediction before attempting the challenge or requesting help",
        description=(
            "Record a pre-attempt prediction before attempting the changed challenge "
            "or requesting help."
        ),
    )
    transfer_check_begin.add_argument("--check", required=True)
    transfer_check_begin.add_argument("--prediction", required=True)
    transfer_check_begin.add_argument("--json", action="store_true")

    transfer_check_complete = transfer_check_commands.add_parser(
        "complete", help="append attributed delayed transfer evidence"
    )
    transfer_check_complete.add_argument("--check", required=True)
    transfer_check_complete.add_argument("--attempt", required=True)
    transfer_check_complete.add_argument(
        "--assistance",
        choices=("none", "light", "substantial", "unknown"),
        required=True,
    )
    transfer_check_complete.add_argument("--assistance-detail")
    transfer_check_complete.add_argument("--acceptance-evidence", required=True)
    transfer_check_complete.add_argument("--teach-back", required=True)
    transfer_check_complete.add_argument(
        "--outcome", choices=("successful", "partial", "unsuccessful"), required=True
    )
    transfer_check_complete.add_argument("--artifact")
    transfer_check_complete.add_argument("--json", action="store_true")
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
        if args.command == "retire":
            status = workspace.retire_item(args.id)
            _emit({"item_id": args.id, "status": status}, as_json=args.json)
            return 0
        if args.command == "next":
            selection = workspace.select_next(
                datetime.now(timezone.utc), focus=args.focus
            )
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
            administered_only = {
                "--response": args.response,
                "--result": args.result,
                "--confidence": args.confidence,
            }
            if args.administer:
                missing = [
                    flag
                    for flag, value in administered_only.items()
                    if value is None
                ]
                if missing:
                    raise WorkspaceError(
                        "practice --administer requires "
                        + ", ".join(sorted(administered_only))
                        + "; missing: "
                        + ", ".join(missing)
                    )
                result = PracticeService(workspace).run_administered(
                    item_id=args.item,
                    response=args.response,
                    result=args.result,
                    confidence=args.confidence,
                    agent_help=args.agent_help or "substantial",
                )
                _emit(
                    {
                        "event_id": result.attempt.event_id,
                        "item_id": result.attempt.item_id,
                        "result": result.attempt.result,
                        "confidence": result.attempt.confidence,
                        "agent_help": result.attempt.agent_help,
                        "administered": result.attempt.administered,
                        "initial_latency_ms": result.attempt.initial_latency_ms,
                        "occurred_at": result.attempt.occurred_at.isoformat(),
                        "proposal_due_at": result.proposal.due_at.isoformat(),
                        "proposal_algorithm": result.proposal.algorithm,
                    },
                    as_json=args.json,
                )
                return 0
            supplied = [
                flag
                for flag, value in administered_only.items()
                if value is not None
            ]
            if supplied:
                raise WorkspaceError(
                    ", ".join(supplied)
                    + " only apply to agent-administered practice; add --administer"
                )
            PracticeService(workspace).run(
                item_id=args.item,
                io=ConsoleIO(),
                agent_help=args.agent_help or "none",
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
        if args.command == "queries":
            from . import queries as queries_module

            if args.query_command == "focus":
                _emit(
                    {
                        "schema": "virtuoso/focus-performance@0.1",
                        "focuses": [
                            asdict(summary)
                            for summary in queries_module.focus_performance(
                                workspace.db_path
                            )
                        ],
                    },
                    as_json=args.json,
                )
                return 0
            if args.query_command == "history":
                _emit(
                    {
                        "schema": "virtuoso/item-history@0.1",
                        "item_id": args.item,
                        "attempts": [
                            asdict(entry)
                            for entry in queries_module.item_history(
                                workspace.db_path, args.item
                            )
                        ],
                    },
                    as_json=args.json,
                )
                return 0
            if args.query_command == "workload":
                _emit(
                    {
                        "schema": "virtuoso/workload-by-focus@0.1",
                        "focuses": queries_module.workload_by_focus(
                            workspace.db_path
                        ),
                    },
                    as_json=args.json,
                )
                return 0
            if args.query_command == "stale-links":
                _emit(
                    {
                        "schema": "virtuoso/stale-links@0.1",
                        "links": queries_module.stale_links(workspace.db_path),
                    },
                    as_json=args.json,
                )
                return 0
        if args.command == "doctor":
            _emit(workspace.doctor(), as_json=args.json)
            return 0
        if args.command == "search":
            from dataclasses import asdict as _asdict

            from . import search as search_module

            if args.search_command == "lex":
                hits = search_module.lexical_search(
                    workspace, args.query, limit=args.limit
                )
                _emit(
                    {
                        "schema": "virtuoso/lexical-search@0.1",
                        "query": args.query,
                        "hits": [_asdict(hit) for hit in hits],
                    },
                    as_json=args.json,
                )
                return 0
            if args.search_command == "embed":
                try:
                    vector = json.loads(args.vector)
                    if not isinstance(vector, list):
                        raise ValueError("vector must be a JSON array")
                except ValueError as exc:
                    raise search_module.SearchError(
                        f"invalid vector JSON: {exc}"
                    ) from exc
                search_module.embed_upsert(
                    workspace,
                    item_id=args.item,
                    model=args.model,
                    vector=vector,
                )
                _emit(
                    {
                        "schema": "virtuoso/embed-upsert@0.1",
                        "item_id": args.item,
                        "model": args.model,
                        "dim": len(vector),
                    },
                    as_json=True,
                )
                return 0
            if args.search_command == "sem":
                try:
                    vector = json.loads(args.vector)
                    if not isinstance(vector, list):
                        raise ValueError("vector must be a JSON array")
                except ValueError as exc:
                    raise search_module.SearchError(
                        f"invalid vector JSON: {exc}"
                    ) from exc
                hits = search_module.semantic_search(
                    workspace, model=args.model, query_vector=vector, limit=args.limit
                )
                _emit(
                    {
                        "schema": "virtuoso/semantic-search@0.1",
                        "model": args.model,
                        "hits": [_asdict(hit) for hit in hits],
                    },
                    as_json=args.json,
                )
                return 0
            if args.search_command == "status":
                _emit(search_module.search_status(workspace), as_json=args.json)
                return 0
        if args.command == "candidate":
            service = CandidateService(workspace)
            if args.candidate_command == "generate":
                run = service.generate(
                    source_id=args.source,
                    relative_path=args.path,
                    limit=args.limit,
                    adapter=args.adapter,
                    persist=not args.dry_run,
                )
                _emit(run.to_dict(), as_json=args.json)
                return 0
            if args.candidate_command == "delta":
                run = service.delta(
                    source_id=args.source,
                    relative_path=args.path,
                    limit=args.limit,
                )
                if run is not None:
                    _emit(run.to_dict(), as_json=args.json)
                return 0
            if args.candidate_command == "list":
                candidates = service.list(
                    source_id=args.source,
                    kind=args.kind,
                    run_id=args.run,
                    current_only=args.current_only,
                )
                _emit(
                    {
                        "schema": "virtuoso/review-candidate-list@0.1",
                        "candidates": [candidate.to_dict() for candidate in candidates],
                    },
                    as_json=args.json,
                )
                return 0
            if args.candidate_command == "show":
                _emit(service.get(args.id).to_dict(), as_json=args.json)
                return 0
            if args.candidate_command == "decide":
                edit_values = {
                    "item_id": args.item_id,
                    "title": args.title,
                    "focus": args.focus,
                    "prompt": args.prompt,
                    "answer": args.answer,
                    "hint": args.hint,
                    "follow_up": args.follow_up,
                }
                edits = {
                    field: value
                    for field, value in edit_values.items()
                    if value is not None
                }
                result = service.decide(
                    candidate_id=args.id,
                    decision=args.decision,
                    note=args.note,
                    edits=edits or None,
                )
                _emit(result.to_dict(), as_json=args.json)
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
            if args.transfer_command == "check":
                if args.transfer_check_command == "create":
                    check = workspace.create_transfer_check(
                        transfer_event_id=args.event,
                        context_kind=args.context_kind,
                        context_description=args.context,
                        challenge_prompt=args.prompt,
                        acceptance_criteria=args.acceptance_criteria,
                        scorer_kind=args.scorer_kind,
                        scorer_reference=args.scorer_reference,
                    )
                    _emit(asdict(check), as_json=args.json)
                    return 0
                if args.transfer_check_command == "due":
                    as_of = (
                        _parse_cli_timestamp(args.as_of, field="as-of timestamp")
                        if args.as_of is not None
                        else datetime.now(timezone.utc)
                    )
                    checks = workspace.list_due_transfer_checks(as_of=as_of)
                    if args.json:
                        _emit(
                            {
                                "as_of": as_of.astimezone(timezone.utc).isoformat(),
                                "checks": [asdict(check) for check in checks],
                            },
                            as_json=True,
                        )
                    elif not checks:
                        print("No delayed transfer checks are due.")
                    else:
                        for index, check in enumerate(checks):
                            if index:
                                print()
                            print(f"{check.check_id} [{check.status}]")
                            print(f"due_at: {check.due_at}")
                            print(f"project_id: {check.project_id}")
                            print(f"context_kind: {check.context_kind}")
                            print(f"challenge_prompt: {check.challenge_prompt}")
                            print(f"acceptance_criteria: {check.acceptance_criteria}")
                            print(
                                "scorer: "
                                f"{check.scorer_kind} ({check.scorer_reference})"
                            )
                    return 0
                if args.transfer_check_command == "begin":
                    prediction = workspace.begin_transfer_check(
                        check_id=args.check,
                        pre_attempt_prediction=args.prediction,
                    )
                    _emit(asdict(prediction), as_json=args.json)
                    if not args.json:
                        print(
                            "Prediction recorded before the attempt. Complete the "
                            "challenge before requesting help."
                        )
                    return 0
                if args.transfer_check_command == "complete":
                    completion = workspace.complete_transfer_check(
                        check_id=args.check,
                        independent_attempt=args.attempt,
                        assistance_level=args.assistance,
                        assistance_detail=args.assistance_detail,
                        acceptance_evidence=args.acceptance_evidence,
                        teach_back=args.teach_back,
                        outcome=args.outcome,
                        artifact_reference=args.artifact,
                    )
                    _emit(asdict(completion), as_json=args.json)
                    if not args.json:
                        print(
                            "Delayed transfer evidence recorded. "
                            "No capability or mastery state changed."
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
            if args.source_command == "relink":
                link = workspace.relink_item_source(
                    item_id=args.item,
                    source_id=args.id,
                    relative_path=args.path,
                )
                _emit(link, as_json=args.json)
                return 0
            if args.source_command == "unlink":
                result = workspace.unlink_item_source(
                    item_id=args.item,
                    source_id=args.id,
                    relative_path=args.path,
                )
                _emit(result, as_json=args.json)
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
    except VirtuosoError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except sqlite3.Error as exc:
        print(f"Error: database unavailable: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
