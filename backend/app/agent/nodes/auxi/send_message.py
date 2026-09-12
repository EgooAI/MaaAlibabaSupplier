import time

from pydantic import BaseModel, Field

from maa.agent.agent_server import AgentServer
from maa.context import Context
from maa.custom_action import CustomAction
from maa.pipeline import JActionType, JClickKey, JInputText


VK_ENTER = 0x0D


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

        detail = context.run_task("ContactSearch_GoToSearch")
        if detail is None or not detail.status.succeeded:
            return False
        detail = context.run_action_direct(JActionType.InputText, JInputText(input_text=param.login_id))
        if detail is None or not detail.success:
            return False
        time.sleep(2)
        detail = context.run_action_direct(JActionType.ClickKey, JClickKey(key=VK_ENTER))
        if detail is None or not detail.success:
            return False
        detail = context.run_task("ChatInput_GoToInput")
        if detail is None or not detail.status.succeeded:
            return False
        detail = context.run_action_direct(JActionType.InputText, JInputText(input_text=param.text))
        if detail is None or not detail.success:
            return False

        if not param.dry_run:
            detail = context.run_action("ChatInput_SendMessage")
            if detail is None or not detail.success:
                return False

        return True
