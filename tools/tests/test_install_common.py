"""Release selection for install scripts must honor pinned version patterns."""

import unittest
import importlib
from pathlib import Path
import shutil
import sys
import tempfile
from unittest.mock import patch
import zipfile

from tools import _common

with patch.dict(sys.modules, {"_common": _common}):
    install_maafw = importlib.import_module("tools.install_3_maafw")


class ReleasePatternTests(unittest.TestCase):
    def test_pin_keeps_minor_and_allows_prereleases(self):
        tags = ["1.5.0", "1.40.0", "1.4.8-beta19", "1.4.7"]
        with patch.object(_common, "fetch_json", return_value=[{"tag_name": tag} for tag in tags]):
            release = _common.github_release_by_tag_pattern("owner/repo", "1.4.*")
        self.assertEqual(release["tag_name"], "1.4.8-beta19")

    def test_unmatched_pattern_fails(self):
        with patch.object(_common, "fetch_json", return_value=[{"tag_name": "1.5.0"}]):
            with self.assertRaises(SystemExit) as error:
                _common.github_release_by_tag_pattern("owner/repo", "1.4.*")
        self.assertIn("1.4.*", str(error.exception))


class MaaFrameworkInstallTests(unittest.TestCase):
    def test_version_change_replaces_runtime_preserves_schema_and_reuses_matching_install(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backend = root / "backend"
            deps = backend / "deps"
            (deps / "bin").mkdir(parents=True)
            (deps / "tools").mkdir()
            (deps / "bin/MaaPiCli.exe").write_text("old CLI")
            (deps / "bin/obsolete.dll").write_text("old runtime")
            (deps / "tools/pipeline.schema.json").write_text("tracked schema")
            framework = root / "framework.zip"
            with zipfile.ZipFile(framework, "w") as archive:
                archive.writestr("bin/MaaFramework.dll", "new runtime")
                archive.writestr("bin/MaaUtils.dll", "new path library")
                archive.writestr("bin/MaaPiCli.exe", "bundled CLI")
                archive.writestr("tools/pipeline.schema.json", "upstream schema")

            def download(release, pattern, destination):
                shutil.copy2(framework, destination)
                return destination

            with patch.object(_common, "BACKEND_DIR", backend), patch.object(
                _common, "github_release_by_tag", side_effect=lambda repo, tag: repo,
            ) as fetch, patch.object(_common, "download_release_asset", side_effect=download):
                install_maafw.install_maafw()
                self.assertEqual((deps / "bin/MaaFramework.dll").read_text(), "new runtime")
                self.assertEqual((backend / "assets/MaaUtils.dll").read_text(), "new path library")
                self.assertEqual((backend / "assets/MaaPiCli.exe").read_text(), "bundled CLI")
                self.assertEqual((deps / "tools/pipeline.schema.json").read_text(), "tracked schema")
                self.assertFalse((deps / "bin/obsolete.dll").exists())
                fetch.reset_mock()
                install_maafw.install_maafw()
                fetch.assert_not_called()
                # A changed pin must invalidate the existing installation.
                with patch.object(_common, "MAAFW_VERSION", "v99.0.0"):
                    install_maafw.install_maafw()
                    fetch.assert_any_call(_common.MAAFW_REPO, "v99.0.0")


if __name__ == "__main__":
    unittest.main()
