from pathlib import Path

import os
import shutil
import sys

try:
    import jsonc
except ModuleNotFoundError as e:
    raise ImportError(
        "Missing dependency 'json-with-comments' (imported as 'jsonc').\n"
        f"Install it with:\n  {sys.executable} -m pip install json-with-comments\n"
        "Or add it to your project's requirements."
    ) from e

working_dir = Path(__file__).parent.parent.resolve()
install_path = working_dir / Path("install")
assets_dir = working_dir / "assets"
version = len(sys.argv) > 1 and sys.argv[1] or "v0.0.1"
bundled_python_dir = os.getenv("BUNDLED_PYTHON_DIR")
bundled_python_exec_relpath = os.getenv("BUNDLED_PYTHON_EXEC_RELPATH", "")

DOTNET_PLATFORM_TAG = "win-x64"


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
    if not (working_dir / "deps" / "bin").exists():
        print('Please download the MaaFramework to "deps" first.')
        print('请先下载 MaaFramework 到 "deps"。')
        sys.exit(1)

    shutil.copytree(
        working_dir / "deps" / "bin",
        install_path / "runtimes" / DOTNET_PLATFORM_TAG / "native",
        ignore=shutil.ignore_patterns(
            "*MaaDbgControlUnit*",
            "*MaaThriftControlUnit*",
            "*MaaRpc*",
            "*MaaHttp*",
            "plugins",
            "*.node",
            "*MaaPiCli*",
        ),
        dirs_exist_ok=True,
    )
    shutil.copytree(
        working_dir / "deps" / "share" / "MaaAgentBinary",
        install_path / "libs" / "MaaAgentBinary",
        dirs_exist_ok=True,
    )
    shutil.copytree(
        working_dir / "deps" / "bin" / "plugins",
        install_path / "plugins" / DOTNET_PLATFORM_TAG,
        dirs_exist_ok=True,
    )


def install_python_runtime():
    if not bundled_python_dir:
        print("No bundled Python runtime configured, skipping.")
        return

    python_dir = Path(bundled_python_dir).resolve()
    if not python_dir.exists():
        print(f"Bundled Python runtime not found: {python_dir}")
        sys.exit(1)

    shutil.copytree(
        python_dir,
        install_path / "python",
        dirs_exist_ok=True,
        symlinks=True,
    )


def get_bundled_python_exec():
    if not bundled_python_exec_relpath:
        return None

    normalized_relpath = bundled_python_exec_relpath.replace("\\", "/").lstrip("./")
    return f"./python/{normalized_relpath}"


def normalize_agent_args(child_args):
    if not isinstance(child_args, list):
        return child_args

    normalized_args = []
    for arg in child_args:
        if isinstance(arg, str) and arg.startswith("./../agent/"):
            normalized_args.append("./agent/" + arg.removeprefix("./../agent/"))
        else:
            normalized_args.append(arg)
    return normalized_args



def install_resource():
    configure_ocr_model()

    shutil.copytree(
        assets_dir / "resource",
        install_path / "resource",
        dirs_exist_ok=True,
    )
    shutil.copy2(
        assets_dir / "interface.json",
        install_path,
    )

    with open(install_path / "interface.json", "r", encoding="utf-8") as f:
        interface = jsonc.load(f)

    interface["version"] = version

    agent_config = interface.get("agent")
    if isinstance(agent_config, dict):
        agent_config["child_args"] = normalize_agent_args(agent_config.get("child_args"))

        packaged_python_exec = get_bundled_python_exec()
        if packaged_python_exec is not None:
            agent_config["child_exec"] = packaged_python_exec

    with open(install_path / "interface.json", "w", encoding="utf-8") as f:
        jsonc.dump(interface, f, ensure_ascii=False, indent=4)


def install_chores():
    shutil.copy2(
        working_dir / "README.md",
        install_path,
    )
    shutil.copy2(
        working_dir / "LICENSE",
        install_path,
    )


def install_agent():
    shutil.copytree(
        working_dir / "agent",
        install_path / "agent",
        dirs_exist_ok=True,
    )


if __name__ == "__main__":
    install_deps()
    install_python_runtime()
    install_resource()
    install_chores()
    install_agent()

    print(f"Install to {install_path} successfully.")
