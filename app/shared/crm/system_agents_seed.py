from __future__ import annotations

import json
import sqlite3
from functools import lru_cache
from pathlib import Path
from typing import Any

from loguru import logger

from app.shared.agent.chat_tools import (
    CHAT_CUSTOMER_INTENT_AGENT_APID,
    CHAT_CUSTOMER_STAGE_AGENT_APID,
    CHAT_REPLY_SUGGESTION_AGENT_APID,
    CHAT_TRANSLATION_AGENT_APID,
)
from app.shared.crm.sdk import load_sdk

SYSTEM_AGENT_APIDS = (
    CHAT_TRANSLATION_AGENT_APID,
    CHAT_REPLY_SUGGESTION_AGENT_APID,
    CHAT_CUSTOMER_INTENT_AGENT_APID,
    CHAT_CUSTOMER_STAGE_AGENT_APID,
)

_SYSTEM_AGENT_SQL_PATH = Path(__file__).resolve().parents[2] / "crm_sdk" / "sql" / "system_agents.sql"


def _default_agentpreset_schema_sql() -> str:
    return """
    CREATE TABLE agentpreset (
        apid TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        description TEXT NOT NULL,
        prompt TEXT NOT NULL,
        intelevel INTEGER NOT NULL,
        tools TEXT NOT NULL
    )
    """


@lru_cache(maxsize=1)
def _load_system_agent_defaults() -> dict[str, dict[str, Any]]:
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute(_default_agentpreset_schema_sql())
        connection.executescript(_SYSTEM_AGENT_SQL_PATH.read_text(encoding="utf-8"))
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT apid, name, description, prompt, intelevel, tools
            FROM agentpreset
            ORDER BY apid
            """
        ).fetchall()
        defaults: dict[str, dict[str, Any]] = {}
        for row in rows:
            tools = json.loads(row["tools"] or "[]")
            defaults[str(row["apid"])] = {
                "apid": str(row["apid"]),
                "name": str(row["name"]),
                "description": str(row["description"]),
                "prompt": str(row["prompt"]),
                "intelevel": int(row["intelevel"]),
                "tools": tools,
            }
        return defaults
    finally:
        connection.close()


def _build_system_agent_preset(defaults: dict[str, dict[str, Any]], apid: str) -> Any:
    sdk = load_sdk()
    default = defaults.get(apid)
    if default is None:
        raise ValueError(f"Unknown system agent apid: {apid}")
    return sdk["AgentPreset"](**default)


def ensure_system_agents_seeded(database_path: Path | str | None = None) -> list[str]:
    sdk = load_sdk()
    manager = sdk["AgentPresetManager"](database_path=database_path)
    resolved_database_path = manager.database_path
    defaults = _load_system_agent_defaults()

    try:
        existing_apids = {preset.apid for preset in manager.list_agent_preset()}
        missing_apids = [apid for apid in SYSTEM_AGENT_APIDS if apid not in existing_apids]
        if not missing_apids:
            return []

        for apid in missing_apids:
            manager.upsert_agent_preset(_build_system_agent_preset(defaults, apid))
        logger.info(
            "Seeded missing system agents into {}: {}",
            resolved_database_path,
            ", ".join(missing_apids),
        )
        return missing_apids
    finally:
        manager.engine.dispose()


def restore_system_agent_default(apid: str, database_path: Path | str | None = None) -> Any:
    sdk = load_sdk()
    manager = sdk["AgentPresetManager"](database_path=database_path)
    try:
        preset = _build_system_agent_preset(_load_system_agent_defaults(), apid)
        manager.upsert_agent_preset(preset)
        logger.info("Restored default system agent {} into {}", apid, manager.database_path)
        return preset
    finally:
        manager.engine.dispose()
