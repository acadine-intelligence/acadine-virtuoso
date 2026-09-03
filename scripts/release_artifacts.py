#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tarfile
import tempfile
import tomllib
import zipfile
from email.parser import Parser
from pathlib import Path, PurePosixPath
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RELEASE_VERSION = "0.1.0"
_STABLE_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_OBSIDIAN_ASSETS = ("main.js", "manifest.json", "versions.json")
_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
_LOCAL_PATH_PATTERNS = (
    re.compile(rb"/Users/[A-Za-z0-9._-]+/"),
    re.compile(rb"/home/[A-Za-z0-9._-]+/"),
    re.compile(rb"/root/"),
    re.compile(rb"[A-Za-z]:[\\/]+Users[\\/]+[A-Za-z0-9._-]+[\\/]"),
)


class ReleaseArtifactError(ValueError):
    pass


def _read_json(path: Path, root: Path) -> dict[str, Any]:
    _regular_file(path, "release metadata", root=root)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseArtifactError(f"cannot read release metadata: {path.name}") from exc
    if not isinstance(value, dict):
        raise ReleaseArtifactError(f"release metadata must be an object: {path.name}")
    return value


def _hermes_version(root: Path) -> str:
    path = root / "plugins" / "hermes" / "plugin.yaml"
    _regular_file(path, "release metadata", root=root)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ReleaseArtifactError("cannot read release metadata: plugin.yaml") from exc
    versions = [line.split(":", 1)[1].strip() for line in lines if line.startswith("version:")]
    if len(versions) != 1:
        raise ReleaseArtifactError("Hermes plugin must declare one top-level version")
    return versions[0]


def validate_source_versions(root: Path = ROOT) -> str:
    pyproject_path = root / "pyproject.toml"
    _regular_file(pyproject_path, "release metadata", root=root)
    try:
        project = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))[
            "project"
        ]
    except (OSError, tomllib.TOMLDecodeError, KeyError, TypeError) as exc:
        raise ReleaseArtifactError("cannot read release metadata: pyproject.toml") from exc

    product = _read_json(root / "product.json", root)
    obsidian_root = root / "plugins" / "obsidian"
    manifest = _read_json(obsidian_root / "manifest.json", root)
    package = _read_json(obsidian_root / "package.json", root)
    lock = _read_json(obsidian_root / "package-lock.json", root)
    versions = _read_json(obsidian_root / "versions.json", root)
    try:
        observed = {
            "pyproject.toml": project["version"],
            "product.json": product["release"]["version"],
            "plugins/hermes/plugin.yaml": _hermes_version(root),
            "plugins/obsidian/manifest.json": manifest["version"],
            "plugins/obsidian/package.json": package["version"],
            "plugins/obsidian/package-lock.json": lock["version"],
            "plugins/obsidian/package-lock.json root": lock["packages"][""]["version"],
        }
        minimum_app_version = manifest["minAppVersion"]
    except (KeyError, TypeError) as exc:
        raise ReleaseArtifactError("release metadata has an invalid structure") from exc

    if not _STABLE_VERSION.fullmatch(RELEASE_VERSION):
        raise ReleaseArtifactError("release version must be stable semantic version text")
    mismatches = {
        name: value for name, value in observed.items() if value != RELEASE_VERSION
    }
    if mismatches:
        details = ", ".join(f"{name}={value!r}" for name, value in sorted(mismatches.items()))
        raise ReleaseArtifactError(f"release version mismatch: {details}")
    if versions != {RELEASE_VERSION: minimum_app_version}:
        raise ReleaseArtifactError("plugins/obsidian/versions.json does not match the manifest")
    return RELEASE_VERSION


def _wheel_name(version: str) -> str:
    return f"acadine_virtuoso-{version}-py3-none-any.whl"


def _sdist_name(version: str) -> str:
    return f"acadine_virtuoso-{version}.tar.gz"


def _bundle_name(version: str) -> str:
    return f"virtuoso-obsidian-{version}.zip"


