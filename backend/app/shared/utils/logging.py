from __future__ import annotations

import sys

from loguru import logger

_CONFIGURED = False


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
    _CONFIGURED = True
