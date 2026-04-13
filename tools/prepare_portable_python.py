from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
from pathlib import Path


PYTHON_BUILD_STANDALONE_REPOSITORY = "astral-sh/python-build-standalone"
GITHUB_API_BASE = "https://api.github.com/repos"

TARGETS = {
    ("win", "x86_64"): {
        "asset_pattern": r"^cpython-.*-x86_64-pc-windows-msvc(?:-shared)?-install_only\.tar\.gz$",
        "exec_candidates": ("python.exe",),
    },
    ("macos", "x86_64"): {
        "asset_pattern": r"^cpython-.*-x86_64-apple-darwin-install_only\.tar\.gz$",
        "exec_candidates": ("python3", "python"),
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download and extract a relocatable Python runtime for packaging."
    )
    parser.add_argument("target_os", choices=sorted({target[0] for target in TARGETS}))
    parser.add_argument("target_arch", choices=sorted({target[1] for target in TARGETS}))
    parser.add_argument("destination", type=Path)
    parser.add_argument("--github-output", type=Path)
    return parser.parse_args()


def github_headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "MaaAlibabaSupplier-ci",
    }
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def fetch_latest_release(repository: str) -> dict:
    request = urllib.request.Request(
        f"{GITHUB_API_BASE}/{repository}/releases/latest",
        headers=github_headers(),
    )
    with urllib.request.urlopen(request) as response:
        return json.load(response)


def download_file(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers=github_headers())
    with urllib.request.urlopen(request) as response, destination.open("wb") as target:
        shutil.copyfileobj(response, target)


def resolve_archive_asset(target_os: str, target_arch: str) -> dict:
    release = fetch_latest_release(PYTHON_BUILD_STANDALONE_REPOSITORY)
    target = TARGETS.get((target_os, target_arch))
    if target is None:
        supported = ", ".join(f"{os_name}/{arch}" for os_name, arch in sorted(TARGETS))
        raise ValueError(
            f"Unsupported Python packaging target: {target_os}/{target_arch}. Supported: {supported}"
        )

    asset_pattern = re.compile(target["asset_pattern"])
    for asset in release.get("assets", []):
        if asset_pattern.match(asset["name"]):
            return asset

    raise RuntimeError(
        f"Unable to find a Python archive for {target_os}/{target_arch} in the latest "
        f"{PYTHON_BUILD_STANDALONE_REPOSITORY} release."
    )


def find_normalized_root(extract_dir: Path) -> Path:
    current = extract_dir
    while True:
        entries = list(current.iterdir())
        directories = [entry for entry in entries if entry.is_dir()]
        files = [entry for entry in entries if entry.is_file()]
        if len(directories) != 1 or files:
            return current
        current = directories[0]


def copy_normalized_tree(source: Path, destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination, symlinks=True)


def locate_python_executable(destination: Path, candidates: tuple[str, ...]) -> Path:
    for candidate in candidates:
        matches = sorted(
            path for path in destination.rglob(candidate) if path.is_file()
        )
        if matches:
            return min(matches, key=lambda path: len(path.parts))

    available = "\n".join(str(path.relative_to(destination)) for path in destination.rglob("*"))
    raise RuntimeError(
        "Unable to locate the packaged Python executable. Extracted files:\n"
        f"{available}"
    )


def write_github_outputs(output_file: Path, outputs: dict[str, str]) -> None:
    with output_file.open("a", encoding="utf-8") as handle:
        for key, value in outputs.items():
            handle.write(f"{key}={value}\n")


def main() -> int:
    args = parse_args()
    target = TARGETS.get((args.target_os, args.target_arch))
    if target is None:
        print(f"Unsupported target: {args.target_os}/{args.target_arch}", file=sys.stderr)
        return 1

    try:
        asset = resolve_archive_asset(args.target_os, args.target_arch)
        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            archive_path = temp_dir / asset["name"]
            extract_dir = temp_dir / "extract"
            extract_dir.mkdir()

            print(f"Downloading {asset['name']} from {asset['browser_download_url']}")
            download_file(asset["browser_download_url"], archive_path)

            with tarfile.open(archive_path, "r:gz") as archive:
                archive.extractall(extract_dir)

            normalized_root = find_normalized_root(extract_dir)
            copy_normalized_tree(normalized_root, args.destination)

        python_executable = locate_python_executable(
            args.destination,
            target["exec_candidates"],
        )
        outputs = {
            "python_dir": str(args.destination.resolve()),
            "python_exec_path": str(python_executable.resolve()),
            "python_exec_relpath": python_executable.relative_to(args.destination).as_posix(),
        }

        if args.github_output is not None:
            write_github_outputs(args.github_output, outputs)

        print(json.dumps(outputs, indent=2))
        return 0
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, RuntimeError, ValueError) as exc:
        print(f"Failed to prepare portable Python: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
