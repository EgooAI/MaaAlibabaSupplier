"""Single entry point for running system chat tool agents via the CRM SDK pipeline."""

from __future__ import annotations

from backend.app.shared.crm.sdk import AgentPreset, AgentPresetManager


class AgentRunError(RuntimeError):
    """Raised when the agent pipeline cannot produce a final answer."""


def run_chat_tool_agent(
    apid: str,
    user_input: str,
    *,
    timeout_seconds: float = 60.0,
    preset_override: AgentPreset | None = None,
) -> str:
    """Run the preset agent and return its final output text.

    ``preset_override`` runs an unsaved draft preset through the same pipeline;
    the apid still selects the output normalizer. Raises :class:`AgentRunError`
    when the pipeline hits the context or tool-round limit; other failures
    propagate as-is so callers can surface ``str(exc)``.
    """
    from agent_pipeline import (
        AgentPipeline,
        AgentPipelineInput,
        AgentPipelineResultStatus,
    )
    from agent_pipeline.llm import OpenAICompatibleLLMClient

    manager = AgentPresetManager()
    if preset_override is None:
        preset = manager.get_agent_preset(apid)
        if preset is None:
            raise ValueError(f"AgentPreset {apid} not found")
        llm_config = _require_llm_registered(preset.llm_level)
        pipeline_input = AgentPipelineInput(user_input=user_input, apid=apid)
    else:
        llm_config = _require_llm_registered(preset_override.llm_level)
        pipeline_input = AgentPipelineInput(user_input=user_input, agent_preset=preset_override)
    result = AgentPipeline(
        llm_client=OpenAICompatibleLLMClient(llm_config, timeout_seconds=timeout_seconds),
        manager=manager,
    ).run(pipeline_input)
    if result.status is AgentPipelineResultStatus.CONTEXT_LIMITED:
        raise AgentRunError("上下文超过限制")
    if result.status is AgentPipelineResultStatus.TOOL_ROUNDS_LIMITED:
        raise AgentRunError("调用超过次数限制")
    return result.output_text


def _require_llm_registered(llm_level: int):
    """Return the registered LLM config for a level, seeding it from the DB when missing.

    ``llm_registry`` 是进程内存注册表，只有 ``app/main.py`` 启动路径会调用
    ``register_default_llms``。其他入口（如直接运行 web server 或脚本）下，
    数据库里即使已有 level 配置也会报 "LLM level N is not registered"，
    这里兜底从数据库补注册一次。
    """
    from agent_pipeline.registry import llm_registry

    config = llm_registry.get(llm_level)
    if config is None:
        _register_default_llms()
        config = llm_registry.get(llm_level)
    if config is None:
        raise ValueError(f"LLM level {llm_level} is not registered")
    return config


def _register_default_llms() -> None:
    from agent_pipeline.llm_api import register_default_llms

    register_default_llms()


__all__ = ["AgentRunError", "run_chat_tool_agent"]
