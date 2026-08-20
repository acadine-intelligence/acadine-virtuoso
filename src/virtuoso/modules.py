from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import subprocess
import tempfile
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
    """A module invocation failed before Virtuoso accepted its proposal."""


def _require_exact_keys(value: dict[str, Any], expected: set[str], label: str) -> None:
    unknown = sorted(set(value) - expected)
    missing = sorted(expected - set(value))
    if unknown:
        raise ModuleError(f"unknown {label} fields: " + ", ".join(unknown))
    if missing:
        raise ModuleError(f"missing {label} fields: " + ", ".join(missing))


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
    trust: str
    manifest_sha256: str

    @classmethod
    def load(cls, path: Path | str) -> "ModuleManifest":
        manifest_path = Path(path).resolve()
        try:
            raw = manifest_path.read_bytes()
            value = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ModuleError(f"module manifest is not valid JSON: {exc}") from exc
        if not isinstance(value, dict):
            raise ModuleError("module manifest must be a JSON object")
        if value.get("schema") != MANIFEST_SCHEMA:
            raise ModuleError(
                f"unsupported module schema: {value.get('schema')!r}; expected {MANIFEST_SCHEMA}"
            )
        _require_exact_keys(
            value,
            {"schema", "id", "version", "category", "command", "capabilities", "trust"},
            "manifest",
        )

        module_id = value["id"]
        version = value["version"]
        category = value["category"]
        trust = value["trust"]
        if not isinstance(module_id, str) or not _MODULE_ID.fullmatch(module_id):
            raise ModuleError("module id must be lowercase dash-separated words")
        if not isinstance(version, str) or not _SEMVER.fullmatch(version):
            raise ModuleError("module version must be semantic version x.y.z")
        if category not in _ALLOWED_CATEGORIES:
            raise ModuleError(f"unsupported module category: {category!r}")
        if trust != "local-executable":
            raise ModuleError("module trust must be 'local-executable'")

        command = value["command"]
        if not isinstance(command, dict):
            raise ModuleError("module command must declare an argv object")
        _require_exact_keys(command, {"argv", "timeout_seconds"}, "command")
        argv = command["argv"]
        if (
            not isinstance(argv, list)
            or not argv
            or any(not isinstance(arg, str) or not arg for arg in argv)
        ):
            raise ModuleError("module command must use a non-empty argv string array")
        timeout = command["timeout_seconds"]
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool):
            raise ModuleError("module timeout_seconds must be numeric")
        if not 0 < float(timeout) <= 60:
            raise ModuleError("module timeout_seconds must be greater than 0 and at most 60")

        capabilities = value["capabilities"]
        if not isinstance(capabilities, dict):
            raise ModuleError("module capabilities must be an object")
        _require_exact_keys(capabilities, {"reads", "returns"}, "capability")
        reads = capabilities["reads"]
        if not isinstance(reads, list) or any(not isinstance(item, str) for item in reads):
            raise ModuleError("module capabilities.reads must be a string array")
        unknown_reads = sorted(set(reads) - _ALLOWED_READS)
        if unknown_reads:
            raise ModuleError(
                "module requests undeclared read projections: " + ", ".join(unknown_reads)
            )
        returns = capabilities["returns"]
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
            trust=str(trust),
            manifest_sha256=hashlib.sha256(raw).hexdigest(),
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
        self,
        manifest: ModuleManifest,
        request: dict[str, Any],
        *,
        allow_trusted: bool = False,
    ) -> ModuleRunResult:
        if not allow_trusted:
            raise ModuleError(
                "module is a trusted local executable; explicit allow_trusted=True is required"
            )
        if not isinstance(request, dict):
            raise ModuleError("module request must be a JSON object")
        _require_exact_keys(request, {"schema", "projections"}, "request")
        if request["schema"] != REQUEST_SCHEMA:
            raise ModuleError(
                f"unsupported module request schema: {request['schema']!r}"
            )
        projections = request["projections"]
        if not isinstance(projections, dict):
            raise ModuleError("module request projections must be a JSON object")
        undeclared = sorted(set(projections) - set(manifest.reads))
        if undeclared:
            raise ModuleError(
                "module request includes undeclared projection: " + ", ".join(undeclared)
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
        with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
            try:
                process = subprocess.Popen(
                    list(manifest.argv),
                    cwd=manifest.path.parent,
                    stdin=subprocess.PIPE,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    shell=False,
                    env=environment,
                    start_new_session=(os.name == "posix"),
                )
                try:
                    process.communicate(input=encoded, timeout=manifest.timeout_seconds)
                except subprocess.TimeoutExpired as exc:
                    self._terminate_process_tree(process)
                    process.communicate()
                    raise ModuleError(
                        f"module timed out after {manifest.timeout_seconds:g} seconds"
                    ) from exc
            except ModuleError:
                raise
            except OSError as exc:
                raise ModuleError(f"module process could not start: {exc}") from exc
            duration_ms = max(0, round((time.monotonic() - started) * 1000))
            stdout_file.seek(0, os.SEEK_END)
            stderr_file.seek(0, os.SEEK_END)
            stdout_size = stdout_file.tell()
            stderr_size = stderr_file.tell()
            if stdout_size > self.max_output_bytes:
                raise ModuleError("module exceeded output limit")
            if stderr_size > self.max_output_bytes:
                raise ModuleError("module exceeded error output limit")
            stdout_file.seek(0)
            output = stdout_file.read(self.max_output_bytes + 1)

        if process.returncode != 0:
            raise ModuleError(f"module exited with status {process.returncode}")
        try:
            response = json.loads(output.decode("utf-8"))
        except UnicodeDecodeError as exc:
            raise ModuleError("module output is not UTF-8") from exc
        except json.JSONDecodeError as exc:
            raise ModuleError("module output is not valid JSON") from exc
        if not isinstance(response, dict):
            raise ModuleError("module output must be a JSON object")
        _require_exact_keys(response, {"schema", "module_id", "kind", "payload"}, "result")
        if response["schema"] != RESULT_SCHEMA:
            raise ModuleError("module result schema is incompatible")
        if response["module_id"] != manifest.module_id:
            raise ModuleError("module result id does not match its manifest")
        if response["kind"] != manifest.returns:
            raise ModuleError("module result kind does not match its declared capability")
        payload = response["payload"]
        if not isinstance(payload, dict) or not payload:
            raise ModuleError("module result payload must be a non-empty JSON object")
        if manifest.category == "scoring-signal":
            score = payload.get("score")
            if not isinstance(score, (int, float)) or isinstance(score, bool):
                raise ModuleError("score-proposal payload requires a numeric score")

        return ModuleRunResult(
            module_id=manifest.module_id,
            module_version=manifest.version,
            kind=manifest.returns,
            payload=payload,
            duration_ms=duration_ms,
            stdout_sha256=hashlib.sha256(output).hexdigest(),
        )

    @staticmethod
    def _terminate_process_tree(process: subprocess.Popen[bytes]) -> None:
        if process.poll() is not None:
            return
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGKILL)
                return
            except ProcessLookupError:
                return
        process.kill()

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
