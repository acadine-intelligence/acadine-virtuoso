from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any, Literal, cast

from .workspace import SourceDocument, WorkspaceError, WorkspaceService

CandidateKind = Literal["atomic-note", "link", "practice"]
SourceStatus = Literal["current", "changed", "missing", "unsafe"]

STRUCTURAL_GENERATOR_ID = "structural-candidate-queue"
STRUCTURAL_GENERATOR_VERSION = "0.1"
CURRICULUM_GENERATOR_ID = "curriculum-markdown-import"
CURRICULUM_GENERATOR_VERSION = "0.1"

# Backward-compatible names for the original structural generator.
GENERATOR_ID = STRUCTURAL_GENERATOR_ID
GENERATOR_VERSION = STRUCTURAL_GENERATOR_VERSION

_ITEM_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_FRONTMATTER_FIELD = re.compile(r"^([A-Za-z0-9_-]+)\s*:\s*(.*?)\s*$")
_PRACTICE_BLOCK = re.compile(
    r"(?ms)^```virtuoso-practice[ \t]*\n(.*?)[ \t]*\n```[ \t]*$"
)


class CandidateError(WorkspaceError):
    """A candidate operation could not preserve its validation or provenance."""


@dataclass(frozen=True)
class IndexedNoteRef:
    source_id: str
    relative_path: str
    title: str
    content_hash: str


@dataclass(frozen=True)
class CandidateDraft:
    kind: CandidateKind
    title: str
    reason_code: str
    rationale: str
    uncertainty: str | None
    proposal: dict[str, object]
    source_refs: tuple[IndexedNoteRef, ...]


@dataclass(frozen=True)
class CandidateBatch:
    drafts: tuple[CandidateDraft, ...]
    snapshot_sha256: str
    omitted_count: int
    truncated: bool


@dataclass(frozen=True)
class ReviewCandidate:
    candidate_id: str
    run_id: str
    kind: CandidateKind
    title: str
    reason_code: str
    rationale: str
    uncertainty: str | None
    proposal: dict[str, object]
    source_refs: tuple[IndexedNoteRef, ...]
    source_status: SourceStatus
    authority: str = "proposal"
    review_state: str = "proposed"
    claims_mastery: bool = False
    decision: str | None = None
    materialized_item_id: str | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema": "virtuoso/review-candidate@0.1",
            "candidate_id": self.candidate_id,
            "run_id": self.run_id,
            "kind": self.kind,
            "title": self.title,
            "reason_code": self.reason_code,
            "rationale": self.rationale,
            "uncertainty": self.uncertainty,
            "authority": self.authority,
            "review_state": self.review_state,
            "claims_mastery": self.claims_mastery,
            "source_refs": [asdict(ref) for ref in self.source_refs],
            "proposal": self.proposal,
            "source_status": self.source_status,
        }
        if self.decision is not None:
            payload["decision"] = self.decision
        if self.materialized_item_id is not None:
            payload["materialized_item_id"] = self.materialized_item_id
        return payload


