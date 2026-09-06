"""Install the built distributions and exercise the CLI outside the checkout.

Only synthetic temporary workspaces are used. Environment preparation downloads
hash-locked dependencies. Artifact installation disables dependency resolution.
This is an installation test for trusted build artifacts, not a code sandbox.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def check_distributions(dist_dir: Path, requirements: Path) -> None:
    wheels = sorted(dist_dir.glob("*.whl"))
    sources = sorted(dist_dir.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sources) != 1:
        raise ValueError("expected one wheel and one source distribution")
    if not requirements.is_file():
        raise ValueError("missing hash-locked installation requirements")
    uv = shutil.which("uv")
    if uv is None:
        raise ValueError("uv is required; see ci/bootstrap.txt")
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    expected_names = {
        f"acadine_virtuoso-{version}-py3-none-any.whl",
        f"acadine_virtuoso-{version}.tar.gz",
    }
    artifacts = wheels + sources
    if {path.name for path in artifacts} != expected_names:
        raise ValueError("distribution names do not match the project version")
    if any(path.is_symlink() or not path.is_file() for path in artifacts):
        raise ValueError("distributions must be regular files")

    for artifact in artifacts:
        with tempfile.TemporaryDirectory(prefix="virtuoso-install-") as temporary:
            work = Path(temporary).resolve()
            env = {
                key: value for key, value in os.environ.items()
                if not key.startswith(("PYTHON", "UV_")) and key != "VIRTUAL_ENV"
            }
            env.update({
                "HOME": str(work), "UV_NO_CONFIG": "1",
                "UV_CACHE_DIR": str(work / "cache"),
                "UV_PYTHON_DOWNLOADS": "never", "PYTHONNOUSERSITE": "1",
            })

            def run(*args: str, as_json: bool = False):
                result = subprocess.run(
                    list(args), cwd=work, env=env, text=True,
                    capture_output=True, timeout=120, check=False,
                )
                if result.returncode:
                    raise ValueError(
                        f"{Path(args[0]).name} exited {result.returncode}: "
                        f"{result.stdout}\n{result.stderr}"
                    )
                return json.loads(result.stdout) if as_json else result.stdout.strip()

            venv = work / "venv"
            run(uv, "venv", "--python", sys.executable, str(venv))
            binary = venv / ("Scripts" if os.name == "nt" else "bin")
            python = str(binary / ("python.exe" if os.name == "nt" else "python"))
            cli = str(binary / ("virtuoso.exe" if os.name == "nt" else "virtuoso"))
            run(uv, "pip", "sync", "--python", python, "--require-hashes",
                "--only-binary", ":all:", str(requirements.resolve()))
            run(uv, "pip", "install", "--python", python, "--offline",
                "--no-index", "--no-deps", "--no-build-isolation", str(artifact.resolve()))
            run(uv, "pip", "check", "--python", python)
            # -I prevents an ambient source tree or user site from providing imports.
            installed = run(python, "-I", "-c", (
                "import json, pathlib, sys, virtuoso; "
                "from importlib.metadata import version; "
                "p=pathlib.Path(virtuoso.__file__).resolve(); "
                "print(json.dumps({'isolated': p.is_relative_to(pathlib.Path(sys.prefix).resolve()), "
                "'typed': p.with_name('py.typed').is_file(), "
                "'version': version('acadine-virtuoso')}))"
            ), as_json=True)
            if installed != {"isolated": True, "typed": True, "version": version}:
                raise ValueError("installed package has wrong origin, data files or version")
            if version not in run(cli, "--version"):
                raise ValueError("installed CLI has the wrong version")
            run(cli, "--help")
            workspace = str(work / "learner")

            def command(*args: str):
                return run(cli, "--workspace", workspace, *args, "--json", as_json=True)

            if command("init")["status"] != "initialized":
                raise ValueError("installed CLI did not initialize a workspace")
            command("add", "--id", "install-check", "--title", "Installation fixture",
                    "--focus", "testing", "--prompt", "What does a package contain?",
                    "--answer", "Code and declared resources.")
            selected = command("next")
            if selected["item_id"] != "install-check" or selected["action"] != "practice":
                raise ValueError("installed CLI selected the wrong practice item")
            command("practice", "--item", "install-check", "--administer",
                    "--response", "Code and declared resources.", "--result", "demonstrated",
                    "--confidence", "4")
            attempts = command("attempts")
            if len(attempts["attempts"]) != 1 or attempts["attempts"][0]["administered"] != 1:
                raise ValueError("installed CLI lost the administered attempt")
            if len(attempts["proposals"]) != 1 or attempts["proposals"][0]["algorithm"] != "fsrs":
                raise ValueError("installed CLI did not run its scheduler dependency")
            doctor = command("doctor")
            if doctor["status"] != "healthy" or doctor["attempts"] != 1:
                raise ValueError("installed workspace is unhealthy")
            print(f"PASS {artifact.name}: isolated install, practice and doctor")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist-dir", type=Path, default=ROOT / "dist/python")
    parser.add_argument("--requirements", type=Path, default=ROOT / "dist/install-requirements.txt")
    args = parser.parse_args()
    try:
        check_distributions(args.dist_dir.resolve(), args.requirements.resolve())
    except (ValueError, KeyError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"installation check failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
