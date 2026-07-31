import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.shared.utils import env as env_utils


class EnvUtilsTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        self.env_file = self.temp_path / ".env"
        self.original_loaded_paths = dict(env_utils._ENV_LOADED_PATHS)
        self.original_system_prompt = os.environ.get("SYSTEM_PROMPT")

    def tearDown(self) -> None:
        env_utils._ENV_LOADED_PATHS.clear()
        env_utils._ENV_LOADED_PATHS.update(self.original_loaded_paths)
        if self.original_system_prompt is None:
            os.environ.pop("SYSTEM_PROMPT", None)
        else:
            os.environ["SYSTEM_PROMPT"] = self.original_system_prompt
        self.temp_dir.cleanup()

    def test_set_env_text_updates_existing_key_and_preserves_other_lines(self) -> None:
        self.env_file.write_text("FOO=bar\nSYSTEM_PROMPT=old\nBAR=baz\n", encoding="utf-8")

        env_utils.set_env_text("SYSTEM_PROMPT", 'Line 1\nLine "2"', env_filename=str(self.env_file))

        self.assertEqual(
            self.env_file.read_text(encoding="utf-8"),
            'FOO=bar\nSYSTEM_PROMPT="Line 1\\nLine \\"2\\""\nBAR=baz\n',
        )
        self.assertEqual(os.environ["SYSTEM_PROMPT"], 'Line 1\nLine "2"')

    def test_get_env_text_decodes_newlines_from_loaded_env(self) -> None:
        self.env_file.write_text('SYSTEM_PROMPT="Line 1\\nLine 2"\n', encoding="utf-8")

        env_utils._ENV_LOADED_PATHS.pop(str(self.env_file), None)
        env_utils.load_workdir_env(str(self.env_file))

        self.assertEqual(env_utils.get_env_text("SYSTEM_PROMPT"), "Line 1\nLine 2")


if __name__ == "__main__":
    unittest.main()
