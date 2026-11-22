# Compare production pipeline resources with the logging allowlist.
# Avoid constant-only or mutually derived set assertions; API/GUI tests cover entry behavior.

from pathlib import Path

from backend.app.shared.backend.maafw_nodes import PIPELINE_NODE_NAMES
from tools.validate_schema import load_jsonc

ASSETS = Path(__file__).resolve().parents[1] / "assets"


def test_pipeline_registry_matches_pipeline_files():
    names = set()
    for path in (ASSETS / "resource" / "pipeline").glob("*.json"):
        names.update(load_jsonc(path))
    assert names == set(PIPELINE_NODE_NAMES)
