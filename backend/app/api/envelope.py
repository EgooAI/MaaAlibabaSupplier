from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse


class AppError(Exception):
    def __init__(self, message: str, *, status_code: int = 500, code: int = 1) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code


def ok(data: Any = None) -> dict:
    return {"code": 0, "msg": "ok", "data": data}


def err(message: str, code: int = 1) -> dict:
    return {"code": code, "msg": message, "data": None}


def api_error(message: str, *, status_code: int = 500, code: int = 1) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=err(message, code))


def not_found(message: str = "会话不存在") -> JSONResponse:
    return api_error(message, status_code=404)


def bad_request(message: str) -> JSONResponse:
    return api_error(message, status_code=400)
