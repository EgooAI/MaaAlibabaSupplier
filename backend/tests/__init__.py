"""Do not import test modules before the isolation plugin has configured pytest."""

import os

import pytest

if getattr(pytest, "_maa_backend_isolation_pid", None) != os.getpid():
    raise pytest.UsageError(
        "Backend test isolation is not active. Run from the repository root: "
        "python -m pytest -c pytest.ini"
    )
