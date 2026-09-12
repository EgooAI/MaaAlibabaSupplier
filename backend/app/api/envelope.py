from __future__ import annotations

from typing import Any


def ok(data: Any = None) -> dict:
    return {"code": 0, "msg": "ok", "data": data}


def err(message: str, code: int = 1) -> dict:
    return {"code": code, "msg": message, "data": None}
