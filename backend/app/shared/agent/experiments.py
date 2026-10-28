"""Experiments: one frozen starting point compared across several agent versions.

An experiment is a host concept built from SDK primitives. The SDK stores its
identity and an opaque ``content`` document; this module owns that document's
business rules (steps, annotations, notes) and the ``ChatHistory`` rows that
serve as variants.

Everything here is plain Python so it can run inside a request, a test, or a
background worker without FastAPI.
"""

from __future__ import annotations

from pathlib import Path
from threading import Lock
from typing import Any, Iterable

from backend.app.api.envelope import AppError
from backend.app.shared.agent.chat_history_content import (
    ChatHistoryPin,
    ChatHistorySource,
    build_history_content,
    load_history_content,
)
from backend.app.shared.agent.experiment_content import (
    MAX_STEPS,
    STEP_MODES,
    ExperimentAnnotation,
    ExperimentStep,
    build_experiment_content,
    load_experiment_content,
)
from backend.app.shared.agent.versions import (
    current_scope,
    draft_override,
    pin_to_draft,
    pinned_override,
    require_runnable_preset,
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

VARIANT_SEPARATOR = " · "

# Variant threads finish at the same time and all update the experiment's step
# progress, so their read-modify-write cycles must not interleave.
_PROGRESS_LOCK = Lock()


def create_experiment(
    *,
    agent_id: str,
    name: str,
    frozen_prefix: str,
    source: dict[str, Any],
    variants: list[dict[str, Any]],
    requires_tool: bool,
    messages: list[dict[str, Any]] | None = None,
) -> tuple[Experiment, list[ChatHistory]]:
    """Create an experiment and one playground session per variant.

    ``variants`` entries are already-resolved pins: ``{"label": str, "pinned":
    ChatHistoryPin}``. ``messages`` seeds every variant with turns that already
    happened (used when an experiment continues an existing session); a new
    experiment from a conversation or raw text starts empty.

    The caller validates the pins; this only persists them.
    """
    if not variants:
        raise AppError("实验至少需要一个变体", status_code=422)
    if len(variants) > MAX_VARIANTS_PER_EXPERIMENT:
        raise AppError(
            f"一个实验最多 {MAX_VARIANTS_PER_EXPERIMENT} 个变体", status_code=422
        )
    agent_name = _agent_name(agent_id)
    experiment = Experiment(
        agent_id=agent_id,
        name=name,
        content=build_experiment_content(
            frozen_prefix=frozen_prefix, source=_with_owner(source),
        ),
    )
    manager = ExperimentManager()
    manager.add_experiment(experiment)
    if experiment.id is None:
        raise AppError("实验创建失败", status_code=500)

    histories = ChatHistoryManager()
    created: list[ChatHistory] = []
    for variant in variants:
        hist = _new_variant(
            agent_id=agent_id, agent_name=agent_name, label=str(variant.get("label") or ""),
            pinned=variant.get("pinned"), frozen_prefix=frozen_prefix, source=source,
            requires_tool=requires_tool,
            messages=list(messages or []),
        )
        histories.add_chat_history(hist)
        manager.attach_variant(experiment.id, hist.id)
        created.append(hist)
    return experiment, created


def append_step(
    experiment: Experiment, *, mode: str, text: str, label: str = "",
) -> ExperimentStep:
    """Append one scripted step that every variant will receive.

    ``buyer`` steps send the text as the buyer's next message; ``agent`` steps
    ask the agent to continue on its own current context, so their text may be
    empty (it is then only a label for the UI).
    """
    if mode not in STEP_MODES:
        raise AppError(f"mode 只能是 {'、'.join(sorted(STEP_MODES))}", status_code=422)
    content = load_experiment_content(experiment.content)
    if len(content.steps) >= MAX_STEPS:
        raise AppError(f"一个实验最多 {MAX_STEPS} 步", status_code=422)
    body = (text or "").strip()
    if mode == "buyer" and not body:
        raise AppError("买家模式下步骤内容不能为空", status_code=422)
    step = ExperimentStep(
        index=len(content.steps), mode=mode, text=body,
        label=(label or "").strip(),
    )
    steps = [*content.steps, step]
    _save_content(experiment, steps=steps)
    return step


def update_annotations(
    experiment: Experiment, annotations: Iterable[ExperimentAnnotation],
) -> None:
    """Replace every annotation; the editor sends the full list."""
    _save_content(experiment, annotations=list(annotations))


def set_notes(experiment: Experiment, *, name: str | None = None, notes: str | None = None) -> None:
    """Rename the experiment and/or replace its notes."""
    if name is not None:
        experiment.name = name
        ExperimentManager().edit_experiment(
            experiment.id, Experiment(agent_id=experiment.agent_id, name=name, content=experiment.content),
        )
    if notes is not None:
        _save_content(experiment, notes=notes)


def add_variant(
    experiment: Experiment, *, label: str, pinned: ChatHistoryPin, requires_tool: bool,
) -> ChatHistory:
    """Attach one more variant, seeded with the experiment's source turns."""
    manager = ExperimentManager()
    existing = manager.list_variants(experiment.id)
    if len(existing) >= MAX_VARIANTS_PER_EXPERIMENT:
        raise AppError(f"一个实验最多 {MAX_VARIANTS_PER_EXPERIMENT} 个变体", status_code=422)
    content = load_experiment_content(experiment.content)
    # The new variant starts from the same source turns as its siblings: the
    # experiment's own frozen prefix plus whatever the variants inherited.
    # Taking turns from another variant would bind it to that variant's answers.
    messages = _lineage_messages(existing)
    hist = _new_variant(
        agent_id=experiment.agent_id, agent_name=_agent_name(experiment.agent_id),
        label=label, pinned=pinned, frozen_prefix=content.frozen_prefix,
        source=content.source, requires_tool=requires_tool,
        messages=messages,
    )
    ChatHistoryManager().add_chat_history(hist)
    manager.attach_variant(experiment.id, hist.id)
    return hist


def delete_variant(experiment: Experiment, session_id: str) -> None:
    """Detach a variant and delete its session.

    Removing a variant from the comparison deletes the session with it: the
    merchant asked to compare fewer versions, and a detached row would linger in
    the playground history with no experiment to explain it.
    """
    hist = _variant_of(experiment.id, session_id)
    ExperimentManager().detach_variant(hist.id)
    ChatHistoryManager().delete_chat_history(hist.id)


def delete_experiment(experiment_id: int) -> None:
    """Delete an experiment and every variant session.

    The SDK only detaches variants so plain experiments stay readable; here the
    sessions exist solely for the comparison, so they are deleted with it rather
    than left behind as orphaned playground sessions.
    """
    manager = ExperimentManager()
    experiment = manager.get_experiment(experiment_id)
    if experiment is None:
        return
    histories = ChatHistoryManager()
    for variant in manager.list_variants(experiment_id):
        manager.detach_variant(variant.id)
        histories.delete_chat_history(variant.id)
    manager.delete_experiment(experiment_id)


def variant_presets(
    experiment: Experiment, variants: list[ChatHistory],
) -> dict[str, AgentPreset]:
    """The preset each variant runs, resolved from the version it is pinned to.

    A draft-pinned variant runs its snapshot; a preset-pinned variant runs the
    saved version it captured, even if the agent was edited since. Raises
    :class:`AppError` when the agent is gone or a variant can no longer run.
    """
    preset = AgentPresetManager().get_agent_preset(experiment.agent_id)
    if preset is None:
        raise AppError("Agent 不存在", status_code=404)
    resolved: dict[str, AgentPreset] = {}
    for variant in variants:
        pinned = load_history_content(variant.content).pinned
        draft = pin_to_draft(pinned)
        override = draft_override(preset, draft) if draft is not None else pinned_override(preset, pinned)
        require_runnable_preset(override or preset)
        resolved[str(variant.id)] = override or preset
    return resolved


# ---------------------------------------------------------------------------
# Internals


def _save_content(
    experiment: Experiment,
    *,
    steps: list[ExperimentStep] | None = None,
    annotations: list[ExperimentAnnotation] | None = None,
    notes: str | None = None,
) -> None:
    if experiment.id is None:
        raise AppError("实验不存在", status_code=404)
    experiment.content = build_experiment_content(
        existing=_adopt_owner(experiment), steps=steps, annotations=annotations, notes=notes,
    )
    ExperimentManager().edit_experiment(
        experiment.id,
        Experiment(agent_id=experiment.agent_id, name=experiment.name, content=experiment.content),
    )


def _adopt_owner(experiment: Experiment) -> dict[str, Any]:
    """Stamp ownership onto a record that predates it, on its first write."""
    raw = experiment.content if isinstance(experiment.content, dict) else {}
    content = load_experiment_content(raw)
    owner = content.source.get("owner")
    if isinstance(owner, dict) and _text(owner.get("seller")):
        return raw
    return build_experiment_content(existing=raw, source=_with_owner(content.source))


def _new_variant(
    *,
    agent_id: str,
    agent_name: str,
    label: str,
    pinned: ChatHistoryPin | None,
    frozen_prefix: str,
    source: dict[str, Any],
    requires_tool: bool,
    messages: list[dict[str, Any]],
) -> ChatHistory:
    return ChatHistory(
        name=f"{agent_name}{VARIANT_SEPARATOR}{label}" if label else agent_name,
        content=build_history_content(
            apid=agent_id,
            agent_name=agent_name,
            messages=messages,
            session_type="playground",
            scope=current_scope(),
            pinned=pinned,
            source=_source_model(source, frozen_prefix),
            requires_tool=requires_tool,
        ),
    )


def _source_model(source: dict[str, Any], frozen_prefix: str) -> ChatHistorySource:
    """Project the experiment's provenance record onto a session source."""
    return ChatHistorySource(
        session_id=_text(source.get("sessionId")) or None,
        message_index=_int_or_none(source.get("messageIndex")),
        conversation_id=_int_or_none(source.get("conversationId")),
        conversation_title=_text(source.get("conversationTitle")),
        frozen_input=frozen_prefix,
    )


def _lineage_messages(variants: list[ChatHistory]) -> list[dict[str, Any]]:
    """Turns a variant added now starts from: the experiment's shared dialogue.

    Variants share the frozen prefix and the scripted buyer steps, and differ in
    the answers those steps produced. Seeding a new variant with the shared
    ``buyer`` turns (but not the variant answers) lets it join the comparison at
    the same point and answer every step itself, instead of inheriting another
    variant's answers or silently skipping the steps already run.
    """
    if not variants:
        return []
    contents = [load_history_content(variant.content) for variant in variants]
    shared = min((content.messages for content in contents), key=len, default=[])
    common: list[dict[str, Any]] = []
    for index, message in enumerate(shared):
        payload = message.model_dump()
        if any(content.messages[index].model_dump() != payload for content in contents):
            break
        common.append(payload)
    return [message for message in common if message.get("role") == "user"]


def _variant_of(experiment_id: int, session_id: str) -> ChatHistory:
    try:
        raw_id = int(session_id)
    except (TypeError, ValueError):
        raise AppError("变体不存在", status_code=404)
    hist = ChatHistoryManager().get_chat_history(raw_id)
    if hist is None or hist.experiment_id != experiment_id:
        raise AppError("变体不存在", status_code=404)
    return hist


def _agent_name(agent_id: str) -> str:
    preset = AgentPresetManager().get_agent_preset(agent_id)
    if preset is None:
        raise AppError("Agent 不存在", status_code=404)
    return preset.name


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _with_owner(source: dict[str, Any]) -> dict[str, Any]:
    """Stamp the owning seller and data directory onto the provenance record.

    The SDK keeps experiments in one table with no scope column, so ownership is
    part of the host's content document: reading or editing an experiment must
    check it against the current account, exactly like a session's scope.
    """
    scope = current_scope()
    return {**source, "owner": {"seller": scope.seller, "dataDir": scope.data_dir}}


def owned(experiment: Experiment) -> Experiment:
    """Refuse an experiment that belongs to another account."""
    content = load_experiment_content(experiment.content)
    scope = current_scope()
    owner = content.source.get("owner")
    owner = owner if isinstance(owner, dict) else {}
    owner_seller = _text(owner.get("seller"))
    owner_dir = _text(owner.get("dataDir"))
    if not owner_seller and not owner_dir:
        # Created before ownership was recorded: the first write adopts it.
        return experiment
    if owner_seller != scope.seller:
        raise AppError("实验不存在", status_code=404)
    if owner_dir and not _same_dir(owner_dir, scope.data_dir):
        raise AppError("实验不存在", status_code=404)
    return experiment


def _same_dir(left: str, right: str) -> bool:
    try:
        return str(Path(left).expanduser().resolve()).casefold() == str(
            Path(right).expanduser().resolve()
        ).casefold()
    except (OSError, RuntimeError):
        return left.strip().casefold() == right.strip().casefold()


# ---------------------------------------------------------------------------
# Run progress
#
# Steps are replayed into the variants' transcripts, so a step must not be sent
# twice unless the merchant explicitly asks for a fresh comparison.


def step_progress(experiment: Experiment) -> dict[str, Any]:
    """Per-step completion records: ``{step: {"variants": [...], "before": {...}}}``."""
    content = load_experiment_content(experiment.content)
    runs = content.source.get("runs")
    return runs if isinstance(runs, dict) else {}


def guard_step_run(
    experiment: Experiment, step_index: int, variants: list[ChatHistory], *, force: bool,
) -> list[ChatHistory]:
    """Return the variants that still need this step, refusing a no-op run.

    Steps replay a buyer message into each transcript, so re-running one must not
    append it twice: a retried variant is truncated back to the checkpoint taken
    before the step was first applied. ``force`` only lifts the "already done"
    refusal; it never injects a duplicate turn.
    """
    if not variants:
        return []
    progress = step_progress(experiment)
    complete = set(progress.get(str(step_index), {}).get("variants") or [])
    pending = [hist for hist in variants if str(hist.id) not in complete]
    if not pending:
        if not force:
            raise AppError("该步骤已经运行过，如需重试请显式确认", status_code=409)
        return list(variants)
    if step_index > 0 and not progress.get(str(step_index - 1), {}).get("variants"):
        raise AppError("请先运行上一步", status_code=409)
    return pending


def step_checkpoint(experiment: Experiment, step_index: int, variant_id: str) -> int | None:
    """Turn count this variant had before the step was first applied."""
    record = step_progress(experiment).get(str(step_index)) or {}
    before = record.get("before") if isinstance(record, dict) else None
    value = (before or {}).get(variant_id)
    return int(value) if isinstance(value, int) else None


def record_step_progress(
    experiment: Experiment, step_index: int, variant_id: str, *, before: int,
) -> None:
    """Record that one variant finished a step, without touching other fields.

    Variants run in parallel and each records its own completion, so the
    read-modify-write of the shared content document is serialized here.
    ``before`` is the turn count from before the step, used to truncate a retry.
    """
    with _PROGRESS_LOCK:
        raw = experiment.content if isinstance(experiment.content, dict) else {}
        content = load_experiment_content(raw)
        runs = dict(content.source.get("runs") or {})
        record = dict(runs.get(str(step_index)) or {})
        done = [str(item) for item in (record.get("variants") or [])]
        if variant_id not in done:
            done.append(variant_id)
        before_map = dict(record.get("before") or {})
        before_map.setdefault(variant_id, before)
        record["variants"] = done
        record["before"] = before_map
        runs[str(step_index)] = record
        source = {**content.source, "runs": runs}
        experiment.content = build_experiment_content(existing=raw, source=source)
        ExperimentManager().edit_experiment(
            experiment.id,
            Experiment(agent_id=experiment.agent_id, name=experiment.name, content=experiment.content),
        )


__all__ = [
    "VARIANT_SEPARATOR",
    "add_variant",
    "append_step",
    "create_experiment",
    "delete_experiment",
    "delete_variant",
    "guard_step_run",
    "owned",
    "record_step_progress",
    "set_notes",
    "step_checkpoint",
    "step_progress",
    "update_annotations",
    "variant_presets",
]