def _release_asset_names(version: str) -> tuple[str, ...]:
    return (
        _wheel_name(version),
        _sdist_name(version),
        _bundle_name(version),
        *_OBSIDIAN_ASSETS,
        "SHA256SUMS",
    )


def _regular_file(path: Path, label: str, *, root: Path | None = None) -> Path:
    if root is not None:
        try:
            root_mode = root.lstat().st_mode
        except OSError as exc:
            raise ReleaseArtifactError("repository root is missing") from exc
        if stat.S_ISLNK(root_mode) or not stat.S_ISDIR(root_mode):
            raise ReleaseArtifactError("repository root must be a real directory")
        try:
            relative = path.relative_to(root)
        except ValueError as exc:
            raise ReleaseArtifactError(f"{label} escapes the repository root") from exc
        current = root
        for part in relative.parts:
            current = current / part
            try:
                component_mode = current.lstat().st_mode
            except OSError as exc:
                raise ReleaseArtifactError(f"missing {label}: {path.name}") from exc
            if stat.S_ISLNK(component_mode):
                raise ReleaseArtifactError(
                    f"{label} has a symlink component: {current.relative_to(root)}"
                )
    try:
        mode = path.lstat().st_mode
    except OSError as exc:
        raise ReleaseArtifactError(f"missing {label}: {path.name}") from exc
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        raise ReleaseArtifactError(f"{label} must be a regular file: {path.name}")
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_obsidian_bundle(target: Path, obsidian_root: Path, root: Path) -> None:
    with zipfile.ZipFile(
        target,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
        strict_timestamps=True,
    ) as archive:
        for name in sorted(_OBSIDIAN_ASSETS):
            source = _regular_file(
                obsidian_root / name, "Obsidian release input", root=root
            )
            info = zipfile.ZipInfo(name, date_time=_ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, source.read_bytes(), compress_type=zipfile.ZIP_DEFLATED)


def _write_checksums(release_dir: Path, names: list[str]) -> None:
    lines = [f"{_sha256(release_dir / name)}  {name}\n" for name in sorted(names)]
    (release_dir / "SHA256SUMS").write_text("".join(lines), encoding="ascii")


def assemble_release(root: Path = ROOT) -> Path:
    version = validate_source_versions(root)
    python_dir = root / "dist" / "python"
    obsidian_root = root / "plugins" / "obsidian"
    sources = {
        _wheel_name(version): _regular_file(
            python_dir / _wheel_name(version), "Python wheel", root=root
        ),
        _sdist_name(version): _regular_file(
            python_dir / _sdist_name(version), "Python source distribution", root=root
        ),
    }
    for name in _OBSIDIAN_ASSETS:
        sources[name] = _regular_file(
            obsidian_root / name, "Obsidian release input", root=root
        )

    dist_dir = root / "dist"
    dist_dir.mkdir(parents=True, exist_ok=True)
    if dist_dir.is_symlink() or not dist_dir.is_dir():
        raise ReleaseArtifactError("dist must be a real directory")
    staging = Path(tempfile.mkdtemp(prefix=".release-", dir=dist_dir))
    release_dir = dist_dir / "release"
    try:
        for name, source in sources.items():
            shutil.copyfile(source, staging / name, follow_symlinks=False)
        _write_obsidian_bundle(staging / _bundle_name(version), obsidian_root, root)
        checksum_names = [name for name in _release_asset_names(version) if name != "SHA256SUMS"]
        _write_checksums(staging, checksum_names)
        _verify_release_dir(root, staging, version)
        if release_dir.is_symlink():
            raise ReleaseArtifactError("release directory must not be a symlink")
        if release_dir.exists():
            if not release_dir.is_dir():
                raise ReleaseArtifactError("release output must be a directory")
            shutil.rmtree(release_dir)
        os.replace(staging, release_dir)
    except BaseException:
        if staging.exists():
            shutil.rmtree(staging)
        raise
    verify_release(root)
    return release_dir


