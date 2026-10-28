"""Agent experiments: compare several versions of one agent on one input.

An experiment starts from a frozen point (a real conversation message, an
existing session, or raw text) and fans out over 1-4 variants. Each variant is
an ordinary playground session pinned to the version it tests, so its answers
are read with the same session schema the playground uses.

Steps are scripted once and replayed across variants. A run executes one step in
a background thread and reports per-variant progress through the in-process
registry; the request returns immediately, and the UI polls the run.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from loguru import logger
from pydantic import BaseModel, Field, ValidationError

from backend.app.api.account_scope import AccountRoute
from backend.app.api.envelope import AppError, api_error, ok
from backend.app.api.routers.agent import (
    _db_preset_to_dict,
    _run_turn,
    _session_to_dict,
    _turn_message,
    _updated_at_text,
    _load_session,
)
from backend.app.shared.agent.chat_history_content import (
    build_history_content,
    history_messages,
    load_history_content,
)
from backend.app.shared.agent.experiment_content import (
    ExperimentAnnotation,
    load_experiment_content,
)
from backend.app.shared.agent.experiment_runs import (
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_RUNNING,
    RunConflict,
    runs,
)
from backend.app.shared.agent.experiments import (
    VARIANT_SEPARATOR,
    add_variant,
    append_step,
    create_experiment,
    delete_experiment,
    delete_variant,
    guard_step_run,
    owned,
    record_step_progress,
    set_notes,
    step_checkpoint,
    step_progress,
    update_annotations,
    variant_presets,
)
from backend.app.shared.agent.freezing import freeze_conversation_message
from backend.app.shared.agent.preset_history import record_preset_revision
from backend.app.shared.agent.versions import (
    draft_override,
    find_revision,
    parse_draft,
    pinned_from_draft,
    pinned_from_preset,
    pinned_from_revision,
    pinned_to_preset,
    require_runnable_preset,
)
from backend.app.shared.backend.account_context import (
    AccountContext,
    bind_account_context,
    get_account_context,
)
from backend.app.shared.crm.sdk import (
    AgentPreset,
    AgentPresetManager,
    ChatHistory,
    ChatHistoryManager,
    Experiment,
    ExperimentManager,
    MAX_VARIANTS_PER_EXPERIMENT,
)
from backend.app.shared.crm.views import format_created_at
from backend.app.shared.utils.log_context import log_event

router = APIRouter(route_class=AccountRoute)

# Finished runs stay readable long enough for a page to collect them, then the
# registry drops them (a restart drops them all: they are not persisted).
RUN_RETENTION_SECONDS = 600.0
RUN_MAX_PARALLEL = 4


class DraftPresetInput(BaseModel):
    prompt: str = ""
    level: int = 0
    tools: list[str] = []
    requires_tool: bool | None = None


class ExperimentSourceInput(BaseModel):
    kind: str = "text"
    conversationId: int | None = None
    messageIndex: int | None = None
    sessionId: str | None = None
    text: str = ""


class VariantInput(BaseModel):
    label: str = ""
    versionKind: str = "preset"
    revisionId: int | None = None
    draft: DraftPresetInput | None = None


class CreateExperimentInput(BaseModel):
    agentId: str = ""
    name: str = ""
    source: ExperimentSourceInput = Field(default_factory=ExperimentSourceInput)
    variants: list[VariantInput] = Field(default_factory=list)


class UpdateExperimentInput(BaseModel):
    name: str | None = None
    notes: str | None = None
    annotations: list[dict[str, Any]] | None = None


class StepInput(BaseModel):
    mode: str = "buyer"
    text: str = ""
    label: str = ""


class RunInput(BaseModel):
    stepIndex: int = Field(ge=0)
    # Re-running a step re-sends its buyer message to every variant; the client
    # must ask for that explicitly.
    force: bool = False


class PromoteInput(BaseModel):
    variantId: str = ""
    note: str = ""


# ---------------------------------------------------------------------------
# Projection


def _experiment_to_dict(
    experiment: Experiment,
    *,
    detail: bool = True,
    agent_names: dict[str, str] | None = None,
) -> dict:
    manager = ExperimentManager()
    content = load_experiment_content(experiment.content)
    names = agent_names if agent_names is not None else {}
    if experiment.agent_id not in names:
        preset = AgentPresetManager().get_agent_preset(experiment.agent_id)
        names[experiment.agent_id] = preset.name if preset is not None else ""
    agent_name = names[experiment.agent_id]
    variants = manager.list_variants(experiment.id)
    payload = {
        "id": str(experiment.id),
        "agentId": experiment.agent_id,
        "name": experiment.name,
        "frozenPrefix": content.frozen_prefix,
        # Ownership and run bookkeeping are internal; the response exposes the
        # provenance of the starting point plus progress the UI needs.
        "source": {
            key: value for key, value in content.source.items()
            if key not in ("owner", "runs")
        },
        "progress": step_progress(experiment),
        "steps": [step.model_dump() for step in content.steps],
        "annotations": [annotation.model_dump() for annotation in content.annotations],
        "notes": content.notes,
        "createdAt": format_created_at(experiment.created_time) or "",
        "updatedAt": format_created_at(experiment.updated_time) or "",
        "activeRunId": _active_run_id(experiment.id),
    }
    if not detail:
        payload["variants"] = [_variant_summary(hist, agent_name) for hist in variants]
        return payload
    sessions = []
    for hist in variants:
        session = _session_to_dict(hist)
        if session is None:
            continue
        # The label is the experiment's own vocabulary; the title repeats it.
        session["label"] = _label_of(hist, agent_name)
        sessions.append(session)
    payload["variants"] = sessions
    return payload


def _variant_summary(hist: ChatHistory, agent_name: str) -> dict:
    content = load_history_content(hist.content)
    return {
        "id": str(hist.id),
        "label": _label_of(hist, agent_name),
        "pinned": content.pinned.model_dump() if content.pinned is not None else None,
        "turnCount": len(content.messages),
        "draft": content.draft is not None or (
            content.pinned is not None and content.pinned.kind == "draft"
        ),
    }


def _label_of(hist: ChatHistory, agent_name: str) -> str:
    """The variant label: the session name without its ``Agent · `` prefix."""
    name = hist.name or ""
    prefix = f"{agent_name}{VARIANT_SEPARATOR}"
    return name[len(prefix):] if name.startswith(prefix) else name


def _active_run_id(experiment_id: int | None) -> str | None:
    if experiment_id is None:
        return None
    state = runs.active_for(experiment_id)
    return state.run_id if state is not None else None


def _experiment_or_404(experiment_id: str) -> Experiment:
    try:
        raw_id = int(experiment_id)
    except (TypeError, ValueError):
        raise AppError("实验不存在", status_code=404)
    experiment = ExperimentManager().get_experiment(raw_id)
    if experiment is None:
        raise AppError("实验不存在", status_code=404)
    return owned(experiment)


def _agent_preset_or_404(agent_id: str) -> AgentPreset:
    preset = AgentPresetManager().get_agent_preset(agent_id)
    if preset is None:
        raise AppError("Agent 不存在", status_code=404)
    return preset


def _variant_of(experiment: Experiment, variant_id: str) -> ChatHistory:
    try:
        raw_id = int(variant_id)
    except (TypeError, ValueError):
        raise AppError("变体不存在", status_code=404)
    hist = ChatHistoryManager().get_chat_history(raw_id)
    if hist is None or hist.experiment_id != experiment.id:
        raise AppError("变体不存在", status_code=404)
    return hist


# ---------------------------------------------------------------------------
# Variant resolution


def _resolve_variant(
    agent_id: str, preset: AgentPreset, variant: VariantInput, index: int,
) -> dict[str, Any]:
    """Turn one request variant into the pin it runs, refusing unrunnable ones."""
    label = (variant.label or "").strip() or f"变体 {index + 1}"
    if variant.versionKind not in ("preset", "draft"):
        raise AppError("versionKind must be preset or draft", status_code=422)
    if variant.versionKind == "draft":
        if variant.draft is None:
            raise AppError("draft is required when versionKind=draft", status_code=422)
        draft = parse_draft(
            prompt=variant.draft.prompt, level=variant.draft.level,
            tools=variant.draft.tools, requires_tool=variant.draft.requires_tool,
        )
        override = draft_override(preset, draft)
        require_runnable_preset(override)
        return {
            "label": label,
            "pinned": pinned_from_draft(draft),
            "requires_tool": bool(draft.requires_tool),
        }
    if variant.revisionId is not None:
        revision = find_revision(agent_id, variant.revisionId)
        if revision is None:
            raise AppError("版本不存在", status_code=404)
        pinned = pinned_from_revision(revision)
        require_runnable_preset(pinned_to_preset(preset, pinned))
        return {
            "label": label,
            "pinned": pinned,
            "requires_tool": bool(pinned.requires_tool),
        }
    require_runnable_preset(preset)
    return {
        "label": label,
        "pinned": pinned_from_preset(preset),
        "requires_tool": bool(getattr(preset, "requires_tool", False)),
    }


def _validate_variant_count(count: int) -> None:
    if count < 1:
        raise AppError("实验至少需要一个变体", status_code=422)
    if count > MAX_VARIANTS_PER_EXPERIMENT:
        raise AppError(
            f"一个实验最多 {MAX_VARIANTS_PER_EXPERIMENT} 个变体", status_code=422
        )


# ---------------------------------------------------------------------------
# Creating: the three starting points


def _source_and_messages(
    agent_id: str, preset: AgentPreset, source: ExperimentSourceInput,
) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
    """Resolve the frozen starting point, its provenance and inherited turns."""
    kind = (source.kind or "").strip()
    if kind == "conversation":
        if source.conversationId is None or source.messageIndex is None:
            raise AppError("conversation 来源需要 conversationId 和 messageIndex", status_code=422)
        frozen, provenance = freeze_conversation_message(source.conversationId, source.messageIndex)
        return frozen, {"kind": "conversation", **provenance}, []
    if kind == "session":
        if not source.sessionId:
            raise AppError("session 来源需要 sessionId", status_code=422)
        hist = _load_session(ChatHistoryManager(), source.sessionId)
        raw = hist.content if isinstance(hist.content, dict) else {}
        content = load_history_content(raw)
        if content.apid != agent_id:
            raise AppError("会话不属于该 Agent", status_code=400)
        session_source = content.source
        frozen = session_source.frozen_input if session_source is not None else ""
        # An experiment continued from a session starts where that session is:
        # every variant inherits its turns so the comparison shares one history.
        return frozen, {
            "kind": "session",
            "sessionId": str(hist.id),
            "conversationId": session_source.conversation_id if session_source else None,
            "conversationTitle": session_source.conversation_title if session_source else "",
            "frozenChars": len(frozen),
        }, history_messages(content)
    if kind == "text":
        text = source.text or ""
        return text, {"kind": "text"}, []
    raise AppError("source.kind 只能是 conversation、session 或 text", status_code=422)


# ---------------------------------------------------------------------------
# Endpoints


@router.post("/api/agent/experiments")
def create_experiment_endpoint(body: CreateExperimentInput) -> dict:
    agent_id = (body.agentId or "").strip()
    if not agent_id:
        raise AppError("agentId is required", status_code=422)
    preset = _agent_preset_or_404(agent_id)
    _validate_variant_count(len(body.variants))
    frozen, source, messages = _source_and_messages(agent_id, preset, body.source)
    variants = [
        _resolve_variant(agent_id, preset, variant, index)
        for index, variant in enumerate(body.variants)
    ]
    name = (body.name or "").strip() or f"{preset.name} 对比实验"
    try:
        experiment, created = create_experiment(
            agent_id=agent_id,
            name=name,
            frozen_prefix=frozen,
            source=source,
            variants=variants,
            requires_tool=bool(getattr(preset, "requires_tool", False)),
            messages=messages,
        )
    except AppError:
        raise
    except Exception:
        logger.exception("request failed")
        return api_error("服务器内部错误", status_code=500)
    log_event(
        "agent.experiment_created", experiment_id=experiment.id, agent_id=agent_id,
        source_kind=source.get("kind"), frozen_chars=len(frozen),
        variant_count=len(created),
    )
    return ok(_experiment_to_dict(experiment))


@router.get("/api/agent/experiments")
def list_experiments_endpoint(agentId: str | None = None) -> dict:
    agent_id = (agentId or "").strip() or None
    try:
        experiments = ExperimentManager().list_experiments(agent_id)
        visible = []
        # One name lookup per distinct agent, not per experiment.
        agent_names: dict[str, str] = {}
        for item in experiments:
            try:
                visible.append(_experiment_to_dict(owned(item), detail=False, agent_names=agent_names))
            except AppError:
                # Another account's experiment must not even be listed.
                continue
        return ok(visible)
    except Exception:
        logger.exception("request failed")
        return api_error("服务器内部错误", status_code=500)


@router.get("/api/agent/experiments/{experiment_id}")
def get_experiment_endpoint(experiment_id: str) -> dict:
    return ok(_experiment_to_dict(_experiment_or_404(experiment_id)))


@router.patch("/api/agent/experiments/{experiment_id}")
def update_experiment_endpoint(experiment_id: str, body: UpdateExperimentInput) -> dict:
    experiment = _experiment_or_404(experiment_id)
    if body.annotations is not None:
        update_annotations(experiment, _parse_annotations(experiment, body.annotations))
    set_notes(
        experiment,
        name=body.name.strip() if body.name is not None else None,
        notes=body.notes,
    )
    return ok(_experiment_to_dict(experiment))


def _parse_annotations(
    experiment: Experiment, rows: list[dict[str, Any]],
) -> list[ExperimentAnnotation]:
    content = load_experiment_content(experiment.content)
    variant_ids = {
        str(hist.id) for hist in ExperimentManager().list_variants(experiment.id)
    }
    annotations: list[ExperimentAnnotation] = []
    for row in rows:
        try:
            annotation = ExperimentAnnotation.model_validate(row)
        except ValidationError:
            raise AppError("批注格式错误", status_code=422)
        if annotation.variantId not in variant_ids:
            raise AppError("批注指向的变体不属于该实验", status_code=422)
        if not 0 <= annotation.step < len(content.steps):
            raise AppError("批注指向的步骤不存在", status_code=422)
        if annotation.verdict not in ("", "better", "worse"):
            raise AppError("verdict 只能是 better、worse 或空", status_code=422)
        annotations.append(annotation)
    return annotations


@router.post("/api/agent/experiments/{experiment_id}/variants")
def add_variant_endpoint(experiment_id: str, body: VariantInput) -> dict:
    experiment = _experiment_or_404(experiment_id)
    preset = _agent_preset_or_404(experiment.agent_id)
    resolved = _resolve_variant(experiment.agent_id, preset, body, 0)
    try:
        add_variant(
            experiment,
            label=resolved["label"],
            pinned=resolved["pinned"],
            requires_tool=resolved["requires_tool"],
        )
    except AppError:
        raise
    except Exception:
        logger.exception("request failed")
        return api_error("服务器内部错误", status_code=500)
    return ok(_experiment_to_dict(experiment))


@router.delete("/api/agent/experiments/{experiment_id}/variants/{session_id}")
def delete_variant_endpoint(experiment_id: str, session_id: str) -> dict:
    experiment = _experiment_or_404(experiment_id)
    try:
        delete_variant(experiment, session_id)
    except AppError:
        raise
    except Exception:
        logger.exception("request failed")
        return api_error("服务器内部错误", status_code=500)
    return ok(_experiment_to_dict(experiment))


@router.delete("/api/agent/experiments/{experiment_id}")
def delete_experiment_endpoint(experiment_id: str) -> dict:
    experiment = _experiment_or_404(experiment_id)
    try:
        delete_experiment(experiment.id)
    except Exception:
        logger.exception("request failed")
        return api_error("服务器内部错误", status_code=500)
    return ok(None)


@router.post("/api/agent/experiments/{experiment_id}/steps")
def add_step_endpoint(experiment_id: str, body: StepInput) -> dict:
    experiment = _experiment_or_404(experiment_id)
    append_step(experiment, mode=body.mode, text=body.text, label=body.label)
    return ok(_experiment_to_dict(experiment))


# ---------------------------------------------------------------------------
# Runs


@router.post("/api/agent/experiments/{experiment_id}/runs")
def start_run_endpoint(experiment_id: str, body: RunInput) -> dict:
    experiment = _experiment_or_404(experiment_id)
    content = load_experiment_content(experiment.content)
    if body.stepIndex >= len(content.steps):
        raise AppError("步骤不存在", status_code=404)
    variants = ExperimentManager().list_variants(experiment.id)
    if not variants:
        raise AppError("实验没有可运行的变体", status_code=422)
    pending = guard_step_run(experiment, body.stepIndex, variants, force=body.force)
    account = get_account_context()
    try:
        state = runs.start(
            experiment.id, body.stepIndex, [str(hist.id) for hist in pending]
        )
    except RunConflict as conflict:
        # The caller needs the id to attach to the run already in progress.
        return JSONResponse(
            status_code=409,
            content={
                "code": 1,
                "msg": "该步骤已有运行中的任务",
                "data": {"runId": conflict.run_id},
            },
        )
    runs.bind_owner(state.run_id, account)
    threading.Thread(
        target=_execute_run,
        args=(experiment.id, body.stepIndex, state, account),
        name=f"experiment-run-{state.run_id}",
        daemon=True,
    ).start()
    log_event(
        "agent.experiment_run_started", experiment_id=experiment.id,
        run_id=state.run_id, step_index=body.stepIndex, variant_count=len(variants),
    )
    return ok(state.snapshot())


def _execute_run(
    experiment_id: int, step_index: int, state: Any, account: AccountContext,
) -> None:
    """Replay one step across the experiment's variants, in the background.

    The whole run belongs to the account that started it: the thread reads the
    live account once and refuses to continue if it changed, and every variant
    thread then runs bound to the captured account so a switch cannot move the
    work into another workspace.
    """
    from agent_pipeline.errors import RunCancelledError
    from agent_pipeline.experiment_runner import ExperimentStep as RunStep, run_experiment

    try:
        experiment = ExperimentManager().get_experiment(experiment_id)
        if experiment is None:
            _fail_all(state, "实验不存在")
            return
        content = load_experiment_content(experiment.content)
        if step_index >= len(content.steps):
            _fail_all(state, "步骤不存在")
            return
        step = content.steps[step_index]
        variants = [
            hist for hist in ExperimentManager().list_variants(experiment_id)
            if str(hist.id) in state.variants
        ]
        live = get_account_context()
        if (live.self_ali_id, live.data_dir) != (account.self_ali_id, account.data_dir):
            _fail_all(state, "账号已切换，本次实验运行已停止")
            return
        with bind_account_context(account):
            presets = variant_presets(experiment, variants)
        sessions = {str(hist.id): hist for hist in variants}

        def execute(variant_id: str, run_step: RunStep) -> dict[str, Any]:
            started = time.perf_counter()
            state.update(variant_id, status=STATUS_RUNNING)
            # Pool threads are fresh: re-check the account before binding it.
            current = get_account_context()
            if (current.self_ali_id, current.data_dir) != (
                account.self_ali_id, account.data_dir
            ):
                _fail_variant(state, variant_id, started, "账号已切换，本次实验运行已停止")
                raise AppError("账号已切换，本次实验运行已停止", status_code=409)
            try:
                with bind_account_context(account):
                    result = _run_variant(
                        experiment=experiment, step=step,
                        session=sessions[variant_id], preset=presets[variant_id],
                        cancel_token=state.token,
                    )
            except RunCancelledError:
                # The run was stopped on purpose: not a variant failure.
                state.update(
                    variant_id, status=STATUS_CANCELLED,
                    duration_ms=(time.perf_counter() - started) * 1000.0,
                )
                raise
            except Exception as exc:
                _fail_variant(state, variant_id, started, _error_text(exc))
                raise
            state.update(
                variant_id, status=STATUS_COMPLETED,
                duration_ms=(time.perf_counter() - started) * 1000.0,
            )
            return result

        run_experiment(
            steps=[RunStep(index=step.index, input=step.text, kind=step.mode, label=step.label)],
            variant_ids=[str(hist.id) for hist in variants],
            execute=execute,
            cancel_token=state.token,
            max_parallel=RUN_MAX_PARALLEL,
        )
    except AppError as exc:
        _fail_all(state, exc.message)
    except Exception:
        logger.exception("Experiment run failed")
        _fail_all(state, "实验运行失败")
    finally:
        # Anything still queued never started: the run was cancelled or abandoned.
        for variant_id, variant in list(state.variants.items()):
            if variant.status not in (STATUS_COMPLETED, STATUS_FAILED, STATUS_CANCELLED):
                state.update(variant_id, status=STATUS_CANCELLED)
        state.finish()
        log_event(
            "agent.experiment_run_finished", run_id=state.run_id,
            experiment_id=experiment_id, status=state.status,
        )


def _run_variant(
    *, experiment: Experiment, step: Any, session: ChatHistory, preset: AgentPreset,
    cancel_token: Any = None,
) -> dict[str, Any]:
    """Run one step for one variant and persist the resulting turn.

    A retried step first truncates back to the checkpoint taken before its first
    attempt, so the buyer message is appended exactly once. A cancelled turn is
    never written.
    """
    manager = ChatHistoryManager()
    hist = manager.get_chat_history(session.id)
    if hist is None:
        raise AppError("变体不存在", status_code=404)
    raw = hist.content if isinstance(hist.content, dict) else {}
    content = load_history_content(raw)
    messages = history_messages(content)
    before = step_checkpoint(experiment, step.index, str(hist.id))
    if before is not None and before <= len(messages):
        messages = messages[:before]
    if step.mode == "buyer":
        messages.append({"role": "user", "content": step.text})
    turn = _run_turn(
        apid=experiment.agent_id,
        preset=preset,
        override=preset,
        messages=messages,
        frozen=content.source.frozen_input if content.source is not None else "",
        mode="simulated",
        conversation_id=content.source.conversation_id if content.source is not None else None,
        revision_id=content.pinned.revision_id if content.pinned is not None else None,
        prior_replies=(),
        cancel_token=cancel_token,
    )
    messages.append(_turn_message(turn))
    manager.edit_chat_history(hist.id, ChatHistory(
        name=hist.name,
        content=build_history_content(
            apid=content.apid, agent_name=content.agent_name,
            messages=messages, existing=raw,
        ),
    ))
    record_step_progress(experiment, step.index, str(hist.id), before=before if before is not None else len(messages) - 2)
    return {"variantId": str(hist.id), "text": turn.text}


def _fail_variant(state: Any, variant_id: str, started: float, reason: str) -> None:
    state.update(
        variant_id, status=STATUS_FAILED, error=reason,
        duration_ms=(time.perf_counter() - started) * 1000.0,
    )


def _fail_all(state: Any, reason: str) -> None:
    for variant_id in list(state.variants):
        state.update(variant_id, status=STATUS_FAILED, error=reason)


def _error_text(exc: Exception) -> str:
    message = getattr(exc, "message", None)
    return str(message or exc or type(exc).__name__)


@router.get("/api/agent/runs/{run_id}")
def get_run_endpoint(run_id: str) -> dict:
    state = _owned_run(run_id)
    if (
        not state.active
        and state.finished_at is not None
        and time.time() - state.finished_at > RUN_RETENTION_SECONDS
    ):
        runs.forget(run_id)
        raise AppError("运行不存在", status_code=404)
    return ok(state.snapshot())


@router.delete("/api/agent/runs/{run_id}")
def cancel_run_endpoint(run_id: str) -> dict:
    state = _owned_run(run_id)
    # Cooperative: the run stops at its next boundary and then reports cancelled.
    state.token.cancel()
    return ok(state.snapshot())


def _owned_run(run_id: str):
    state = runs.get(run_id)
    if state is None:
        raise AppError("运行不存在", status_code=404)
    owner = runs.owner_of(run_id)
    current = get_account_context()
    if owner is not None and (owner.self_ali_id, owner.data_dir) != (
        current.self_ali_id, current.data_dir
    ):
        # A run belongs to the account that started it.
        raise AppError("运行不存在", status_code=404)
    return state


# ---------------------------------------------------------------------------
# Promote: make a variant the agent's saved version


@router.post("/api/agent/experiments/{experiment_id}/promote")
def promote_variant_endpoint(experiment_id: str, body: PromoteInput) -> dict:
    experiment = _experiment_or_404(experiment_id)
    hist = _variant_of(experiment, body.variantId)
    content = load_history_content(hist.content)
    pinned = content.pinned
    if pinned is None or not pinned.prompt:
        raise AppError("该变体没有可保存的版本", status_code=422)
    preset = _agent_preset_or_404(experiment.agent_id)
    promoted = AgentPreset(
        apid=preset.apid,
        name=pinned.name or preset.name,
        description=preset.description,
        prompt=pinned.prompt,
        llm_level=pinned.llm_level,
        tools=list(pinned.tools),
        # Promotion saves the version, not the switch: keep the agent's state.
        enabled=preset.enabled,
        requires_tool=bool(pinned.requires_tool),
    )
    try:
        AgentPresetManager().upsert_agent_preset(promoted)
    except Exception:
        logger.exception("request failed")
        return api_error("服务器内部错误", status_code=500)
    record_preset_revision(
        promoted,
        source="manual",
        note=body.note or f"从实验「{experiment.name or experiment.id}」的变体提升",
    )
    log_event(
        "agent.experiment_promoted", experiment_id=experiment.id,
        variant_id=str(hist.id), apid=promoted.apid, revision_id=pinned.revision_id,
    )
    return ok(_db_preset_to_dict(promoted, updated_at=_updated_at_text(promoted.apid)))


__all__ = ["router"]
