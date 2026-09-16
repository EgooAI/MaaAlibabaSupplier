from pydantic import BaseModel

from maa.agent.agent_server import AgentServer
from maa.context import Context
from maa.custom_action import CustomAction

from backend.app.shared.backend.maafw_runner import chat_input_override


class ChatSendParam(BaseModel):
    text: str
    confirm: bool = False

    model_config = {
        "populate_by_name": True,
        "extra": "forbid",
    }


@AgentServer.custom_action("ChatSend")
class ChatSend(CustomAction):

    def run(
        self,
        context: Context,
        argv: CustomAction.RunArg,
    ) -> bool:
        if isinstance(argv.custom_action_param, str):
            param = ChatSendParam.model_validate_json(argv.custom_action_param)
        else:
            param = ChatSendParam.model_validate(argv.custom_action_param)

        detail = context.run_task(
            "ChatInput", chat_input_override(param.text, send=param.confirm)
        )
        return detail is not None and detail.status.succeeded
