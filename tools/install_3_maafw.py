"""Stage 3: download MaaFramework binaries and update submodules.

Default: download the pinned MaaFramework win-x64 release, including MaaPiCli, into backend/deps
(where tools/install.py expects it) and `git submodule update --init
--recursive` for the OCR assets.

Usage:
  python tools/install_3_maafw.py
"""

from __future__ import annotations

import shutil
from pathlib import Path

import _common as common

MAAFW_ASSET_PATTERN = r"^MAA-win-x86_64.*\.zip$"


def install_cli_launcher(deps_dir: Path) -> None:
    """Places the CLI and its path-resolving library beside interface.json."""
    assets_dir = common.BACKEND_DIR / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    for name in ("MaaPiCli.exe", "MaaUtils.dll"):
        shutil.copy2(deps_dir / "bin" / name, assets_dir / name)


def install_maafw() -> None:
    deps_dir = common.BACKEND_DIR / "deps"
    version_file = deps_dir / "bin" / ".maafw-version"
    version = common.MAAFW_VERSION + "\n"
    if (
        (deps_dir / "bin" / "MaaPiCli.exe").exists()
        and version_file.is_file()
        and version_file.read_text(encoding="utf-8") == version
    ):
        install_cli_launcher(deps_dir)
        common.log(f"MaaFramework {common.MAAFW_VERSION} already present at {deps_dir}.")
        return
    release = common.github_release_by_tag(common.MAAFW_REPO, common.MAAFW_VERSION)
    deps_dir.mkdir(parents=True, exist_ok=True)
    # deps/tools holds tracked schema files; never clobber it.
    with common.temp_directory() as temp_dir:
        archive = common.download_release_asset(release, MAAFW_ASSET_PATTERN, temp_dir / "maafw.zip")
        extract_dir = temp_dir / "extract"
        extract_dir.mkdir()
        common.extract_zip(archive, extract_dir)
        framework_root = common.find_normalized_root(extract_dir)
        common.replace_children(framework_root, deps_dir, skip=("tools",))
    version_file.write_text(version, encoding="utf-8")
    install_cli_launcher(deps_dir)
    common.log(f"MaaFramework {common.MAAFW_VERSION} installed to {deps_dir}")


def main() -> int:
    common.log("=== Stage 3: MaaFramework ===")
    install_maafw()
    common.run(["git", "submodule", "update", "--init", "--recursive"], cwd=common.REPO_ROOT)
    common.log("Stage 3 done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
