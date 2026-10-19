"""Run system chat tool agents through the CRM SDK pipeline.

``run_chat_agent`` is the structured entry point used by the business layer;
``run_chat_tool_agent`` stays as a thin text-only wrapper for older callers.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional
from uuid import uuid4

from agent_pipeline.types import EffectIntent
from backend.app.shared.crm.sdk import AgentPreset, AgentPresetManager, ensure_sdk_path


ROLE_SYSTEM = "system"
ROLE_ANALYSIS = "analysis"
ROLE_COMPOSER = "composer"


class AgentRunError(RuntimeError):
    """Raised when the agent pipeline cannot produce a final answer."""

    def __init__(self, message: str, *, status_code: int = 500, kind: str = "failed") -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.kind = kind


@dataclass(frozen=True)
class ChatAgentTurn:
    """Everything one agent run produced, ready to be stored on a session turn."""

    text: str
    apid: str
    agent_name: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    intents: list[dict[str, Any]] = field(default_factory=list)
    iterations: int = 0
    usage: dict[str, Any] = field(default_factory=dict)
    duration_ms: float = 0.0
    restricted: bool = False
    mode: str = "live"
    batch_id: str = ""
    status: str = "completed"
    revision_id: int | None = None

    @property
    def called_tools(self) -> bool:
        return bool(self.tool_calls)

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "apid": self.apid,
            "agentName": self.agent_name,
            "toolCalls": list(self.tool_calls),
            "intents": list(self.intents),
            "iterations": self.iterations,
            "usage": dict(self.usage),
            "durationMs": round(self.duration_ms, 1),
            "restricted": self.restricted,
            # The assistant message stores the execution policy under "mode";
            # making it explicit here keeps the payload a plain field map.
            "mode": self.mode,
            "batchId": self.batch_id,
            "status": self.status,
        }


# ---------------------------------------------------------------------------
# Registration


def register_business_tools() -> list[str]:
    """Register host-owned tools; idempotent, called once at startup."""
    ensure_sdk_path()
    from agent_pipeline.registry import register_tool

    from backend.app.shared.agent.product_tool import (
        TOOL_NAME as PRODUCT_TOOL_NAME,
        TOOL_SPEC as PRODUCT_TOOL_SPEC,
        query_product,
    )
    from backend.app.shared.agent.reply_tool import TOOL_NAME, TOOL_SPEC, reply_customer

    register_tool(TOOL_NAME, reply_customer, spec=TOOL_SPEC)
    register_tool(PRODUCT_TOOL_NAME, query_product, spec=PRODUCT_TOOL_SPEC)
    return [TOOL_NAME, PRODUCT_TOOL_NAME]


def agent_role(apid: str) -> str:
    """Business role of an agent, used for limit semantics."""
    from backend.app.shared.agent.system_agents import (
        CHAT_CUSTOMER_INTENT_AGENT_APID,
        CHAT_CUSTOMER_STAGE_AGENT_APID,
        CHAT_REPLY_SUGGESTION_AGENT_APID,
        CHAT_TRANSLATION_AGENT_APID,
    )

    if apid == CHAT_REPLY_SUGGESTION_AGENT_APID:
        return ROLE_COMPOSER
    if apid in (CHAT_CUSTOMER_INTENT_AGENT_APID, CHAT_CUSTOMER_STAGE_AGENT_APID):
        return ROLE_ANALYSIS
    return ROLE_SYSTEM


# ---------------------------------------------------------------------------
# Public entry points


def run_chat_agent(
    apid: str,
    user_input: str,
    *,
    timeout_seconds: float = 60.0,
    preset_override: AgentPreset | None = None,
    tool_context: Any = None,
    tool_choice: str = "auto",
    mode: str = "live",
    conversation_id: int | None = None,
    revision_id: int | None = None,
) -> ChatAgentTurn:
    """Run one agent turn and return its structured result.

    Raises :class:`AgentRunError` for configuration errors, context limits and
    (for non-composer agents) exhausted tool budgets. Composer agents return a
    restricted turn instead so collected intents survive.
    """
    from agent_pipeline import (
        AgentPipeline,
        AgentPipelineInput,
        AgentPipelineResultStatus,
        ToolContext,
    )
    from agent_pipeline.llm import OpenAICompatibleLLMClient

    manager = AgentPresetManager()
    if preset_override is None:
        preset = manager.get_agent_preset(apid)
        if preset is None:
            raise AgentRunError(f"AgentPreset {apid} not found", status_code=404, kind="not_found")
        llm_config = _require_llm_registered(preset.llm_level)
        resolved_override: AgentPreset | None = None
    else:
        preset = preset_override
        llm_config = _require_llm_registered(preset.llm_level)
        resolved_override = preset_override

    context = tool_context if isinstance(tool_context, ToolContext) else ToolContext(mode=mode)
    pipeline_input = AgentPipelineInput(
        user_input=user_input,
        apid=None if resolved_override is not None else apid,
        agent_preset=resolved_override,
        tool_context=context,
        tool_choice=tool_choice,
    )

    batch_id = uuid4().hex
    started = time.perf_counter()
    try:
        result = AgentPipeline(
            llm_client=OpenAICompatibleLLMClient(llm_config, timeout_seconds=timeout_seconds),
            manager=manager,
        ).run(pipeline_input)
    except AgentRunError:
        raise
    except Exception as exc:
        # Preset resolution failures are configuration problems; surface them as
        # such instead of a generic 500 so the merchant knows what to fix.
        from agent_pipeline import AgentPresetResolutionError

        if isinstance(exc, AgentPresetResolutionError):
            raise AgentRunError(
                f"Agent 配置错误：{exc}", status_code=409, kind="configuration",
            ) from exc
        raise

    if result.status is AgentPipelineResultStatus.CONTEXT_LIMITED:
        raise AgentRunError("上下文超过限制", status_code=422, kind="context_limited")

    restricted = result.status is AgentPipelineResultStatus.TOOL_ROUNDS_LIMITED
    if restricted and agent_role(apid) != ROLE_COMPOSER:
        # Analysis agents must not present a partial answer as a success.
        raise AgentRunError(_limit_message(result), status_code=409, kind="tool_rounds_limited")

    dispatched = _dispatch_intents(
        result.intents, mode=mode, batch_id=batch_id, context=context,
        apid=preset.apid, agent_name=preset.name, conversation_id=conversation_id,
        revision_id=revision_id,
    )

    return ChatAgentTurn(
        text=result.output_text,
        apid=preset.apid,
        agent_name=preset.name,
        tool_calls=[_record_payload(record) for record in result.tool_call_records],
        intents=[intent.to_record() for intent in dispatched],
        iterations=result.iterations,
        usage=_usage_payload(result.usage),
        duration_ms=time.perf_counter() - started,
        restricted=restricted,
        mode=mode,
        batch_id=batch_id,
        status="restricted" if restricted else "completed",
        revision_id=revision_id,
    )


def run_chat_tool_agent(
    apid: str,
    user_input: str,
    *,
    timeout_seconds: float = 60.0,
    preset_override: AgentPreset | None = None,
    tool_context: Any = None,
    tool_choice: str = "auto",
    mode: str = "live",
) -> str:
    """Run the preset agent and return only its final output text.

    Thin wrapper over :func:`run_chat_agent` for translation, analysis and older
    callers that only need the text.
    """
    turn = run_chat_agent(
        apid, user_input, timeout_seconds=timeout_seconds,
        preset_override=preset_override, tool_context=tool_context,
        tool_choice=tool_choice, mode=mode,
    )
    return turn.text


def _limit_message(result) -> str:
    if result.tool_call_records:
        return "调用超过次数限制"
    return "调用超过次数限制，且没有得到最终回答"


def _dispatch_intents(
    intents: list[EffectIntent], *, mode: str, batch_id: str, context: Any,
    apid: str, agent_name: str, conversation_id: int | None, revision_id: int | None,
) -> list[Any]:
    from dataclasses import replace

    from backend.app.shared.agent.effect_dispatch import IntentDispatcher

    if not intents:
        return []
    # Tools attach their own host scope; fall back to the run context only for
    # tools that emitted a bare payload.
    scope = getattr(context, "scope", None)
    prepared = []
    for intent in intents:
        content = dict(intent.content)
        if scope is not None and "scope" not in content:
            content["scope"] = scope
        prepared.append(replace(intent, content=content))
    return IntentDispatcher().dispatch(prepared, mode=mode, batch_id=batch_id).intents


def _record_payload(record) -> dict[str, Any]:
    payload = {
        "callId": record.call_id,
        "name": record.name,
        "arguments": record.arguments,
        "ok": record.ok,
        "content": _json_safe(record.content),
        "error": record.error,
        "durationMs": round(record.duration_ms, 1),
        "round": record.round,
    }
    if record.original_chars is not None:
        payload["originalChars"] = record.original_chars
    return payload


def _usage_payload(usage) -> dict[str, Any]:
    payload = {
        "promptTokens": usage.prompt_tokens,
        "completionTokens": usage.completion_tokens,
        "totalTokens": usage.total_tokens,
    }
    return {key: value for key, value in payload.items() if value is not None}


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return str(value)


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
        raise AgentRunError(
            f"LLM level {llm_level} is not registered", status_code=409, kind="configuration",
        )
    return config


def _register_default_llms() -> None:
    from agent_pipeline.llm_api import register_default_llms

    register_default_llms()


__all__ = [
    "AgentRunError",
    "ChatAgentTurn",
    "ROLE_ANALYSIS",
    "ROLE_COMPOSER",
    "ROLE_SYSTEM",
    "agent_role",
    "register_business_tools",
    "run_chat_agent",
    "run_chat_tool_agent",
]
