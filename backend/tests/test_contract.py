from backend.app.shared import contract
from backend.app.shared.crm.sdk import LLM_MAX_LEVEL as SDK_LLM_MAX_LEVEL


def test_llm_max_level_matches_sdk_validation():
    assert contract.LLM_MAX_LEVEL == SDK_LLM_MAX_LEVEL
