from pydantic import BaseModel, Field

from maa.agent.agent_server import AgentServer
from maa.context import Context
from maa.custom_action import CustomAction
from backend.app.shared.backend.maafw_runner import chat_input_override


class SendMessageParam(BaseModel):
    login_id: str = Field(..., alias="login id")
    text: str
    dry_run: bool = Field(False, alias="dry run")

    model_config = {
        "populate_by_name": True,
        "extra": "forbid",
    }


@AgentServer.custom_action("SendMessage")
class SendMessage(CustomAction):

    def run(
        self,
        context: Context,
        argv: CustomAction.RunArg,
    ) -> bool:
        raw_param = argv.custom_action_param
        if isinstance(raw_param, str):
            param = SendMessageParam.model_validate_json(raw_param)
        elif isinstance(raw_param, dict):
            param = SendMessageParam.model_validate(raw_param)
        else:
            raise ValueError("`custom_action_param` must be JSON string or object.")

        detail = context.run_task("ContactSearch", {
            "ContactSearch_InputText": {
                "action": {"param": {"input_text": param.login_id}}
            }
        })
        if detail is None or not detail.status.succeeded:
            return False
        detail = context.run_task(
            "ChatInput", chat_input_override(param.text, send=not param.dry_run)
        )
        return detail is not None and detail.status.succeeded
