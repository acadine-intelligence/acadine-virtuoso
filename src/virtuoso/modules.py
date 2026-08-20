from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


MANIFEST_SCHEMA = "virtuoso/module@0.1"
REQUEST_SCHEMA = "virtuoso/module-request@0.1"
RESULT_SCHEMA = "virtuoso/module-result@0.1"
_ALLOWED_CATEGORIES = {
    "scheduler": "scheduler-proposal",
    "practice-format": "practice-proposal",
    "source-adapter": "source-projection",
    "scoring-signal": "score-proposal",
    "output-adapter": "output-receipt",
}
_ALLOWED_READS = {
    "challenge.summary",
    "attempt.summary",
    "focus.summary",
    "scheduler.summary",
    "project.summary",
}
_PRIVATE_STATE_KEYS = {
    "database_path",
    "db_path",
    "state_path",
    "sqlite_path",
    "credentials",
    "secrets",
}
_MODULE_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?$")


class ModuleError(RuntimeError):
    """A module was rejected before it could affect Virtuoso state."""


@dataclass(frozen=True)
class ModuleManifest:
    path: Path
    module_id: str
    version: str
    category: str
    argv: tuple[str, ...]
    timeout_seconds: float
    reads: tuple[str, ...]
    returns: str

    @classmethod
    def load(cls, path: Path | str) -> "ModuleManifest":
        manifest_path = Path(path).resolve()
        try:
            value = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ModuleError(f"module manifest is not valid JSON: {exc}") from exc
        if not isinstance(value, dict):
            raise ModuleError("module manifest must be a JSON object")
        if value.get("schema") != MANIFEST_SCHEMA:
            raise ModuleError(
                f"unsupported module schema: {value.get('schema')!r}; expected {MANIFEST_SCHEMA}"
            )

        module_id = value.get("id")
        version = value.get("version")
        category = value.get("category")
        if not isinstance(module_id, str) or not _MODULE_ID.fullmatch(module_id):
            raise ModuleError("module id must be lowercase dash-separated words")
        if not isinstance(version, str) or not _SEMVER.fullmatch(version):
            raise ModuleError("module version must be semantic version x.y.z")
        if category not in _ALLOWED_CATEGORIES:
            raise ModuleError(f"unsupported module category: {category!r}")

        command = value.get("command")
        if not isinstance(command, dict):
            raise ModuleError("module command must declare an argv object")
        argv = command.get("argv")
        if (
            not isinstance(argv, list)
            or not argv
            or any(not isinstance(arg, str) or not arg for arg in argv)
        ):
            raise ModuleError("module command must use a non-empty argv string array")
        timeout = command.get("timeout_seconds", 10)
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool):
            raise ModuleError("module timeout_seconds must be numeric")
        if not 0 < float(timeout) <= 60:
            raise ModuleError("module timeout_seconds must be greater than 0 and at most 60")

        capabilities = value.get("capabilities")
        if not isinstance(capabilities, dict):
            raise ModuleError("module capabilities must be an object")
        writes = capabilities.get("writes", [])
        if writes:
            raise ModuleError("external modules must not declare writes to core state")
        reads = capabilities.get("reads", [])
        if not isinstance(reads, list) or any(not isinstance(item, str) for item in reads):
            raise ModuleError("module capabilities.reads must be a string array")
        unknown_reads = sorted(set(reads) - _ALLOWED_READS)
        if unknown_reads:
            raise ModuleError(
                "module requests undeclared read projections: " + ", ".join(unknown_reads)
            )
        returns = capabilities.get("returns")
        expected_return = _ALLOWED_CATEGORIES[str(category)]
        if returns != expected_return:
            raise ModuleError(
                f"module category {category} must return {expected_return!r}"
            )

        return cls(
            path=manifest_path,
            module_id=module_id,
            version=version,
            category=str(category),
            argv=tuple(argv),
            timeout_seconds=float(timeout),
            reads=tuple(reads),
            returns=str(returns),
        )


@dataclass(frozen=True)
class ModuleRunResult:
    module_id: str
    module_version: str
    kind: str
    payload: dict[str, Any]
    duration_ms: int
    stdout_sha256: str


class ModuleRunner:
    def __init__(
        self,
        *,
        max_input_bytes: int = 65_536,
        max_output_bytes: int = 65_536,
    ) -> None:
        self.max_input_bytes = max_input_bytes
        self.max_output_bytes = max_output_bytes

    def run(
        self, manifest: ModuleManifest, request: dict[str, Any]
    ) -> ModuleRunResult:
        if request.get("schema") != REQUEST_SCHEMA:
            raise ModuleError(
                f"unsupported module request schema: {request.get('schema')!r}"
            )
        projections = request.get("projections")
        if not isinstance(projections, dict):
            raise ModuleError("module request projections must be a JSON object")
        undeclared = sorted(set(projections) - set(manifest.reads))
        if undeclared:
            raise ModuleError(
                "module request includes undeclared projection: "
                + ", ".join(undeclared)
            )
        if self._contains_private_state_path(request):
            raise ModuleError("module request contains a private state path or secret")
        encoded = (json.dumps(request, sort_keys=True) + "\n").encode("utf-8")
        if len(encoded) > self.max_input_bytes:
            raise ModuleError("module request exceeds input limit")

        environment = {
            "PATH": os.environ.get("PATH", ""),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PYTHONIOENCODING": "utf-8",
        }
        started = time.monotonic()
        try:
            completed = subprocess.run(
                list(manifest.argv),
                cwd=manifest.path.parent,
                input=encoded,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=manifest.timeout_seconds,
                check=False,
                shell=False,
                env=environment,
            )
        except subprocess.TimeoutExpired as exc:
            raise ModuleError(
                f"module timed out after {manifest.timeout_seconds:g} seconds"
            ) from exc
        except OSError as exc:
            raise ModuleError(f"module process could not start: {exc}") from exc
        duration_ms = max(0, round((time.monotonic() - started) * 1000))

        if len(completed.stdout) > self.max_output_bytes:
            raise ModuleError("module exceeded output limit")
        if len(completed.stderr) > self.max_output_bytes:
            raise ModuleError("module exceeded error output limit")
        if completed.returncode != 0:
            raise ModuleError(f"module exited with status {completed.returncode}")
        try:
            decoded = completed.stdout.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ModuleError("module output is not UTF-8") from exc
        try:
            response = json.loads(decoded)
        except json.JSONDecodeError as exc:
            raise ModuleError("module output is not valid JSON") from exc
        if not isinstance(response, dict):
            raise ModuleError("module output must be a JSON object")
        if response.get("schema") != RESULT_SCHEMA:
            raise ModuleError("module result schema is incompatible")
        if response.get("module_id") != manifest.module_id:
            raise ModuleError("module result id does not match its manifest")
        if response.get("kind") != manifest.returns:
            raise ModuleError("module result kind does not match its declared capability")
        payload = response.get("payload")
        if not isinstance(payload, dict):
            raise ModuleError("module result payload must be a JSON object")

        return ModuleRunResult(
            module_id=manifest.module_id,
            module_version=manifest.version,
            kind=manifest.returns,
            payload=payload,
            duration_ms=duration_ms,
            stdout_sha256=hashlib.sha256(completed.stdout).hexdigest(),
        )

    @classmethod
    def _contains_private_state_path(cls, value: object) -> bool:
        if isinstance(value, dict):
            for key, child in value.items():
                if str(key).lower() in _PRIVATE_STATE_KEYS:
                    return True
                if cls._contains_private_state_path(child):
                    return True
        elif isinstance(value, list):
            return any(cls._contains_private_state_path(child) for child in value)
        return False
