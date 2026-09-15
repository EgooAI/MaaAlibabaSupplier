"""Assemble the distributable payload for Inno Setup packaging.

Payload layout mirrors the repository so every file-relative convention in the
backend keeps working (backend/deps/bin, backend/assets, backend/.portable/yak,
backend/yak_mitm.yak, frontend/out):

    install/
    ├── backend/
    │   ├── __init__.py + app/          # FastAPI backend and business agent
    │   ├── deps/bin/ + deps/share/     # MaaFramework binaries (incl. MaaPiCli)
    │   ├── assets/                     # MaaFW workdir: interface.json + resource
    │   ├── yak_mitm.yak (+ .yakc)
    │   ├── .portable/yak/yak.exe       # Yak CLI (matches main.py discovery)
    │   └── python/                     # bundled CPython with requirements
    ├── frontend/out/                   # exported frontend (NEXT_EXPORT=1)
    └── .env.example + README.md + LICENSE + Start-Debug.bat

Usage:
  python tools/install.py [VERSION] [--bundled-python-dir DIR]
                          [--bundled-python-exec-relpath RELPATH]

Both bundled-python flags are optional: without them the payload simply has no
bundled Python runtime (local dev) and no agent child_exec override.
"""

import argparse
import shutil
import sys
from pathlib import Path

import jsonc

DEFAULT_VERSION = "v0.0.0-local"

working_dir = (Path(__file__).parent.parent / "backend").resolve()
repo_root = working_dir.parent
install_path = repo_root / "install"
payload_backend = install_path / "backend"
assets_dir = working_dir / "assets"

IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("version", nargs="?", default=DEFAULT_VERSION, help="Version embedded into payload interface.json")
    parser.add_argument("--bundled-python-dir", type=Path, help="Bundled CPython runtime directory to copy into the payload")
    parser.add_argument("--bundled-python-exec-relpath", help="Python executable relpath inside the bundled runtime (drives agent child_exec)")
    return parser.parse_args()


def configure_ocr_model():
    assets_ocr_dir = assets_dir / "MaaCommonAssets" / "OCR"
    if not assets_ocr_dir.exists():
        print(f"File Not Found: {assets_ocr_dir}")
        sys.exit(1)

    ocr_dir = assets_dir / "resource" / "model" / "ocr"
    if not ocr_dir.exists():
        shutil.copytree(
            assets_ocr_dir / "ppocr_v5" / "zh_cn",
            ocr_dir,
            dirs_exist_ok=True,
        )
    else:
        print("Found existing OCR directory, skipping default OCR model import.")


def install_deps():
    deps_bin = working_dir / "deps" / "bin"
    if not deps_bin.exists():
        print('Please download the MaaFramework to "backend/deps" first (tools/install_3_maafw.py).')
        sys.exit(1)

    shutil.copytree(
        deps_bin,
        payload_backend / "deps" / "bin",
        ignore=shutil.ignore_patterns("__pycache__"),
        dirs_exist_ok=True,
    )
    shutil.copytree(
        working_dir / "deps" / "share" / "MaaAgentBinary",
        payload_backend / "deps" / "share" / "MaaAgentBinary",
        dirs_exist_ok=True,
    )


def install_app():
    shutil.copy2(working_dir / "__init__.py", payload_backend, follow_symlinks=True)
    shutil.copytree(
        working_dir / "app",
        payload_backend / "app",
        ignore=IGNORE,
        dirs_exist_ok=True,
    )


def install_python_runtime(bundled_python_dir: Path | None) -> None:
    if not bundled_python_dir:
        print("No bundled Python runtime configured, skipping.")
        return

    python_dir = bundled_python_dir.resolve()
    if not python_dir.exists():
        print(f"Bundled Python runtime not found: {python_dir}")
        sys.exit(1)

    shutil.copytree(
        python_dir,
        payload_backend / "python",
        dirs_exist_ok=True,
        symlinks=True,
    )


def install_yak():
    yak_script = working_dir / "yak_mitm.yak"
    if yak_script.exists():
        shutil.copy2(yak_script, payload_backend)
    yak_compiled = working_dir / "yak_mitm.yakc"
    if yak_compiled.exists():
        shutil.copy2(yak_compiled, payload_backend)

    yak_exe = repo_root / ".portable" / "yak" / "yak.exe"
    if yak_exe.exists():
        yak_target = payload_backend / ".portable" / "yak" / "yak.exe"
        yak_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(yak_exe, yak_target)
    else:
        print(f"Portable Yak CLI not found at {yak_exe}; MITM will be unavailable in the payload.")


def install_resource(version: str, bundled_python_exec_relpath: str | None) -> None:
    configure_ocr_model()

    shutil.copytree(
        assets_dir / "resource",
        payload_backend / "assets" / "resource",
        dirs_exist_ok=True,
    )
    shutil.copy2(
        assets_dir / "interface.json",
        payload_backend / "assets",
    )

    with open(payload_backend / "assets" / "interface.json", "r", encoding="utf-8") as f:
        interface = jsonc.load(f)

    interface["version"] = version

    agent_config = interface.get("agent")
    if isinstance(agent_config, dict) and bundled_python_exec_relpath:
        normalized_relpath = bundled_python_exec_relpath.replace("\\", "/").lstrip("./")
        # Resolved relative to the MaaFW workdir (backend/assets) inside the payload.
        agent_config["child_exec"] = f"./../python/{normalized_relpath}"

    with open(payload_backend / "assets" / "interface.json", "w", encoding="utf-8") as f:
        jsonc.dump(interface, f, ensure_ascii=False, indent=4)


def install_frontend():
    frontend_out = repo_root / "frontend" / "out"
    if not frontend_out.is_dir():
        print(f"Exported frontend not found at {frontend_out}; build it with NEXT_EXPORT=1 first.")
        sys.exit(1)
    shutil.copytree(
        frontend_out,
        install_path / "frontend" / "out",
        dirs_exist_ok=True,
    )


def install_chores():
    shutil.copy2(
        repo_root / "README.md",
        install_path,
    )
    shutil.copy2(
        repo_root / "LICENSE",
        install_path,
    )
    shutil.copy2(
        repo_root / ".env.example",
        install_path,
    )
    shutil.copy2(
        repo_root / "tools" / "packaging" / "Start-Debug.bat",
        install_path,
    )


def main():
    args = parse_args()
    payload_backend.mkdir(parents=True, exist_ok=True)
    install_deps()
    install_app()
    install_python_runtime(args.bundled_python_dir)
    install_yak()
    install_resource(args.version, args.bundled_python_exec_relpath)
    install_frontend()
    install_chores()
    print(f"Install to {install_path} successfully.")


if __name__ == "__main__":
    main()
