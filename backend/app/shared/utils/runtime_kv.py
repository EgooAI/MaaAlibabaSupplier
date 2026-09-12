from __future__ import annotations

import re
import threading
from typing import Any


_VALID_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_.:-]{0,127}$")


def is_valid_variable_key(key: str) -> bool:
    if not isinstance(key, str):
        return False
    trimmed = key.strip()
    if not trimmed:
        return False
    return _VALID_KEY_PATTERN.fullmatch(trimmed) is not None


class RuntimeKVStore:
    """Process-wide thread-safe in-memory key-value store."""

    _instance: "RuntimeKVStore" | None = None
    _instance_lock = threading.Lock()

    def __new__(cls) -> "RuntimeKVStore":
        with cls._instance_lock:
            if cls._instance is None:
                instance = super().__new__(cls)
                instance._lock = threading.Lock()
                instance._data = {}
                cls._instance = instance
            return cls._instance

    def set(self, key: str, value: Any) -> None:
        if not is_valid_variable_key(key):
            raise ValueError(f"Invalid variable key: {key!r}")
        with self._lock:
            self._data[key.strip()] = value

    def get(self, key: str, default: Any = None) -> Any:
        if not is_valid_variable_key(key):
            return default
        with self._lock:
            return self._data.get(key.strip(), default)


def get_runtime_kv_store() -> RuntimeKVStore:
    return RuntimeKVStore()
