from __future__ import annotations

import logging
import sys
import threading

from loguru import logger

from backend.app.shared.utils.settings import LOG_ROTATION, resolve_backend_root

_CONFIGURED = False
_CONFIGURE_LOCK = threading.Lock()


class _InterceptHandler(logging.Handler):
    """Route std-lib logging records (uvicorn/fastapi) into loguru."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        logger.opt(depth=6, exception=record.exc_info).log(level, record.getMessage())


def configure_logging() -> None:
    global _CONFIGURED
    with _CONFIGURE_LOCK:
        if _CONFIGURED:
            return
        _CONFIGURED = True
    logger.remove()
    # sys.stderr is None when started via pythonw (no console); skip the console sink.
    if sys.stderr is not None:
        logger.add(
            sys.stderr,
            colorize=True,
            format="<green>{time:HH:mm:ss}</green> <level>{level: <3}</level> <cyan>{name}</cyan> - <level>{message}</level>",
        )
    # File sink keeps uvicorn/access logs visible when started via pythonw (no console).
    log_dir = resolve_backend_root() / "data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logger.add(log_dir / "api.log", rotation=LOG_ROTATION, level="INFO", enqueue=True)
    logging.basicConfig(handlers=[_InterceptHandler()], level=logging.INFO, force=True)
