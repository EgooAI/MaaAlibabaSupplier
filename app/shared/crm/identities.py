from __future__ import annotations

"""Alibaba IM 身份与会话键约定。

`sender_id` 的格式为 ``{ali_id}@icbu``，会话键格式为
``{platform}:{self_ali_id}:{contact_ali_id}``。这些约定在多个模块中重复出现，
统一在此维护。
"""

PLATFORM_PID = "alibaba_icbu"
ICBU_SUFFIX = "@icbu"


def self_sender_id(ali_id: str) -> str:
    return f"{ali_id}{ICBU_SUFFIX}"


def strip_icbu_suffix(sender_id: str | None) -> str:
    if not sender_id:
        return ""
    return sender_id.removesuffix(ICBU_SUFFIX)


def session_key(self_ali_id: str, contact_ali_id: str) -> str:
    return f"{PLATFORM_PID}:{self_ali_id}:{contact_ali_id}"


def session_key_prefix(self_ali_id: str) -> str:
    return f"{PLATFORM_PID}:{self_ali_id}:"


__all__ = [
    "PLATFORM_PID",
    "ICBU_SUFFIX",
    "self_sender_id",
    "strip_icbu_suffix",
    "session_key",
    "session_key_prefix",
]