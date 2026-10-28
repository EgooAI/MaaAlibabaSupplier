"""Version, ownership and scope helpers shared by the agent routers.

A session runs against the version it was *pinned* to, not against whatever the
editor currently holds. These helpers translate between the three shapes that
version can take — a saved preset, a saved revision of it, an unsaved draft —
and decide whether running one is allowed at all.

The router keeps these under their historical private names so existing call
sites and tests are unchanged; the business service layer (experiments) calls
them directly.
"""

from __future__ import annotations

from pathlib import Path

from backend.app.api.envelope import AppError
from backend.app.shared.agent.chat_history_content import (
    ChatHistoryContent,
    ChatHistoryDraft,
    ChatHistoryPin,
    ChatHistoryScope,
)
from backend.app.shared.agent.preset_history import (
    MAX_REVISIONS_PER_PRESET,
    list_preset_revisions,
)
from backend.app.shared.agent.system_agents import SYSTEM_AGENT_APIDS
from backend.app.shared.agent.tools import unknown_tool_names
from backend.app.shared.crm.sdk import AgentPreset, AgentPresetRevision


# ---------------------------------------------------------------------------
# Account scope


def current_scope() -> ChatHistoryScope:
    """The seller + data directory that owns everything written right now."""
    from backend.app.shared.backend.account_context import get_account_context

    context = get_account_context()
    return ChatHistoryScope(seller=context.self_ali_id, data_dir=context.data_dir)


def scope_allows(content: ChatHistoryContent) -> bool:
    """Sessions without a scope belong to the current account (legacy behaviour).

    A scoped session is only visible to the seller + data directory that owns it.
    """
    scope = content.scope
    if scope is None or (not scope.seller and not scope.data_dir):
        return True
    current = current_scope()
    if not current.seller or not current.data_dir:
        return False
    if scope.seller and scope.seller != current.seller:
        return False
    if scope.data_dir and _normalize_dir(scope.data_dir) != _normalize_dir(current.data_dir):
        return False
    return True


def _normalize_dir(value: str) -> str:
    try:
        return str(Path(value).expanduser().resolve()).casefold()
    except (OSError, RuntimeError):
        return value.strip().casefold()


def adopt_scope(content: ChatHistoryContent) -> bool:
    """Attach the current scope to an unscoped legacy session; returns changed."""
    if content.scope is not None and (content.scope.seller or content.scope.data_dir):
        return False
    content.scope = current_scope()
    return True


# ---------------------------------------------------------------------------
# Pinned versions


def pinned_from_preset(preset: AgentPreset) -> ChatHistoryPin:
    """The pin recording a saved preset exactly as it runs today."""
    return ChatHistoryPin(
        kind="preset",
        revision_id=None,
        name=preset.name,
        prompt=preset.prompt,
        llm_level=preset.llm_level,
        tools=list(preset.tools or []),
        requires_tool=bool(getattr(preset, "requires_tool", False)),
    )


def pinned_from_draft(draft: ChatHistoryDraft) -> ChatHistoryPin:
    """The pin recording an unsaved editor snapshot."""
    return ChatHistoryPin(
        kind="draft",
        name="",
        prompt=draft.prompt,
        llm_level=draft.llm_level,
        tools=list(draft.tools),
        requires_tool=bool(draft.requires_tool),
    )


def pin_to_draft(pin: ChatHistoryPin | None) -> ChatHistoryDraft | None:
    """The draft snapshot a draft-pinned session carries, if any."""
    if pin is None or pin.kind != "draft":
        return None
    return ChatHistoryDraft(
        prompt=pin.prompt, llm_level=pin.llm_level, tools=list(pin.tools),
        requires_tool=bool(pin.requires_tool),
    )


def pinned_from_revision(revision: AgentPresetRevision) -> ChatHistoryPin:
    """The pin recording one saved revision of a preset."""
    return ChatHistoryPin(
        kind="preset", revision_id=revision.id, name=revision.name,
        prompt=revision.prompt, llm_level=revision.llm_level,
        tools=list(revision.tools or []),
        requires_tool=bool(getattr(revision, "requires_tool", False)),
    )


