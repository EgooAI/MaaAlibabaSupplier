from __future__ import annotations

from typing import Any


def is_empty_value(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    return value is None or value == "" or value == []


def is_unset_value(value: Any) -> bool:
    """Treat numeric zero as unset for best-effort profile enrichment merges."""
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)) and value == 0:
        return True
    return is_empty_value(value)
