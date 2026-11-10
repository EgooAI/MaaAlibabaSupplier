"""Single source for repository-relative filesystem paths.

Functions resolve against the current settings on every call so tests and
runtime relocations keep working. Callers that already hold an explicit root
pass it as an argument; everyone else uses the process-wide settings.
"""

from __future__ import annotations

from pathlib import Path

from backend.app.shared.utils import settings

DATA_DIRNAME = "data"
LOG_DIRNAME = "logs"
CACHE_DIRNAME = ".cache"
OUTBOX_DIRNAME = "outbox"
DEBUG_DIRNAME = "debug"
ASSETS_DIRNAME = "assets"
DEPS_DIRNAME = "deps"
BIN_DIRNAME = "bin"
FRONTEND_DIRNAME = "frontend"
OUT_DIRNAME = "out"
NATIVE_DIRNAME = "native"
RESOURCE_DIRNAME = "resource"
YAK_DIRNAME = ".portable"
YAK_SUBDIRNAME = "yak"

AUTH_DB_FILENAME = "auth.sqlite"
APP_CONFIG_FILENAME = "app_config.json"
API_LOG_FILENAME = "api.log"
AGENT_LOG_FILENAME = "agent.log"
YAK_LOG_FILENAME = "yak_mitm.log"
MAAFW_CLI_LOG_FILENAME = "maafw_cli.log"
STARTUP_FAILURE_LOG_FILENAME = "startup_failure.log"
COPY_TEXT_LATEST_FILENAME = "copy_text.latest.txt"
AES_KEY_FILENAME = "aes_key.bin"
YAK_SCRIPT_FILENAME = "yak_mitm.yak"
YAK_EXECUTABLE_FILENAME = "yak.exe"
MAAFW_CLI_FILENAME = "MaaPiCli.exe"
MAAFW_UTILS_FILENAME = "MaaUtils.dll"
BUILD_INFO_FILENAME = "build-info.json"
BUILD_ID_FILENAME = "build-id.json"


def _backend_root(root: Path | None) -> Path:
    return root if root is not None else settings.resolve_backend_root()


def _repo_root(root: Path | None) -> Path:
    return root if root is not None else settings.resolve_repo_root()


def resolve_backend_relative(raw: str | Path, root: Path | None = None) -> Path:
    path = Path(raw)
    return path if path.is_absolute() else _backend_root(root) / path


def data_dir(root: Path | None = None) -> Path:
    return _backend_root(root) / DATA_DIRNAME


def log_dir(root: Path | None = None) -> Path:
    return data_dir(root) / LOG_DIRNAME


def cache_dir(root: Path | None = None) -> Path:
    return data_dir(root) / CACHE_DIRNAME


def outbox_dir(root: Path | None = None) -> Path:
    return data_dir(root) / OUTBOX_DIRNAME


def debug_dir(root: Path | None = None) -> Path:
    return _backend_root(root) / DEBUG_DIRNAME


def assets_dir(root: Path | None = None) -> Path:
    return _backend_root(root) / ASSETS_DIRNAME


def deps_bin_dir(root: Path | None = None) -> Path:
    return _backend_root(root) / DEPS_DIRNAME / BIN_DIRNAME


def native_log_dir(root: Path | None = None) -> Path:
    return log_dir(root) / NATIVE_DIRNAME


def auth_db_path(root: Path | None = None) -> Path:
    return data_dir(root) / AUTH_DB_FILENAME


def app_config_path(root: Path | None = None) -> Path:
    return data_dir(root) / APP_CONFIG_FILENAME


def api_log_path(root: Path | None = None) -> Path:
    return log_dir(root) / API_LOG_FILENAME


def agent_log_path(root: Path | None = None) -> Path:
    return log_dir(root) / AGENT_LOG_FILENAME


def yak_log_path(root: Path | None = None) -> Path:
    return log_dir(root) / YAK_LOG_FILENAME


def maafw_cli_log_path(root: Path | None = None) -> Path:
    return log_dir(root) / MAAFW_CLI_LOG_FILENAME


def startup_failure_log_path(root: Path | None = None) -> Path:
    return log_dir(root) / STARTUP_FAILURE_LOG_FILENAME


def copy_text_latest_path(root: Path | None = None) -> Path:
    return debug_dir(root) / COPY_TEXT_LATEST_FILENAME


def aes_key_path(root: Path | None = None) -> Path:
    return cache_dir(root) / AES_KEY_FILENAME


def yak_script_path(root: Path | None = None) -> Path:
    return _backend_root(root) / YAK_SCRIPT_FILENAME


def yak_executable_path(root: Path | None = None) -> Path:
    return _backend_root(root) / YAK_DIRNAME / YAK_SUBDIRNAME / YAK_EXECUTABLE_FILENAME


def maafw_cli_path(root: Path | None = None) -> Path:
    return assets_dir(root) / MAAFW_CLI_FILENAME


def maafw_utils_path(root: Path | None = None) -> Path:
    return assets_dir(root) / MAAFW_UTILS_FILENAME


def maafw_resource_dir(root: Path | None = None) -> Path:
    return assets_dir(root) / RESOURCE_DIRNAME


def frontend_out_dir(repo_root: Path | None = None) -> Path:
    return _repo_root(repo_root) / FRONTEND_DIRNAME / OUT_DIRNAME


def build_info_path(repo_root: Path | None = None) -> Path:
    return _repo_root(repo_root) / BUILD_INFO_FILENAME


def build_id_path(repo_root: Path | None = None) -> Path:
    return frontend_out_dir(repo_root) / BUILD_ID_FILENAME
