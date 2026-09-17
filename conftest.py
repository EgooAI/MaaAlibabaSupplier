"""Reject collection when the backend isolation plugin was not configured."""

import os

import pytest


def pytest_sessionstart(session):
    if getattr(pytest, "_maa_backend_isolation_pid", None) != os.getpid():
        raise pytest.UsageError(
            "Backend test isolation is not active. Run from the repository root: "
            "python -m pytest -c pytest.ini"
        )
