"""Run generic SDK tests without the host application or its pytest fixtures."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory

# Locally the suite finishes in ~15s; CI Windows runners run an order of
# magnitude slower (cold caches plus real-time scanning of every SQLite
# scratch file). The timeout only exists to bound a genuine hang, so keep it
# far above the slowest expected CI run.
SDK_TEST_TIMEOUT = 600


def main() -> int:
    source = Path(__file__).resolve().parents[1] / "backend/app/crm_sdk"
    with TemporaryDirectory(prefix="crm-sdk-standalone-") as temporary:
        root = Path(temporary)
        for package in ("core", "models", "utils", "agent_pipeline", "agent_tools", "tests"):
            for path in (source / package).rglob("*.py"):
                target = root / path.relative_to(source)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)
        shutil.copyfile(source / "pyproject.toml", root / "pyproject.toml")
        scratch = root / "temporary"
        scratch.mkdir()
        environment = dict(os.environ)
        for name in ("PYTHONPATH", "PYTEST_ADDOPTS", "PYTEST_PLUGINS"):
            environment.pop(name, None)
        environment.update(TEMP=str(scratch), TMP=str(scratch), PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
        # A runtime guard in this host-owned verifier detects accidental SDK
        # dependencies without putting host policy into the SDK itself.
        code = """
import importlib.abc
import pathlib
import sys

class RejectHost(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'backend' or fullname.startswith('backend.'):
            raise ImportError('Generic SDK must not import the host application')

sys.meta_path.insert(0, RejectHost())
sys.path.insert(0, str(pathlib.Path.cwd()))
import pytest
raise SystemExit(pytest.main(['-c', 'pyproject.toml', '--rootdir=.', '--confcutdir=.',
                             '--noconftest', '-p', 'no:cacheprovider', '-q', 'tests']))
"""
        try:
            completed = subprocess.run(
                [sys.executable, "-I", "-B", "-X", "utf8", "-c", code],
                cwd=root, env=environment, timeout=SDK_TEST_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            print(
                f"SDK standalone verification exceeded {SDK_TEST_TIMEOUT}s; child terminated.",
                file=sys.stderr,
            )
            return 1
        return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
