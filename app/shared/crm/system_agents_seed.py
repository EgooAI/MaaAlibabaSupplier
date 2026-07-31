from __future__ import annotations

"""Compatibility wrapper for legacy imports.

The shared CRM system agent logic lives in ``system_agents.py``.
Keep this module as a thin forwarder so older imports continue to work
without maintaining a second implementation.
"""

from app.shared.crm.system_agents import (
    SYSTEM_AGENT_APIDS,
    ensure_system_agents_seeded,
    restore_system_agent_default,
)

__all__ = [
    "SYSTEM_AGENT_APIDS",
    "ensure_system_agents_seeded",
    "restore_system_agent_default",
]
