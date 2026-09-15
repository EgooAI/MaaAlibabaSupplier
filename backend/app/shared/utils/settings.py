from __future__ import annotations

from pathlib import Path

# backend/app/shared/utils/settings.py -> parents[4] is the repository root.
REPO_ROOT = Path(__file__).resolve().parents[4]
# backend/ directory (hosts assets/, data/, yak_mitm.yak, .portable/).
BACKEND_ROOT = REPO_ROOT / "backend"

FRONTEND_DEV_ORIGINS = ["http://127.0.0.1:3000", "http://localhost:3000"]

MITM_PROXY_HOST_DEFAULT = "127.0.0.1"
MITM_PROXY_PORT_DEFAULT = 8084
MITM_RECEIVER_HOST_DEFAULT = "127.0.0.1"
MITM_RECEIVER_PORT_DEFAULT = 8085
MAA_API_HOST_DEFAULT = "127.0.0.1"
MAA_API_PORT_DEFAULT = 8000

CRM_DB_RELATIVE_DEFAULT = "data/crm.sqlite"
POOLS_DB_RELATIVE_DEFAULT = "data/pools.db"

MITM_CHECK_TIMEOUT_S = 2.0
IM_DB_CACHE_TTL_S = 5.0
AGENT_LLM_TIMEOUT_S = 60.0
YAK_STARTUP_GRACE_S = 1.5
MAAFW_STOP_TIMEOUT_S = 5.0
EMAIL_SMTP_TIMEOUT_S = 3.0

LOG_ROTATION = "10 MB"


def resolve_repo_root() -> Path:
    return REPO_ROOT


def resolve_backend_root() -> Path:
    return BACKEND_ROOT
