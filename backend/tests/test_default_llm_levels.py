import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.app.shared.agent.default_llm_levels import (
    DEFAULT_SEEDED_LLM_LEVEL,
    ensure_default_llm_levels_seeded,
)
from backend.app.shared.crm.sdk import LLMApiConfig, LLMApiConfigManager


class DefaultLlmLevelsTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        os.environ["MAA_CRM_DB_PATH"] = str(Path(self.temp_dir.name) / "crm.sqlite")
        self.addCleanup(self.temp_dir.cleanup)
        self.addCleanup(os.environ.pop, "MAA_CRM_DB_PATH", None)

    def tearDown(self) -> None:
        try:
            LLMApiConfigManager().engine.dispose()
        except Exception:
            pass

    def test_seeds_level_zero_blank_on_fresh_db(self) -> None:
        ensure_default_llm_levels_seeded()

        config = LLMApiConfigManager().get_config(DEFAULT_SEEDED_LLM_LEVEL)
        self.assertIsNotNone(config)
        assert config is not None
        self.assertEqual(config.base_url, "")
        self.assertEqual(config.api_key, "")
        self.assertEqual(config.model_name, "")

    def test_seed_is_idempotent(self) -> None:
        ensure_default_llm_levels_seeded()
        ensure_default_llm_levels_seeded()

        self.assertEqual(len(LLMApiConfigManager().list_configs()), 1)

    def test_seed_never_overwrites_user_row(self) -> None:
        LLMApiConfigManager().upsert_config(
            LLMApiConfig(
                level=DEFAULT_SEEDED_LLM_LEVEL,
                base_url="https://llm.example",
                api_key="k",
                model_name="m",
            )
        )
        ensure_default_llm_levels_seeded()

        config = LLMApiConfigManager().get_config(DEFAULT_SEEDED_LLM_LEVEL)
        assert config is not None
        self.assertEqual(config.base_url, "https://llm.example")
        self.assertEqual(config.api_key, "k")
        self.assertEqual(config.model_name, "m")


if __name__ == "__main__":
    unittest.main()
