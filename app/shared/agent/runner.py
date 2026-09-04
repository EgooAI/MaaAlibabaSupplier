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
    runtime = require_agent_preset_by_apid(manager, apid)
    result = AgentPipeline(
        llm_client=OpenAICompatibleLLMClient(runtime.llm, timeout_seconds=timeout_seconds),
        manager=manager,
    ).run(AgentPipelineInput(user_input=user_input, apid=apid))
    if result.status is AgentPipelineResultStatus.CONTEXT_LIMITED:
        raise AgentRunError("上下文超过限制")
    if result.status is AgentPipelineResultStatus.TOOL_ROUNDS_LIMITED:
        raise AgentRunError("调用超过次数限制")
    return result.output_text


__all__ = ["AgentRunError", "run_chat_tool_agent"]
