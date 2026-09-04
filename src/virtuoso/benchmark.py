"""Benchmark-directed focus: BenchmarkRun, OperatingLevelMap, reruns, export."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .errors import VirtuosoError

RUN_SCHEMA = "virtuoso/benchmark-run@0.1"
RERUN_SCHEMA = "virtuoso/benchmark-rerun@0.1"

# Versioned operating-level map. Unknown levels fail the import.
OPERATING_LEVEL_MAP: dict[str, tuple[str, ...]] = {
    "opmap@1": ("planning", "execution", "verification", "communication"),
}

_RUN_FIELDS = {
    "schema",
    "run_id",
    "source_reference",
    "tested_commit",
    "harness",
    "harness_version",
    "model_id",
    "prompt_hash",
    "tool_permissions",
    "environment",
    "operating_level_map_version",
    "occurred_at",
    "observations",
}
_OBSERVATION_FIELDS = {"criterion", "level", "status", "metric", "value"}
_STATUSES = {"pass", "fail"}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SHA1 = re.compile(r"^[0-9a-f]{40}$")
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

# Comparability fields that must match between a baseline and a rerun.
_COMPARABILITY_FIELDS = (
    "tested_commit",
    "harness",
    "harness_version",
    "model_id",
    "prompt_hash",
    "tool_permissions",
    "environment",
)

_PRIVATE_PATH = re.compile(
    r"(^|[\s=])("
    r"/(?:Users|home)/[^/\s]+"
    r"|~/"
    r"|/private/tmp/"
    r"|[A-Za-z]:[\\/]+Users[\\/]+"
    r")"
)


class BenchmarkError(VirtuosoError):
    """Benchmark evidence cannot be trusted as imported or compared."""


@dataclass(frozen=True)
class BenchmarkRun:
    run_id: str
    source_reference: str
    source_hash: str
    payload: dict[str, Any]
    observations: tuple[dict[str, Any], ...]
    occurred_at: str


class BenchmarkService:
    def __init__(self, workspace: Any) -> None:
        self.workspace = workspace

    # ------------------------------------------------------------------
    # Import
    # ------------------------------------------------------------------

    def import_run(
        self,
        artifact_path: Path,
        *,
        source_reference: str | None = None,
        _allow_reference_reuse: bool = False,
    ) -> BenchmarkRun:
        raw = self._read_json(artifact_path)
        run = self._validate(raw)
        reference = (
            source_reference.strip()
            if source_reference is not None
            else run["source_reference"]
        )
        if not reference:
            raise BenchmarkError("source_reference must be non-empty")
        if _PRIVATE_PATH.search(reference) or reference.startswith(("/", "~", ".")):
            raise BenchmarkError(
                "source_reference must not be a local filesystem path"
            )
        run["source_reference"] = reference
        run_id = run["run_id"]

        source_hash = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
        with self.workspace._connect() as db:
            duplicate = db.execute(
                "SELECT run_id FROM benchmark_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if duplicate is not None:
                raise BenchmarkError(f"benchmark run already imported: {run_id}")
            stale = db.execute(
                """SELECT run_id FROM benchmark_runs
                   WHERE source_reference = ? AND source_hash != ?""",
                (reference, source_hash),
            ).fetchone()
            if stale is not None and not _allow_reference_reuse:
                raise BenchmarkError(
                    f"source content changed for reference '{reference}' "
                    f"(existing run {stale['run_id']}); "
                    "use a distinct source_reference"
                )
            try:
                db.execute("BEGIN IMMEDIATE")
                db.execute(
                    """INSERT INTO benchmark_runs(
                           run_id, source_reference, source_hash, payload_json,
                           occurred_at
                       ) VALUES (?, ?, ?, ?, ?)""",
                    (
                        run_id,
                        reference,
                        source_hash,
                        json.dumps(
                            run, sort_keys=True, separators=(",", ":"),
                            ensure_ascii=True,
                        ),
                        run["occurred_at"],
                    ),
                )
                for observation in run["observations"]:
                    db.execute(
                        """INSERT INTO benchmark_observations(
                               run_id, criterion, level, status, metric, value
                           ) VALUES (?, ?, ?, ?, ?, ?)""",
                        (
                            run_id,
                            observation["criterion"],
                            observation["level"],
                            observation["status"],
                            observation["metric"],
                            observation["value"],
                        ),
                    )
                db.execute("COMMIT")
            except sqlite3.Error as exc:
                db.execute("ROLLBACK")
                raise BenchmarkError(
                    f"benchmark run could not be recorded: {exc}"
                ) from exc

        return BenchmarkRun(
            run_id=run_id,
            source_reference=reference,
            source_hash=source_hash,
            payload=run,
            observations=tuple(run["observations"]),
            occurred_at=run["occurred_at"],
        )

    # ------------------------------------------------------------------
    # Reruns
    # ------------------------------------------------------------------

    def import_rerun(
        self, artifact_path: Path, *, baseline_run_id: str
    ) -> dict[str, Any]:
        baseline = self.get_run(baseline_run_id)
        run = self.import_run(
            artifact_path,
            source_reference=baseline.source_reference,
            _allow_reference_reuse=True,
        )
        if run.run_id == baseline_run_id:
            raise BenchmarkError("rerun run_id must differ from the baseline id")

        baseline_payload = baseline.payload
        warnings: list[str] = []
        for field in _COMPARABILITY_FIELDS:
            if baseline_payload.get(field) != run.payload.get(field):
                warnings.append(
                    f"comparability: {field} changed "
                    f"({baseline_payload.get(field)!r} -> {run.payload.get(field)!r})"
                )

        baseline_by_pair = {
            (observation["criterion"], observation["metric"]): observation
            for observation in baseline.observations
        }
        rerun_by_pair = {
            (observation["criterion"], observation["metric"]): observation
            for observation in run.observations
        }
        changes: list[dict[str, Any]] = []
        pairs = sorted(
            set(baseline_by_pair) | set(rerun_by_pair), key=lambda pair: pair[0]
        )
        for criterion, metric in pairs:
            base = baseline_by_pair.get((criterion, metric))
            rerun = rerun_by_pair.get((criterion, metric))
            if base is None or rerun is None:
                missing_side = "baseline" if base is None else "rerun"
                changes.append(
                    {
                        "criterion": criterion,
                        "metric": metric,
                        "comparison": "metric-missing",
                        "detail": f"no matching observation in {missing_side}",
                    }
                )
                continue
            delta = round(rerun["value"] - base["value"], 10)
            changes.append(
                {
                    "criterion": criterion,
                    "metric": metric,
                    "comparison": "measured",
                    "baseline_value": base["value"],
                    "rerun_value": rerun["value"],
                    "delta": delta,
                    "baseline_status": base["status"],
                    "rerun_status": rerun["status"],
                }
            )

        with self.workspace._connect() as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                db.execute(
                    """INSERT INTO benchmark_reruns(
                           run_id, baseline_run_id, warnings_json, changes_json
                       ) VALUES (?, ?, ?, ?)""",
                    (
                        run.run_id,
                        baseline_run_id,
                        json.dumps(warnings, sort_keys=True),
                        json.dumps(changes, sort_keys=True),
                    ),
                )
                db.execute("COMMIT")
            except sqlite3.Error as exc:
                db.execute("ROLLBACK")
                raise BenchmarkError(
                    f"rerun link could not be recorded: {exc}"
                ) from exc

        return {
            "schema": RERUN_SCHEMA,
            "run_id": run.run_id,
            "baseline_run_id": baseline_run_id,
            "warnings": warnings,
            "changes": changes,
            "claims_mastery": False,
        }

    # ------------------------------------------------------------------
    # Proposal inputs
    # ------------------------------------------------------------------

    def next_failed_observation(self) -> dict[str, Any] | None:
        """Earliest unproposed failed observation, deterministic order."""
        with self.workspace._connect() as db:
            row = db.execute(
                """SELECT o.run_id, o.criterion, o.level, o.status, o.metric,
                          o.value, r.occurred_at
                   FROM benchmark_observations AS o
                   JOIN benchmark_runs AS r ON r.run_id = o.run_id
                   WHERE o.status = 'fail'
                     AND NOT EXISTS (
                         SELECT 1 FROM benchmark_proposals AS bp
                         WHERE bp.run_id = o.run_id
                           AND bp.criterion = o.criterion
                     )
                   ORDER BY r.occurred_at ASC, o.criterion ASC, o.run_id ASC
                   LIMIT 1"""
            ).fetchone()
        if row is None:
            return None
        return dict(row)

    def record_proposal(self, *, run_id: str, criterion: str) -> None:
        with self.workspace._connect() as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                db.execute(
                    """INSERT INTO benchmark_proposals(run_id, criterion)
                       VALUES (?, ?)""",
                    (run_id, criterion),
                )
                db.execute("COMMIT")
            except sqlite3.IntegrityError as exc:
                db.execute("ROLLBACK")
                raise BenchmarkError(
                    f"observation already proposed: {run_id}/{criterion}"
                ) from exc
            except sqlite3.Error as exc:
                db.execute("ROLLBACK")
                raise BenchmarkError(
                    f"proposal link could not be recorded: {exc}"
                ) from exc

    def get_run(self, run_id: str) -> BenchmarkRun:
        with self.workspace._connect() as db:
            row = db.execute(
                """SELECT run_id, source_reference, source_hash, payload_json,
                          occurred_at
                   FROM benchmark_runs WHERE run_id = ?""",
                (run_id,),
            ).fetchone()
        if row is None:
            raise BenchmarkError(f"no benchmark run with id: {run_id}")
        payload = json.loads(row["payload_json"])
        return BenchmarkRun(
            run_id=row["run_id"],
            source_reference=row["source_reference"],
            source_hash=row["source_hash"],
            payload=payload,
            observations=tuple(payload["observations"]),
            occurred_at=row["occurred_at"],
        )

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def export(self, run_id: str) -> dict[str, Any]:
        run = self.get_run(run_id)
        with self.workspace._connect() as db:
            rerun_row = db.execute(
                "SELECT baseline_run_id FROM benchmark_reruns WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        exported = {
            "schema": RUN_SCHEMA,
            "run_id": run.run_id,
            "source_reference": run.source_reference,
            "source_hash": run.source_hash,
            "tested_commit": run.payload["tested_commit"],
            "harness": run.payload["harness"],
            "harness_version": run.payload["harness_version"],
            "model_id": run.payload["model_id"],
            "prompt_hash": run.payload["prompt_hash"],
            "tool_permissions": list(run.payload["tool_permissions"]),
            "environment": run.payload["environment"],
            "operating_level_map_version": run.payload[
                "operating_level_map_version"
            ],
            "occurred_at": run.occurred_at,
            "observations": [
                {field: observation[field] for field in sorted(_OBSERVATION_FIELDS)}
                for observation in run.observations
            ],
            "redacted": True,
        }
        if rerun_row is not None:
            exported["baseline_run_id"] = rerun_row["baseline_run_id"]
        return exported

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _read_json(self, artifact_path: Path) -> Any:
        try:
            raw = json.loads(artifact_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise BenchmarkError(
                f"benchmark artifact must be valid JSON: {exc}"
            ) from exc
        if not isinstance(raw, dict):
            raise BenchmarkError("benchmark artifact must be a JSON object")
        return raw

    def _validate(self, raw: dict[str, Any]) -> dict[str, Any]:
        if raw.get("schema") != RUN_SCHEMA:
            raise BenchmarkError(f"benchmark schema must be {RUN_SCHEMA}")
        missing = _RUN_FIELDS - set(raw)
        unknown = set(raw) - _RUN_FIELDS
        if missing:
            raise BenchmarkError(f"benchmark artifact missing fields: {sorted(missing)}")
        if unknown:
            raise BenchmarkError(f"benchmark artifact has unknown fields: {sorted(unknown)}")
        run_id = raw["run_id"]
        if not isinstance(run_id, str) or _RUN_ID.fullmatch(run_id) is None:
            raise BenchmarkError("run_id must match [A-Za-z0-9][A-Za-z0-9._-]{0,63}")
        for field in (
            "source_reference",
            "tested_commit",
            "harness",
            "harness_version",
            "model_id",
            "prompt_hash",
            "environment",
            "operating_level_map_version",
        ):
            value = raw[field]
            if not isinstance(value, str) or not value.strip():
                raise BenchmarkError(f"{field} must be a non-empty string")
        if _SHA1.fullmatch(raw["tested_commit"]) is None:
            raise BenchmarkError("tested_commit must be a 40-character sha1")
        if _SHA256.fullmatch(raw["prompt_hash"]) is None:
            raise BenchmarkError("prompt_hash must be a 64-character sha256")
        if raw["operating_level_map_version"] not in OPERATING_LEVEL_MAP:
            raise BenchmarkError(
                "unknown operating level map: "
                f"{raw['operating_level_map_version']}"
            )
        permissions = raw["tool_permissions"]
        if (
            not isinstance(permissions, list)
            or not permissions
            or not all(
                isinstance(entry, str) and entry.strip() for entry in permissions
            )
        ):
            raise BenchmarkError("tool_permissions must be a list of non-empty strings")
        occurred_at = raw["occurred_at"]
        try:
            parsed = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
        except (AttributeError, ValueError) as exc:
            raise BenchmarkError("occurred_at must be a valid timestamp") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise BenchmarkError("occurred_at must include a timezone")
        raw["occurred_at"] = parsed.astimezone(timezone.utc).isoformat()

        observations = raw["observations"]
        if not isinstance(observations, list) or not observations:
            raise BenchmarkError("observations must be a non-empty list")
        levels = OPERATING_LEVEL_MAP[raw["operating_level_map_version"]]
        seen: set[str] = set()
        for observation in observations:
            if not isinstance(observation, dict):
                raise BenchmarkError("each observation must be an object")
            missing_obs = _OBSERVATION_FIELDS - set(observation)
            unknown_obs = set(observation) - _OBSERVATION_FIELDS
            if missing_obs:
                raise BenchmarkError(
                    f"observation missing fields: {sorted(missing_obs)}"
                )
            if unknown_obs:
                raise BenchmarkError(
                    f"observation has unknown fields: {sorted(unknown_obs)}"
                )
            criterion = observation["criterion"]
            if not isinstance(criterion, str) or not criterion.strip():
                raise BenchmarkError("criterion must be a non-empty string")
            if criterion in seen:
                raise BenchmarkError(f"duplicate criterion: {criterion}")
            seen.add(criterion)
            if observation["level"] not in levels:
                raise BenchmarkError(
                    f"unknown operating level: {observation['level']!r} "
                    f"for map {raw['operating_level_map_version']}"
                )
            if observation["status"] not in _STATUSES:
                raise BenchmarkError(
                    "status must be pass or fail, got: "
                    f"{observation['status']!r}"
                )
            if not isinstance(observation["metric"], str) or not observation["metric"].strip():
                raise BenchmarkError("metric must be a non-empty string")
            value = observation["value"]
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or value != value
            ):
                raise BenchmarkError("value must be a finite number")
            observation["value"] = float(value)
        return raw
