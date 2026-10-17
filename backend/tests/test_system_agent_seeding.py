import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from backend.app.shared.agent.system_agents import CHAT_TRANSLATION_AGENT_APID
from backend.app.shared.agent.system_presets import (
    apply_system_agent_default,
    ensure_system_agents_seeded,
    find_system_agent_default,
    preset_fingerprint_of,
    preset_seed_fingerprint,
    seed_meta_key,
)
from backend.app.shared.crm.sdk import AgentPreset, AgentPresetManager, Meta, MetaManager

APID = CHAT_TRANSLATION_AGENT_APID


class SystemAgentSeedTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.enterContext(mock.patch.dict(os.environ, {
            "MAA_CRM_DB_PATH": str(Path(self.temp_dir.name) / "crm.sqlite"),
        }))

    def tearDown(self) -> None:
        try:
            AgentPresetManager().engine.dispose()
        finally:
            self.temp_dir.cleanup()

    def _preset(self) -> AgentPreset:
        preset = AgentPresetManager().get_agent_preset(APID)
        assert preset is not None
        return preset

    def test_seeding_writes_default_and_fingerprint(self) -> None:
        ensure_system_agents_seeded()

        default = find_system_agent_default(APID)
        assert default is not None
        preset = self._preset()
        self.assertEqual(preset.prompt, default["prompt"])
        self.assertTrue(preset.enabled)
        stored = MetaManager().get_meta(seed_meta_key(APID))
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored.value, preset_seed_fingerprint(default))

    def test_user_edit_survives_reseeding(self) -> None:
        ensure_system_agents_seeded()
        manager = AgentPresetManager()
        edited = self._preset()
        edited.prompt = "用户改写的提示词"
        manager.upsert_agent_preset(edited)

        ensure_system_agents_seeded()

        self.assertEqual(self._preset().prompt, "用户改写的提示词")

    def test_pristine_previous_default_is_upgraded(self) -> None:
        ensure_system_agents_seeded()
        manager = AgentPresetManager()
        old_default = self._preset()
        old_default.prompt = "旧版本默认提示词"
        manager.upsert_agent_preset(old_default)
        # 模拟上一版本种子：指纹记录的是旧默认内容。
        MetaManager().upsert_meta(Meta(
            key=seed_meta_key(APID),
            value=preset_fingerprint_of(old_default),
        ))

        ensure_system_agents_seeded()

        default = find_system_agent_default(APID)
        assert default is not None
        self.assertEqual(self._preset().prompt, default["prompt"])

    def test_legacy_modified_preset_without_fingerprint_is_kept(self) -> None:
        AgentPresetManager().upsert_agent_preset(AgentPreset(
            apid=APID,
            name="翻译",
            description="旧库手工描述",
            prompt="旧库手工内容",
            llm_level=2,
            tools=[],
        ))

        ensure_system_agents_seeded()

        preset = self._preset()
        self.assertEqual(preset.prompt, "旧库手工内容")
        self.assertEqual(preset.llm_level, 2)

    def test_restore_default_resets_prompt_and_fingerprint(self) -> None:
        ensure_system_agents_seeded()
        manager = AgentPresetManager()
        edited = self._preset()
        edited.prompt = "custom"
        manager.upsert_agent_preset(edited)

        restored = apply_system_agent_default(APID)

        assert restored is not None
        default = find_system_agent_default(APID)
        assert default is not None
        self.assertEqual(restored.prompt, default["prompt"])
        stored = MetaManager().get_meta(seed_meta_key(APID))
        assert stored is not None
        self.assertEqual(stored.value, preset_seed_fingerprint(default))
        ensure_system_agents_seeded()
        self.assertEqual(self._preset().prompt, restored.prompt)


if __name__ == "__main__":
    unittest.main()
