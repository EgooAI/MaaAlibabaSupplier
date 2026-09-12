from __future__ import annotations

import importlib
import sys
from pathlib import Path


_SDK_PATH = Path(__file__).resolve().parents[2] / "crm_sdk"


def ensure_sdk_path() -> None:
    sdk_path = str(_SDK_PATH)
    if sdk_path not in sys.path:
        sys.path.insert(0, sdk_path)


ensure_sdk_path()

core = importlib.import_module("core")
models = importlib.import_module("models")

Account = models.Account
AccountManager = core.AccountManager
AccountMapping = models.AccountMapping
AccountMappingManager = core.AccountMappingManager
Customer = models.Customer
CustomerManager = core.CustomerManager
LLMApiConfig = models.LLMApiConfig
LLMApiConfigManager = core.LLMApiConfigManager
Message = models.Message
MessageManager = core.MessageManager
ChatHistory = models.ChatHistory
ChatHistoryManager = core.ChatHistoryManager
Platform = models.Platform
PlatformManager = core.PlatformManager
SessionMeta = models.SessionMeta
SessionMetaManager = core.SessionMetaManager
Translate = models.Translate
TranslateManager = core.TranslateManager
AgentPreset = models.AgentPreset
AgentPresetManager = core.AgentPresetManager

__all__ = [
    "Account",
    "AccountManager",
    "AccountMapping",
    "AccountMappingManager",
    "AgentPreset",
    "AgentPresetManager",
    "ChatHistory",
    "ChatHistoryManager",
    "Customer",
    "CustomerManager",
    "LLMApiConfig",
    "LLMApiConfigManager",
    "Message",
    "MessageManager",
    "Platform",
    "PlatformManager",
    "SessionMeta",
    "SessionMetaManager",
    "Translate",
    "TranslateManager",
    "ensure_sdk_path",
]
