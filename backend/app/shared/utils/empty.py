from __future__ import annotations

from typing import Any


def is_empty_value(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    return value is None or value == "" or value == []
