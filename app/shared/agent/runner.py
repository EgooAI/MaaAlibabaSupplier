"""Single entry point for running system chat tool agents via the CRM SDK pipeline."""

from __future__ import annotations

from app.shared.crm.sdk import AgentPresetManager


class AgentRunError(RuntimeError):
    """Raised when the agent pipeline cannot produce a final answer."""


def run_chat_tool_agent(apid: str, user_input: str, *, timeout_seconds: float = 60.0) -> str:
    """Run the preset agent and return its final output text.

    Raises :class:`AgentRunError` when the pipeline hits the context or tool-round
    limit; other failures propagate as-is so callers can surface ``str(exc)``.
    """
    from agent_pipeline import (
        AgentPipeline,
        AgentPipelineInput,
        AgentPipelineResultStatus,
    )
    from agent_pipeline.llm import OpenAICompatibleLLMClient
    from agent_pipeline.resolver import require_agent_preset_by_apid

    manager = AgentPresetManager()
    runtime = _require_runtime_with_llm_registered(manager, apid)
    result = AgentPipeline(
        llm_client=OpenAICompatibleLLMClient(runtime.llm, timeout_seconds=timeout_seconds),
        manager=manager,
    ).run(AgentPipelineInput(user_input=user_input, apid=apid))
    if result.status is AgentPipelineResultStatus.CONTEXT_LIMITED:
        raise AgentRunError("上下文超过限制")
    if result.status is AgentPipelineResultStatus.TOOL_ROUNDS_LIMITED:
        raise AgentRunError("调用超过次数限制")
    return result.output_text


def _require_runtime_with_llm_registered(manager: AgentPresetManager, apid: str):
    """Resolve the preset, lazily registering default LLM levels when missing.

    ``llm_registry`` 是进程内存注册表，只有 ``app/main.py`` 启动路径会调用
    ``register_default_llms``。其他入口（如直接运行 web server 或脚本）下，
    数据库里即使已有 level 配置也会报 "LLM level N is not registered"，
    这里兜底从数据库补注册一次。
    """
    from agent_pipeline.llm_api import register_default_llms
    from agent_pipeline.registry import llm_registry
    from agent_pipeline.resolver import require_agent_preset_by_apid

    runtime = require_agent_preset_by_apid(manager, apid)
    if llm_registry.get(runtime.preset.llm_level) is not None:
        return runtime
    register_default_llms()
    return require_agent_preset_by_apid(manager, apid)


__all__ = ["AgentRunError", "run_chat_tool_agent"]
