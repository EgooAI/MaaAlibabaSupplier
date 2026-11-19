"""Errors for persisted agent content that no longer matches its schema."""

from backend.app.api.envelope import AppError


class ContentSchemaError(AppError):
    """Stored session or experiment content failed schema validation."""

    def __init__(self) -> None:
        super().__init__("记录内容已损坏，无法继续操作；请新建会话或实验。", status_code=409)


__all__ = ["ContentSchemaError"]
