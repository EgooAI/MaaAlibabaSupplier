from __future__ import annotations

import logging
import sys
from pathlib import Path

from loguru import logger

_CONFIGURED = False


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
    if _CONFIGURED:
        return
    logger.remove()
    logger.add(
        sys.stderr,
        colorize=True,
        format="<green>{time:HH:mm:ss}</green> <level>{level: <3}</level> <cyan>{name}</cyan> - <level>{message}</level>",
    )
    # File sink keeps uvicorn/access logs visible when started via pythonw (no console).
    log_dir = Path(__file__).resolve().parents[3] / "data" / "logs"
    logger.add(log_dir / "api.log", rotation="10 MB", level="INFO")
    logging.basicConfig(handlers=[_InterceptHandler()], level=logging.INFO, force=True)
    _CONFIGURED = True
