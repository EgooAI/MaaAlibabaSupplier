from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse


def user_message(message: str) -> str:
    """Translate shared GUI guard messages at the product API boundary."""
    translations = {
        "Manual confirmation only, not automatic login identity verification. Account changes inside the same window cannot be detected; reconnect and confirm again.": "人工确认不代表系统已自动核验登录身份。同一窗口内切换账号无法自动识别，请重新连接并确认。",
        "Selected account manually confirmed.": "已人工确认当前所选账号。",
        "Manual account confirmation required.": "请人工确认客户端窗口中的登录账号。",
        "Client is not connected; connect and manually confirm the selected account.": "客户端尚未连接，请连接后人工确认所选账号。",
        "Client window changed; reconnect and manually confirm again.": "客户端窗口已变化，请重新连接并人工确认。",
        "Client connection lost; reconnect and manually confirm again.": "客户端连接已断开，请重新连接并人工确认。",
        "Client window is connected.": "客户端窗口已连接。",
        "Client connection failed: ": "客户端连接失败：",
        "Account context changed; reload and manually confirm again.": "账号已切换，请刷新后重新人工确认。",
        "Select a seller before confirming the client.": "请先选择卖家账号，再确认客户端。",
        "Client window changed during confirmation; reconnect and confirm again.": "确认期间客户端窗口已变化，请重新连接并确认。",
        "Manually confirm the selected account and client window first.": "请先人工确认所选账号与客户端窗口一致。",
        "GUI session expired; reconnect and manually confirm again.": "客户端操作授权已失效，请重新连接并人工确认。",
        "GUI writes require a manually confirmed session and run_guarded.": "客户端写入操作需要有效的人工确认授权，请重新连接并确认。",
        "IM source database is missing or not configured": "未配置聊天数据源或源数据库不存在",
    }
    for source, translated in translations.items():
        message = message.replace(source, translated)
    return message


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
