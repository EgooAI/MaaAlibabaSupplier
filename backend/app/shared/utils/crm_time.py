from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def coerce_epoch(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, datetime):
        return value.timestamp()
    if isinstance(value, (int, float)):
        return float(value) / 1000.0 if value > 10**12 else float(value)
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", errors="ignore")
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return 0.0
        try:
            return datetime.fromisoformat(value).timestamp()
        except ValueError:
            try:
                number = int(value)
            except ValueError:
                return 0.0
            return float(number) / 1000.0 if number > 10**12 else float(number)
    return 0.0


def format_created_at(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", errors="ignore")
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return ""
        try:
            return datetime.fromisoformat(stripped).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            return stripped
    epoch = coerce_epoch(value)
    if epoch <= 0:
        return str(value)
    return datetime.fromtimestamp(epoch, tz=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
