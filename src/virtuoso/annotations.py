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
import os
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any

from . import credentials as credential_resolution
from .credentials import CredentialError, HostReader, ResolvedCredential
from .errors import VirtuosoError
from .modules import ModuleError, ModuleManifest, ModuleRunner
from .workspace import LearningItem, WorkspaceError, WorkspaceService

QUESTION_SET_SCHEMA = "virtuoso/annotation-question-set@0.1"
ANNOTATION_SCHEMA = "virtuoso/annotation@0.1"
ANNOTATION_LIST_SCHEMA = "virtuoso/annotation-list@0.1"
ANNOTATOR_DOCTOR_SCHEMA = "virtuoso/annotator-doctor@0.1"
CONSENT_COMMAND = "virtuoso annotate allow-remote --yes"
NOT_CHECKED_REASON = (
    "this workspace has not allowed remote annotators, so doctor read no "
    f"credential source; run `{CONSENT_COMMAND}` to let doctor check env and keychain"
)

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

# Frozen static definitions: workspace-dependent options are supplied per run
# and bound by the subject hash, never substituted into this set's identity.
INTAKE_TRIAGE_QUESTION_SET: dict[str, Any] = {
    "schema": QUESTION_SET_SCHEMA,
    "id": "intake_triage_v1",
    "subject_kind": "candidate",
    "questions": [
        {
            "id": "focus",
            "primitive": "choice",
            "text": "Which supplied workspace focus best fits this candidate?",
            "options": [],
        },
        {
            "id": "entry_mode",
            "primitive": "choice",
            "text": "Is this candidate ready for recall, in need of learning first, or only a wish to park in the backlog?",
            "options": ["recall-ready", "learn-first", "wish-only"],
        },
        {
            "id": "covered_by",
            "primitive": "choice",
            "text": "Which supplied active item already covers this candidate, or none?",
            "options": [],
        },
        {
            "id": "answer_in_prompt",
            "primitive": "noul",
            "text": "Does the candidate prompt give away its answer? Ask only when both prompt and answer are present.",
            "options": [],
        },
    ],
}

