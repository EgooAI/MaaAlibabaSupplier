from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse


def user_message(message: str) -> str:
    """Translate shared GUI guard messages at the product API boundary."""
    translations = {
        "Client is not connected; connect first.": "客户端尚未连接，请先连接客户端。",
        "Client window changed; reconnect and retry.": "客户端窗口已变化，请重新连接后重试。",
        "Client connection lost; reconnect and retry.": "客户端连接已断开，请重新连接后重试。",
        "Client window is connected.": "客户端窗口已连接。",
        "Client connection failed: ": "客户端连接失败：",
        "Account context changed; reload and retry.": "账号已切换，请刷新后重试。",
        "Select a seller before operating the client.": "请先选择卖家账号，再操作客户端。",
        "GUI session expired; reconnect and retry.": "客户端操作会话已失效，请重新连接后重试。",
        "GUI writes require a valid session and run_guarded.": "客户端写入操作需要有效会话，请重新连接后重试。",
        "IM source database is missing or not configured": "未配置聊天数据源或源数据库不存在",
        "Outbox service is stopping": "发送任务服务正在停止，请稍后重试。",
        "Outbox task not found in this scope": "当前账号下未找到该发送任务。",
        "Outbox task not found": "发送任务不存在。",
        "Idempotency key already belongs to a different payload": "该请求标识已用于其他内容，请刷新任务状态。",
        "Outbox status or version changed": "任务状态或版本已变化，请刷新后重试。",
        "Screenshot or task version changed": "截图或任务版本已变化，请刷新后重新确认。",
        "Screenshot expired or replaced": "截图已过期或被替换，请刷新任务状态。",
        "Screenshot not found": "截图不存在。",
        "Original GUI session expired; explicit retry is required": "原客户端操作授权已失效，请显式重试任务。",
        "Account context changed": "账号已切换，请刷新后重试。",
        "A possibly sent task cannot be retried": "该任务可能已发送，无法重试，请检查聊天记录。",
        "Source baseline unavailable; no text was entered": "无法读取发送前的消息记录，尚未输入文本。",
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
