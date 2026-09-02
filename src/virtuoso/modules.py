from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shlex
import shutil
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import resource
except ImportError:  # pragma: no cover - non-POSIX platforms fail closed at runtime
    resource = None  # type: ignore[assignment]


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
    "access_token",
    "accesstoken",
    "api_key",
    "apikey",
    "auth",
    "bearer_token",
    "client_secret",
    "database_path",
    "db_path",
    "directory",
    "file",
    "home",
    "password",
    "path",
    "private_key",
    "refresh_token",
    "state_path",
    "sqlite_path",
    "token",
    "workspace_path",
    "workspace_root",
    "credentials",
    "secrets",
}
_SHELL_EXECUTABLES = {
    "bash",
    "cmd",
    "cmd.exe",
    "csh",
    "dash",
    "fish",
    "powershell",
    "pwsh",
    "sh",
    "tcsh",
    "zsh",
}
_COMMAND_WRAPPERS = {"env"}
_MODULE_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?$")
_WINDOWS_ABSOLUTE_PATH = re.compile(r"^[A-Za-z]:[\\/]")
_PROJECTION_FIELDS: dict[str, dict[str, type | tuple[type, ...]]] = {
    "challenge.summary": {
        "item_id": str,
        "title": str,
        "focus": str,
        "prompt": str,
        "learning_context": str,
        "priority": (int, float),
    },
    "attempt.summary": {
        "result": str,
        "initial_latency_ms": int,
        "confidence": int,
        "open_notes": bool,
        "agent_help": str,
        "support_actions": list,
    },
    "focus.summary": {
        "focus": str,
        "session_intent": str,
        "available_minutes": int,
    },
    "scheduler.summary": {
        "algorithm": str,
        "algorithm_version": str,
        "learning_context": str,
        "due_at": str,
        "retrievability": (int, float),
    },
    "project.summary": {
        "project_id": str,
        "title": str,
        "summary": str,
    },
}
_RESULT_FIELDS: dict[str, dict[str, type | tuple[type, ...]]] = {
    "score-proposal": {"score": (int, float), "rationale": str},
    "scheduler-proposal": {
        "due_at": str,
        "algorithm": str,
        "algorithm_version": str,
        "learning_context": str,
        "configuration": dict,
        "rationale": str,
    },
    "practice-proposal": {
        "title": str,
        "prompt": str,
        "hint": str,
        "follow_up": str,
    },
    "source-projection": {
        "source_id": str,
        "relative_path": str,
        "title": str,
        "content_hash": str,
        "wikilinks": list,
    },
    "output-receipt": {"status": str, "reference": str},
}
_RESULT_REQUIRED_FIELDS: dict[str, set[str]] = {
    "score-proposal": {"score"},
    "scheduler-proposal": set(_RESULT_FIELDS["scheduler-proposal"]),
    "practice-proposal": set(_RESULT_FIELDS["practice-proposal"]),
    "source-projection": set(_RESULT_FIELDS["source-projection"]),
    "output-receipt": set(_RESULT_FIELDS["output-receipt"]),
}
_SUPPORT_ACTION_FIELDS: dict[str, type | tuple[type, ...]] = {
    "kind": str,
    "response": (str, type(None)),
    "latency_ms": (int, type(None)),
}
_SUPPORT_ACTION_KINDS = {
    "retry",
    "retry-unaided",
    "hint",
    "worked-feedback",
    "follow-up",
    "follow-up-offered",
}


def _resolved_executable(command: str, *, cwd: Path) -> Path | None:
    """Resolve an executable far enough to enforce the no-shell boundary."""
    if "/" in command or "\\" in command:
        candidate = Path(command)
        if not candidate.is_absolute():
            candidate = cwd / candidate
        try:
            return candidate.resolve(strict=True)
        except OSError:
            return None
    located = shutil.which(command)
    if located is None:
        return None
    try:
        return Path(located).resolve(strict=True)
    except OSError:
        return None


def _shebang_command(executable: Path) -> str | None:
    try:
        with executable.open("rb") as handle:
            first_line = handle.readline(4097)
    except OSError:
        return None
    if len(first_line) > 4096 or not first_line.startswith(b"#!"):
        return None
    try:
        tokens = shlex.split(first_line[2:].decode("utf-8").strip())
    except (UnicodeDecodeError, ValueError):
        return None
    if not tokens:
        return None
    interpreter = tokens[0]
    if Path(interpreter).name.casefold() != "env":
        return interpreter
    remaining = tokens[1:]
    # Complex env option processing is a wrapper, not an executable identity
    # Virtuoso can inspect safely.
    if not remaining or any(token.startswith("-") for token in remaining[:-1]):
        return "env"
    return remaining[-1]


