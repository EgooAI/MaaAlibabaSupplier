"""Agent preset revision history policy over the SDK revision store.

业务策略：每次成功保存/恢复/实际写入内置默认后追加一条快照，与最新一条完全
相同则跳过，每个预设最多保留 :data:`MAX_REVISIONS_PER_PRESET` 条。历史写入
是尽力而为：调用方记录失败但不阻断保存本身，用户的编辑意图优先。
"""

from __future__ import annotations

from backend.app.shared.crm.sdk import (
    AgentPreset,
    AgentPresetRevision,
    AgentPresetRevisionManager,
)
from backend.app.shared.utils.log_context import log_event
from loguru import logger

REVISION_SOURCES = frozenset({"manual", "restore_default", "seed"})
MAX_REVISIONS_PER_PRESET = 50
MAX_REVISION_NOTE_LENGTH = 200


def normalize_revision_source(source: str | None) -> str:
    value = (source or "").strip()
    return value if value in REVISION_SOURCES else "manual"


def record_preset_revision(
    preset: AgentPreset,
    *,
    source: str = "manual",
    note: str = "",
) -> AgentPresetRevision | None:
    """Append the saved preset state to its history.

    Returns the appended revision, or ``None`` when the head already matches.
    Never raises: history is best effort and must not fail the user's save.
    """
    try:
        manager = AgentPresetRevisionManager()
        head = next(iter(manager.list_revisions(preset.apid, limit=1)), None)
        if head is not None and _matches(head, preset):
            return None
        revision = AgentPresetRevision(
            apid=preset.apid,
            name=preset.name,
            description=preset.description,
            prompt=preset.prompt,
            llm_level=preset.llm_level,
            tools=list(preset.tools or []),
            requires_tool=bool(getattr(preset, "requires_tool", False)),
            source=normalize_revision_source(source),
            note=(note or "").strip()[:MAX_REVISION_NOTE_LENGTH],
        )
        manager.add_revision(revision)
        manager.prune_revisions(preset.apid, keep=MAX_REVISIONS_PER_PRESET)
        return revision
    except Exception:
        logger.exception("request failed")
        log_event("agent.preset_revision_failed", apid=preset.apid, source=source)
        return None


def latest_revision(preset_id: str) -> AgentPresetRevision | None:
    try:
        return next(iter(AgentPresetRevisionManager().list_revisions(preset_id, limit=1)), None)
    except Exception:
        logger.exception("request failed")
        return None


def list_preset_revisions(preset_id: str, limit: int = MAX_REVISIONS_PER_PRESET) -> list[AgentPresetRevision]:
    return AgentPresetRevisionManager().list_revisions(preset_id, limit=limit)


def _matches(revision: AgentPresetRevision, preset: AgentPreset) -> bool:
    return (
        revision.name == preset.name
        and revision.description == preset.description
        and revision.prompt == preset.prompt
        and revision.llm_level == preset.llm_level
        and list(revision.tools or []) == list(preset.tools or [])
        and bool(getattr(revision, "requires_tool", False))
        == bool(getattr(preset, "requires_tool", False))
    )


__all__ = [
    "MAX_REVISION_NOTE_LENGTH",
    "MAX_REVISIONS_PER_PRESET",
    "REVISION_SOURCES",
    "latest_revision",
    "list_preset_revisions",
    "normalize_revision_source",
    "record_preset_revision",
]
