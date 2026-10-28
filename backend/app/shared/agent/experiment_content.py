"""Host-owned experiment document stored in ``Experiment.content``.

The SDK keeps this JSON opaque. Everything here is business vocabulary: the
frozen starting point, the scripted inputs applied to every variant, and the
merchant's annotations. A variant is a ``ChatHistory`` row; its own pinned
version and turns stay in the existing session schema.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, ValidationError

EXPERIMENT_SCHEMA_VERSION = 1
MAX_STEPS = 20
STEP_MODES = frozenset({"buyer", "agent"})


class ExperimentStep(BaseModel):
    """One input every variant receives at the same position."""

    index: int = 0
    mode: str = "buyer"
    text: str = ""
    label: str = ""


class ExperimentAnnotation(BaseModel):
    step: int
    variantId: str
    verdict: str = ""  # "" | "better" | "worse"
    note: str = ""


class ExperimentContent(BaseModel):
    schema_version: int = EXPERIMENT_SCHEMA_VERSION
    frozen_prefix: str = ""
    source: dict[str, Any] = Field(default_factory=dict)
    steps: list[ExperimentStep] = Field(default_factory=list)
    annotations: list[ExperimentAnnotation] = Field(default_factory=list)
    notes: str = ""


def load_experiment_content(raw: Any) -> ExperimentContent:
    if not isinstance(raw, dict):
        return ExperimentContent()
    try:
        return ExperimentContent.model_validate(raw)
    except ValidationError:
        return ExperimentContent()


def build_experiment_content(
    *,
    existing: dict[str, Any] | None = None,
    frozen_prefix: str | None = None,
    source: dict[str, Any] | None = None,
    steps: list[ExperimentStep] | None = None,
    annotations: list[ExperimentAnnotation] | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    current = load_experiment_content(existing)
    content = current.model_copy(update={
        "frozen_prefix": current.frozen_prefix if frozen_prefix is None else frozen_prefix,
        "source": current.source if source is None else source,
        "steps": current.steps if steps is None else steps,
        "annotations": current.annotations if annotations is None else annotations,
        "notes": current.notes if notes is None else notes,
    })
    return content.model_dump()


__all__ = [
    "EXPERIMENT_SCHEMA_VERSION",
    "MAX_STEPS",
    "STEP_MODES",
    "ExperimentAnnotation",
    "ExperimentContent",
    "ExperimentStep",
    "build_experiment_content",
    "load_experiment_content",
]
