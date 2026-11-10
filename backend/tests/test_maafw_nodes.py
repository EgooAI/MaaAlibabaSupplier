from pathlib import Path

from backend.app.shared.backend.maafw_nodes import (
    DIAGNOSTIC_NODE_NAMES,
    MANUAL_NODE_TESTS,
    PIPELINE_NODE_NAMES,
)
from tools.validate_schema import load_jsonc

ASSETS = Path(__file__).resolve().parents[1] / "assets"


def test_pipeline_registry_matches_pipeline_files():
    names = set()
    for path in (ASSETS / "resource" / "pipeline").glob("*.json"):
        names.update(load_jsonc(path))
    assert names == set(PIPELINE_NODE_NAMES)


def test_diagnostic_nodes_are_registered_and_selectable():
    assert DIAGNOSTIC_NODE_NAMES <= PIPELINE_NODE_NAMES
    assert set(MANUAL_NODE_TESTS.values()) == set(DIAGNOSTIC_NODE_NAMES)
