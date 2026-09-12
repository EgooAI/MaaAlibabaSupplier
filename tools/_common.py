"""Shared helpers for tools/install_*.py. Requires Python 3.12+.

Env surface (intentionally minimal, read ONLY in this file):
- GITHUB_TOKEN: optional; authenticates GitHub API requests for a higher rate limit.
No other environment variables are consumed by the install scripts. Version
selection is minor-pinned and there are deliberately no version overrides.

Packaging wiring (tools/install.py) reads BUNDLED_PYTHON_DIR /
BUNDLED_PYTHON_EXEC_RELPATH from the environment; those are written to
GITHUB_OUTPUT by install_2_backend.py and are never read here.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import NoReturn

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
FRONTEND_DIR = REPO_ROOT / "frontend"
TOOLS_DIR = REPO_ROOT / "tools"
PORTABLE_DIR = REPO_ROOT / ".portable"

# Version policy: minor-only pins, no overrides.
PYTHON_MINOR = "3.12"
NODE_MAJOR = "22"
MAAFW_VERSION = "v5.10.0"

PBS_REPO = "astral-sh/python-build-standalone"
YAK_REPO = "yaklang/yaklang"
MAAFW_REPO = "MaaXYZ/MaaFramework"
GITHUB_API = "https://api.github.com"


def log(message: str) -> None:
    print(message, flush=True)


def fail(message: str) -> NoReturn:
    raise SystemExit(f"[install] {message}")


@contextlib.contextmanager
def temp_directory() -> Iterator[Path]:
    with tempfile.TemporaryDirectory() as name:
        yield Path(name)


def github_token() -> str:
    return os.getenv("GITHUB_TOKEN", "").strip()


def _request(url: str, accept: str | None = None) -> urllib.request.Request:
    headers = {"User-Agent": "MaaAlibabaSupplier-installer"}
    token = github_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if accept:
        headers["Accept"] = accept
    return urllib.request.Request(url, headers=headers)


def fetch_json(url: str) -> dict:
    with urllib.request.urlopen(_request(url), timeout=120) as response:
        return json.load(response)


def fetch_text(url: str) -> str:
    with urllib.request.urlopen(_request(url), timeout=120) as response:
        return response.read().decode("utf-8")


def github_latest_release(repository: str) -> dict:
    return fetch_json(f"{GITHUB_API}/repos/{repository}/releases/latest")


def github_release_by_tag(repository: str, tag: str) -> dict:
    return fetch_json(f"{GITHUB_API}/repos/{repository}/releases/tags/{tag}")


def asset_by_pattern(release: dict, pattern: str) -> dict:
    regex = re.compile(pattern)
    for asset in release.get("assets", []):
        if regex.match(asset["name"]):
            return asset
    available = "\n".join(asset["name"] for asset in release.get("assets", []))
    fail(f"No asset matching {pattern!r} in release {release.get('tag_name')!r}.\nAssets:\n{available}")


def download(url: str, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    log(f"Downloading {url}")
    with urllib.request.urlopen(_request(url), timeout=300) as response, destination.open("wb") as target:
        shutil.copyfileobj(response, target)
    return destination


def extract_zip(archive: Path, destination: Path) -> None:
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(destination)


def extract_targz(archive: Path, destination: Path) -> None:
    with tarfile.open(archive, "r:gz") as bundle:
        bundle.extractall(destination, filter="data")


def find_normalized_root(extract_dir: Path) -> Path:
    current = extract_dir
    while True:
        entries = list(current.iterdir())
        directories = [entry for entry in entries if entry.is_dir()]
        files = [entry for entry in entries if entry.is_file()]
        if len(directories) != 1 or files:
            return current
        current = directories[0]


def install_tree(source: Path, destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination, symlinks=True)


def replace_children(source: Path, destination: Path, skip: tuple[str, ...] = ()) -> None:
    """Merge the top-level entries of `source` into `destination`.

    Existing entries with the same name are replaced; anything else under
    `destination` (e.g. tracked files like deps/tools) is preserved.
    """
    destination.mkdir(parents=True, exist_ok=True)
    for entry in source.iterdir():
        if entry.name in skip:
            log(f"Skipping {entry.name}")
            continue
        target = destination / entry.name
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
        shutil.move(str(entry), str(target))


def run(command: list[str], cwd: Path | None = None) -> None:
    log(f"Running: {' '.join(str(part) for part in command)}")
    result = subprocess.run(
        [str(part) for part in command],
        cwd=cwd,
    )
    if result.returncode != 0:
        fail(f"Command failed with exit code {result.returncode}: {command}")


def write_github_output(output_file: Path | None, outputs: dict[str, str]) -> None:
    if output_file is None:
        return
    with output_file.open("a", encoding="utf-8") as handle:
        for key, value in outputs.items():
            handle.write(f"{key}={value}\n")


def download_release_asset(release: dict, pattern: str, destination: Path) -> Path:
    asset = asset_by_pattern(release, pattern)
    log(f"Resolved asset {asset['name']} (release {release.get('tag_name')})")
    return download(asset["browser_download_url"], destination)
