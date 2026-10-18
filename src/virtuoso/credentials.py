"""Credentials for remote annotator modules, resolved by name at run time.

A manifest declares the credential it needs by name, provider and permitted
sources. Virtuoso resolves the value for one run in a fixed order (host, env,
keychain), passes it to the module subprocess environment under the declared
name, and forgets it. The value never enters the workspace database,
``virtuoso.json``, receipts, command output, or error messages. Receipts and
annotation rows record only the source that resolved and the provider.

The keychain source shells out to the operating system tool with shell
disabled, so the core gains no runtime dependency:

- macOS: ``/usr/bin/security find-generic-password -s virtuoso -a NAME -w``
- Linux: ``secret-tool lookup service virtuoso account NAME``, with
  ``secret-tool`` taken only from ``/usr/bin`` or ``/bin``

The tool is never looked up on PATH, so a directory earlier on PATH cannot
substitute its own program. A missing tool, a non-zero exit, a timeout, or an
empty entry means the keychain source does not resolve.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from .errors import VirtuosoError

# Fixed precedence. A manifest may narrow the set but never reorder it.
CREDENTIAL_SOURCES: tuple[str, ...] = ("host", "env", "keychain")
KEYCHAIN_SERVICE = "virtuoso"
MAX_VALUE_BYTES = 4096
KEYCHAIN_TIMEOUT_SECONDS = 10.0
REDACTED = "[redacted]"

# Absolute keychain tool paths, tried in order. Never resolved from PATH.
# This mapping is the only test seam: tests replace it to point at fakes.
KEYCHAIN_TOOL_CANDIDATES: dict[str, tuple[str, ...]] = {
    "security": ("/usr/bin/security",),
    "secret-tool": ("/usr/bin/secret-tool", "/bin/secret-tool"),
}

CREDENTIAL_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
# Allowlist: a credential name must look like a credential. Names such as
# NODE_OPTIONS, HTTPS_PROXY or SSL_CERT_FILE configure the process instead.
CREDENTIAL_NAME_SUFFIXES: tuple[str, ...] = ("_API_KEY", "_TOKEN", "_SECRET", "_KEY")
CREDENTIAL_PROVIDER = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_CREDENTIAL_FIELDS = {"name", "provider", "sources"}
# Refused even when a name has a credential suffix, as a second layer.
_RESERVED_NAMES = {
    "PATH",
    "LANG",
    "HOME",
    "SHELL",
    "IFS",
    "ENV",
    "BASH_ENV",
    "TMPDIR",
    "PWD",
    "USER",
    "LOGNAME",
}
_RESERVED_PREFIXES = ("LC_", "LD_", "DYLD_", "PYTHON")


class CredentialError(VirtuosoError):
    """A credential declaration or resolution was refused. Never carries a value."""


@dataclass(frozen=True)
class CredentialSpec:
    """One credential a manifest needs, by name only."""

    name: str
    provider: str
    sources: tuple[str, ...]


@dataclass(frozen=True)
class ResolvedCredential:
    """A value resolved for one run. ``repr`` and ``str`` never show the value."""

    name: str
    provider: str
    source: str
    value: str = field(repr=False)

    def __str__(self) -> str:
        return f"{self.name} ({self.provider}) from {self.source}"


def parse_credentials(value: object) -> tuple[CredentialSpec, ...]:
    """Validate a manifest ``credentials`` array. Raises CredentialError."""
    if not isinstance(value, list):
        raise CredentialError("module credentials must be an array")
    if len(value) != 1:
        raise CredentialError(
            "module credentials must declare exactly one credential in this version"
        )
    specs: list[CredentialSpec] = []
    for index, entry in enumerate(value):
        label = f"module credentials[{index}]"
        if not isinstance(entry, dict):
            raise CredentialError(f"{label} must be a JSON object")
        unknown = sorted(set(entry) - _CREDENTIAL_FIELDS)
        missing = sorted(_CREDENTIAL_FIELDS - set(entry))
        if unknown:
            raise CredentialError(f"unknown {label} fields: " + ", ".join(unknown))
        if missing:
            raise CredentialError(f"missing {label} fields: " + ", ".join(missing))
        name = entry["name"]
        if (
            not isinstance(name, str)
            or not CREDENTIAL_NAME.fullmatch(name)
            or not name.endswith(CREDENTIAL_NAME_SUFFIXES)
        ):
            raise CredentialError(
                f"{label}.name must match ^[A-Z][A-Z0-9_]{{0,63}}$ and end with "
                + ", ".join(CREDENTIAL_NAME_SUFFIXES[:-1])
                + f", or {CREDENTIAL_NAME_SUFFIXES[-1]}"
            )
        if name in _RESERVED_NAMES or name.startswith(_RESERVED_PREFIXES):
            raise CredentialError(
                f"{label}.name {name} is reserved for the process environment"
            )
        provider = entry["provider"]
        if (
            not isinstance(provider, str)
            or len(provider) > 64
            or not CREDENTIAL_PROVIDER.fullmatch(provider)
        ):
            raise CredentialError(
                f"{label}.provider must be lowercase dash-separated words of at most 64 characters"
            )
        sources = entry["sources"]
        if (
            not isinstance(sources, list)
            or not sources
            or any(not isinstance(source, str) for source in sources)
        ):
            raise CredentialError(f"{label}.sources must be a non-empty string array")
        unknown_sources = sorted(set(sources) - set(CREDENTIAL_SOURCES))
        if unknown_sources:
            raise CredentialError(
                f"{label}.sources has unknown sources: " + ", ".join(unknown_sources)
            )
        if len(set(sources)) != len(sources):
            raise CredentialError(f"{label}.sources must not repeat a source")
        specs.append(
            CredentialSpec(
                name=name,
                provider=provider,
                sources=tuple(s for s in CREDENTIAL_SOURCES if s in sources),
            )
        )
    return tuple(specs)


def _usable(value: str | None) -> str | None:
    """A single-line, bounded, non-empty value, or None."""
    if value is None or not value:
        return None
    if any(character in value for character in ("\n", "\r", "\0")):
        return None
    if len(value.encode("utf-8", errors="surrogateescape")) > MAX_VALUE_BYTES:
        return None
    return value


def keychain_tool() -> str | None:
    """The keychain tool name for this platform, or None when unsupported."""
    if sys.platform == "darwin":
        return "security"
    if sys.platform.startswith("linux"):
        return "secret-tool"
    return None


def keychain_executable() -> str | None:
    """The absolute path of this platform's keychain tool, or None.

    Only the fixed absolute candidates are considered. PATH is never read.
    """
    tool = keychain_tool()
    if tool is None:
        return None
    for candidate in KEYCHAIN_TOOL_CANDIDATES.get(tool, ()):
        if (
            os.path.isabs(candidate)
            and os.path.isfile(candidate)
            and os.access(candidate, os.X_OK)
        ):
            return candidate
    return None


def _keychain_argv(tool: str, executable: str, name: str) -> list[str]:
    if tool == "security":
        return [executable, "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", name, "-w"]
    return [executable, "lookup", "service", KEYCHAIN_SERVICE, "account", name]


def keychain_lookup(name: str, *, environ: Mapping[str, str]) -> str | None:
    """Read one keychain entry. Any failure means the source does not resolve."""
    tool = keychain_tool()
    executable = keychain_executable()
    if tool is None or executable is None:
        return None
    try:
        completed = subprocess.run(
            _keychain_argv(tool, executable, name),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            shell=False,
            env=dict(environ),
            timeout=KEYCHAIN_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    output = completed.stdout
    if len(output) > MAX_VALUE_BYTES + 2:
        return None
    try:
        text = output.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if text.endswith("\r\n"):
        text = text[:-2]
    elif text.endswith("\n"):
        text = text[:-1]
    return _usable(text)


def parse_host_value(raw: bytes) -> str:
    """Validate the bytes a host piped on stdin. Raises without echoing them."""
    if len(raw) > MAX_VALUE_BYTES + 2:
        raise CredentialError(
            f"host credential on stdin exceeds {MAX_VALUE_BYTES} bytes"
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CredentialError("host credential on stdin is not UTF-8") from exc
    if text.endswith("\r\n"):
        text = text[:-2]
    elif text.endswith("\n"):
        text = text[:-1]
    value = _usable(text)
    if value is None:
        raise CredentialError(
            "host credential on stdin must be one non-empty line"
        )
    return value


HostReader = Callable[[], str]


def resolve(
    spec: CredentialSpec,
    *,
    host_reader: HostReader | None,
    environ: Mapping[str, str],
) -> ResolvedCredential:
    """Resolve one credential in the fixed order the manifest permits.

    A host offer is explicit, so an unusable host value fails closed instead
    of silently falling back to a local key. Raises CredentialError when no
    permitted source resolves; the message names sources, never values.
    """
    if host_reader is not None:
        if "host" not in spec.sources:
            raise CredentialError(
                f"credential {spec.name} does not accept the host source; "
                "omit --host-credential-stdin"
            )
        return ResolvedCredential(
            name=spec.name, provider=spec.provider, source="host", value=host_reader()
        )
    reasons: list[str] = []
    for source in spec.sources:
        if source == "host":
            reasons.append("host (not offered for this run)")
            continue
        if source == "env":
            value = _usable(environ.get(spec.name))
            if value is not None:
                return ResolvedCredential(
                    name=spec.name, provider=spec.provider, source="env", value=value
                )
            reasons.append(f"env ({spec.name} is unset or empty)")
            continue
        value = keychain_lookup(spec.name, environ=environ)
        if value is not None:
            return ResolvedCredential(
                name=spec.name, provider=spec.provider, source="keychain", value=value
            )
        reasons.append(
            f"keychain (no entry for service {KEYCHAIN_SERVICE}, account {spec.name})"
        )
    raise CredentialError(
        f"credential {spec.name} for provider {spec.provider} did not resolve; "
        "tried " + "; ".join(reasons)
    )


def not_checked(spec: CredentialSpec, *, reason: str) -> dict[str, object]:
    """A doctor report that reads no source, for when consent is missing."""
    return {
        "name": spec.name,
        "provider": spec.provider,
        "sources": {
            source: "not_checked" if source in spec.sources else "not-declared"
            for source in CREDENTIAL_SOURCES
        },
        "local_source": None,
        "reason": reason,
    }


def probe(spec: CredentialSpec, *, environ: Mapping[str, str]) -> dict[str, object]:
    """Which sources resolve for doctor. Reads values and discards them.

    Call only after the workspace allows remote annotators.
    """
    statuses: dict[str, str] = {}
    local_source: str | None = None
    tool_available = keychain_executable() is not None
    for source in CREDENTIAL_SOURCES:
        if source not in spec.sources:
            statuses[source] = "not-declared"
        elif source == "host":
            statuses[source] = "per-run"
        elif source == "env":
            statuses[source] = (
                "resolves" if _usable(environ.get(spec.name)) is not None else "unresolved"
            )
        elif not tool_available:
            statuses[source] = "unavailable"
        else:
            statuses[source] = (
                "resolves"
                if keychain_lookup(spec.name, environ=environ) is not None
                else "unresolved"
            )
        if local_source is None and statuses[source] == "resolves":
            local_source = source
    return {
        "name": spec.name,
        "provider": spec.provider,
        "sources": statuses,
        "local_source": local_source,
        "reason": None,
    }


def _escaped_forms(value: str) -> set[str]:
    """The value as raw text and as repr(), ascii() and json.dumps() render it.

    Quotes are stripped so a form also matches inside a larger repr or JSON
    document, where the surrounding quote style may differ.
    """
    forms = {value}
    for rendered in (
        repr(value),
        ascii(value),
        json.dumps(value),
        json.dumps(value, ensure_ascii=False),
    ):
        forms.add(rendered[1:-1])
    # repr() picks its quote by content, and the value may sit inside a longer
    # string whose repr chose the other style. Cover both styles: a trailing
    # quote pair forces single quotes (suffix \'" is 3 characters), and a
    # trailing single quote alone forces double quotes (suffix ' is 1).
    forms.add(repr(value + "'\"")[1:-1][:-3])
    if '"' not in value:
        forms.add(repr(value + "'")[1:-1][:-1])
    return {form for form in forms if form}


def redact(message: str, values: tuple[str, ...]) -> str:
    """Remove any resolved value from text that may reach the learner.

    Covers the raw value and its repr(), ascii() and json.dumps() escapes.
    Longer forms are replaced first so a raw match never splits an escape.
    """
    forms: set[str] = set()
    for value in values:
        if value:
            forms |= _escaped_forms(value)
    for form in sorted(forms, key=len, reverse=True):
        message = message.replace(form, REDACTED)
    return message