def _reject_disallowed_executable(command: str, *, cwd: Path) -> None:
    seen: set[Path] = set()
    current = command
    for _ in range(8):
        resolved = _resolved_executable(current, cwd=cwd)
        if resolved is None or resolved in seen:
            return
        seen.add(resolved)
        name = resolved.name.casefold()
        if name in _SHELL_EXECUTABLES or name in _COMMAND_WRAPPERS:
            raise ModuleError(
                "module command argv must not invoke shell executables or command wrappers"
            )
        interpreter = _shebang_command(resolved)
        if interpreter is None:
            return
        current = interpreter
        cwd = resolved.parent
    raise ModuleError("module executable interpreter ancestry is too deep")


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
        executable_names = {Path(arg).name.casefold() for arg in argv}
        if (
            executable_names & _SHELL_EXECUTABLES
            or Path(argv[0]).name.casefold() in _COMMAND_WRAPPERS
        ):
            raise ModuleError(
                "module command argv must not invoke shell executables or command wrappers"
            )
        _reject_disallowed_executable(argv[0], cwd=manifest_path.parent)
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
        self._validate_projections(projections)
        self._require_finite_json(request)
        encoded = (json.dumps(request, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
        if len(encoded) > self.max_input_bytes:
            raise ModuleError("module request exceeds input limit")

        environment = {
            "PATH": os.environ.get("PATH", ""),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PYTHONIOENCODING": "utf-8",
        }
        if resource is None or not hasattr(resource, "RLIMIT_NPROC"):
            raise ModuleError(
                "module execution requires an OS process limit that prevents descendants"
            )
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
                    preexec_fn=self._deny_descendant_processes,
                )
                assert process.stdin is not None
                process.stdin.write(encoded)
                process.stdin.close()
                deadline = started + manifest.timeout_seconds
                descendants: set[int] = set()
                while process.poll() is None:
                    descendants.update(self._descendant_pids(process.pid))
                    if os.fstat(stdout_file.fileno()).st_size > self.max_output_bytes:
                        self._terminate_process_tree(process, descendants)
                        process.wait()
                        raise ModuleError("module exceeded output limit")
                    if os.fstat(stderr_file.fileno()).st_size > self.max_output_bytes:
                        self._terminate_process_tree(process, descendants)
                        process.wait()
                        raise ModuleError("module exceeded error output limit")
                    if time.monotonic() >= deadline:
                        descendants.update(self._descendant_pids(process.pid))
                        self._terminate_process_tree(process, descendants)
                        process.wait()
                        raise ModuleError(
                            f"module timed out after {manifest.timeout_seconds:g} seconds"
                        )
                    time.sleep(0.01)
                # A successful executable must not leave work running after its
                # proposal has been accepted. The process group still exists
                # after the group leader exits, so always terminate it.
                self._terminate_process_tree(process, descendants)
            except ModuleError:
                raise
            except (OSError, subprocess.SubprocessError) as exc:
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
        self._require_finite_json(payload)
        self._validate_result_payload(manifest.returns, payload)

        return ModuleRunResult(
            module_id=manifest.module_id,
            module_version=manifest.version,
            kind=manifest.returns,
            payload=payload,
            duration_ms=duration_ms,
            stdout_sha256=hashlib.sha256(output).hexdigest(),
        )

    @staticmethod
    def _deny_descendant_processes() -> None:
        """Run in the module child before exec; v0 modules have no spawn capability."""
        assert resource is not None
        resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))

    @staticmethod
    def _terminate_process_tree(
        process: subprocess.Popen[bytes], descendants: set[int] | None = None
    ) -> None:
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            for pid in descendants or ():
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            return
        if process.poll() is None:
            process.kill()

    @staticmethod
    def _descendant_pids(root_pid: int) -> set[int]:
        if os.name != "posix":
            return set()
        try:
            value = subprocess.run(
                ["ps", "-axo", "pid=,ppid="],
                check=True,
                capture_output=True,
                text=True,
                timeout=1,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            return set()
        children: dict[int, set[int]] = {}
        for line in value.splitlines():
            parts = line.split()
            if len(parts) != 2:
                continue
            try:
                pid, ppid = map(int, parts)
            except ValueError:
                continue
            children.setdefault(ppid, set()).add(pid)
        result: set[int] = set()
        pending = list(children.get(root_pid, ()))
        while pending:
            pid = pending.pop()
            if pid in result:
                continue
            result.add(pid)
            pending.extend(children.get(pid, ()))
        return result

    @classmethod
    def _validate_projections(cls, projections: dict[str, Any]) -> None:
        for projection, body in projections.items():
            if not isinstance(body, dict):
                raise ModuleError(f"{projection} must be a JSON object")
            schema = _PROJECTION_FIELDS[projection]
            unknown = sorted(set(body) - set(schema))
            if unknown:
                raise ModuleError(
                    f"unknown {projection} fields: " + ", ".join(unknown)
                )
            for field, value in body.items():
                expected = schema[field]
                if isinstance(value, bool) and expected != bool:
                    valid = False
                else:
                    valid = isinstance(value, expected)
                if not valid:
                    type_name = (
                        "number"
                        if isinstance(expected, tuple)
                        else "string"
                        if expected is str
                        else expected.__name__
                    )
                    raise ModuleError(f"{projection}.{field} must be a {type_name}")
            if projection == "attempt.summary" and "support_actions" in body:
                cls._validate_support_actions(body["support_actions"])

    @classmethod
    def _validate_result_payload(cls, kind: str, payload: dict[str, Any]) -> None:
        schema = _RESULT_FIELDS[kind]
        unknown = sorted(set(payload) - set(schema))
        if unknown:
            raise ModuleError(f"unknown {kind} fields: " + ", ".join(unknown))
        required = _RESULT_REQUIRED_FIELDS[kind]
        missing = sorted(required - set(payload))
        if missing:
            raise ModuleError(f"missing {kind} fields: " + ", ".join(missing))
        if not payload:
            raise ModuleError("module result payload must be a non-empty JSON object")
        for field, value in payload.items():
            expected = schema[field]
            if isinstance(value, bool) and expected != bool:
                valid = False
            else:
                valid = isinstance(value, expected)
            if not valid:
                type_name = (
                    "number"
                    if isinstance(expected, tuple)
                    else "string"
                    if expected is str
                    else expected.__name__
                )
                raise ModuleError(f"{kind}.{field} must be a {type_name}")
        if kind == "source-projection":
            cls._validate_string_list(
                payload["wikilinks"], "source-projection.wikilinks"
            )

    @classmethod
    def _validate_support_actions(cls, value: object) -> None:
        assert isinstance(value, list)
        for index, action in enumerate(value):
            label = f"attempt.summary.support_actions[{index}]"
            if not isinstance(action, dict):
                raise ModuleError(f"{label} must be a JSON object")
            _require_exact_keys(action, set(_SUPPORT_ACTION_FIELDS), label)
            for field, child in action.items():
                expected = _SUPPORT_ACTION_FIELDS[field]
                if isinstance(child, bool) and expected != bool:
                    valid = False
                else:
                    valid = isinstance(child, expected)
                if not valid:
                    raise ModuleError(f"{label}.{field} has an invalid type")
            if action["kind"] not in _SUPPORT_ACTION_KINDS:
                raise ModuleError(f"{label}.kind is not a supported action")
            latency = action["latency_ms"]
            if isinstance(latency, int) and latency < 0:
                raise ModuleError(f"{label}.latency_ms must be non-negative")

    @staticmethod
    def _validate_string_list(value: object, label: str) -> None:
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise ModuleError(f"{label} must be a string array")

    @classmethod
    def _require_finite_json(cls, value: object) -> None:
        if isinstance(value, float) and not math.isfinite(value):
            raise ModuleError("module data must contain only finite JSON numbers")
        if isinstance(value, dict):
            for child in value.values():
                cls._require_finite_json(child)
        elif isinstance(value, list):
            for child in value:
                cls._require_finite_json(child)

    @classmethod
    def _contains_private_state_path(cls, value: object) -> bool:
        if isinstance(value, dict):
            for key, child in value.items():
                normalized_key = re.sub(r"[^a-z0-9]+", "_", str(key).casefold()).strip("_")
                if normalized_key in _PRIVATE_STATE_KEYS:
                    return True
                if cls._contains_private_state_path(child):
                    return True
        elif isinstance(value, list):
            return any(cls._contains_private_state_path(child) for child in value)
        elif isinstance(value, str):
            normalized = value.strip()
            if (
                normalized.startswith(("/", "~/", "file://"))
                or _WINDOWS_ABSOLUTE_PATH.match(normalized)
                or "/.virtuoso/" in normalized.replace("\\", "/")
            ):
                return True
        return False