@dataclass(frozen=True)
class CandidateRun:
    run_id: str
    generator_id: str
    generator_version: str
    source_id: str
    scope_relative_path: str
    snapshot_sha256: str
    max_candidates: int
    candidate_count: int
    omitted_count: int
    truncated: bool
    created_at: str
    candidates: tuple[ReviewCandidate, ...]
    persisted_new: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": "virtuoso/candidate-run@0.1",
            "run_id": self.run_id,
            "generator_id": self.generator_id,
            "generator_version": self.generator_version,
            "source_id": self.source_id,
            "scope_relative_path": self.scope_relative_path,
            "snapshot_sha256": self.snapshot_sha256,
            "max_candidates": self.max_candidates,
            "candidate_count": self.candidate_count,
            "omitted_count": self.omitted_count,
            "truncated": self.truncated,
            "created_at": self.created_at,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
        }


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _sha256_json(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _normalize(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().casefold()


def _validated_source_path(relative_path: str) -> PurePosixPath:
    path = PurePosixPath(relative_path)
    if (
        not relative_path
        or path.is_absolute()
        or ".." in path.parts
        or path.as_posix() != relative_path
    ):
        raise CandidateError("candidate source path must stay inside its source root")
    return path


def _document_snapshot(document: SourceDocument) -> dict[str, object]:
    return {
        "source_id": document.source_id,
        "relative_path": document.relative_path,
        "title": document.title,
        "content_hash": document.content_hash,
        "wikilinks": list(document.wikilinks),
        "modified_ns": document.modified_ns,
        "byte_size": document.byte_size,
    }


def _indexed_ref(document: SourceDocument) -> IndexedNoteRef:
    return IndexedNoteRef(
        source_id=document.source_id,
        relative_path=document.relative_path,
        title=document.title,
        content_hash=document.content_hash,
    )


def _snapshot_sha256(catalog: tuple[SourceDocument, ...]) -> str:
    return _sha256_json(
        {
            "schema": "virtuoso/source-metadata-snapshot@0.1",
            "documents": [_document_snapshot(document) for document in catalog],
        }
    )


def _content_snapshot_sha256(document: SourceDocument) -> str:
    return _sha256_json(
        {
            "schema": "virtuoso/source-content-snapshot@0.1",
            "source_ref": asdict(_indexed_ref(document)),
        }
    )


def _frontmatter(text: str) -> tuple[dict[str, str], str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise CandidateError("curriculum note must start with YAML frontmatter")
    fields: dict[str, str] = {}
    closing_index: int | None = None
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            closing_index = index
            break
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = _FRONTMATTER_FIELD.fullmatch(line)
        if match is None:
            raise CandidateError("curriculum frontmatter must contain scalar fields only")
        key, raw_value = match.groups()
        if key in fields:
            raise CandidateError(f"curriculum frontmatter repeats field: {key}")
        value = raw_value.strip()
        if value.startswith('"'):
            try:
                decoded = json.loads(value)
            except json.JSONDecodeError as exc:
                raise CandidateError(
                    f"curriculum frontmatter field {key} has invalid JSON quoting"
                ) from exc
            if not isinstance(decoded, str):
                raise CandidateError(f"curriculum frontmatter field {key} must be text")
            value = decoded
        elif len(value) >= 2 and value[0] == value[-1] == "'":
            value = value[1:-1]
        fields[key] = value
    if closing_index is None:
        raise CandidateError("curriculum frontmatter is not closed")
    return fields, "\n".join(lines[closing_index + 1 :])


def _practice_item(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise CandidateError("virtuoso-practice block must contain one JSON object")
    expected_fields = {
        "schema",
        "id",
        "title",
        "focus",
        "prompt",
        "answer",
        "hint",
        "follow_up",
        "state",
        "historical_due_at",
    }
    if set(value) != expected_fields:
        raise CandidateError("virtuoso-practice fields are malformed")
    if value["schema"] != "virtuoso/practice-item@0.1":
        raise CandidateError("unsupported virtuoso-practice schema")
    item_id = value["id"]
    if not isinstance(item_id, str) or _ITEM_ID.fullmatch(item_id) is None:
        raise CandidateError(
            "practice item id must be lowercase words or numbers separated by single dashes"
        )
    if value["state"] != "active":
        raise CandidateError("unsupported practice item state; expected active")
    for field, maximum in (
        ("title", 256),
        ("focus", 256),
        ("prompt", 20_000),
        ("answer", 20_000),
    ):
        field_text = value[field]
        if (
            not isinstance(field_text, str)
            or not field_text.strip()
            or len(field_text) > maximum
        ):
            raise CandidateError(
                f"practice item {field} must contain 1-{maximum} characters"
            )
        if any(
            ord(character) < 32 and character not in {"\n", "\t"}
            for character in field_text
        ):
            raise CandidateError(
                f"practice item {field} contains unsafe control characters"
            )
        if re.search(r"(?m)^# ", field_text):
            raise CandidateError(
                f"practice item {field} must not contain a top-level Markdown heading"
            )
    for field in ("hint", "follow_up"):
        field_text = value[field]
        if field_text is not None and (
            not isinstance(field_text, str)
            or not field_text.strip()
            or len(field_text) > 20_000
            or re.search(r"(?m)^# ", field_text)
        ):
            raise CandidateError(
                f"practice item {field} must be null or contain 1-20000 safe characters"
            )
        if isinstance(field_text, str) and any(
            ord(character) < 32 and character not in {"\n", "\t"}
            for character in field_text
        ):
            raise CandidateError(
                f"practice item {field} contains unsafe control characters"
            )
    historical_due_at = value["historical_due_at"]
    if historical_due_at is not None and (
        not isinstance(historical_due_at, str)
        or not historical_due_at.strip()
        or len(historical_due_at) > 128
    ):
        raise CandidateError(
            "practice item historical_due_at must be null or a non-empty string"
        )
    return {
        "schema": "virtuoso/item-draft@0.1",
        "item_id": item_id,
        "title": cast(str, value["title"]).strip(),
        "focus": cast(str, value["focus"]).strip(),
        "prompt": cast(str, value["prompt"]).strip(),
        "answer": cast(str, value["answer"]).strip(),
        "hint": (
            cast(str, value["hint"]).strip()
            if value["hint"] is not None
            else None
        ),
        "follow_up": (
            cast(str, value["follow_up"]).strip()
            if value["follow_up"] is not None
            else None
        ),
        "learning_context": "atomic-recall",
        "historical_due_at": historical_due_at,
    }


def _reject_nonfinite(token: str) -> None:
    raise ValueError(token)


def generate_curriculum_candidates(
    origin: SourceDocument,
    raw: bytes,
    *,
    limit: int = 20,
) -> CandidateBatch:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
        raise CandidateError("candidate limit must be an integer between 1 and 50")
    if hashlib.sha256(raw).hexdigest() != origin.content_hash:
        raise CandidateError(
            "curriculum note hash does not match the indexed source; rescan it"
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CandidateError("curriculum note must be valid UTF-8") from exc
    fields, body = _frontmatter(text)
    schema = fields.get("schema")
    if schema == "virtuoso/curriculum@0.1":
        blocks = _PRACTICE_BLOCK.findall(body)
        block_openings = len(
            re.findall(r"(?m)^```virtuoso-practice[ \t]*$", body)
        )
        if block_openings == 0:
            raise CandidateError(
                "curriculum note must declare at least one virtuoso-practice block"
            )
        if len(blocks) != block_openings:
            raise CandidateError("curriculum note has an incomplete practice block")
        items: list[dict[str, object]] = []
        for block in blocks:
            try:
                value = json.loads(block, parse_constant=_reject_nonfinite)
            except (json.JSONDecodeError, ValueError) as exc:
                raise CandidateError(
                    "virtuoso-practice block is not finite valid JSON"
                ) from exc
            items.append(_practice_item(value))
    elif schema == "virtuoso/item@0.1":
        expected_frontmatter = {
            "schema",
            "id",
            "title",
            "focus",
            "practice-format",
            "learning-context",
        }
        if set(fields) != expected_frontmatter:
            raise CandidateError("source item frontmatter fields are malformed")
        if (
            fields["practice-format"] != "active-recall"
            or fields["learning-context"] != "atomic-recall"
        ):
            raise CandidateError("source item uses an unsupported practice format or context")
        items = [
            _practice_item(
                {
                    "schema": "virtuoso/practice-item@0.1",
                    "id": fields["id"],
                    "title": fields["title"],
                    "focus": fields["focus"],
                    "prompt": WorkspaceService._section(
                        body, "Prompt", required=True
                    ),
                    "answer": WorkspaceService._section(
                        body, "Answer", required=True
                    ),
                    "hint": WorkspaceService._section(body, "Hint", required=False),
                    "follow_up": WorkspaceService._section(
                        body, "Follow-up challenge", required=False
                    ),
                    "state": "active",
                    "historical_due_at": None,
                }
            )
        ]
    else:
        raise CandidateError("unsupported curriculum note schema")
    item_ids = [cast(str, item["item_id"]) for item in items]
    duplicate_ids = sorted(
        item_id for item_id in set(item_ids) if item_ids.count(item_id) > 1
    )
    if duplicate_ids:
        raise CandidateError(
            "curriculum note repeats practice item ids: " + ", ".join(duplicate_ids)
        )
    items.sort(key=lambda item: cast(str, item["item_id"]))
    origin_ref = _indexed_ref(origin)
    drafts = [
        CandidateDraft(
            kind="practice",
            title=cast(str, item["title"]),
            reason_code="curriculum-practice-import",
            rationale=(
                "The explicitly selected curriculum note declares this versioned "
                "practice item for human review before import."
            ),
            uncertainty=(
                "The source due value is historical metadata and will not create "
                "scheduler state."
                if item["historical_due_at"] is not None
                else None
            ),
            proposal={
                "schema": "virtuoso/practice-import-candidate@0.1",
                "mode": "import",
                "adapter_id": CURRICULUM_GENERATOR_ID,
                "adapter_version": CURRICULUM_GENERATOR_VERSION,
                "item": item,
                "requires_human_decision": True,
                "creates_learning_item": True,
                "creates_scheduler_state": False,
                "creates_evidence_event": False,
            },
            source_refs=(origin_ref,),
        )
        for item in items
    ]
    omitted_count = max(0, len(drafts) - limit)
    return CandidateBatch(
        drafts=tuple(drafts[:limit]),
        snapshot_sha256=_content_snapshot_sha256(origin),
        omitted_count=omitted_count,
        truncated=omitted_count > 0,
    )


def _draft_sort_key(draft: CandidateDraft) -> tuple[int, str, str]:
    if draft.reason_code == "resolved-link-practice" and draft.title.startswith(
        "Connection practice: "
    ):
        target = draft.title.removeprefix("Connection practice: ")
    elif draft.reason_code == "curriculum-practice-import" and isinstance(
        draft.proposal.get("item"), dict
    ):
        target = str(cast(dict[str, object], draft.proposal["item"]).get("item_id", ""))
    else:
        target = str(draft.proposal.get("observed_target", ""))
    return (
        {"atomic-note": 0, "link": 1, "practice": 2}[draft.kind],
        _normalize(target),
        _canonical_json(draft.proposal),
    )


def _validate_text(
    value: object,
    *,
    field: str,
    minimum: int,
    maximum: int,
) -> str:
    if not isinstance(value, str) or not minimum <= len(value) <= maximum:
        raise CandidateError(
            f"candidate {field} must contain between {minimum} and {maximum} characters"
        )
    if any(ord(character) < 32 and character not in {"\n", "\t"} for character in value):
        raise CandidateError(f"candidate {field} contains unsafe control characters")
    return value


def _validate_draft(
    draft: CandidateDraft,
    *,
    origin: SourceDocument,
    catalog_refs: dict[tuple[str, str], IndexedNoteRef],
) -> None:
    if draft.kind not in {"atomic-note", "link", "practice"}:
        raise CandidateError("candidate output has an unknown kind")
    _validate_text(draft.title, field="title", minimum=1, maximum=256)
    _validate_text(draft.reason_code, field="reason code", minimum=1, maximum=128)
    _validate_text(draft.rationale, field="rationale", minimum=1, maximum=2000)
    if draft.uncertainty is not None:
        _validate_text(
            draft.uncertainty,
            field="uncertainty",
            minimum=0,
            maximum=2000,
        )
    if not isinstance(draft.proposal, dict):
        raise CandidateError("candidate proposal must be a JSON object")
    try:
        proposal_json = _canonical_json(draft.proposal)
    except (TypeError, ValueError) as exc:
        raise CandidateError(f"candidate proposal is malformed: {exc}") from exc
    if len(proposal_json.encode("utf-8")) > 65_536:
        raise CandidateError("candidate proposal exceeds the bounded output size")
    if not isinstance(draft.source_refs, tuple) or not draft.source_refs:
        raise CandidateError("candidate source refs must include the origin note")
    if len(draft.source_refs) > 51:
        raise CandidateError("candidate source refs exceed the bounded output limit")
    expected_origin = _indexed_ref(origin)
    if draft.source_refs[0] != expected_origin:
        raise CandidateError("candidate source refs do not preserve the exact origin hash")
    seen_refs: set[tuple[str, str]] = set()
    for ref in draft.source_refs:
        if not isinstance(ref, IndexedNoteRef):
            raise CandidateError("candidate source ref is malformed")
        key = (ref.source_id, ref.relative_path)
        if key in seen_refs:
            raise CandidateError("candidate source refs contain a duplicate note")
        seen_refs.add(key)
        if catalog_refs.get(key) != ref:
            raise CandidateError("candidate source ref hash does not match the indexed snapshot")

    proposal = draft.proposal
    if draft.kind == "atomic-note":
        expected_fields = {
            "schema",
            "mode",
            "observed_target",
            "suggested_title",
            "claim",
            "requires_human_drafting",
        }
        if set(proposal) != expected_fields:
            raise CandidateError("atomic-note candidate proposal fields are malformed")
        if (
            draft.reason_code != "unresolved-wikilink"
            or proposal["schema"] != "virtuoso/atomic-note-candidate@0.1"
            or proposal["mode"] != "unresolved-wikilink"
            or not isinstance(proposal["observed_target"], str)
            or not proposal["observed_target"]
            or not isinstance(proposal["suggested_title"], str)
            or not proposal["suggested_title"]
            or proposal["claim"] is not None
            or proposal["requires_human_drafting"] is not True
            or len(draft.source_refs) != 1
        ):
            raise CandidateError("atomic-note candidate proposal is malformed")
        return

    if draft.kind == "link":
        expected_fields = {
            "schema",
            "mode",
            "observed_target",
            "options",
            "selected_target",
            "requires_human_choice",
        }
        if set(proposal) != expected_fields:
            raise CandidateError("link candidate proposal fields are malformed")
        options = proposal["options"]
        if (
            draft.reason_code != "ambiguous-wikilink"
            or proposal["schema"] != "virtuoso/link-candidate@0.1"
            or proposal["mode"] != "disambiguate-existing-wikilink"
            or not isinstance(proposal["observed_target"], str)
            or not proposal["observed_target"]
            or not isinstance(options, list)
            or not 2 <= len(options) <= 50
            or proposal["selected_target"] is not None
            or proposal["requires_human_choice"] is not True
            or len(draft.source_refs) != len(options) + 1
        ):
            raise CandidateError("link candidate proposal is malformed")
        expected_options = [
            {
                "source_id": ref.source_id,
                "relative_path": ref.relative_path,
                "content_hash": ref.content_hash,
            }
            for ref in draft.source_refs[1:]
        ]
        if options != expected_options or any(
            not isinstance(option, dict)
            or set(option) != {"source_id", "relative_path", "content_hash"}
            for option in options
        ):
            raise CandidateError("link candidate options do not match exact source refs")
        return

    if proposal.get("schema") == "virtuoso/practice-import-candidate@0.1":
        expected_fields = {
            "schema",
            "mode",
            "adapter_id",
            "adapter_version",
            "item",
            "requires_human_decision",
            "creates_learning_item",
            "creates_scheduler_state",
            "creates_evidence_event",
        }
        item = proposal.get("item")
        expected_item_fields = {
            "schema",
            "item_id",
            "title",
            "focus",
            "prompt",
            "answer",
            "hint",
            "follow_up",
            "learning_context",
            "historical_due_at",
        }
        if (
            set(proposal) != expected_fields
            or draft.reason_code != "curriculum-practice-import"
            or draft.kind != "practice"
            or proposal["mode"] != "import"
            or proposal["adapter_id"] != CURRICULUM_GENERATOR_ID
            or proposal["adapter_version"] != CURRICULUM_GENERATOR_VERSION
            or proposal["requires_human_decision"] is not True
            or proposal["creates_learning_item"] is not True
            or proposal["creates_scheduler_state"] is not False
            or proposal["creates_evidence_event"] is not False
            or not isinstance(item, dict)
            or set(item) != expected_item_fields
            or item.get("schema") != "virtuoso/item-draft@0.1"
            or item.get("learning_context") != "atomic-recall"
            or len(draft.source_refs) != 1
        ):
            raise CandidateError("practice import candidate proposal is malformed")
        source_shape = {
            "schema": "virtuoso/practice-item@0.1",
            "id": item["item_id"],
            "title": item["title"],
            "focus": item["focus"],
            "prompt": item["prompt"],
            "answer": item["answer"],
            "hint": item["hint"],
            "follow_up": item["follow_up"],
            "state": "active",
            "historical_due_at": item["historical_due_at"],
        }
        if _practice_item(source_shape) != item:
            raise CandidateError("practice import candidate item is malformed")
        return

    expected_fields = {
        "schema",
        "mode",
        "prompt",
        "answer",
        "requires_human_answer",
        "creates_learning_item",
        "creates_evidence_event",
    }
    if set(proposal) != expected_fields:
        raise CandidateError("practice candidate proposal fields are malformed")
    mode = proposal["mode"]
    if (
        draft.reason_code
        != ("resolved-link-practice" if mode == "connect" else "isolated-note-practice")
        or proposal["schema"] != "virtuoso/practice-candidate@0.1"
        or mode not in {"connect", "explain"}
        or not isinstance(proposal["prompt"], str)
        or not proposal["prompt"]
        or len(proposal["prompt"]) > 2000
        or proposal["answer"] is not None
        or proposal["requires_human_answer"] is not True
        or proposal["creates_learning_item"] is not False
        or proposal["creates_evidence_event"] is not False
        or len(draft.source_refs) != (2 if mode == "connect" else 1)
    ):
        raise CandidateError("practice candidate proposal is malformed")


def _validate_batch(
    batch: CandidateBatch,
    *,
    origin: SourceDocument,
    catalog: tuple[SourceDocument, ...],
    limit: int,
    expected_snapshot_sha256: str | None = None,
) -> None:
    if not isinstance(batch, CandidateBatch):
        raise CandidateError("candidate generator returned a malformed batch")
    if len(batch.drafts) > limit:
        raise CandidateError("candidate generator output exceeds the requested limit")
    if (
        isinstance(batch.omitted_count, bool)
        or not isinstance(batch.omitted_count, int)
        or batch.omitted_count < 0
        or not isinstance(batch.truncated, bool)
        or batch.truncated != (batch.omitted_count > 0)
    ):
        raise CandidateError("candidate generator returned malformed truncation metadata")
    expected_snapshot = expected_snapshot_sha256 or _snapshot_sha256(catalog)
    if batch.snapshot_sha256 != expected_snapshot:
        raise CandidateError("candidate snapshot hash does not match indexed source provenance")
    catalog_refs = {
        (document.source_id, document.relative_path): _indexed_ref(document)
        for document in catalog
    }
    for draft in batch.drafts:
        _validate_draft(draft, origin=origin, catalog_refs=catalog_refs)
    if list(batch.drafts) != sorted(batch.drafts, key=_draft_sort_key):
        raise CandidateError("candidate generator output is not deterministically ordered")


def _matching_documents(
    target: str,
    *,
    origin: SourceDocument,
    catalog: tuple[SourceDocument, ...],
) -> tuple[SourceDocument, ...]:
    normalized_target = _normalize(target)
    matches: list[SourceDocument] = []
    for document in catalog:
        if (
            document.source_id == origin.source_id
            and document.relative_path == origin.relative_path
        ):
            continue
        path_without_suffix = (
            document.relative_path[:-3]
            if document.relative_path.lower().endswith(".md")
            else document.relative_path
        )
        keys = {
            _normalize(document.title),
            _normalize(PurePosixPath(document.relative_path).stem),
            _normalize(path_without_suffix),
        }
        if normalized_target in keys:
            matches.append(document)
    return tuple(sorted(matches, key=lambda value: (value.source_id, value.relative_path)))


def generate_structural_candidates(
    origin: SourceDocument,
    catalog: tuple[SourceDocument, ...],
    *,
    limit: int = 20,
) -> CandidateBatch:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
        raise CandidateError("candidate limit must be an integer between 1 and 50")
    ordered_catalog = tuple(
        sorted(catalog, key=lambda value: (value.source_id, value.relative_path))
    )
    if not any(document == origin for document in ordered_catalog):
        raise CandidateError("candidate origin is not part of the indexed source snapshot")
    snapshot_sha256 = _snapshot_sha256(ordered_catalog)
    drafts: list[CandidateDraft] = []
    origin_ref = _indexed_ref(origin)
    for target in origin.wikilinks:
        matches = _matching_documents(target, origin=origin, catalog=ordered_catalog)
        if len(matches) > 1:
            drafts.append(
                CandidateDraft(
                    kind="link",
                    title=f"Disambiguate link: {target}",
                    reason_code="ambiguous-wikilink",
                    rationale=(
                        f"The explicit link “{target}” matches multiple notes in the "
                        "current metadata index; a human must choose the intended note."
                    ),
                    uncertainty=(
                        "Indexed titles, basenames, and paths do not establish intent; "
                        "human verification is required."
                    ),
                    proposal={
                        "schema": "virtuoso/link-candidate@0.1",
                        "mode": "disambiguate-existing-wikilink",
                        "observed_target": target,
                        "options": [
                            {
                                "source_id": match.source_id,
                                "relative_path": match.relative_path,
                                "content_hash": match.content_hash,
                            }
                            for match in matches
                        ],
                        "selected_target": None,
                        "requires_human_choice": True,
                    },
                    source_refs=(origin_ref, *map(_indexed_ref, matches)),
                )
            )
            continue
        if len(matches) == 1:
            target_note = matches[0]
            drafts.append(
                CandidateDraft(
                    kind="practice",
                    title=f"Connection practice: {target}",
                    reason_code="resolved-link-practice",
                    rationale=(
                        f"The explicit link “{target}” resolves to one non-self note in "
                        "the current metadata index, so it can support a recall-first "
                        "connection prompt without drafting an answer."
                    ),
                    uncertainty=None,
                    proposal={
                        "schema": "virtuoso/practice-candidate@0.1",
                        "mode": "connect",
                        "prompt": (
                            f"Without consulting either note, explain how “{origin.title}” "
                            f"connects to “{target_note.title}”."
                        ),
                        "answer": None,
                        "requires_human_answer": True,
                        "creates_learning_item": False,
                        "creates_evidence_event": False,
                    },
                    source_refs=(origin_ref, _indexed_ref(target_note)),
                )
            )
            continue
        drafts.append(
            CandidateDraft(
                kind="atomic-note",
                title=f"Atomic note needed: {target}",
                reason_code="unresolved-wikilink",
                rationale=(
                    f"The indexed note links explicitly to “{target}”, but the current "
                    "metadata index has no matching title, basename, or path."
                ),
                uncertainty=(
                    "Aliases and full Obsidian resolution semantics are not indexed; "
                    "human verification is required before treating this target as absent."
                ),
                proposal={
                    "schema": "virtuoso/atomic-note-candidate@0.1",
                    "mode": "unresolved-wikilink",
                    "observed_target": target,
                    "suggested_title": target,
                    "claim": None,
                    "requires_human_drafting": True,
                },
                source_refs=(origin_ref,),
            )
        )
    if not origin.wikilinks:
        drafts.append(
            CandidateDraft(
                kind="practice",
                title=f"Explain {origin.title}",
                reason_code="isolated-note-practice",
                rationale=(
                    "The indexed note has no outgoing wikilinks, so one recall-first "
                    "explanation prompt is proposed without drafting an answer."
                ),
                uncertainty=None,
                proposal={
                    "schema": "virtuoso/practice-candidate@0.1",
                    "mode": "explain",
                    "prompt": (
                        f"Without consulting the note, explain “{origin.title}” in your "
                        "own words and identify one connection worth checking."
                    ),
                    "answer": None,
                    "requires_human_answer": True,
                    "creates_learning_item": False,
                    "creates_evidence_event": False,
                },
                source_refs=(origin_ref,),
            )
        )
    drafts.sort(key=_draft_sort_key)
    omitted_count = max(0, len(drafts) - limit)
    return CandidateBatch(
        drafts=tuple(drafts[:limit]),
        snapshot_sha256=snapshot_sha256,
        omitted_count=omitted_count,
        truncated=omitted_count > 0,
    )


class CandidateService:
    def __init__(self, workspace: WorkspaceService) -> None:
        self.workspace = workspace

    def generate(
        self,
        *,
        source_id: str,
        relative_path: str,
        limit: int = 20,
        adapter: str = "structural",
        persist: bool = True,
    ) -> CandidateRun:
        path = _validated_source_path(relative_path)
        normalized_path = path.as_posix()
        catalog = tuple(self.workspace.list_source_documents(source_id))
        origin = next(
            (
                document
                for document in catalog
                if document.relative_path == normalized_path
            ),
            None,
        )
        if origin is None:
            raise CandidateError(
                f"source note is not indexed: {source_id}/{normalized_path}; scan it first"
            )
        if adapter == "structural":
            generator_id = STRUCTURAL_GENERATOR_ID
            generator_version = STRUCTURAL_GENERATOR_VERSION
            batch = generate_structural_candidates(origin, catalog, limit=limit)
            expected_snapshot = _snapshot_sha256(catalog)
        elif adapter == "curriculum":
            generator_id = CURRICULUM_GENERATOR_ID
            generator_version = CURRICULUM_GENERATOR_VERSION
            source = self.workspace._source(source_id)
            try:
                raw = self.workspace._read_source_document_bytes(
                    source.root,
                    normalized_path,
                    max_bytes=origin.byte_size,
                )
            except WorkspaceError as exc:
                raise CandidateError(
                    "curriculum note changed or became unsafe; rescan the source: "
                    f"{exc}"
                ) from exc
            batch = generate_curriculum_candidates(origin, raw, limit=limit)
            expected_snapshot = _content_snapshot_sha256(origin)
        else:
            raise CandidateError("candidate adapter must be structural or curriculum")
        _validate_batch(
            batch,
            origin=origin,
            catalog=catalog,
            limit=limit,
            expected_snapshot_sha256=expected_snapshot,
        )
        referenced_notes = tuple(
            dict.fromkeys(
                ref
                for draft in batch.drafts
                for ref in draft.source_refs
            )
        ) or (_indexed_ref(origin),)
        snapshot_status = self._source_status(referenced_notes)
        if snapshot_status != "current":
            raise CandidateError(
                f"candidate source snapshot is {snapshot_status}; rescan the source before generating"
            )
        run_digest = _sha256_json(
            {
                "generator_id": generator_id,
                "generator_version": generator_version,
                "source_id": source_id,
                "scope_relative_path": normalized_path,
                "snapshot_sha256": batch.snapshot_sha256,
                "max_candidates": limit,
            }
        )
        run_id = f"candidate-run-{run_digest}"
        created_at = datetime.now(timezone.utc).isoformat()
        candidates: list[ReviewCandidate] = []
        storage_candidates: list[dict[str, Any]] = []
        for ordinal, draft in enumerate(batch.drafts):
            candidate_digest = _sha256_json(
                {
                    "generator_id": generator_id,
                    "generator_version": generator_version,
                    "snapshot_sha256": batch.snapshot_sha256,
                    "source_id": source_id,
                    "scope_relative_path": normalized_path,
                    "max_candidates": limit,
                    "ordinal": ordinal,
                    "kind": draft.kind,
                    "title": draft.title,
                    "reason_code": draft.reason_code,
                    "rationale": draft.rationale,
                    "uncertainty": draft.uncertainty,
                    "proposal": draft.proposal,
                    "source_refs": [asdict(ref) for ref in draft.source_refs],
                }
            )
            candidate_id = f"candidate-{candidate_digest}"
            review_candidate = ReviewCandidate(
                candidate_id=candidate_id,
                run_id=run_id,
                kind=draft.kind,
                title=draft.title,
                reason_code=draft.reason_code,
                rationale=draft.rationale,
                uncertainty=draft.uncertainty,
                proposal=draft.proposal,
                source_refs=draft.source_refs,
                source_status=self._source_status(draft.source_refs),
            )
            candidates.append(review_candidate)
            storage_candidates.append(
                {
                    "candidate_id": candidate_id,
                    "ordinal": ordinal,
                    "kind": draft.kind,
                    "title": draft.title,
                    "reason_code": draft.reason_code,
                    "rationale": draft.rationale,
                    "uncertainty": draft.uncertainty,
                    "proposal_json": _canonical_json(draft.proposal),
                    "authority": "proposal",
                    "review_state": "proposed",
                    "claims_mastery": False,
                    "source_refs": [
                        {
                            **asdict(ref),
                            "role": (
                                "origin"
                                if index == 0
                                else "target-option"
                                if draft.kind == "link"
                                else "target"
                            ),
                        }
                        for index, ref in enumerate(draft.source_refs)
                    ],
                }
            )
        run_record = {
            "run_id": run_id,
            "generator_id": generator_id,
            "generator_version": generator_version,
            "source_id": source_id,
            "scope_relative_path": normalized_path,
            "snapshot_sha256": batch.snapshot_sha256,
            "max_candidates": limit,
            "candidate_count": len(candidates),
            "omitted_count": batch.omitted_count,
            "truncated": batch.truncated,
            "created_at": created_at,
        }
        persisted_new = False
        if persist:
            stored_created_at, persisted_new = self.workspace.persist_candidate_run(
                run=run_record,
                candidates=storage_candidates,
            )
            run_record["created_at"] = stored_created_at
        return CandidateRun(
            **run_record,
            candidates=tuple(candidates),
            persisted_new=persisted_new,
        )

    def delta(
        self,
        *,
        source_id: str,
        relative_path: str,
        limit: int = 20,
    ) -> CandidateRun | None:
        """Persist a curriculum run only when its exact source snapshot is new."""
        path = _validated_source_path(relative_path)
        source = self.workspace._source(source_id)
        try:
            raw = self.workspace._read_source_document_bytes(
                source.root,
                relative_path,
                max_bytes=2_000_000,
            )
        except WorkspaceError as exc:
            raise CandidateError(f"curriculum note cannot be scanned safely: {exc}") from exc
        current_hash = hashlib.sha256(raw).hexdigest()
        indexed = next(
            (
                document
                for document in self.workspace.list_source_documents(source_id)
                if document.relative_path == relative_path
            ),
            None,
        )
        if indexed is None or indexed.content_hash != current_hash:
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise CandidateError("curriculum note must be valid UTF-8") from exc
            current_document = SourceDocument(
                source_id=source_id,
                relative_path=relative_path,
                title=WorkspaceService._source_title(text, path.stem),
                content_hash=current_hash,
                wikilinks=WorkspaceService._wikilinks(text),
                modified_ns=0,
                byte_size=len(raw),
            )
            # Validate the complete proposal before the source index changes.
            generate_curriculum_candidates(current_document, raw, limit=limit)
            self.workspace.scan_source(source_id)
        preview = self.generate(
            source_id=source_id,
            relative_path=relative_path,
            limit=limit,
            adapter="curriculum",
            persist=False,
        )
        if self.workspace.candidate_run_exists(preview.run_id):
            return None
        persisted = self.generate(
            source_id=source_id,
            relative_path=relative_path,
            limit=limit,
            adapter="curriculum",
            persist=True,
        )
        return persisted if persisted.persisted_new else None

    def list(
        self,
        *,
        source_id: str | None = None,
        kind: CandidateKind | None = None,
        run_id: str | None = None,
        current_only: bool = False,
    ) -> list[ReviewCandidate]:
        if kind is not None and kind not in {"atomic-note", "link", "practice"}:
            raise CandidateError("candidate kind must be atomic-note, link, or practice")
        records = self.workspace.candidate_records(
            source_id=source_id,
            kind=kind,
            run_id=run_id,
        )
        candidates = [self._candidate_from_record(record) for record in records]
        if current_only:
            candidates = [
                candidate
                for candidate in candidates
                if candidate.source_status == "current"
            ]
        return candidates

    def get(self, candidate_id: str) -> ReviewCandidate:
        records = self.workspace.candidate_records(candidate_id=candidate_id)
        if not records:
            raise CandidateError(f"no review candidate with id: {candidate_id}")
        if len(records) != 1:
            raise CandidateError("workspace database corruption: duplicate candidate id")
        return self._candidate_from_record(records[0])

    def decide(
        self,
        *,
        candidate_id: str,
        decision: str,
        note: str | None,
        edits: dict[str, object] | None = None,
    ) -> ReviewCandidate:
        """Record one human decision and import an accepted curriculum item."""
        if decision not in {"accept", "edit", "skip", "reject"}:
            raise CandidateError(
                "candidate decision must be accept, edit, skip, or reject"
            )
        if note is not None and not (1 <= len(note) <= 2000):
            raise CandidateError("candidate decision note must be 1-2000 characters")
        candidate = self.get(candidate_id)
        if candidate.decision is not None:
            raise CandidateError(
                f"a decision for candidate {candidate.candidate_id} already exists"
            )
        is_import = (
            candidate.proposal.get("schema")
            == "virtuoso/practice-import-candidate@0.1"
        )
        materialized_item: dict[str, Any] | None = None
        if decision in {"accept", "edit"} and is_import:
            if candidate.source_status != "current":
                raise CandidateError(
                    f"candidate source snapshot is {candidate.source_status}; "
                    "generate a new candidate before importing"
                )
            proposed = candidate.proposal.get("item")
            if not isinstance(proposed, dict):
                raise CandidateError("import candidate item is malformed")
            materialized_item = {
                key: proposed[key]
                for key in (
                    "item_id",
                    "title",
                    "focus",
                    "prompt",
                    "answer",
                    "hint",
                    "follow_up",
                )
            }
            if decision == "accept":
                if edits:
                    raise CandidateError("accept does not allow item edits; use edit")
            else:
                allowed_edits = {
                    "item_id",
                    "title",
                    "focus",
                    "prompt",
                    "answer",
                    "hint",
                    "follow_up",
                }
                if not edits:
                    raise CandidateError("edit requires at least one item field")
                unknown = sorted(set(edits) - allowed_edits)
                if unknown:
                    raise CandidateError(
                        "unknown candidate edit fields: " + ", ".join(unknown)
                    )
                materialized_item.update(edits)
        elif decision == "edit":
            raise CandidateError("only curriculum import candidates support edit")
        elif edits:
            raise CandidateError(f"{decision} does not allow item edits")

        decided_at = datetime.now(timezone.utc).isoformat()
        decision_id = "decision-" + hashlib.sha256(
            json.dumps(
                {
                    "candidate_id": candidate.candidate_id,
                    "decision": decision,
                    "decided_at": decided_at,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        self.workspace.record_candidate_decision(
            decision_id=decision_id,
            candidate_id=candidate.candidate_id,
            action=decision,
            note=note,
            decided_at=decided_at,
            item=materialized_item,
            source_refs=(
                [asdict(source_ref) for source_ref in candidate.source_refs]
                if materialized_item is not None
                else []
            ),
        )
        return self.get(candidate_id)

    @staticmethod
    def _has_derived_id(value: object, *, prefix: str) -> bool:
        if not isinstance(value, str) or not value.startswith(prefix):
            return False
        digest = value[len(prefix) :]
        return len(digest) == 64 and all(
            character in "0123456789abcdef" for character in digest
        )

    def _candidate_from_record(self, record: dict[str, Any]) -> ReviewCandidate:
        supported_generator = (
            record.get("generator_id"),
            record.get("generator_version"),
        ) in {
            (STRUCTURAL_GENERATOR_ID, STRUCTURAL_GENERATOR_VERSION),
            (CURRICULUM_GENERATOR_ID, CURRICULUM_GENERATOR_VERSION),
        }
        if (
            not self._has_derived_id(record.get("run_id"), prefix="candidate-run-")
            or not self._has_derived_id(record.get("candidate_id"), prefix="candidate-")
            or not supported_generator
            or record.get("kind") not in {"atomic-note", "link", "practice"}
            or record.get("authority") != "proposal"
            or record.get("review_state") != "proposed"
            or record.get("claims_mastery") != 0
            or not isinstance(record.get("ordinal"), int)
            or record["ordinal"] < 0
            or not isinstance(record.get("max_candidates"), int)
            or not 1 <= record["max_candidates"] <= 50
            or not isinstance(record.get("snapshot_sha256"), str)
            or len(record["snapshot_sha256"]) != 64
        ):
            raise CandidateError("stored review candidate envelope is malformed")
        try:
            proposal = json.loads(record["proposal_json"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise CandidateError("stored candidate proposal JSON is malformed") from exc
        if not isinstance(proposal, dict):
            raise CandidateError("stored candidate proposal JSON must be an object")
        stored_refs = record.get("source_refs")
        if not isinstance(stored_refs, list) or not stored_refs:
            raise CandidateError("stored candidate source refs are malformed")
        refs: list[IndexedNoteRef] = []
        roles: list[str] = []
        for expected_ordinal, stored_ref in enumerate(stored_refs):
            if (
                not isinstance(stored_ref, dict)
                or set(stored_ref)
                != {
                    "candidate_id",
                    "ordinal",
                    "role",
                    "source_id",
                    "relative_path",
                    "title",
                    "content_hash",
                }
                or stored_ref["candidate_id"] != record["candidate_id"]
                or stored_ref["ordinal"] != expected_ordinal
                or stored_ref["role"] not in {"origin", "target", "target-option"}
                or not isinstance(stored_ref["source_id"], str)
                or not stored_ref["source_id"]
                or not isinstance(stored_ref["relative_path"], str)
                or not stored_ref["relative_path"]
                or not isinstance(stored_ref["title"], str)
                or not stored_ref["title"]
                or not isinstance(stored_ref["content_hash"], str)
                or len(stored_ref["content_hash"]) != 64
                or any(
                    character not in "0123456789abcdef"
                    for character in stored_ref["content_hash"]
                )
            ):
                raise CandidateError("stored candidate source ref is malformed")
            relative = PurePosixPath(stored_ref["relative_path"])
            if (
                relative.is_absolute()
                or ".." in relative.parts
                or relative.as_posix() != stored_ref["relative_path"]
            ):
                raise CandidateError("stored candidate source ref path is unsafe")
            refs.append(
                IndexedNoteRef(
                    source_id=stored_ref["source_id"],
                    relative_path=stored_ref["relative_path"],
                    title=stored_ref["title"],
                    content_hash=stored_ref["content_hash"],
                )
            )
            roles.append(stored_ref["role"])

        draft = CandidateDraft(
            kind=cast(CandidateKind, record["kind"]),
            title=record["title"],
            reason_code=record["reason_code"],
            rationale=record["rationale"],
            uncertainty=record["uncertainty"],
            proposal=proposal,
            source_refs=tuple(refs),
        )
        origin_ref = refs[0]
        origin = SourceDocument(
            source_id=origin_ref.source_id,
            relative_path=origin_ref.relative_path,
            title=origin_ref.title,
            content_hash=origin_ref.content_hash,
            wikilinks=(),
            modified_ns=0,
            byte_size=0,
        )
        _validate_draft(
            draft,
            origin=origin,
            catalog_refs={
                (ref.source_id, ref.relative_path): ref for ref in refs
            },
        )
        expected_roles = ["origin"] + [
            "target-option" if draft.kind == "link" else "target"
            for _ in refs[1:]
        ]
        if roles != expected_roles:
            raise CandidateError("stored candidate source ref roles are malformed")

        run_digest = _sha256_json(
            {
                "generator_id": record["generator_id"],
                "generator_version": record["generator_version"],
                "source_id": record["run_source_id"],
                "scope_relative_path": record["scope_relative_path"],
                "snapshot_sha256": record["snapshot_sha256"],
                "max_candidates": record["max_candidates"],
            }
        )
        if record["run_id"] != f"candidate-run-{run_digest}":
            raise CandidateError("stored candidate run id does not match its immutable data")
        candidate_digest = _sha256_json(
            {
                "generator_id": record["generator_id"],
                "generator_version": record["generator_version"],
                "snapshot_sha256": record["snapshot_sha256"],
                "source_id": record["run_source_id"],
                "scope_relative_path": record["scope_relative_path"],
                "max_candidates": record["max_candidates"],
                "ordinal": record["ordinal"],
                "kind": draft.kind,
                "title": draft.title,
                "reason_code": draft.reason_code,
                "rationale": draft.rationale,
                "uncertainty": draft.uncertainty,
                "proposal": draft.proposal,
                "source_refs": [asdict(ref) for ref in draft.source_refs],
            }
        )
        if record["candidate_id"] != f"candidate-{candidate_digest}":
            raise CandidateError(
                "stored candidate id does not match its immutable proposal and provenance"
            )
        return ReviewCandidate(
            candidate_id=record["candidate_id"],
            run_id=record["run_id"],
            kind=draft.kind,
            title=draft.title,
            reason_code=draft.reason_code,
            rationale=draft.rationale,
            uncertainty=draft.uncertainty,
            proposal=draft.proposal,
            source_refs=draft.source_refs,
            source_status=self._source_status(draft.source_refs),
            review_state=record.get("effective_review_state") or "proposed",
            decision=record.get("decision_action"),
            materialized_item_id=record.get("materialized_item_id"),
        )

    def _source_status(self, refs: tuple[IndexedNoteRef, ...]) -> SourceStatus:
        roots = {source.source_id: source.root for source in self.workspace.list_sources()}
        statuses: set[SourceStatus] = set()
        for ref in refs:
            root = roots.get(ref.source_id)
            if root is None:
                statuses.add("missing")
                continue
            if root.is_symlink() or root.resolve(strict=False) != root:
                statuses.add("unsafe")
                continue
            if not root.is_dir():
                statuses.add("missing")
                continue
            relative = PurePosixPath(ref.relative_path)
            if relative.is_absolute() or ".." in relative.parts:
                statuses.add("unsafe")
                continue
            path = root
            unsafe = False
            for part in relative.parts:
                path = path / part
                if path.is_symlink():
                    unsafe = True
                    break
            if unsafe:
                statuses.add("unsafe")
                continue
            if not path.is_file():
                statuses.add("missing")
                continue
            try:
                current_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError:
                statuses.add("unsafe")
                continue
            statuses.add("current" if current_hash == ref.content_hash else "changed")
        for status in ("unsafe", "missing", "changed", "current"):
            if status in statuses:
                return status  # type: ignore[return-value]
        return "missing"