def find_revision(preset_id: str, revision_id: int) -> AgentPresetRevision | None:
    """One saved revision of the preset, or ``None`` when it no longer exists."""
    return next(
        (item for item in list_preset_revisions(preset_id, limit=MAX_REVISIONS_PER_PRESET)
         if item.id == revision_id),
        None,
    )


def pinned_to_preset(preset: AgentPreset, pin: ChatHistoryPin | None) -> AgentPreset:
    """The preset a pin describes, carrying over the preset's identity fields."""
    if pin is None:
        return preset
    return AgentPreset(
        apid=preset.apid,
        name=pin.name or preset.name,
        description=preset.description,
        prompt=pin.prompt,
        llm_level=pin.llm_level,
        tools=list(pin.tools),
        enabled=True,
        requires_tool=bool(pin.requires_tool),
    )


# ---------------------------------------------------------------------------
# Overrides and runnability


def draft_override(preset: AgentPreset, draft: ChatHistoryDraft) -> AgentPreset:
    """Build the unsaved draft preset used to run one draft test session."""
    return AgentPreset(
        apid=preset.apid,
        name=preset.name,
        description=preset.description,
        prompt=draft.prompt,
        llm_level=draft.llm_level,
        tools=list(draft.tools),
        enabled=True,
        requires_tool=bool(draft.requires_tool),
    )


def draft_matches_preset(draft: ChatHistoryDraft, preset: AgentPreset) -> bool:
    return (
        draft.prompt == preset.prompt
        and draft.llm_level == preset.llm_level
        and list(draft.tools or []) == list(preset.tools or [])
        and bool(draft.requires_tool) == bool(getattr(preset, "requires_tool", False))
    )


def pinned_override(preset: AgentPreset, pinned: ChatHistoryPin | None) -> AgentPreset | None:
    """Run a session against the version it was pinned to, not the current one."""
    if pinned is None or not pinned.prompt:
        return None
    if (
        pinned.kind == "preset"
        and pinned.prompt == preset.prompt
        and pinned.llm_level == preset.llm_level
        and list(pinned.tools) == list(preset.tools or [])
        and bool(pinned.requires_tool) == bool(getattr(preset, "requires_tool", False))
    ):
        return None
    return pinned_to_preset(preset, pinned)


def require_runnable_preset(preset: AgentPreset) -> None:
    """System agents are integral and always runnable; regular agents honor 停用."""
    if preset.apid not in SYSTEM_AGENT_APIDS and not preset.enabled:
        raise AppError("Agent 已停用，请先启用后再运行", status_code=409)
    unknown = unknown_tool_names(preset.tools or [])
    if unknown:
        raise AppError(
            f"该 Agent 挂载了未注册的工具：{'、'.join(unknown)}。请先在编辑器中移除后再运行。",
            status_code=409,
        )
    if bool(getattr(preset, "requires_tool", False)) and not (preset.tools or []):
        raise AppError(
            "该 Agent 要求必须调用工具，但没有挂载任何工具。请添加工具或关闭「必须调用工具」。",
            status_code=409,
        )


def parse_draft(
    prompt: str = "", level: int = 0, tools: list[str] | None = None,
    requires_tool: bool | None = None,
) -> ChatHistoryDraft:
    """The draft snapshot from request fields, with the level clamped to the grid."""
    from backend.app.shared.crm.sdk import LLM_MAX_LEVEL

    try:
        llm_level = max(0, min(LLM_MAX_LEVEL, int(level)))
    except (TypeError, ValueError):
        raise AppError(f"draft.level 必须是 0 到 {LLM_MAX_LEVEL} 的整数", status_code=422)
    return ChatHistoryDraft(
        prompt=prompt,
        llm_level=llm_level,
        tools=[tool for tool in (tools or []) if isinstance(tool, str) and tool.strip()],
        requires_tool=bool(requires_tool),
    )


__all__ = [
    "adopt_scope",
    "current_scope",
    "draft_matches_preset",
    "draft_override",
    "find_revision",
    "parse_draft",
    "pin_to_draft",
    "pinned_from_draft",
    "pinned_from_preset",
    "pinned_from_revision",
    "pinned_override",
    "pinned_to_preset",
    "require_runnable_preset",
    "scope_allows",
]
