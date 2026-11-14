"""Shares the clipboard action between the Web resource and the standalone Agent."""

import json

from loguru import logger
from maa.custom_action import CustomAction

from backend.app.shared.backend.clipboard_input import ClipboardInputError, replace_text


class PasteChatText(CustomAction):
    def run(self, context, argv) -> bool:
        """Uses the originating controller without acquiring the caller's locks."""
        try:
            params = json.loads(argv.custom_action_param)
            if not isinstance(params, dict) or set(params) != {"text"}:
                raise ValueError("Invalid clipboard action parameters")
            controller = context.tasker.controller.info
            if controller.get("type") != "win32":
                raise ValueError("Clipboard input requires a Win32 controller")
            replace_text(controller.get("hwnd"), params["text"])
            return True
        except Exception as exc:
            # Neither parameters nor copied business text belong in ordinary logs.
            reason = str(exc) if isinstance(exc, ClipboardInputError) else type(exc).__name__
            logger.error("Clipboard text replacement failed: {}", reason)
            return False
