"""Alibaba IM 身份与会话键约定。

`sender_id` 的格式为 ``{ali_id}@icbu``，会话键格式为
``{platform}:{self_ali_id}:{contact_ali_id}``。这些约定在多个模块中重复出现，
统一在此维护。
"""

from __future__ import annotations

import re

PLATFORM_PID = "alibaba_icbu"
ICBU_SUFFIX = "@icbu"
# The client may keep several storage profiles for one account, named
# ``{ali_id}@icbu`` and ``{ali_id}@icbu_1``. The numeric instance suffix is
# part of the storage profile, not of the account identity.
_ICBU_PROFILE_RE = re.compile(re.escape(ICBU_SUFFIX) + r"(?:_[0-9]+)?$")


def self_sender_id(ali_id: str) -> str:
    return f"{ali_id}{ICBU_SUFFIX}"


def strip_icbu_suffix(sender_id: str | None) -> str:
    if not sender_id:
        return ""
    return sender_id.removesuffix(ICBU_SUFFIX)


def canonical_ali_id(value: str | None) -> str:
    """Account id with the platform and any instance profile suffix removed."""
    if not value:
        return ""
    return _ICBU_PROFILE_RE.sub("", value)


def canonical_sender_id(value: str | None) -> str:
    """Sender id with only an instance suffix normalized to ``@icbu``."""
    if not value:
        return ""
    if not _ICBU_PROFILE_RE.search(value):
        return value
    return _ICBU_PROFILE_RE.sub(ICBU_SUFFIX, value)


def sender_matches(sender_id: str | None, ali_id: str | None) -> bool:
    """True when *sender_id* is the platform sender id of *ali_id*.

    Accepts the canonical ``{ali_id}@icbu`` and instance profiles such as
    ``{ali_id}@icbu_1``; a bare account id is not a sender id.
    """
    account = canonical_ali_id(ali_id)
    return bool(account) and canonical_sender_id(sender_id) == self_sender_id(account)


def session_key(self_ali_id: str, contact_ali_id: str) -> str:
    return f"{PLATFORM_PID}:{self_ali_id}:{contact_ali_id}"


def session_key_prefix(self_ali_id: str) -> str:
    return f"{PLATFORM_PID}:{self_ali_id}:"


def message_external_id(self_ali_id: str, table_name: str, mid: str) -> str:
    if not self_ali_id or ":" in self_ali_id or not table_name or ":" in table_name or not mid:
        raise ValueError("Message identity requires a seller, source table and message ID")
    return f"{PLATFORM_PID}:{self_ali_id}:{table_name}:{mid}"


__all__ = [
    "PLATFORM_PID",
    "ICBU_SUFFIX",
    "self_sender_id",
    "strip_icbu_suffix",
    "canonical_ali_id",
    "canonical_sender_id",
    "sender_matches",
    "session_key",
    "session_key_prefix",
    "message_external_id",
]
