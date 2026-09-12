"""Stage 2: prepare a portable Python runtime and install backend dependencies.

Default: download the latest CPython 3.12 (minor-pinned, no overrides) from
python-build-standalone into .portable/python, then pip install
backend/app/requirements.txt into it.

Usage:
  python tools/install_2_backend.py [--into [PYTHON]] [--dev] [--github-output PATH]

Outputs (when --github-output is given, consumed by tools/install.py via CI env):
  python_dir, python_exec_path, python_exec_relpath
"""

from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path

import _common as common

PYTHON_ASSET_PATTERN = re.compile(
    rf"^cpython-{re.escape(common.PYTHON_MINOR)}\.[0-9]+\+[0-9]+-x86_64-pc-windows-msvc-install_only\.tar\.gz$"
)
REQUIREMENTS = common.BACKEND_DIR / "app" / "requirements.txt"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--into",
        nargs="?",
        const="PATH",
        default=None,
        help="Install into the given interpreter; bare --into uses python from PATH (default: portable runtime)",
    )
    parser.add_argument("--dev", action="store_true", help="Also install pytest")
    parser.add_argument("--github-output", type=Path, help="File to write CI outputs to")
    return parser.parse_args()


def prepare_portable() -> Path:
    python_dir = common.PORTABLE_DIR / "python"
    python_exe = python_dir / "python.exe"
    if not python_exe.exists():
        release = common.github_latest_release(common.PBS_REPO)
        asset = next((item for item in release.get("assets", []) if PYTHON_ASSET_PATTERN.match(item["name"])), None)
        if asset is None:
            common.fail(f"No cpython {common.PYTHON_MINOR}.x windows-msvc install_only archive in {common.PBS_REPO} latest release.")
        common.log(f"Resolved {asset['name']} (release {release.get('tag_name')})")
        with common.temp_directory() as temp_dir:
            archive = common.download(asset["browser_download_url"], temp_dir / asset["name"])
            common.extract_targz(archive, temp_dir / "extract")
            common.install_tree(common.find_normalized_root(temp_dir / "extract"), python_dir)
    if not python_exe.exists():
        common.fail(f"Python runtime not found after install: {python_exe}")
    # python-build-standalone distributions bundle pip.
    common.run([str(python_exe), "-m", "pip", "install", "--upgrade", "--no-cache-dir", "pip"])
    return python_exe


def main() -> int:
    args = parse_args()
    common.log(f"=== Stage 2: backend (into={args.into}) ===")

    if args.into is None:
        python_exe = prepare_portable().resolve()
    elif args.into == "PATH":
        found = shutil.which("python")
        if not found:
            common.fail("python not found on PATH; use the portable default instead.")
        python_exe = Path(found)
    else:
        python_exe = args.into.resolve()
        if not python_exe.exists():
            common.fail(f"Interpreter does not exist: {python_exe}")

    packages = ["-r", str(REQUIREMENTS)]
    if args.dev:
        packages.append("pytest")
    common.run([str(python_exe), "-m", "pip", "install", "--upgrade", "--no-cache-dir", *packages])

    resolved = python_exe.resolve()
    outputs = {
        "python_dir": str(resolved.parent),
        "python_exec_path": str(resolved),
    }
    portable_root = (common.PORTABLE_DIR / "python").resolve()
    if resolved.is_relative_to(portable_root):
        outputs["python_exec_relpath"] = resolved.relative_to(portable_root).as_posix()
    common.write_github_output(args.github_output, outputs)
    common.log("Stage 2 done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
