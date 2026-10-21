"""Portable workspace archives; SQLite is snapshotted, never copied live.

Archive metadata is untrusted. Import validates and hashes the complete input
before creating a private staging workspace, then validates again while writing.
Only a workspace that opens successfully is published at the requested path.
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import re
import sqlite3
import stat
import tarfile
import tempfile
import time
import unicodedata
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from . import __version__
from .errors import VirtuosoError
from .workspace import WorkspaceService, _PRIVATE_DIRECTORY_MODE, _PRIVATE_FILE_MODE

EXPORT_SCHEMA = "virtuoso/workspace-export@0.1"
IMPORT_SCHEMA = "virtuoso/workspace-import@0.1"
MANIFEST_NAME = "virtuoso-export.json"
DATABASE_PATH = ".virtuoso/state.sqlite3"
MAX_TOTAL_SIZE = 1024 * 1024 * 1024
MAX_MANIFEST_SIZE = 4 * 1024 * 1024
MAX_MEMBERS = 100_000
_CHUNK = 1024 * 1024
_EXCLUDED = {
    ".virtuoso/search.sqlite3", ".virtuoso/search.sqlite3-wal",
    ".virtuoso/search.sqlite3-shm", ".virtuoso/state.sqlite3-wal",
    ".virtuoso/state.sqlite3-shm",
}


class WorkspaceArchiveError(VirtuosoError):
    """An archive cannot be exported or restored safely."""


def _safe_path(value: object) -> str:
    if (not isinstance(value, str) or not value or len(value.encode("utf-8")) > 4096
            or "\\" in value or ":" in value
            or any(ord(c) < 32 for c in value)
            or any(part in ("", ".", "..") for part in value.split("/"))):
        raise WorkspaceArchiveError("archive contains an unsafe relative path")
    return value


def _owned_file(name: str) -> bool:
    return (name in ("virtuoso.json", DATABASE_PATH)
            or (name.startswith("items/") and name.endswith(".md"))
            or (name.startswith("modules/") and not name.endswith(("-wal", "-shm"))))


def _owned_directory(name: str) -> bool:
    return name in ("items", ".virtuoso", "modules") or name.startswith(("items/", "modules/"))


def _no_symlink_path(path: Path) -> None:
    for component in (path, *path.parents):
        if component.is_symlink():
            raise WorkspaceArchiveError(f"path must not contain a symlink: {component}")


def _destination(path: Path) -> None:
    _no_symlink_path(path)
    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        raise WorkspaceArchiveError("import destination must be absent or an empty directory")


def _private_parents(path: Path) -> None:
    """Make missing parents private without changing existing parent permissions."""
    missing = []
    while not path.exists():
        missing.append(path)
        path = path.parent
    for directory in reversed(missing):
        directory.mkdir(mode=_PRIVATE_DIRECTORY_MODE)
        directory.chmod(_PRIVATE_DIRECTORY_MODE)


def _copy_hash(source: BinaryIO, destination: BinaryIO | None = None) -> tuple[int, str]:
    size, digest = 0, hashlib.sha256()
    while chunk := source.read(_CHUNK):
        size += len(chunk)
        if size > MAX_TOTAL_SIZE:
            raise WorkspaceArchiveError("archive exceeds the total size limit")
        digest.update(chunk)
        if destination is not None:
            destination.write(chunk)
    return size, digest.hexdigest()


def _database_version(path: Path) -> int:
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("PRAGMA trusted_schema = OFF")
        version = db.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
        if type(version) is not int or version < 1:
            raise WorkspaceArchiveError("invalid database schema version")
        return version


def _snapshot(workspace: WorkspaceService, target: Path) -> int:
    workspace._validate_owned_paths(require_database=True)
    target.touch(mode=_PRIVATE_FILE_MODE)
    target.chmod(_PRIVATE_FILE_MODE)
    started = time.monotonic()
    page_size = 4096

    def progress(status: int, remaining: int, total: int) -> None:
        if time.monotonic() - started > 30:
            raise WorkspaceArchiveError("workspace database is busy; retry export")
        if total * page_size > MAX_TOTAL_SIZE:
            raise WorkspaceArchiveError("workspace database exceeds the size limit")

    with closing(sqlite3.connect(workspace.db_path.as_uri() + "?mode=ro", uri=True)) as source:
        page_size = source.execute("PRAGMA page_size").fetchone()[0]
        with closing(sqlite3.connect(target)) as snapshot:
            source.backup(snapshot, pages=256, progress=progress, sleep=0.05)
            # A portable backup has no journal dependency, including a WAL source.
            snapshot.execute("PRAGMA journal_mode = DELETE")
    target.chmod(_PRIVATE_FILE_MODE)
    return _database_version(target)


def _collect(workspace: WorkspaceService, staging: Path) -> tuple[list[dict], list[str], list[str]]:
    files, directories, excluded = [], [], set(_EXCLUDED)
    total = 0

    def visit(directory_fd: int, prefix: str = "") -> None:
        nonlocal total
        for name in sorted(os.listdir(directory_fd)):
            relative = _safe_path(f"{prefix}/{name}" if prefix else name)
            try:
                info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            except FileNotFoundError:
                if relative in _EXCLUDED:
                    continue  # SQLite may remove its transient journals.
                raise
            if stat.S_ISLNK(info.st_mode):
                raise WorkspaceArchiveError(f"refusing workspace symlink: {relative}")
            if stat.S_ISDIR(info.st_mode):
                if _owned_directory(relative):
                    directories.append(relative)
                else:
                    excluded.add(relative)
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY, dir_fd=directory_fd)
                try:
                    visit(fd, relative)
                finally:
                    os.close(fd)
            elif not stat.S_ISREG(info.st_mode):
                raise WorkspaceArchiveError(f"workspace entry must be a regular file: {relative}")
            elif relative == DATABASE_PATH:
                continue  # Only the SQLite backup API may read this payload.
            elif not _owned_file(relative):
                excluded.add(relative)
            else:
                target = staging / relative
                _private_parents(target.parent)
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
                with os.fdopen(fd, "rb") as source:
                    before = os.fstat(source.fileno())
                    if not stat.S_ISREG(before.st_mode):
                        raise WorkspaceArchiveError(f"workspace entry changed type: {relative}")
                    with target.open("xb") as output:
                        os.fchmod(output.fileno(), _PRIVATE_FILE_MODE)
                        size, digest = _copy_hash(source, output)
                    after = os.fstat(source.fileno())
                    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                            after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                        raise WorkspaceArchiveError(f"workspace file changed during export: {relative}")
                total += size
                files.append({"path": relative, "size": size, "sha256": digest})
            if total > MAX_TOTAL_SIZE or len(files) + len(directories) + len(excluded) > MAX_MEMBERS:
                raise WorkspaceArchiveError("workspace exceeds archive limits")

    fd = os.open(workspace.root, os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY)
    try:
        visit(fd)
    finally:
        os.close(fd)
    return sorted(files, key=lambda entry: entry["path"]), sorted(directories), sorted(excluded)


def export_workspace(workspace: WorkspaceService, output: Path, *, now: datetime | None = None) -> dict:
    """Write a private, atomic tar.gz without changing learner state."""
    temporary_archive: Path | None = None
    try:
        output = Path(output).expanduser().absolute()
        _no_symlink_path(output)
        if output.resolve().is_relative_to(workspace.root):
            raise WorkspaceArchiveError("export target must live outside the workspace")
        workspace._validate_owned_paths(require_database=True)
        observed_at = now or datetime.now(timezone.utc)
        if observed_at.utcoffset() is None:
            raise WorkspaceArchiveError("export timestamp must be timezone-aware")
        created_at = observed_at.astimezone(timezone.utc).isoformat()
        # The output parent must already exist, avoiding accidental directory creation.
        with tempfile.TemporaryDirectory(prefix=".virtuoso-export-", dir=output.parent) as temporary:
            staging = Path(temporary)
            staging.chmod(_PRIVATE_DIRECTORY_MODE)
            _private_parents(staging / ".virtuoso")
            # Inventory first: even excluded symlinks must be rejected before
            # SQLite can inspect a sidecar or any workspace payload is archived.
            files, directories, excluded = _collect(workspace, staging)
            version = _snapshot(workspace, staging / DATABASE_PATH)
            with (staging / DATABASE_PATH).open("rb") as source:
                size, digest = _copy_hash(source)
            files.append({"path": DATABASE_PATH, "size": size, "sha256": digest})
            files.sort(key=lambda entry: entry["path"])
            manifest = {"schema": EXPORT_SCHEMA, "virtuoso_version": __version__,
                        "database_schema_version": version, "created_at": created_at,
                        "files": files, "directories": directories, "excluded_paths": excluded}
            data = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
            if sum(entry["size"] for entry in files) + len(data) > MAX_TOTAL_SIZE:
                raise WorkspaceArchiveError("workspace exceeds the total size limit")
            if len(data) > MAX_MANIFEST_SIZE:
                raise WorkspaceArchiveError("archive manifest exceeds the size limit")
            descriptor, filename = tempfile.mkstemp(prefix=".virtuoso-archive-", dir=output.parent)
            temporary_archive = Path(filename)
            with os.fdopen(descriptor, "wb") as raw:
                os.fchmod(raw.fileno(), _PRIVATE_FILE_MODE)
                with tarfile.open(fileobj=raw, mode="w:gz", format=tarfile.PAX_FORMAT) as archive:
                    info = tarfile.TarInfo(MANIFEST_NAME)
                    info.mode, info.size = _PRIVATE_FILE_MODE, len(data)
                    archive.addfile(info, io.BytesIO(data))
                    for name in directories:
                        info = tarfile.TarInfo(name)
                        info.type, info.mode = tarfile.DIRTYPE, _PRIVATE_DIRECTORY_MODE
                        archive.addfile(info)
                    for entry in files:
                        info = tarfile.TarInfo(entry["path"])
                        info.mode, info.size = _PRIVATE_FILE_MODE, entry["size"]
                        with (staging / entry["path"]).open("rb") as source:
                            archive.addfile(info, source)
                raw.flush()
                os.fsync(raw.fileno())
            # Apply the importer's exact portability and metadata limits before
            # replacing an existing archive (including case/Unicode aliases).
            with temporary_archive.open("rb") as raw:
                _scan(raw)
            os.replace(temporary_archive, output)
            temporary_archive = None
        return {"schema": EXPORT_SCHEMA, "archive": str(output), "created_at": created_at,
                "file_count": len(files)}
    except WorkspaceArchiveError:
        raise
    except (OSError, ValueError, RecursionError, sqlite3.Error, VirtuosoError) as exc:
        raise WorkspaceArchiveError(f"workspace export failed: {exc}") from exc
    finally:
        if temporary_archive is not None:
            temporary_archive.unlink(missing_ok=True)


class _ArchiveInfo(tarfile.TarInfo):
    def _proc_member(self, archive):
        # Bound extension metadata before tarfile allocates its payload.
        archive._workspace_header_count = getattr(archive, "_workspace_header_count", 0) + 1
        if archive._workspace_header_count > MAX_MEMBERS:
            raise WorkspaceArchiveError("archive has too many members")
        if self.type not in (tarfile.REGTYPE, tarfile.AREGTYPE, tarfile.DIRTYPE):
            if self.type not in (tarfile.XHDTYPE, tarfile.XGLTYPE, tarfile.GNUTYPE_LONGNAME):
                raise WorkspaceArchiveError("archive contains a link or unsupported member type")
            if self.size > 65536:
                raise WorkspaceArchiveError("archive extension metadata exceeds the size limit")
        return super()._proc_member(archive)


class _BoundedReader:
    def __init__(self, stream: BinaryIO) -> None:
        self.stream, self.total = stream, 0

    def read(self, size: int) -> bytes:
        data = self.stream.read(size)
        self.total += len(data)
        if self.total > MAX_TOTAL_SIZE + MAX_MANIFEST_SIZE + MAX_MEMBERS * 2048:
            raise WorkspaceArchiveError("decompressed archive exceeds the size limit")
        return data


def _json_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise WorkspaceArchiveError("manifest has a duplicate JSON field")
        result[key] = value
    return result


def _validate_manifest(manifest: object, actual: dict[str, dict], directories: set[str]) -> dict:
    if not isinstance(manifest, dict) or manifest.get("schema") != EXPORT_SCHEMA:
        raise WorkspaceArchiveError("unsupported workspace export manifest")
    if (not isinstance(manifest.get("virtuoso_version"), str)
            or type(manifest.get("database_schema_version")) is not int
            or manifest["database_schema_version"] < 1
            or not isinstance(manifest.get("created_at"), str)):
        raise WorkspaceArchiveError("invalid export version or timestamp metadata")
    try:
        if datetime.fromisoformat(manifest["created_at"]).utcoffset() is None:
            raise ValueError("timestamp has no timezone")
    except ValueError as exc:
        raise WorkspaceArchiveError("invalid export timestamp") from exc
    declared = {}
    if not isinstance(manifest.get("files"), list):
        raise WorkspaceArchiveError("manifest files must be an array")
    for entry in manifest["files"]:
        if not isinstance(entry, dict) or set(entry) != {"path", "size", "sha256"}:
            raise WorkspaceArchiveError("invalid manifest file entry")
        name = _safe_path(entry["path"])
        if (not _owned_file(name) or name in declared or type(entry["size"]) is not int
                or entry["size"] < 0 or not isinstance(entry["sha256"], str)
                or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])):
            raise WorkspaceArchiveError("invalid manifest file path, size or hash")
        declared[name] = entry
    listed_directories = manifest.get("directories")
    if not isinstance(listed_directories, list):
        raise WorkspaceArchiveError("manifest directories must be an array")
    for name in listed_directories:
        if not _owned_directory(_safe_path(name)):
            raise WorkspaceArchiveError("manifest contains an unsupported directory")
    excluded = manifest.get("excluded_paths")
    if not isinstance(excluded, list):
        raise WorkspaceArchiveError("manifest excluded_paths must be an array")
    for name in excluded:
        _safe_path(name)
    if (declared != actual or set(listed_directories) != directories
            or len(listed_directories) != len(directories)):
        raise WorkspaceArchiveError("archive inventory, sizes or sha256 hashes do not match the manifest")
    if not {"virtuoso.json", DATABASE_PATH}.issubset(declared) or not {"items", ".virtuoso"}.issubset(directories):
        raise WorkspaceArchiveError("archive is missing required workspace files or directories")
    for name in (*actual, *directories):
        for parent in PurePosixPath(name).parents:
            if str(parent) != "." and str(parent) not in directories:
                raise WorkspaceArchiveError("archive has an undeclared parent directory")
    return manifest


def _scan(raw: BinaryIO, staging: Path | None = None) -> dict:
    raw.seek(0)
    actual, directories, names = {}, set(), set()
    total, manifest = 0, None
    with gzip.GzipFile(fileobj=raw, mode="rb") as compressed:
        bounded = _BoundedReader(compressed)
        with tarfile.open(fileobj=bounded, mode="r|", tarinfo=_ArchiveInfo) as archive:
            for member in archive:
                name = _safe_path(member.name)
                identity = unicodedata.normalize("NFC", name).casefold()
                if identity in names or member.sparse is not None or any("sparse" in key.lower() for key in member.pax_headers):
                    raise WorkspaceArchiveError("archive contains a duplicate or sparse member")
                names.add(identity)
                if member.isdir():
                    if not _owned_directory(name) or member.size != 0:
                        raise WorkspaceArchiveError("archive contains an unsupported directory")
                    directories.add(name)
                    continue
                if not member.isfile() or (name != MANIFEST_NAME and not _owned_file(name)):
                    raise WorkspaceArchiveError("archive contains an unsupported file")
                total += member.size
                if member.size < 0 or total > MAX_TOTAL_SIZE:
                    raise WorkspaceArchiveError("archive exceeds the total size limit")
                source = archive.extractfile(member)
                assert source is not None
                if name == MANIFEST_NAME:
                    if member.size > MAX_MANIFEST_SIZE:
                        raise WorkspaceArchiveError("archive manifest exceeds the size limit")
                    manifest = json.loads(source.read(), object_pairs_hook=_json_object)
                elif staging is None:
                    size, digest = _copy_hash(source)
                    actual[name] = {"path": name, "size": size, "sha256": digest}
                else:
                    target = staging / name
                    _private_parents(target.parent)
                    with target.open("xb") as output:
                        os.fchmod(output.fileno(), _PRIVATE_FILE_MODE)
                        size, digest = _copy_hash(source, output)
                    actual[name] = {"path": name, "size": size, "sha256": digest}
        # Read through the gzip trailer as well; truncated or corrupt streams fail.
        while bounded.read(_CHUNK):
            pass
    result = _validate_manifest(manifest, actual, directories)
    if staging is not None:
        for name in sorted(directories):
            _private_parents(staging / name)
    return result


def import_workspace(archive: Path, destination: Path) -> dict:
    """Validate, privately stage, and publish a workspace without module execution."""
    try:
        archive = Path(archive).expanduser().absolute()
        destination = Path(destination).expanduser().absolute()
        _destination(destination)
        _no_symlink_path(archive)
        descriptor = os.open(archive, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as raw:
            if not stat.S_ISREG(os.fstat(raw.fileno()).st_mode):
                raise WorkspaceArchiveError("archive must be a regular file")
            manifest = _scan(raw)  # No destination or staging writes before validation.
            _private_parents(destination.parent)
            with tempfile.TemporaryDirectory(prefix=".virtuoso-import-", dir=destination.parent) as temporary:
                Path(temporary).chmod(_PRIVATE_DIRECTORY_MODE)
                staging = Path(temporary) / "workspace"
                _private_parents(staging)
                if _scan(raw, staging) != manifest:
                    raise WorkspaceArchiveError("archive changed during import")
                if _database_version(staging / DATABASE_PATH) != manifest["database_schema_version"]:
                    raise WorkspaceArchiveError("database schema version does not match the manifest")
                workspace = WorkspaceService.open(staging)
                # doctor reads item paths from SQLite: keep them inside the validated inventory.
                inventory = {entry["path"] for entry in manifest["files"]}
                with closing(workspace._connect()) as db:
                    for (relative,) in db.execute("SELECT relative_path FROM items"):
                        if not _safe_path(relative).startswith("items/") or relative not in inventory:
                            raise WorkspaceArchiveError("database item path is outside the archived items")
                report = workspace.doctor()
                _destination(destination)
                os.replace(staging, destination)
        return {"schema": IMPORT_SCHEMA, "workspace": str(destination),
                "file_count": len(manifest["files"]), "doctor": report}
    except WorkspaceArchiveError:
        raise
    except (OSError, EOFError, ValueError, TypeError, RecursionError, tarfile.TarError, sqlite3.Error, VirtuosoError) as exc:
        raise WorkspaceArchiveError(f"workspace import failed: {exc}") from exc
