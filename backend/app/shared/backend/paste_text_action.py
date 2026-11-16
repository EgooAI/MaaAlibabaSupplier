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
            if not isinstance(params, dict) or "text" not in params or set(params) - {"text", "content_format"}:
                raise ValueError("Invalid clipboard action parameters")
            controller = context.tasker.controller.info
            if controller.get("type") != "win32":
                raise ValueError("Clipboard input requires a Win32 controller")
            replace_text(controller.get("hwnd"), params["text"], content_format=params.get("content_format", "text"))
            return True
        except Exception as exc:
            # Keeps codes and safe stack metadata through both log sanitizers.
            # Native callbacks do not inherit the submitting thread's ContextVars.
            reason = str(exc) if isinstance(exc, ClipboardInputError) else "unexpected_exception"
            logger.bind(event="gui.clipboard_failed", context={
                "reason": reason, "node": argv.node_name,
                "native_job_id": argv.task_detail.task_id,
            }).opt(exception=True).error("Clipboard text replacement failed")
            return False
