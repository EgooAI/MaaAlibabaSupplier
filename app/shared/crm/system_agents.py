from __future__ import annotations

import json
import sqlite3
from functools import lru_cache
from pathlib import Path
from typing import Any

from loguru import logger

from app.crm_sdk.core.system_agents import (
    CHAT_CUSTOMER_INTENT_AGENT_APID,
    CHAT_CUSTOMER_STAGE_AGENT_APID,
    CHAT_REPLY_SUGGESTION_AGENT_APID,
    CHAT_TRANSLATION_AGENT_APID,
    SYSTEM_AGENT_APID_ORDER,
    SYSTEM_AGENT_APIDS,
)
from app.shared.crm.sdk import load_sdk

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
            ORDER BY rowid
            """
        ).fetchall()
        defaults: dict[str, dict[str, Any]] = {}
        for row in rows:
            defaults[str(row["apid"])] = {
                "apid": str(row["apid"]),
                "name": str(row["name"]),
                "description": str(row["description"]),
                "prompt": str(row["prompt"]),
                "intelevel": int(row["intelevel"]),
                "tools": json.loads(row["tools"] or "[]"),
            }
        return defaults
    finally:
        connection.close()


def _seed_system_agents_sql(database_path: Path | str) -> None:
    sql_text = _SYSTEM_AGENT_SQL_PATH.read_text(encoding="utf-8")
    connection = sqlite3.connect(str(Path(database_path).resolve()))
    try:
        connection.executescript(sql_text)
        connection.commit()
    finally:
        connection.close()


def load_system_agent_definitions(database_path: Path | str | None = None) -> list[tuple[str, str, str]]:
    sdk = load_sdk()
    manager = sdk["AgentPresetManager"](database_path=database_path)
    try:
        presets_by_apid = {preset.apid: preset for preset in manager.list_agent_preset()}
        definitions: list[tuple[str, str, str]] = []
        defaults = _load_system_agent_defaults()
        for apid in SYSTEM_AGENT_APID_ORDER:
            preset = presets_by_apid.get(apid)
            if preset is not None:
                definitions.append((preset.name, preset.apid, preset.description))
                continue

            default = defaults.get(apid)
            if default is not None:
                definitions.append((default["name"], default["apid"], default["description"]))
        return definitions
    finally:
        manager.engine.dispose()


def ensure_system_agents_seeded(database_path: Path | str | None = None) -> list[str]:
    sdk = load_sdk()
    manager = sdk["AgentPresetManager"](database_path=database_path)
    resolved_database_path = manager.database_path

    try:
        existing_apids = {preset.apid for preset in manager.list_agent_preset()}
        missing_apids = [apid for apid in SYSTEM_AGENT_APID_ORDER if apid not in existing_apids]
        if not missing_apids:
            return []

        logger.info(
            "System agent presets missing in {}: {}. Seeding from {}",
            resolved_database_path,
            ", ".join(missing_apids),
            _SYSTEM_AGENT_SQL_PATH,
        )
        _seed_system_agents_sql(resolved_database_path)
        logger.info("Seeded system agent presets into {}", resolved_database_path)
        return missing_apids
    finally:
        manager.engine.dispose()


def restore_system_agent_default(apid: str, database_path: Path | str | None = None) -> Any:
    sdk = load_sdk()
    manager = sdk["AgentPresetManager"](database_path=database_path)
    try:
        default = _load_system_agent_defaults().get(apid)
        if default is None:
            raise ValueError(f"Unknown system agent apid: {apid}")

        preset = sdk["AgentPreset"](**default)
        manager.upsert_agent_preset(preset)
        logger.info("Restored default system agent {} into {}", apid, manager.database_path)
        return preset
    finally:
        manager.engine.dispose()


__all__ = [
    "CHAT_CUSTOMER_INTENT_AGENT_APID",
    "CHAT_CUSTOMER_STAGE_AGENT_APID",
    "CHAT_REPLY_SUGGESTION_AGENT_APID",
    "CHAT_TRANSLATION_AGENT_APID",
    "SYSTEM_AGENT_APIDS",
    "ensure_system_agents_seeded",
    "load_system_agent_definitions",
    "restore_system_agent_default",
]
