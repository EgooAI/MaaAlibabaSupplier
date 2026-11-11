"""Account-scoped suggestion previews; model calls run outside request locks."""

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from backend.app.api.account_scope import AccountRoute, OutboxObservationRoute, outbox_context
from backend.app.api.envelope import ok
from backend.app.api.routers.agent import _db_preset_to_dict
from backend.app.shared import contract
from backend.app.shared.agent.reply_tool import MAX_REPLIES_PER_RUN
from backend.app.shared.agent.suggestion_runs import get_suggestion_runs, public_run
from backend.app.shared.agent.system_agents import CHAT_REPLY_SUGGESTION_AGENT_APID, SYSTEM_AGENT_APIDS
from backend.app.shared.backend.account_context import get_account_context
from backend.app.shared.crm.sdk import AgentPresetManager

router = APIRouter(route_class=AccountRoute)
observations = APIRouter(route_class=OutboxObservationRoute)


class DraftInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str = Field(max_length=100_000)
    level: int = Field(ge=0, le=contract.LLM_MAX_LEVEL)
    tools: list[str] = Field(max_length=50)
    requires_tool: bool = False


class RunInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    runId: str = Field(min_length=16, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$")
    agentId: str = Field(min_length=1, max_length=128)
    draft: DraftInput | None = None
    contextRunId: str | None = Field(default=None, max_length=128)


class AdoptInput(BaseModel):
    index: int = Field(ge=0, le=MAX_REPLIES_PER_RUN - 1)


@router.get("/api/agent/suggestion-agents")
def catalog():
    presets = AgentPresetManager().list_agent_preset()
    return ok({"defaultAgentId": CHAT_REPLY_SUGGESTION_AGENT_APID, "agents": [
        _db_preset_to_dict(preset) for preset in presets
        if preset.apid == CHAT_REPLY_SUGGESTION_AGENT_APID or preset.apid not in SYSTEM_AGENT_APIDS
    ]})


@router.post("/api/conversations/{conversation_id}/suggestion-runs")
def submit(conversation_id: int, body: RunInput):
    return ok(public_run(get_suggestion_runs().submit(get_account_context(), conversation_id, body.model_dump())))


@observations.get("/api/conversations/{conversation_id}/suggestion-runs")
def recent(conversation_id: int):
    service = get_suggestion_runs()
    return ok([public_run(record) for record in service.store.read(outbox_context.get(), conversation_id)])


@observations.get("/api/conversations/{conversation_id}/suggestion-runs/{run_id}")
def observe(conversation_id: int, run_id: str):
    return ok(public_run(get_suggestion_runs().require(outbox_context.get(), conversation_id, run_id)))


@router.post("/api/conversations/{conversation_id}/suggestion-runs/{run_id}/adopt")
def adopt(conversation_id: int, run_id: str, body: AdoptInput):
    return ok(get_suggestion_runs().adopt(get_account_context(), conversation_id, run_id, body.index))


router.include_router(observations)
