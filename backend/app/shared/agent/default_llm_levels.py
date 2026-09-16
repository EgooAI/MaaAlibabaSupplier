"""Default LLM level seeding.

首次安装时 ``llm_api_config`` 表是空的，前端会显示"0 个层级"且所有系统
Agent 因 level 未注册而不可用。本模块在每次启动时补上缺失的默认层级行
（值留空，由用户在 LLM 页填写），**只插入缺失行，绝不覆盖用户已改的行**。
"""

from __future__ import annotations

from loguru import logger

from backend.app.shared.crm.sdk import LLMApiConfig, LLMApiConfigManager

DEFAULT_SEEDED_LLM_LEVEL = 0


def ensure_default_llm_levels_seeded() -> None:
    """Insert the default LLM level row when missing; never touch existing rows."""
    manager = LLMApiConfigManager()
    if manager.get_config(DEFAULT_SEEDED_LLM_LEVEL) is not None:
        return
    manager.upsert_config(
        LLMApiConfig(level=DEFAULT_SEEDED_LLM_LEVEL, base_url="", api_key="", model_name="")
    )
    logger.info(
        "Seeded default LLM level {} into {}", DEFAULT_SEEDED_LLM_LEVEL, manager.database_path
    )


__all__ = ["DEFAULT_SEEDED_LLM_LEVEL", "ensure_default_llm_levels_seeded"]
