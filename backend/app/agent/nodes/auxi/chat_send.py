from pydantic import BaseModel, Field

from maa.agent.agent_server import AgentServer
from maa.context import Context
from maa.custom_action import CustomAction


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

        override: dict = {
            "ChatInput_InputText": {
                "action": {"param": {"input_text": param.text}}
            },
        }
        if param.confirm:
            override["ChatInput_SendMessage"] = {"enabled": True}

        detail = context.run_task("ChatInput", override)
        return detail is not None and detail.status.succeeded
