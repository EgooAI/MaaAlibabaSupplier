"""Stage 4: download a portable Yak CLI and (re)compile the MITM script.

Default: download the latest yak_windows_amd64.exe from yaklang/yaklang
(sha256-verified) into .portable/yak. Compilation is opt-in because the
compiled backend/yak_mitm.yakc is tracked in git and yakc output is not
deterministic; use --compile to refresh it explicitly.

Usage:
  python tools/install_4_yak.py [--compile]
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
from pathlib import Path

import _common as common

YAK_ASSET = "yak_windows_amd64.exe"
YAK_SHA_ASSET = f"{YAK_ASSET}.sha256.txt"
YAK_SCRIPT = common.BACKEND_DIR / "yak_mitm.yak"
YAK_COMPILED = common.BACKEND_DIR / "yak_mitm.yakc"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compile", action="store_true", help="Recompile backend/yak_mitm.yakc from the tracked source")
    return parser.parse_args()


def install_cli() -> Path:
    yak_dir = common.PORTABLE_DIR / "yak"
    yak_exe = yak_dir / "yak.exe"
    if yak_exe.exists():
        common.log(f"Reusing existing Yak CLI: {yak_exe}")
        return yak_exe

    release = common.github_latest_release(common.YAK_REPO)
    with common.temp_directory() as temp_dir:
        archive = common.download_release_asset(release, f"^{re.escape(YAK_ASSET)}$", temp_dir / YAK_ASSET)
        shasums = common.download_release_asset(release, f"^{re.escape(YAK_SHA_ASSET)}$", temp_dir / YAK_SHA_ASSET)
        expected = shasums.read_text(encoding="utf-8").split()[0]
        actual = hashlib.sha256(archive.read_bytes()).hexdigest()
        if actual != expected:
            common.fail(f"Yak CLI sha256 mismatch: expected {expected}, got {actual}")
        common.log("Yak CLI sha256 verified")
        yak_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(archive), str(yak_exe))
    common.log(f"Yak CLI {release.get('tag_name')} installed to {yak_exe}")
    return yak_exe


def compile_script(yak_exe: Path) -> None:
    if not YAK_SCRIPT.exists():
        common.fail(f"MITM script not found: {YAK_SCRIPT}")
    common.run([str(yak_exe), "compile", "-o", str(YAK_COMPILED), str(YAK_SCRIPT)])
    common.log(f"Compiled {YAK_COMPILED}")


def main() -> int:
    args = parse_args()
    common.log("=== Stage 4: Yak ===")
    yak_exe = install_cli()
    if args.compile:
        compile_script(yak_exe)
    common.log("Stage 4 done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
