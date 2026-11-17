"""Resolves scoped immutable image bytes for the native clipboard callback."""

import json

from loguru import logger
from maa.custom_action import CustomAction

from backend.app.shared.backend.clipboard_input import replace_image, ClipboardInputError
from backend.app.shared.crm.image_attachment_store import ImageAttachmentStore


class PasteChatImage(CustomAction):
    def run(self, context, argv) -> bool:
        try:
            params = json.loads(argv.custom_action_param)
            if not isinstance(params, dict) or set(params) != {"attachment_id", "seller", "data_dir", "conversation_id", "sha256"}:
                raise ValueError("Invalid image action parameters")
            controller = context.tasker.controller.info
            if controller.get("type") != "win32":
                raise ValueError("Image input requires a Win32 controller")
            metadata, png = ImageAttachmentStore().get(params["attachment_id"], seller=params["seller"],
                data_dir=params["data_dir"], conversation_id=params["conversation_id"], include_data=True)
            if metadata["sha256"] != params["sha256"]:
                raise ValueError("Image attachment changed")
            replace_image(controller.get("hwnd"), png)
            return True
        except Exception as exc:
            reason = str(exc) if isinstance(exc, ClipboardInputError) else "unexpected_exception"
            logger.bind(event="gui.clipboard_failed", context={
                "reason": reason, "node": argv.node_name, "native_job_id": argv.task_detail.task_id,
            }).opt(exception=True).error("Clipboard image replacement failed")
            return False