def _metadata_version(text: str, artifact: str) -> None:
    metadata = Parser().parsestr(text)
    if metadata.get("Name") != "acadine-virtuoso":
        raise ReleaseArtifactError(f"{artifact} has the wrong project name")
    if metadata.get("Version") != RELEASE_VERSION:
        raise ReleaseArtifactError(f"{artifact} has the wrong version")


def _unsafe_archive_name(name: str) -> bool:
    path = PurePosixPath(name)
    return (
        not name
        or "\\" in name
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or bool(re.match(r"^[A-Za-z]:", name))
    )


def _forbidden_archive_name(name: str) -> bool:
    parts = PurePosixPath(name).parts
    return ".buildos" in parts or ".project-meta.json" in parts


def _validate_archive_content(
    data: bytes, artifact: str, name: str, *, root: Path
) -> None:
    root_markers = {
        str(root.absolute()).encode("utf-8"),
        str(root.resolve()).encode("utf-8"),
    }
    if any(marker in data for marker in root_markers) or any(
        pattern.search(data) for pattern in _LOCAL_PATH_PATTERNS
    ):
        raise ReleaseArtifactError(f"forbidden {artifact} content: {name}")


def _validate_zip_members(archive: zipfile.ZipFile, artifact: str) -> None:
    seen: set[str] = set()
    for info in archive.infolist():
        mode = info.external_attr >> 16
        file_type = stat.S_IFMT(mode)
        if info.filename in seen or _unsafe_archive_name(info.filename):
            raise ReleaseArtifactError(f"unsafe {artifact} member: {info.filename}")
        if file_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
            raise ReleaseArtifactError(f"unsafe {artifact} member: {info.filename}")
        if (file_type == stat.S_IFDIR) != info.is_dir() and file_type != 0:
            raise ReleaseArtifactError(f"unsafe {artifact} member: {info.filename}")
        if _forbidden_archive_name(info.filename):
            raise ReleaseArtifactError(
                f"forbidden {artifact} content: {info.filename}"
            )
        seen.add(info.filename)


def _verify_wheel(path: Path, root: Path) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            _validate_zip_members(archive, "wheel")
            for info in archive.infolist():
                if not info.is_dir():
                    _validate_archive_content(
                        archive.read(info), "wheel", info.filename, root=root
                    )
            names = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
            if len(names) != 1:
                raise ReleaseArtifactError("wheel must contain one METADATA file")
            _metadata_version(archive.read(names[0]).decode("utf-8"), path.name)
    except (OSError, UnicodeDecodeError, zipfile.BadZipFile) as exc:
        raise ReleaseArtifactError("wheel is unreadable") from exc


def _validate_tar_members(archive: tarfile.TarFile, artifact: str) -> None:
    seen: set[str] = set()
    for member in archive.getmembers():
        if (
            member.name in seen
            or _unsafe_archive_name(member.name)
            or not (member.isfile() or member.isdir())
        ):
            raise ReleaseArtifactError(
                f"unsafe {artifact} member: {member.name}"
            )
        if _forbidden_archive_name(member.name):
            raise ReleaseArtifactError(
                f"forbidden {artifact} content: {member.name}"
            )
        seen.add(member.name)


def _verify_sdist(path: Path, root: Path) -> None:
    try:
        with tarfile.open(path, "r:gz") as archive:
            _validate_tar_members(archive, "source distribution")
            for member in archive.getmembers():
                if member.isfile():
                    member_file = archive.extractfile(member)
                    if member_file is None:
                        raise ReleaseArtifactError(
                            f"source distribution member is unreadable: {member.name}"
                        )
                    _validate_archive_content(
                        member_file.read(),
                        "source distribution",
                        member.name,
                        root=root,
                    )
            metadata_name = f"acadine_virtuoso-{RELEASE_VERSION}/PKG-INFO"
            members = [
                member for member in archive.getmembers() if member.name == metadata_name
            ]
            if len(members) != 1:
                raise ReleaseArtifactError(
                    "source distribution must contain one root PKG-INFO file"
                )
            handle = archive.extractfile(members[0])
            if handle is None:
                raise ReleaseArtifactError("source distribution PKG-INFO is unreadable")
            _metadata_version(handle.read().decode("utf-8"), path.name)
    except (OSError, UnicodeDecodeError, tarfile.TarError) as exc:
        raise ReleaseArtifactError("source distribution is unreadable") from exc


