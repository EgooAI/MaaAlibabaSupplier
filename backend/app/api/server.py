from __future__ import annotations

import os

import uvicorn

from backend.app.shared.utils.env import load_workdir_env


def run() -> None:
    load_workdir_env()
    host = os.environ.get("MAA_API_HOST", "127.0.0.1")
    port = int(os.environ.get("MAA_API_PORT", "8000"))
    uvicorn.run("backend.app.api.main:app", host=host, port=port, reload=False)
