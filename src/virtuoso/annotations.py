"""Calibrated decision annotations: typed judgments recorded beside evidence.

An annotator module answers a fixed question set about one subject. Virtuoso
records the answers as append-only evidence with the module receipt, the
subject hash, and the question set hash. Nothing in selection, composition,
or scheduling reads this table. The learner or an agent reads it through
``virtuoso annotate list`` and decides what to do.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .errors import VirtuosoError
from .modules import ModuleError, ModuleRunner
from .workspace import LearningItem, WorkspaceError, WorkspaceService

QUESTION_SET_SCHEMA = "virtuoso/annotation-question-set@0.1"
ANNOTATION_SCHEMA = "virtuoso/annotation@0.1"
ANNOTATION_LIST_SCHEMA = "virtuoso/annotation-list@0.1"

# The bundled question set. The hash of its canonical JSON travels with every
# annotation, so a later wording change never masquerades as the same question.
ITEM_QUALITY_QUESTION_SET: dict[str, Any] = {
    "schema": QUESTION_SET_SCHEMA,
    "id": "item_quality_v1",
    "subject_kind": "item",
    "questions": [
        {
            "id": "atomic",
            "primitive": "noul",
            "text": "Does the prompt test exactly one idea?",
            "options": [],
        },
        {
            "id": "answer_supports_prompt",
            "primitive": "noul",
            "text": "Does the answer fully resolve the prompt without adding new claims?",
            "options": [],
        },
        {
            "id": "difficulty",
            "primitive": "choice",
            "text": "How hard is this item for a learner who has met the focus once?",
            "options": ["easy", "moderate", "hard"],
        },
        {
            "id": "hint_leaks_answer",
            "primitive": "noul",
            "text": "Does the hint give away the answer?",
            "options": [],
        },
    ],
}

_QUESTION_SETS: dict[str, dict[str, Any]] = {
    ITEM_QUALITY_QUESTION_SET["id"]: ITEM_QUALITY_QUESTION_SET,
}


class AnnotationError(VirtuosoError):
    """Raised when an annotation run is refused or produces unusable output."""


@dataclass(frozen=True)
class QuestionSet:
    question_set_id: str
    subject_kind: str
    questions: tuple[dict[str, Any], ...]
    question_set_hash: str

    @classmethod
    def bundled(cls, question_set_id: str) -> "QuestionSet":
        try:
            raw = _QUESTION_SETS[question_set_id]
        except KeyError as exc:
            known = ", ".join(sorted(_QUESTION_SETS))
            raise AnnotationError(
                f"unknown question set: {question_set_id}; bundled sets: {known}"
            ) from exc
        canonical = json.dumps(raw, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return cls(
            question_set_id=raw["id"],
            subject_kind=raw["subject_kind"],
            questions=tuple(dict(question) for question in raw["questions"]),
            question_set_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        )

    def question_ids(self) -> set[str]:
        return {question["id"] for question in self.questions}

    def primitive_for(self, question_id: str) -> str:
        for question in self.questions:
            if question["id"] == question_id:
                return str(question["primitive"])
        raise KeyError(question_id)

    def options_for(self, question_id: str) -> list[str]:
        for question in self.questions:
            if question["id"] == question_id:
                return list(question["options"])
        raise KeyError(question_id)


def list_question_sets() -> list[dict[str, Any]]:
    """Bundled question sets with their hashes, for the CLI and agents."""
    result = []
    for question_set_id in sorted(_QUESTION_SETS):
        question_set = QuestionSet.bundled(question_set_id)
        result.append(
            {
                "id": question_set.question_set_id,
                "subject_kind": question_set.subject_kind,
                "question_set_hash": question_set.question_set_hash,
                "questions": list(question_set.questions),
            }
        )
    return result


def item_subject(item: LearningItem) -> dict[str, Any]:
    """The projection an annotator receives. No evidence, no history, no paths."""
    subject: dict[str, Any] = {
        "title": item.title,
        "focus": item.focus,
        "prompt": item.prompt,
        "answer": item.answer,
        "learning_context": item.learning_context,
        "entry_mode": item.entry_mode,
    }
    if item.hint is not None:
        subject["hint"] = item.hint
    if item.follow_up is not None:
        subject["follow_up"] = item.follow_up
    return subject


class AnnotationService:
    def __init__(self, workspace: WorkspaceService) -> None:
        self.workspace = workspace

    def annotate_item(
        self,
        *,
        item_id: str,
        module_id: str,
        question_set_id: str = ITEM_QUALITY_QUESTION_SET["id"],
        surface: str = "cli",
    ) -> dict[str, Any]:
        """Run one annotator on one item and record the answers as evidence."""
        question_set = QuestionSet.bundled(question_set_id)
        if question_set.subject_kind != "item":
            raise AnnotationError(
                f"question set {question_set_id} does not annotate items"
            )
        try:
            manifest = self.workspace.annotator_module_manifest(module_id)
        except WorkspaceError as exc:
            raise AnnotationError(str(exc)) from exc
        consent = self.workspace.annotator_settings()
        if manifest.network == "remote" and not consent["allow_remote"]:
            raise AnnotationError(
                f"annotator {module_id} declares network: remote; this workspace has not "
                "allowed remote annotators. Run `virtuoso annotate allow-remote --yes` "
                "after reading what the module sends, or use a local module."
            )
        try:
            item = self.workspace.load_item(item_id)
        except WorkspaceError as exc:
            raise AnnotationError(str(exc)) from exc

        request = {
            "schema": "virtuoso/module-request@0.1",
            "projections": {
                "annotation.request": {
                    "subject_kind": "item",
                    "subject_id": item.item_id,
                    "subject_hash": item.content_hash,
                    "question_set_id": question_set.question_set_id,
                    "question_set_hash": question_set.question_set_hash,
                    "questions": list(question_set.questions),
                    "subject": item_subject(item),
                }
            },
        }
        module_started_at = datetime.now(timezone.utc)
        try:
            result = ModuleRunner().run(manifest, request, allow_trusted=True)
        except ModuleError as exc:
            raise AnnotationError(str(exc)) from exc
        module_completed_at = datetime.now(timezone.utc)

        self._check_answers(question_set, result.payload["answers"])
        try:
            record = self.workspace.record_annotation(
                subject_kind="item",
                subject_id=item.item_id,
                subject_hash=item.content_hash,
                question_set_id=question_set.question_set_id,
                question_set_hash=question_set.question_set_hash,
                manifest=manifest,
                result=result,
                module_started_at=module_started_at,
                module_completed_at=module_completed_at,
                surface=surface,
            )
        except WorkspaceError as exc:
            raise AnnotationError(str(exc)) from exc
        record["schema"] = ANNOTATION_SCHEMA
        record["stale"] = False
        record["claims_mastery"] = False
        return record

    def list(self, *, item_id: str | None = None) -> dict[str, Any]:
        try:
            annotations = self.workspace.list_annotations(
                subject_kind="item" if item_id is not None else None,
                subject_id=item_id,
            )
        except WorkspaceError as exc:
            raise AnnotationError(str(exc)) from exc
        return {
            "schema": ANNOTATION_LIST_SCHEMA,
            "annotations": annotations,
            "claims_mastery": False,
        }

    @staticmethod
    def _check_answers(question_set: QuestionSet, answers: dict[str, Any]) -> None:
        """Answers must cover the question set exactly and match each primitive."""
        expected = question_set.question_ids()
        received = set(answers)
        if received != expected:
            missing = ", ".join(sorted(expected - received)) or "none"
            extra = ", ".join(sorted(received - expected)) or "none"
            raise AnnotationError(
                "annotator answers do not match the question set; "
                f"missing: {missing}; unexpected: {extra}"
            )
        for question_id, answer in answers.items():
            primitive = question_set.primitive_for(question_id)
            if answer["primitive"] != primitive:
                raise AnnotationError(
                    f"answer {question_id} used primitive {answer['primitive']}; "
                    f"the question asks for {primitive}"
                )
            if primitive == "choice":
                options = question_set.options_for(question_id)
                if answer["value"] not in options:
                    raise AnnotationError(
                        f"answer {question_id} chose {answer['value']!r}; "
                        f"allowed: {', '.join(options)}"
                    )
