from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any


_SDK_PATH = Path(__file__).resolve().parents[2] / "crm_sdk"


def _ensure_sdk_path() -> None:
    sdk_path = str(_SDK_PATH)
    if sdk_path not in sys.path:
        sys.path.insert(0, sdk_path)


def load_sdk() -> dict[str, Any]:
    """Load the standalone SDK without changing SDK package import style."""
    _ensure_sdk_path()

    try:
        importlib.import_module("app.crm_sdk")
    except ModuleNotFoundError:
        # Fall back to the standalone SDK layout when the package path is unavailable.
        pass

    core = importlib.import_module("core")
    models = importlib.import_module("models")

    return {
        "Account": models.Account,
        "AccountManager": core.AccountManager,
        "AccountMapping": models.AccountMapping,
        "AccountMappingManager": core.AccountMappingManager,
        "Customer": models.Customer,
        "CustomerManager": core.CustomerManager,
        "LLMApiConfig": models.LLMApiConfig,
        "LLMApiConfigManager": core.LLMApiConfigManager,
        "Message": models.Message,
        "MessageManager": core.MessageManager,
        "MessageTest": models.MessageTest,
        "MessageTestManager": core.MessageTestManager,
        "Platform": models.Platform,
        "PlatformManager": core.PlatformManager,
        "SessionMeta": models.SessionMeta,
        "SessionMetaManager": core.SessionMetaManager,
        "Translate": models.Translate,
        "TranslateManager": core.TranslateManager,
        "AgentPreset": models.AgentPreset,
    }
