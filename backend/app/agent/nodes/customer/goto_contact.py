import time

from pydantic import BaseModel, Field

from maa.agent.agent_server import AgentServer
from maa.context import Context
from maa.custom_action import CustomAction
from maa.pipeline import JActionType, JClickKey, JInputText

VK_ENTER = 0x0D


class ContactSearchParam(BaseModel):
    login_id: str = Field(..., alias="login id")

    model_config = {
        "populate_by_name": True,
        "extra": "forbid",
    }


@AgentServer.custom_action("ContactSearch")
class ContactSearch(CustomAction):

    def run(
        self,
        context: Context,
        argv: CustomAction.RunArg,
    ) -> bool:
        if isinstance(argv.custom_action_param, str):
            param = ContactSearchParam.model_validate_json(argv.custom_action_param)
        else:
            param = ContactSearchParam.model_validate(argv.custom_action_param)

        # Focus contact search box
        detail = context.run_task("ContactSearch_GoToSearch")
        if detail is None or not detail.status.succeeded:
            return False

        # Type login ID
        detail = context.run_action_direct(JActionType.InputText, JInputText(input_text=param.login_id))
        if detail is None or not detail.success:
            return False

        # Wait for search results
        time.sleep(2)

        # Press Enter to select
        detail = context.run_action_direct(JActionType.ClickKey, JClickKey(key=VK_ENTER))
        if detail is None or not detail.success:
            return False

        return True
