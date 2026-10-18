"""Agent tool catalog: registered tool descriptions and unknown-name detection.

工具函数由 SDK 注册表在应用启动时建立（``agent_tools.register_builtin_tools``）。
业务层只做只读查询：接口把可用工具下发给前端，保存与运行前拦截未注册的工具名，
避免界面先承诺一个后端并不存在的能力。
"""

from __future__ import annotations

import inspect
from collections.abc import Iterable

from backend.app.shared.crm.sdk import ensure_sdk_path


def list_registered_tools() -> list[dict[str, str]]:
    """Registered tools as ``{"name", "description"}``, sorted by name.

    Description follows the pipeline's convention: the callable docstring, which
    is also what the model sees as the tool description.
    """
    ensure_sdk_path()
    from agent_pipeline.registry import tool_registry

    return [
        {
            "name": name,
            "description": (inspect.getdoc(tool) or f"Call the {name} tool.").strip(),
        }
        for name, tool in sorted(tool_registry.list().items())
    ]


def unknown_tool_names(names: Iterable[str]) -> list[str]:
    """Names that have no registered implementation, in input order and without duplicates."""
    ensure_sdk_path()
    from agent_pipeline.registry import tool_registry

    registered = tool_registry.list()
    unknown: list[str] = []
    for name in names:
        if name not in registered and name not in unknown:
            unknown.append(name)
    return unknown


__all__ = ["list_registered_tools", "unknown_tool_names"]
