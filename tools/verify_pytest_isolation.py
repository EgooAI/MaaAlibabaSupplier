"""Verify pytest entry points in disposable repositories, never real test modules."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory


SOURCE = Path(__file__).resolve().parents[1]
INFRASTRUCTURE = (
    "pytest.ini",
    "conftest.py",
    "backend/conftest.py",
    "backend/tests/__init__.py",
    "backend/app/crm_sdk/pyproject.toml",
)
PROBE = '''from pathlib import Path
import os
import pytest
from backend.app.shared.utils import settings

COLLECTION_CWD = Path.cwd()
SOURCE_ROOT = Path(__file__).resolve().parents[DEPTH]
assert getattr(pytest, "_maa_backend_isolation_pid", None) == os.getpid()
assert settings.REPO_ROOT == COLLECTION_CWD
assert not COLLECTION_CWD.is_relative_to(SOURCE_ROOT)
assert settings.BACKEND_ROOT == COLLECTION_CWD / "backend"
assert Path(os.environ["MAA_CRM_DB_PATH"]).is_relative_to(settings.BACKEND_ROOT)
assert Path(os.environ["MAA_POOLS_DB_PATH"]).is_relative_to(settings.BACKEND_ROOT)
Path(__file__).with_suffix(".imported").touch()

def test_isolation_fixture(request):
    assert "isolated_backend" in request.fixturenames
    assert Path.cwd() != COLLECTION_CWD
    assert Path.cwd().is_relative_to(COLLECTION_CWD)
    assert settings.BACKEND_ROOT == Path.cwd() / "backend"
    from backend.app.shared.utils import env, im_db_decryptor
    from maa.toolkit import Toolkit
    import socket
    import subprocess
    assert env.load_workdir_env() == Path.cwd() / ".env"
    for call in (im_db_decryptor.retrieve_db_key, Toolkit.find_desktop_windows,
                 socket.create_connection, subprocess.Popen):
        with pytest.raises(pytest.fail.Exception, match="External access must be mocked"):
            call()
'''


def main():
    cases = (
        ("default paths", ".", [], True, False),
        ("combined original command", ".", ["backend/tests", "backend/app/crm_sdk/tests"], True, False),
        ("explicit root config", ".", ["-c", "pytest.ini", "backend/tests", "backend/app/crm_sdk/tests"], True, False),
        ("SDK from nested cwd", "backend/app/crm_sdk", ["tests"], True, False),
        ("nested config rejected", ".", ["-c", "backend/app/crm_sdk/pyproject.toml", "backend/tests", "backend/app/crm_sdk/tests"], False, False),
        ("host confcutdir rejected", ".", ["--confcutdir=backend/tests", "backend/tests"], False, False),
        ("no conftest rejected", ".", ["--noconftest"], False, False),
        ("no conftest importlib rejected", ".", ["--noconftest", "--import-mode=importlib"], False, False),
        ("missing backend conftest", ".", [], False, False),
        ("missing root config", ".", ["backend/tests", "backend/app/crm_sdk/tests"], False, False),
        ("fixture execution", ".", ["-c", "pytest.ini"], True, True),
    )
    child_env = dict(os.environ)
    for key in ("PYTEST_ADDOPTS", "PYTEST_PLUGINS", "PYTHONPATH"):
        child_env.pop(key, None)
    child_env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    child_env["PYTHONDONTWRITEBYTECODE"] = "1"

    for label, cwd, args, allowed, execute in cases:
        with TemporaryDirectory(prefix="maa-entry-proof-") as temporary:
            root = Path(temporary)
            for relative in INFRASTRUCTURE:
                if label == "missing backend conftest" and relative == "backend/conftest.py":
                    continue
                if label == "missing root config" and relative == "pytest.ini":
                    continue
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(SOURCE / relative, target)

            # No production modules, data, dotenv files or logs enter the sandbox.
            stubs = {
                "backend/__init__.py": "",
                "backend/app/__init__.py": "",
                "backend/app/crm_sdk/__init__.py": "",
                "backend/app/shared/__init__.py": "",
                "backend/app/shared/utils/__init__.py": "",
                "backend/app/shared/utils/settings.py": "REPO_ROOT = None\nBACKEND_ROOT = None\n",
                "backend/app/shared/utils/env.py": "def load_dotenv(*args, **kwargs):\n    raise AssertionError('dotenv accessed')\ndef load_workdir_env(*args, **kwargs):\n    raise AssertionError('dotenv accessed')\n",
                "backend/app/shared/utils/im_db_decryptor.py": "def retrieve_db_key(*args, **kwargs):\n    raise AssertionError('process accessed')\n",
                "dotenv.py": "def load_dotenv(*args, **kwargs):\n    raise AssertionError('dotenv accessed')\n",
                "maa/__init__.py": "",
                "maa/toolkit.py": "class Toolkit:\n    def find_desktop_windows(*args, **kwargs):\n        raise AssertionError('desktop accessed')\n",
                "backend/tests/test_entry_probe.py": PROBE.replace("[DEPTH]", "[2]"),
                "backend/app/crm_sdk/tests/test_sdk_probe.py": PROBE.replace("[DEPTH]", "[4]"),
            }
            for relative, content in stubs.items():
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")

            command = [sys.executable, "-X", "utf8", "-m", "pytest", "-p", "no:cacheprovider"]
            if not execute:
                command += ["--collect-only", "--trace-config"]
            result = subprocess.run(
                command + args, cwd=root / cwd, env=child_env,
                capture_output=True, text=True, encoding="utf-8", timeout=30,
            )
            output = result.stdout + result.stderr
            imported = list(root.rglob("*.imported"))
            if allowed:
                expected_imports = 1 if cwd != "." else 2
                valid = result.returncode == 0 and len(imported) == expected_imports
                valid = valid and f"rootdir: {root}" in output and "configfile: pytest.ini" in output
                if execute:
                    valid = valid and "2 passed" in output
                else:
                    valid = valid and str(root / "backend" / "conftest.py") in output
            else:
                valid = result.returncode != 0 and not imported and "Backend test" in output
            if not valid:
                raise AssertionError(f"{label}: exit={result.returncode}, imported={imported}\n{output}")
            print(f"PASS: {label} ({'isolated' if allowed else 'blocked before test import'})")


if __name__ == "__main__":
    main()
