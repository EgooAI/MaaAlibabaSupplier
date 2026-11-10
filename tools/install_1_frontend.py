"""Stage 1: prepare a portable Node.js runtime, install frontend dependencies and build.

Default: download the latest Node.js (minor-pinned, no overrides) into
.portable/node, install the pnpm version pinned by frontend/package.json, then
run `pnpm install` and `pnpm build` for frontend/. `--export` also embeds a build
identity that the UI uses to detect pages left open across an installation.

Usage:
  python tools/install_1_frontend.py [--use-system] [--no-frozen] [--export]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path

import _common as common

try:
    from . import generate_frontend_contract
except ImportError:
    import generate_frontend_contract

PNPM_CJS = Path("node_modules") / "pnpm" / "bin" / "pnpm.cjs"
NPM_CLI = Path("node_modules") / "npm" / "bin" / "npm-cli.js"
PNPM_STORE = common.PORTABLE_DIR / "pnpm-store"
NODE_RELEASE_DIR = f"latest-v{common.NODE_MAJOR}.x"
NODE_DIST_BASE = "https://nodejs.org/dist"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--use-system", action="store_true", help="Use node/pnpm from PATH instead of the portable runtime")
    parser.add_argument("--no-frozen", action="store_true", help="Install without --frozen-lockfile")
    parser.add_argument("--export", action="store_true", help="Build a static export into frontend/out (NEXT_EXPORT=1)")
    return parser.parse_args()


def pnpm_version() -> str:
    manifest = json.loads((common.FRONTEND_DIR / "package.json").read_text(encoding="utf-8"))
    match = re.match(r"^pnpm@([0-9.]+)", manifest.get("packageManager", ""))
    if not match:
        common.fail("frontend/package.json does not pin a pnpm version via packageManager.")
    return match.group(1)


def resolve_build_id() -> str:
    """Returns a stable identity for the exported frontend source tree."""
    try:
        tree = subprocess.check_output(
            ["git", "rev-parse", "HEAD:frontend"],
            cwd=common.REPO_ROOT,
            stderr=subprocess.DEVNULL,
        ).decode("ascii").strip()
        dirty = bool(subprocess.check_output(
            ["git", "status", "--porcelain", "--", "frontend"],
            cwd=common.REPO_ROOT,
            stderr=subprocess.DEVNULL,
        ).strip())
    except (OSError, subprocess.CalledProcessError, UnicodeDecodeError):
        return uuid.uuid4().hex
    if not tree:
        return uuid.uuid4().hex
    return f"{tree}-{uuid.uuid4().hex[:12]}" if dirty else tree


def write_build_id(out_dir: Path, build_id: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "build-id.json").write_text(
        json.dumps({"schema_version": 1, "build_id": build_id}, indent=2) + "\n",
        encoding="utf-8",
    )


def verify_build_id(out_dir: Path, build_id: str) -> None:
    for path in out_dir.rglob("*.js"):
        if build_id in path.read_text(encoding="utf-8", errors="ignore"):
            return
    common.fail("The frontend build identity was not embedded in the exported JavaScript.")


def resolve_node_archive() -> str:
    shasums = common.fetch_text(f"{NODE_DIST_BASE}/{NODE_RELEASE_DIR}/SHASUMS256.txt")
    for line in shasums.splitlines():
        match = re.search(r"(node-v[0-9.]+-win-x64\.zip)\s*$", line)
        if match:
            return match.group(1)
    common.fail(f"No node-v*-win-x64.zip found in {NODE_RELEASE_DIR} SHASUMS.")


def prepare_portable() -> tuple[Path, Path]:
    node_dir = common.PORTABLE_DIR / "node"
    node_exe = node_dir / "node.exe"
    if not node_exe.exists():
        filename = resolve_node_archive()
        common.log(f"Resolved Node.js archive: {filename}")
        with common.temp_directory() as temp_dir:
            archive = common.download(f"{NODE_DIST_BASE}/{NODE_RELEASE_DIR}/{filename}", temp_dir / filename)
            common.extract_zip(archive, temp_dir / "extract")
            common.install_tree(common.find_normalized_root(temp_dir / "extract"), node_dir)
    if not node_exe.exists():
        common.fail(f"Node runtime not found after install: {node_exe}")
    pnpm_cjs = node_dir / PNPM_CJS
    if not pnpm_cjs.exists():
        # Pin the global prefix explicitly: CI runners export npm_config_prefix,
        # which would otherwise pull the install out of the portable runtime.
        common.run(
            [node_exe, node_dir / NPM_CLI, "install", "-g", f"--prefix={node_dir}", f"pnpm@{pnpm_version()}"],
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

    install_cmd = pnpm_cmd + ["install", "--store-dir", str(PNPM_STORE)]
    if not args.no_frozen:
        install_cmd.append("--frozen-lockfile")
    common.run(install_cmd, cwd=common.FRONTEND_DIR)
    generate_frontend_contract.write()

    build_id = None
    build_id_path = common.FRONTEND_DIR / "out" / "build-id.json"
    if args.export:
        build_id = resolve_build_id()
        os.environ["NEXT_EXPORT"] = "1"
        os.environ["NEXT_PUBLIC_APP_BUILD"] = build_id
        build_id_path.unlink(missing_ok=True)
        common.log(f"Frontend build identity: {build_id}")
    common.run(pnpm_cmd + ["build"], cwd=common.FRONTEND_DIR)
    if build_id is not None:
        write_build_id(build_id_path.parent, build_id)
        verify_build_id(build_id_path.parent, build_id)

    common.log("Stage 1 done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
