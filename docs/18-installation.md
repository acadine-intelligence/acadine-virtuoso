# Installation and release checks

## Use Virtuoso

uv 0.10.10 is the tested package manager. Python 3.11.15 is the development pin. The package supports Python 3.11 or newer; CI tests installation on macOS and Linux with Python 3.11 through 3.14. Windows and later Python releases have no CI installation proof yet.

The README installs from the current source checkout with `uv tool install --python 3.11 .`. This is a normal, isolated tool installation. It copies the package into the tool environment. It does not require the source checkout when running the command. A developer uses `uv sync --locked` and `uv run --locked` in the checkout instead.

For an available GitHub Release, download its wheel and `SHA256SUMS`. Verify the wheel against the corresponding checksum before installation. Pass the local wheel path to `uv tool install --python 3.11`. The package name is `acadine-virtuoso`; its executable is `virtuoso`. Draft assets require repository access. A draft or an Actions artifact is not a public package-registry release.

Tool installation resolves the package metadata. It does not consume `uv.lock`. The current package pins its runtime dependencies explicitly. The lockfile also records the development and build environment. CI exports that lock to hash-checked installation requirements for its fresh-environment tests.

## Upgrade or roll back

Back up the user-selected workspace before changing versions, including its Markdown and a consistent SQLite backup. Keep the previous wheel or reviewed source revision.

To install an updated source checkout, review and update that checkout first. Run `uv tool install --force --python 3.11 .` there. To replace it with a verified wheel, pass the wheel path instead of `.`. Confirm `virtuoso --version`. Run `virtuoso --workspace PATH doctor --json` against the intended workspace after the version change.

The package installer changes the tool environment. It does not restore learner data or reverse SQLite migrations. Reinstalling an older package alone does not guarantee database compatibility. Restore a compatible workspace backup when a migration prevents rollback. CI's installation fixture does not certify every historical upgrade path.

For Obsidian, use the installed executable path (`command -v virtuoso` on macOS and Linux) in the plugin settings. Desktop apps may inherit a different `PATH` from a terminal.

## CI and draft releases

CI uses GitHub-owned Actions pinned to commit SHAs. `ci/bootstrap.txt` pins uv and approved wheel hashes, so the existing Actions allowlist needs no change. `uv.lock` supplies project dependencies and the build group. `uv build --no-build-isolation` uses the prepared locked backend. Build dependencies are separate from the package's runtime requirements.

The build job uploads a wheel, source distribution and exported installation requirements under a commit-scoped artifact name. Installation jobs download those exact bytes. Each distribution gets a separate environment and a temporary working directory outside the checkout. The check clears Python source overrides, verifies the installed import origin and `py.typed`, then records one synthetic administered attempt through the installed command. This is a functional installation check for trusted artifacts, not an OS sandbox.

The manual, main-only release workflow waits for the complete CI workflow. It downloads the tested distributions without rebuilding them, repeats their installation check, builds the Obsidian plugin and assembles the fixed release assets. The final job verifies transferred assets before creating a draft. Publication remains a maintainer action. This workflow does not upload to a package registry or update a user's installation.

The existing required status, `Python 3.11`, now waits for the Python checks, installation matrix and plugin checks. It fails when any dependency fails, skips or is cancelled. Its name is retained so the active repository rules still enforce the complete check. `Obsidian plugin` also retains its required status name. Repository rules remain unchanged.

Dependency updates belong in reviewed pull requests. Check current advisories as part of maintenance; a pinned version and a passing installation check do not prove that dependencies are free of vulnerabilities.
