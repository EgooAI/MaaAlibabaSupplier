from __future__ import annotations

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

    from core import (  # type: ignore[import-not-found]
        AccountManager,
        AccountMappingManager,
        CustomerManager,
        MessageManager,
        PlatformManager,
        SessionMetaManager,
    )
    from models import (  # type: ignore[import-not-found]
        Account,
        AccountMapping,
        Customer,
        Message,
        Platform,
        SessionMeta,
    )

    return {
        "Account": Account,
        "AccountManager": AccountManager,
        "AccountMapping": AccountMapping,
        "AccountMappingManager": AccountMappingManager,
        "Customer": Customer,
        "CustomerManager": CustomerManager,
        "Message": Message,
        "MessageManager": MessageManager,
        "Platform": Platform,
        "PlatformManager": PlatformManager,
        "SessionMeta": SessionMeta,
        "SessionMetaManager": SessionMetaManager,
    }
