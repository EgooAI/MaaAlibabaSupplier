import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


_ENV_LOADED_PATHS: dict[str, Path] = {}


@dataclass(frozen=True)
class EmailEnvConfig:
    smtp_server: str
    smtp_port: int
    username: str
    password: str
    from_address: str
    from_name: str


def _iter_env_candidates(env_filename: str) -> list[Path]:
    cwd = Path.cwd().resolve()
    candidates: list[Path] = []

    for parent in [cwd, *cwd.parents]:
        candidates.append(parent / env_filename)

    # Fallback to repository root (h:\MaaAlibabaSupplier)
    repo_root = Path(__file__).resolve().parents[3]
    repo_env = repo_root / env_filename
    if repo_env not in candidates:
        candidates.append(repo_env)

    return candidates


def load_workdir_env(env_filename: str = ".env") -> Path:
    """Load env vars by searching .env from cwd upward to parent folders.

    Returns the selected env path (existing one if found, otherwise cwd/.env).
    """
    cached = _ENV_LOADED_PATHS.get(env_filename)
    if cached is not None:
        return cached

    candidates = _iter_env_candidates(env_filename)
    existing = next((path for path in candidates if path.exists()), None)
    env_path = existing if existing is not None else (Path.cwd().resolve() / env_filename)

    resolved = env_path.resolve()
    load_dotenv(dotenv_path=resolved, override=False)
    _ENV_LOADED_PATHS[env_filename] = resolved
    return resolved


def get_env_str(key: str, default: str = "", *, required: bool = False) -> str:
    value = os.getenv(key, default)
    value = value.strip() if isinstance(value, str) else ""
    if required and not value:
        raise ValueError(f"Missing required env var: {key}")
    return value


def get_env_int(key: str, default: int) -> int:
    value = os.getenv(key)
    if value is None or not str(value).strip():
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"Invalid int env var {key}={value!r}") from exc


def get_email_env_config() -> EmailEnvConfig:
    """Read SMTP/email config from env after loading workdir .env.

    Expected env vars:
    - EMAIL_SMTP_SERVER
    - EMAIL_SMTP_PORT
    - EMAIL_SMTP_USERNAME
    - EMAIL_SMTP_PASSWORD
    - EMAIL_FROM_ADDRESS
    - EMAIL_FROM_NAME
    """
    load_workdir_env()

    return EmailEnvConfig(
        smtp_server=get_env_str("EMAIL_SMTP_SERVER", "smtpdm.aliyun.com"),
        smtp_port=get_env_int("EMAIL_SMTP_PORT", 80),
        username=get_env_str("EMAIL_SMTP_USERNAME", required=True),
        password=get_env_str("EMAIL_SMTP_PASSWORD", required=True),
        from_address=get_env_str("EMAIL_FROM_ADDRESS", required=True),
        from_name=get_env_str("EMAIL_FROM_NAME", "MaaAlibabaSupplier"),
    )
