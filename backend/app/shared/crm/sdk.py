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

try:
    from backend.app.crm_sdk import core, models
except ImportError:
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
LLM_MAX_LEVEL = models.LLM_MAX_LEVEL
Message = models.Message
MessageManager = core.MessageManager
Meta = models.Meta
MetaManager = core.MetaManager
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
AgentPresetRevision = models.AgentPresetRevision
AgentPresetRevisionManager = core.AgentPresetRevisionManager
Experiment = models.Experiment
ExperimentManager = core.ExperimentManager
MAX_VARIANTS_PER_EXPERIMENT = core.experiment.MAX_VARIANTS_PER_EXPERIMENT

__all__ = [
    "Account",
    "AccountManager",
    "AccountMapping",
    "AccountMappingManager",
    "AgentPreset",
    "AgentPresetManager",
    "AgentPresetRevision",
    "AgentPresetRevisionManager",
    "ChatHistory",
    "ChatHistoryManager",
    "Customer",
    "CustomerManager",
    "Experiment",
    "ExperimentManager",
    "LLMApiConfig",
    "LLMApiConfigManager",
    "LLM_MAX_LEVEL",
    "MAX_VARIANTS_PER_EXPERIMENT",
    "Message",
    "MessageManager",
    "Meta",
    "MetaManager",
    "Platform",
    "PlatformManager",
    "SessionMeta",
    "SessionMetaManager",
    "Translate",
    "TranslateManager",
    "ensure_sdk_path",
]
