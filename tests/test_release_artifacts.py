from __future__ import annotations

import importlib.util
import hashlib
import io
import json
import os
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "release_artifacts.py"
VERSION = "0.1.0"


def _load_release_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("virtuoso_release_artifacts", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write(path: Path, content: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, str):
        path.write_text(content, encoding="utf-8")
    else:
        path.write_bytes(content)


def _add_tar_file(archive: tarfile.TarFile, name: str, content: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(content)
    info.mode = 0o644
    info.mtime = 0
    archive.addfile(info, io.BytesIO(content))


def _release_fixture(root: Path) -> None:
    _write(
        root / "pyproject.toml",
        "[project]\nname = \"acadine-virtuoso\"\nversion = \"0.1.0\"\n",
    )
    _write(root / "product.json", '{"release":{"version":"0.1.0"}}\n')
    _write(root / "plugins/hermes/plugin.yaml", "name: virtuoso\nversion: 0.1.0\n")
    _write(
        root / "plugins/obsidian/manifest.json",
        json.dumps(
            {
                "id": "virtuoso",
                "name": "Virtuoso",
                "version": VERSION,
                "minAppVersion": "1.5.0",
            },
            separators=(",", ":"),
        )
        + "\n",
    )
    _write(
        root / "plugins/obsidian/package.json",
        '{"name":"virtuoso-obsidian","version":"0.1.0"}\n',
    )
    _write(
        root / "plugins/obsidian/package-lock.json",
        '{"name":"virtuoso-obsidian","version":"0.1.0",'
        '"packages":{"":{"version":"0.1.0"}}}\n',
    )
    _write(root / "plugins/obsidian/versions.json", '{"0.1.0":"1.5.0"}\n')
    _write(root / "plugins/obsidian/main.js", "module.exports = {};\n")

    wheel = root / "dist/python/acadine_virtuoso-0.1.0-py3-none-any.whl"
    wheel.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(wheel, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("virtuoso/__init__.py", '__version__ = "0.1.0"\n')
        archive.writestr(
            "acadine_virtuoso-0.1.0.dist-info/METADATA",
            "Metadata-Version: 2.4\nName: acadine-virtuoso\nVersion: 0.1.0\n",
        )

    sdist = root / "dist/python/acadine_virtuoso-0.1.0.tar.gz"
    with tarfile.open(sdist, "w:gz") as archive:
        prefix = "acadine_virtuoso-0.1.0"
        _add_tar_file(
            archive,
            f"{prefix}/PKG-INFO",
            b"Metadata-Version: 2.4\nName: acadine-virtuoso\nVersion: 0.1.0\n",
        )
        _add_tar_file(archive, f"{prefix}/README.md", b"# Fixture\n")


def _rewrite_checksums(release_dir: Path) -> None:
    names = sorted(path.name for path in release_dir.iterdir() if path.name != "SHA256SUMS")
    lines = [
        f"{hashlib.sha256((release_dir / name).read_bytes()).hexdigest()}  {name}\n"
        for name in names
    ]
    (release_dir / "SHA256SUMS").write_text("".join(lines), encoding="ascii")


class ReleaseArtifactContractTests(unittest.TestCase):
    def test_release_script_reports_the_validated_source_version(self) -> None:
        self.assertTrue(SCRIPT.is_file(), "release artifact script is missing")
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "version"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "0.1.0\n")
        self.assertEqual(result.stderr, "")

    def test_assemble_creates_and_verifies_the_exact_release_assets(self) -> None:
        release = _load_release_module()
        self.assertTrue(
            hasattr(release, "assemble_release"), "assemble_release is missing"
        )
        self.assertTrue(hasattr(release, "verify_release"), "verify_release is missing")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _release_fixture(root)
            release.assemble_release(root)
            release.verify_release(root)
            names = sorted(path.name for path in (root / "dist/release").iterdir())
        self.assertEqual(
            names,
            [
                "SHA256SUMS",
                "acadine_virtuoso-0.1.0-py3-none-any.whl",
                "acadine_virtuoso-0.1.0.tar.gz",
                "main.js",
                "manifest.json",
                "versions.json",
                "virtuoso-obsidian-0.1.0.zip",
            ],
        )

    def test_verify_rejects_a_wheel_with_path_traversal(self) -> None:
        release = _load_release_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _release_fixture(root)
            release.assemble_release(root)
            wheel = root / "dist/release/acadine_virtuoso-0.1.0-py3-none-any.whl"
            with zipfile.ZipFile(wheel, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("../escape.py", "raise RuntimeError\n")
                archive.writestr(
                    "acadine_virtuoso-0.1.0.dist-info/METADATA",
                    "Metadata-Version: 2.4\nName: acadine-virtuoso\nVersion: 0.1.0\n",
                )
            _rewrite_checksums(root / "dist/release")
            with self.assertRaisesRegex(
                release.ReleaseArtifactError, "unsafe wheel member"
            ):
                release.verify_release(root)

    def test_verify_rejects_a_special_file_entry_in_a_wheel(self) -> None:
        release = _load_release_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _release_fixture(root)
            release.assemble_release(root)
            wheel = root / "dist/release/acadine_virtuoso-0.1.0-py3-none-any.whl"
            with zipfile.ZipFile(wheel, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                special = zipfile.ZipInfo("virtuoso/channel")
                special.create_system = 3
                special.external_attr = (stat.S_IFIFO | 0o644) << 16
                archive.writestr(special, b"")
                archive.writestr(
                    "acadine_virtuoso-0.1.0.dist-info/METADATA",
                    "Metadata-Version: 2.4\nName: acadine-virtuoso\nVersion: 0.1.0\n",
                )
            _rewrite_checksums(root / "dist/release")
            with self.assertRaisesRegex(
                release.ReleaseArtifactError, "unsafe wheel member"
            ):
                release.verify_release(root)

    def test_verify_rejects_a_source_distribution_symlink(self) -> None:
        release = _load_release_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _release_fixture(root)
            release.assemble_release(root)
            sdist = root / "dist/release/acadine_virtuoso-0.1.0.tar.gz"
            with tarfile.open(sdist, "w:gz") as archive:
                prefix = "acadine_virtuoso-0.1.0"
                _add_tar_file(
                    archive,
                    f"{prefix}/PKG-INFO",
                    b"Metadata-Version: 2.4\nName: acadine-virtuoso\nVersion: 0.1.0\n",
                )
                link = tarfile.TarInfo(f"{prefix}/escape")
                link.type = tarfile.SYMTYPE
                link.linkname = "../../outside"
                archive.addfile(link)
            _rewrite_checksums(root / "dist/release")
            with self.assertRaisesRegex(
                release.ReleaseArtifactError, "unsafe source distribution member"
            ):
                release.verify_release(root)

    def test_verify_rejects_private_adapter_files_in_a_wheel(self) -> None:
        release = _load_release_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _release_fixture(root)
            release.assemble_release(root)
            wheel = root / "dist/release/acadine_virtuoso-0.1.0-py3-none-any.whl"
            with zipfile.ZipFile(wheel, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("project/.buildos/receipt.json", "{}\n")
                archive.writestr(
                    "acadine_virtuoso-0.1.0.dist-info/METADATA",
                    "Metadata-Version: 2.4\nName: acadine-virtuoso\nVersion: 0.1.0\n",
                )
            _rewrite_checksums(root / "dist/release")
            with self.assertRaisesRegex(
                release.ReleaseArtifactError, "forbidden wheel content"
            ):
                release.verify_release(root)

    def test_verify_rejects_absolute_maintainer_paths_in_sdist(self) -> None:
        release = _load_release_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _release_fixture(root)
            release.assemble_release(root)
            sdist = root / "dist/release/acadine_virtuoso-0.1.0.tar.gz"
            with tarfile.open(sdist, "w:gz") as archive:
                prefix = "acadine_virtuoso-0.1.0"
                _add_tar_file(
                    archive,
                    f"{prefix}/PKG-INFO",
                    b"Metadata-Version: 2.4\nName: acadine-virtuoso\nVersion: 0.1.0\n",
                )
                private_path = b"/" + b"Users/maintainer/private-worktree\n"
                _add_tar_file(archive, f"{prefix}/README.md", private_path)
            _rewrite_checksums(root / "dist/release")
            with self.assertRaisesRegex(
                release.ReleaseArtifactError,
                "forbidden source distribution content",
            ):
                release.verify_release(root)

    def test_verify_rejects_linux_maintainer_paths_in_sdist(self) -> None:
        release = _load_release_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _release_fixture(root)
            release.assemble_release(root)
            sdist = root / "dist/release/acadine_virtuoso-0.1.0.tar.gz"
            with tarfile.open(sdist, "w:gz") as archive:
                prefix = "acadine_virtuoso-0.1.0"
                _add_tar_file(
                    archive,
                    f"{prefix}/PKG-INFO",
                    b"Metadata-Version: 2.4\nName: acadine-virtuoso\nVersion: 0.1.0\n",
                )
                private_path = b"/" + b"home/maintainer/private-worktree\n"
                _add_tar_file(archive, f"{prefix}/README.md", private_path)
            _rewrite_checksums(root / "dist/release")
            with self.assertRaisesRegex(
                release.ReleaseArtifactError,
                "forbidden source distribution content",
            ):
                release.verify_release(root)

    def test_verify_rejects_windows_maintainer_paths_in_a_wheel(self) -> None:
        release = _load_release_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _release_fixture(root)
            release.assemble_release(root)
            wheel = root / "dist/release/acadine_virtuoso-0.1.0-py3-none-any.whl"
            with zipfile.ZipFile(wheel, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                private_path = b"C:" + b"\\Users\\maintainer\\repo\\module.py\n"
                archive.writestr("virtuoso/module.py", private_path)
                archive.writestr(
                    "acadine_virtuoso-0.1.0.dist-info/METADATA",
                    "Metadata-Version: 2.4\nName: acadine-virtuoso\nVersion: 0.1.0\n",
                )
            _rewrite_checksums(root / "dist/release")
            with self.assertRaisesRegex(
                release.ReleaseArtifactError, "forbidden wheel content"
            ):
                release.verify_release(root)

    def test_verify_rejects_the_exact_build_root_in_artifact_content(self) -> None:
        release = _load_release_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _release_fixture(root)
            release.assemble_release(root)
            wheel = root / "dist/release/acadine_virtuoso-0.1.0-py3-none-any.whl"
            with zipfile.ZipFile(wheel, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(
                    "virtuoso/build_path.txt",
                    str(root).encode("utf-8") + b"/src/virtuoso\n",
                )
                archive.writestr(
                    "acadine_virtuoso-0.1.0.dist-info/METADATA",
                    "Metadata-Version: 2.4\nName: acadine-virtuoso\nVersion: 0.1.0\n",
                )
            _rewrite_checksums(root / "dist/release")
            with self.assertRaisesRegex(
                release.ReleaseArtifactError, "forbidden wheel content"
            ):
                release.verify_release(root)

    def test_verify_rejects_a_symlink_entry_in_the_obsidian_bundle(self) -> None:
        release = _load_release_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _release_fixture(root)
            release.assemble_release(root)
            bundle = root / "dist/release/virtuoso-obsidian-0.1.0.zip"
            obsidian = root / "plugins/obsidian"
            with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for name in sorted(("main.js", "manifest.json", "versions.json")):
                    info = zipfile.ZipInfo(name)
                    info.create_system = 3
                    if name == "main.js":
                        info.external_attr = (stat.S_IFLNK | 0o777) << 16
                    else:
                        info.external_attr = (stat.S_IFREG | 0o644) << 16
                    archive.writestr(info, (obsidian / name).read_bytes())
            _rewrite_checksums(root / "dist/release")
            with self.assertRaisesRegex(
                release.ReleaseArtifactError, "unsafe Obsidian bundle member"
            ):
                release.verify_release(root)

    def test_assemble_rejects_local_paths_in_obsidian_output(self) -> None:
        release = _load_release_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _release_fixture(root)
            private_path = b"/" + b"Users/maintainer/private-plugin.js\n"
            _write(root / "plugins/obsidian/main.js", private_path)
            with self.assertRaisesRegex(
                release.ReleaseArtifactError, "forbidden Obsidian bundle content"
            ):
                release.assemble_release(root)

    def test_obsidian_bundle_is_deterministic_across_source_mtime_changes(self) -> None:
        release = _load_release_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _release_fixture(root)
            release.assemble_release(root)
            bundle = root / "dist/release/virtuoso-obsidian-0.1.0.zip"
            first = bundle.read_bytes()
            for name in ("main.js", "manifest.json", "versions.json"):
                os.utime(
                    root / "plugins/obsidian" / name,
                    (1_700_000_000, 1_700_000_000),
                )
            release.assemble_release(root)
            second = bundle.read_bytes()
        self.assertEqual(first, second)

    def test_verify_accepts_downloaded_assets_without_untracked_build_output(self) -> None:
        release = _load_release_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _release_fixture(root)
            release.assemble_release(root)
            (root / "plugins/obsidian/main.js").unlink()
            release.verify_release(root)

    def test_source_version_mismatch_fails_closed(self) -> None:
        release = _load_release_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _release_fixture(root)
            _write(root / "product.json", '{"release":{"version":"0.1.1"}}\n')
            with self.assertRaisesRegex(
                release.ReleaseArtifactError, "release version mismatch"
            ):
                release.validate_source_versions(root)

    def test_verify_rejects_a_tampered_release_asset(self) -> None:
        release = _load_release_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _release_fixture(root)
            release.assemble_release(root)
            target = root / "dist/release/main.js"
            target.write_bytes(target.read_bytes() + b"// tampered\n")
            with self.assertRaisesRegex(
                release.ReleaseArtifactError, "checksum mismatch"
            ):
                release.verify_release(root)

    def test_cli_rejects_a_caller_selected_output_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            requested = Path(tmp) / "chosen-output"
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "assemble",
                    "--output",
                    str(requested),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 2)
            self.assertFalse(requested.exists())

    def test_verify_uses_the_root_sdist_metadata_when_egg_info_is_present(self) -> None:
        release = _load_release_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _release_fixture(root)
            release.assemble_release(root)
            sdist = root / "dist/release/acadine_virtuoso-0.1.0.tar.gz"
            metadata = (
                b"Metadata-Version: 2.4\n"
                b"Name: acadine-virtuoso\n"
                b"Version: 0.1.0\n"
            )
            with tarfile.open(sdist, "w:gz") as archive:
                prefix = "acadine_virtuoso-0.1.0"
                _add_tar_file(archive, f"{prefix}/PKG-INFO", metadata)
                _add_tar_file(
                    archive,
                    f"{prefix}/src/acadine_virtuoso.egg-info/PKG-INFO",
                    metadata,
                )
            _rewrite_checksums(root / "dist/release")
            release.verify_release(root)

    def test_assemble_rejects_a_symlinked_source_directory(self) -> None:
        release = _load_release_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _release_fixture(root)
            obsidian = root / "plugins/obsidian"
            real_obsidian = root / "plugins/obsidian-real"
            obsidian.rename(real_obsidian)
            obsidian.symlink_to(real_obsidian, target_is_directory=True)
            (real_obsidian / "manifest.json").write_text("not-json\n", encoding="utf-8")
            with self.assertRaisesRegex(
                release.ReleaseArtifactError, "symlink component"
            ):
                release.assemble_release(root)

    def test_failed_assembly_preserves_the_previous_verified_release(self) -> None:
        release = _load_release_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _release_fixture(root)
            release.assemble_release(root)
            release_dir = root / "dist/release"
            before = {path.name: path.read_bytes() for path in release_dir.iterdir()}

            wheel = root / "dist/python/acadine_virtuoso-0.1.0-py3-none-any.whl"
            with zipfile.ZipFile(wheel, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(
                    "acadine_virtuoso-0.1.0.dist-info/METADATA",
                    "Metadata-Version: 2.4\nName: acadine-virtuoso\nVersion: 9.9.9\n",
                )
            with self.assertRaisesRegex(release.ReleaseArtifactError, "wrong version"):
                release.assemble_release(root)

            after = {path.name: path.read_bytes() for path in release_dir.iterdir()}
            self.assertEqual(after, before)
            release.verify_release(root)

    def test_verify_rejects_a_symlinked_release_parent(self) -> None:
        release = _load_release_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _release_fixture(root)
            release.assemble_release(root)
            dist = root / "dist"
            real_dist = root / "dist-real"
            dist.rename(real_dist)
            dist.symlink_to(real_dist, target_is_directory=True)
            with self.assertRaisesRegex(
                release.ReleaseArtifactError, "symlink component"
            ):
                release.verify_release(root)


if __name__ == "__main__":
    unittest.main()