def _verify_obsidian_bundle(path: Path, release_dir: Path, root: Path) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            _validate_zip_members(archive, "Obsidian bundle")
            names = archive.namelist()
            if names != sorted(_OBSIDIAN_ASSETS):
                raise ReleaseArtifactError("Obsidian bundle has unexpected entries")
            for name in names:
                source = _regular_file(
                    release_dir / name, "release asset", root=root
                )
                data = archive.read(name)
                _validate_archive_content(
                    data, "Obsidian bundle", name, root=root
                )
                if data != source.read_bytes():
                    raise ReleaseArtifactError(
                        f"Obsidian bundle does not match source: {name}"
                    )
    except (OSError, zipfile.BadZipFile) as exc:
        raise ReleaseArtifactError("Obsidian bundle is unreadable") from exc


def _verify_checksums(release_dir: Path, checksum_path: Path, names: list[str]) -> None:
    expected_names = sorted(names)
    try:
        lines = checksum_path.read_text(encoding="ascii").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ReleaseArtifactError("SHA256SUMS is unreadable") from exc
    parsed: list[tuple[str, str]] = []
    for line in lines:
        match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9._-]+)", line)
        if match is None:
            raise ReleaseArtifactError("SHA256SUMS has an invalid line")
        parsed.append((match.group(1), match.group(2)))
    if [name for _, name in parsed] != expected_names:
        raise ReleaseArtifactError("SHA256SUMS does not list the exact release assets")
    for expected_hash, name in parsed:
        if _sha256(_regular_file(release_dir / name, "release asset")) != expected_hash:
            raise ReleaseArtifactError(f"checksum mismatch: {name}")


def _verify_release_dir(root: Path, release_dir: Path, version: str) -> Path:
    if release_dir.is_symlink() or not release_dir.is_dir():
        raise ReleaseArtifactError("release directory is missing or unsafe")
    expected_names = set(_release_asset_names(version))
    actual_names = {path.name for path in release_dir.iterdir()}
    if actual_names != expected_names:
        raise ReleaseArtifactError("release directory does not contain the exact asset set")
    for name in expected_names:
        _regular_file(release_dir / name, "release asset", root=root)

    checksum_names = sorted(expected_names - {"SHA256SUMS"})
    _verify_checksums(release_dir, release_dir / "SHA256SUMS", checksum_names)
    _verify_wheel(release_dir / _wheel_name(version), root)
    _verify_sdist(release_dir / _sdist_name(version), root)
    _verify_obsidian_bundle(release_dir / _bundle_name(version), release_dir, root)
    obsidian_root = root / "plugins" / "obsidian"
    for name in ("manifest.json", "versions.json"):
        release_asset = _regular_file(
            release_dir / name, "release asset", root=root
        )
        source_asset = _regular_file(
            obsidian_root / name,
            "Obsidian release input",
            root=root,
        )
        if release_asset.read_bytes() != source_asset.read_bytes():
            raise ReleaseArtifactError(f"release asset does not match source: {name}")
    built_main = obsidian_root / "main.js"
    if built_main.exists() or built_main.is_symlink():
        source_main = _regular_file(
            built_main, "Obsidian release input", root=root
        )
        if (release_dir / "main.js").read_bytes() != source_main.read_bytes():
            raise ReleaseArtifactError("release asset does not match source: main.js")
    return release_dir


def verify_release(root: Path = ROOT) -> Path:
    version = validate_source_versions(root)
    return _verify_release_dir(root, root / "dist" / "release", version)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build and verify fixed Virtuoso release assets")
    parser.add_argument("command", choices=("version", "assemble", "verify"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "version":
            print(validate_source_versions())
        elif args.command == "assemble":
            print(assemble_release().relative_to(ROOT))
        elif args.command == "verify":
            verify_release()
            print(f"verified {RELEASE_VERSION}")
        return 0
    except ReleaseArtifactError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
