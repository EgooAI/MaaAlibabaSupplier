"""Stage 3: download MaaFramework binaries and update submodules.

Default: download the pinned MaaFramework win-x64 release into backend/deps
(where tools/install.py expects it) and `git submodule update --init
--recursive` for the OCR assets.

Usage:
  python tools/install_3_maafw.py
"""

from __future__ import annotations

import _common as common

MAAFW_ASSET_PATTERN = r"^MAA-win-x86_64.*\.zip$"


def install_maafw() -> None:
    deps_dir = common.BACKEND_DIR / "deps"
    if (deps_dir / "bin" / "MaaPiCli.exe").exists():
        common.log(f"MaaFramework already present at {deps_dir}; delete {deps_dir / 'bin'} to force a re-download.")
        return
    release = common.github_release_by_tag(common.MAAFW_REPO, common.MAAFW_VERSION)
    deps_dir.mkdir(parents=True, exist_ok=True)
    # deps/tools holds tracked schema files; never clobber it.
    with common.temp_directory() as temp_dir:
        archive = common.download_release_asset(release, MAAFW_ASSET_PATTERN, temp_dir / "maafw.zip")
        extract_dir = temp_dir / "extract"
        extract_dir.mkdir()
        common.extract_zip(archive, extract_dir)
        common.replace_children(common.find_normalized_root(extract_dir), deps_dir, skip=("tools",))
    common.log(f"MaaFramework {common.MAAFW_VERSION} installed to {deps_dir}")


def main() -> int:
    common.log("=== Stage 3: MaaFramework ===")
    install_maafw()
    common.run(["git", "submodule", "update", "--init", "--recursive"], cwd=common.REPO_ROOT)
    common.log("Stage 3 done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
