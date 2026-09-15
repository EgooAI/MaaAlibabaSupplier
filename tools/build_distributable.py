"""Build the distributable Setup installer locally (Windows only).

Runs the full chain in one go:
  1. frontend export build        (tools/install_1_frontend.py --export)
  2. portable backend runtime     (tools/install_2_backend.py)
  3. MaaFramework download        (tools/install_3_maafw.py)
  4. Yak CLI + yakc recompile     (tools/install_4_yak.py --compile)
  5. payload assembly             (tools/install.py <version>)
  6. ISCC compile                 -> dist/MaaAlibabaSupplier-v<version>-Setup.exe

Usage:
  python tools/build_distributable.py [--version v0.0.0-local] [--skip-frontend]

`--skip-frontend` reuses an existing frontend/out export. ISCC is resolved from
ISCC_EXECUTABLE, then the default Inno Setup 6 install locations; when absent
the payload is still assembled under install/ but no installer is compiled.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys

import _common as common


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default="v0.0.0-local", help="Version tag embedded into payload and installer filename")
    parser.add_argument("--skip-frontend", action="store_true", help="Reuse the existing frontend/out export")
    return parser.parse_args()


def locate_iscc() -> str | None:
    from_env = os.getenv("ISCC_EXECUTABLE", "").strip()
    if from_env and os.path.isfile(from_env):
        return from_env

    roots = [
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Inno Setup 6"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Inno Setup 6"),
        os.path.expandvars(r"%ProgramFiles%\Inno Setup 6"),
    ]
    for root in roots:
        path = os.path.join(root, "ISCC.exe")
        if os.path.isfile(path):
            return path

    return shutil.which("ISCC")


def main() -> int:
    if sys.platform != "win32":
        common.fail("The distributable target is Windows-only (MaaPiCli.exe / yak.exe / Setup.exe).")

    args = parse_args()

    if not args.skip_frontend:
        common.run([sys.executable, str(common.TOOLS_DIR / "install_1_frontend.py"), "--export"], cwd=common.REPO_ROOT)
    else:
        common.log("Skipping frontend export build (reusing frontend/out).")

    common.run([sys.executable, str(common.TOOLS_DIR / "install_2_backend.py")], cwd=common.REPO_ROOT)
    common.run([sys.executable, str(common.TOOLS_DIR / "install_3_maafw.py")], cwd=common.REPO_ROOT)
    common.run([sys.executable, str(common.TOOLS_DIR / "install_4_yak.py"), "--compile"], cwd=common.REPO_ROOT)

    # Bundled-python flags mirror what CI passes after install_2_backend.py.
    common.run(
        [
            sys.executable,
            str(common.TOOLS_DIR / "install.py"),
            args.version,
            "--bundled-python-dir",
            str(common.PORTABLE_DIR / "python"),
            "--bundled-python-exec-relpath",
            "python.exe",
        ],
        cwd=common.REPO_ROOT,
    )

    iscc = locate_iscc()
    if iscc is None:
        common.log("Inno Setup 6 (ISCC.exe) not found; payload assembled at install/ but no installer was compiled.")
        common.log("Install Inno Setup 6 or point ISCC_EXECUTABLE at ISCC.exe, then re-run this script.")
        return 0

    version = args.version.removeprefix("v")
    common.run([iscc, str(common.TOOLS_DIR / "packaging" / "Setup.iss"), f"/DMyAppVersion={version}"], cwd=common.REPO_ROOT)

    setup_exe = common.REPO_ROOT / "dist" / f"MaaAlibabaSupplier-v{version}-Setup.exe"
    common.log(f"Installer ready: {setup_exe}" if setup_exe.exists() else "ISCC finished but the installer was not found in dist/.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