_QUESTION_SETS: dict[str, dict[str, Any]] = {
    ITEM_QUALITY_QUESTION_SET["id"]: ITEM_QUALITY_QUESTION_SET,
    INTAKE_TRIAGE_QUESTION_SET["id"]: INTAKE_TRIAGE_QUESTION_SET,
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


def candidate_subject(
    candidate: Mapping[str, Any], *, focus_ids: list[str],
    existing_items: list[dict[str, str]],
) -> dict[str, Any]:
    """Allowlisted candidate text and comparison context; no existing answers."""
    if "none-of-these" in focus_ids or any(item["item_id"] == "none" for item in existing_items):
        raise WorkspaceError(
            "workspace label conflicts with a reserved triage option: "
            "focus none-of-these or active item id none"
        )
    subject = {key: candidate[key] for key in ("title", "kind", "rationale")}
    proposal = candidate["proposal"]
    draft = proposal.get("item", proposal)
    for key in ("prompt", "answer", "hint", "focus"):
        value = draft.get(key)
        if isinstance(value, str) and value:
            subject[key] = value
    subject["focus_options"] = sorted(set(focus_ids)) + ["none-of-these"]
    subject["covered_by_options"] = [
        item["item_id"] for item in existing_items
    ] + ["none"]
    subject["existing_items"] = existing_items
    subject["answer_in_prompt_applicable"] = bool(subject.get("prompt") and subject.get("answer"))
    return subject


def candidate_subject_hash(subject: Mapping[str, Any]) -> str:
    canonical = json.dumps(subject, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _candidate_questions(question_set: QuestionSet, subject: dict[str, Any]) -> QuestionSet:
    """Resolve options/applicability without changing the frozen set hash."""
    questions = []
    for question in question_set.questions:
        question = dict(question)
        question_id = question["id"]
        if question_id == "answer_in_prompt" and not subject["answer_in_prompt_applicable"]:
            continue
        if question_id in {"focus", "covered_by"}:
            question["options"] = list(subject[f"{question_id}_options"])
        questions.append(question)
    return replace(question_set, questions=tuple(questions))


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
        host_credential: HostReader | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        """Run one annotator on one item and record the answers as evidence.

        ``host_credential`` is a reader a host offers for this run only. It is
        called after the consent gate and only when the manifest accepts the
        host source. The resolved value goes to the module environment and
        nowhere else.
        """
        return self._annotate(
            subject_kind="item", subject_id=item_id, module_id=module_id,
            question_set_id=question_set_id, surface=surface,
            host_credential=host_credential, environ=environ,
        )

    def annotate_candidate(
        self,
        *,
        candidate_id: str,
        module_id: str,
        question_set_id: str = INTAKE_TRIAGE_QUESTION_SET["id"],
        surface: str = "cli",
        host_credential: HostReader | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        """Append a triage proposal; never decide or materialize a candidate."""
        return self._annotate(
            subject_kind="candidate", subject_id=candidate_id, module_id=module_id,
            question_set_id=question_set_id, surface=surface,
            host_credential=host_credential, environ=environ,
        )

    def _annotate(
        self, *, subject_kind: str, subject_id: str, module_id: str,
        question_set_id: str, surface: str, host_credential: HostReader | None,
        environ: Mapping[str, str] | None,
    ) -> dict[str, Any]:
        question_set = QuestionSet.bundled(question_set_id)
        if question_set.subject_kind != subject_kind:
            raise AnnotationError(
                f"question set {question_set_id} does not annotate {subject_kind}s"
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
        if host_credential is not None and not manifest.credentials:
            raise AnnotationError(
                f"annotator {module_id} declares no credentials; omit --host-credential-stdin"
            )
        try:
            if subject_kind == "item":
                item = self.workspace.load_item(subject_id)
                subject = item_subject(item)
                subject_hash = item.content_hash
            else:
                subject = self.workspace.candidate_annotation_subject(subject_id)
                subject_hash = candidate_subject_hash(subject)
                question_set = _candidate_questions(question_set, subject)
        except WorkspaceError as exc:
            raise AnnotationError(str(exc)) from exc
        # Resolve after consent and before the module starts. Nothing has been
        # written yet, so a failure here leaves the workspace untouched.
        resolved: tuple[ResolvedCredential, ...] = ()
        try:
            resolved = tuple(
                credential_resolution.resolve(
                    spec,
                    host_reader=host_credential,
                    environ=os.environ if environ is None else environ,
                )
                for spec in manifest.credentials
            )
        except CredentialError as exc:
            raise AnnotationError(f"annotator {module_id}: {exc}") from exc
        secrets = tuple(credential.value for credential in resolved)
        try:
            return self._run_and_record(
                manifest=manifest,
                subject_kind=subject_kind,
                subject_id=subject_id,
                subject_hash=subject_hash,
                subject=subject,
                question_set=question_set,
                surface=surface,
                resolved=resolved,
            )
        except AnnotationError as exc:
            message = credential_resolution.redact(str(exc), secrets)
            raise AnnotationError(message) from None

    def _run_and_record(
        self,
        *,
        manifest: ModuleManifest,
        subject_kind: str,
        subject_id: str,
        subject_hash: str,
        subject: dict[str, Any],
        question_set: QuestionSet,
        surface: str,
        resolved: tuple[ResolvedCredential, ...],
    ) -> dict[str, Any]:
        request = {
            "schema": "virtuoso/module-request@0.1",
            "projections": {
                "annotation.request": {
                    "subject_kind": subject_kind,
                    "subject_id": subject_id,
                    "subject_hash": subject_hash,
                    "question_set_id": question_set.question_set_id,
                    "question_set_hash": question_set.question_set_hash,
                    "questions": list(question_set.questions),
                    "subject": subject,
                }
            },
        }
        module_started_at = datetime.now(timezone.utc)
        try:
            result = ModuleRunner().run(
                manifest,
                request,
                allow_trusted=True,
                credential_environment={
                    credential.name: credential.value for credential in resolved
                },
            )
        except ModuleError as exc:
            raise AnnotationError(str(exc)) from exc
        module_completed_at = datetime.now(timezone.utc)

        self._check_answers(question_set, result.payload["answers"])
        try:
            record = self.workspace.record_annotation(
                subject_kind=subject_kind,
                subject_id=subject_id,
                subject_hash=subject_hash,
                question_set_id=question_set.question_set_id,
                question_set_hash=question_set.question_set_hash,
                manifest=manifest,
                result=result,
                module_started_at=module_started_at,
                module_completed_at=module_completed_at,
                surface=surface,
                credential_source=resolved[0].source if resolved else None,
                credential_provider=resolved[0].provider if resolved else None,
            )
        except WorkspaceError as exc:
            raise AnnotationError(str(exc)) from exc
        record["schema"] = ANNOTATION_SCHEMA
        record["stale"] = False
        record["claims_mastery"] = False
        return record

    def remote_annotators(self) -> dict[str, Any]:
        """Installed remote annotators and the credentials each declares.

        Names and providers only. Reads manifests, never a credential source,
        so consent can be informed before it is given.
        """
        try:
            manifests, errors = self.workspace.installed_annotators()
        except WorkspaceError as exc:
            raise AnnotationError(str(exc)) from exc
        return {
            "remote_annotators": [
                {
                    "module_id": manifest.module_id,
                    "module_version": manifest.version,
                    "credentials": [
                        {
                            "name": spec.name,
                            "provider": spec.provider,
                            "sources": list(spec.sources),
                        }
                        for spec in manifest.credentials
                    ],
                }
                for manifest in manifests
                if manifest.network == "remote"
            ],
            "errors": errors,
        }

    def doctor(self, *, environ: Mapping[str, str] | None = None) -> dict[str, Any]:
        """Per installed remote annotator, which credential sources resolve.

        Without the allow-remote consent it reads no source at all: env and
        keychain are reported ``not_checked`` with a reason naming the consent
        command, because a keychain read can prompt the learner. With consent
        it reads env and keychain values to test them and discards them. Never
        runs a module and never writes. The host source is offered per run,
        so it reports ``per-run``.
        """
        allow_remote = bool(self.workspace.annotator_settings()["allow_remote"])
        try:
            manifests, errors = self.workspace.installed_annotators()
        except WorkspaceError as exc:
            raise AnnotationError(str(exc)) from exc
        annotators = []
        for manifest in manifests:
            if manifest.network != "remote":
                continue
            if allow_remote:
                environment = os.environ if environ is None else environ
                reports = [
                    credential_resolution.probe(spec, environ=environment)
                    for spec in manifest.credentials
                ]
            else:
                reports = [
                    credential_resolution.not_checked(spec, reason=NOT_CHECKED_REASON)
                    for spec in manifest.credentials
                ]
            annotators.append(
                {
                    "module_id": manifest.module_id,
                    "module_version": manifest.version,
                    "network": manifest.network,
                    "credentials": reports,
                    "ready": allow_remote
                    and all(report["local_source"] is not None for report in reports),
                }
            )
        return {
            "schema": ANNOTATOR_DOCTOR_SCHEMA,
            "allow_remote": allow_remote,
            "keychain_tool": credential_resolution.keychain_tool(),
            "keychain_service": credential_resolution.KEYCHAIN_SERVICE,
            "precedence": list(credential_resolution.CREDENTIAL_SOURCES),
            "annotators": annotators,
            "errors": errors,
            "values_shown": False,
        }

    def latest_candidate_proposal(self, candidate_id: str) -> dict[str, Any] | None:
        proposals = [
            annotation for annotation in self.list(candidate_id=candidate_id)["annotations"]
            if annotation["question_set_id"] == INTAKE_TRIAGE_QUESTION_SET["id"]
        ]
        if not proposals:
            return None
        return {**proposals[-1], "authority": "model-proposal"}

    def list(
        self, *, item_id: str | None = None, candidate_id: str | None = None,
    ) -> dict[str, Any]:
        if item_id is not None and candidate_id is not None:
            raise AnnotationError("choose either an item or a candidate filter")
        kind = "item" if item_id is not None else "candidate" if candidate_id is not None else None
        try:
            annotations = self.workspace.list_annotations(
                subject_kind=kind,
                subject_id=item_id if item_id is not None else candidate_id,
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
        # Messages name only question ids and options from the bundled set.
        # A module-supplied value may hold the credential in an escaped form
        # that exact-match redaction cannot see, so it is never echoed.
        expected = question_set.question_ids()
        received = set(answers)
        if received != expected:
            missing = ", ".join(sorted(expected - received)) or "none"
            extra = len(received - expected)
            raise AnnotationError(
                "annotator answers do not match the question set; "
                f"missing: {missing}; unexpected answers: {extra}"
            )
        for question_id in sorted(expected):
            answer = answers[question_id]
            primitive = question_set.primitive_for(question_id)
            if answer["primitive"] != primitive:
                raise AnnotationError(
                    f"answer {question_id} used the wrong primitive; "
                    f"the question asks for {primitive}"
                )
            if primitive == "choice":
                options = question_set.options_for(question_id)
                if answer["value"] not in options:
                    raise AnnotationError(
                        f"answer {question_id} chose a value outside the allowed "
                        f"options: {', '.join(options)}"
                    )
