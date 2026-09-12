"""Stage 1: prepare a portable Node.js runtime, install frontend dependencies and build.

Default: download the latest Node.js (minor-pinned, no overrides) into
.portable/node, install the pnpm version pinned by frontend/package.json, then
run `pnpm install` and `pnpm build` for frontend/.

Usage:
  python tools/install_1_frontend.py [--use-system] [--no-frozen]
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path

import _common as common

PNPM_CJS = Path("node_modules") / "pnpm" / "bin" / "pnpm.cjs"
NPM_CLI = Path("node_modules") / "npm" / "bin" / "npm-cli.js"
NODE_DIST_BASE = "https://nodejs.org/dist"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--use-system", action="store_true", help="Use node/pnpm from PATH instead of the portable runtime")
    parser.add_argument("--no-frozen", action="store_true", help="Install without --frozen-lockfile")
    return parser.parse_args()


def pnpm_version() -> str:
    manifest = json.loads((common.FRONTEND_DIR / "package.json").read_text(encoding="utf-8"))
    match = re.match(r"^pnpm@([0-9.]+)", manifest.get("packageManager", ""))
    if not match:
        common.fail("frontend/package.json does not pin a pnpm version via packageManager.")
    return match.group(1)


def resolve_node_archive() -> str:
    release_dir = f"latest-v{common.NODE_MAJOR}.x"
    shasums = common.fetch_text(f"{NODE_DIST_BASE}/{release_dir}/SHASUMS256.txt")
    for line in shasums.splitlines():
        match = re.search(r"(node-v[0-9.]+-win-x64\.zip)\s*$", line)
        if match:
            return match.group(1)
    common.fail(f"No node-v*-win-x64.zip found in {release_dir} SHASUMS.")


def prepare_portable() -> tuple[Path, Path]:
    node_dir = common.PORTABLE_DIR / "node"
    node_exe = node_dir / "node.exe"
    if not node_exe.exists():
        release_dir = "latest-v22.x"
        filename = resolve_node_archive()
        common.log(f"Resolved Node.js archive: {filename}")
        with common.temp_directory() as temp_dir:
            archive = common.download(f"{NODE_DIST_BASE}/{release_dir}/{filename}", temp_dir / filename)
            common.extract_zip(archive, temp_dir / "extract")
            common.install_tree(common.find_normalized_root(temp_dir / "extract"), node_dir)
    if not node_exe.exists():
        common.fail(f"Node runtime not found after install: {node_exe}")
    pnpm_cjs = node_dir / PNPM_CJS
    if not pnpm_cjs.exists():
        common.run(
            [node_exe, node_dir / NPM_CLI, "install", "-g", f"pnpm@{pnpm_version()}"],
        )
    if not pnpm_cjs.exists():
        common.fail(f"pnpm was not installed to {node_dir / PNPM_CJS}")
    return node_exe, node_dir / PNPM_CJS


def prepare_system() -> tuple[str, str]:
    node = shutil.which("node")
    pnpm = shutil.which("pnpm")
    if not node:
        common.fail("node not found on PATH; use the portable default or install Node.js.")
    if not pnpm:
        common.fail("pnpm not found on PATH; install it with `npm install -g pnpm` or drop --use-system.")
    return node, pnpm


def main() -> int:
    args = parse_args()
    common.log(f"=== Stage 1: frontend (use-system={args.use_system}) ===")

    if args.use_system:
        node, pnpm = prepare_system()
        pnpm_cmd = [pnpm]
    else:
        node, pnpm_cjs = prepare_portable()
        pnpm_cmd = [node, pnpm_cjs]

    install_cmd = pnpm_cmd + ["install"]
    if not args.no_frozen:
        install_cmd.append("--frozen-lockfile")
    common.run(install_cmd, cwd=common.FRONTEND_DIR)

    common.run(pnpm_cmd + ["build"], cwd=common.FRONTEND_DIR)

    common.log("Stage 1 done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
