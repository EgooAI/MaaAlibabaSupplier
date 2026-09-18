"""Packaging contract tests use temporary payloads, never application state."""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools import install


class PackagingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.payload = self.root / "install"
        self.payload.mkdir()
        self.environment = {
            "GITHUB_REPOSITORY": "EgooAI/MaaAlibabaSupplier",
            "GITHUB_SHA": "a" * 40,
            "GITHUB_RUN_ID": "12345678901",
            "GITHUB_RUN_NUMBER": "52",
            "GITHUB_RUN_ATTEMPT": "2",
        }
        self.version = "v1.2.3-ci.4-abcdef0"
        self.expected = {
            "schema_version": 1,
            "app_id": "580868F7-B96A-4214-829A-609D552F2C3A",
            "version": self.version,
            "repository": self.environment["GITHUB_REPOSITORY"],
            "sha": self.environment["GITHUB_SHA"],
            "run_id": 12345678901,
            "run_number": 52,
            "run_attempt": 2,
        }
        self.enterContext(patch.dict(os.environ, self.environment, clear=True))
        self.enterContext(patch.object(install, "install_path", self.payload))

    def test_ci_payload_identity(self):
        install.install_build_info(self.version)
        info = json.loads((self.payload / "build-info.json").read_text(encoding="utf-8"))
        self.assertEqual(info, self.expected)

    def test_local_build_fallbacks(self):
        for environment in ({}, {name: "" for name in self.environment}):
            with self.subTest(environment=environment), patch.dict(os.environ, environment, clear=True):
                install.install_build_info(install.DEFAULT_VERSION)
                info = json.loads((self.payload / "build-info.json").read_text())
                self.assertEqual(info, {
                    **self.expected, "version": "v0.0.0-local", "repository": None,
                    "sha": None, "run_id": 0, "run_number": 0, "run_attempt": 0,
                })

    def test_payload_assembly_writes_identity(self):
        repo = self.root / "repo"
        fixtures = {
            "backend/__init__.py": "",
            "backend/app/main.py": "# fake backend",
            "backend/deps/bin/MaaPiCli.exe": "fake runtime",
            "backend/deps/share/MaaAgentBinary/agent.py": "# fake agent",
            "backend/assets/interface.json": '{"agent": {"child_exec": "python"}}',
            "backend/assets/MaaCommonAssets/OCR/ppocr_v5/zh_cn/model.txt": "fake OCR",
            "frontend/out/index.html": "fake frontend",
            "README.md": "fake readme", "LICENSE": "fake license", ".env.example": "FAKE=1",
            "tools/packaging/Start-Debug.bat": "@echo fake",
        }
        for relative, content in fixtures.items():
            target = repo / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        with patch.multiple(
            install, repo_root=repo, working_dir=repo / "backend",
            assets_dir=repo / "backend/assets", payload_backend=self.payload / "backend",
        ), patch("sys.argv", ["install.py", self.version]):
            install.main()
        self.assertEqual(json.loads((self.payload / "build-info.json").read_text()), self.expected)
        interface = json.loads((self.payload / "backend/assets/interface.json").read_text())
        self.assertEqual(interface["version"], self.version)
        self.assertTrue((self.payload / "frontend/out/index.html").is_file())

class InstallerPolicyTests(unittest.TestCase):
    """Static safety checks complement compilation; they do not exercise Inno rollback."""

    def setUp(self):
        script = (Path(__file__).resolve().parents[1] / "packaging/Setup.iss").read_text(encoding="utf-8")
        self.script = script
        self.code = re.sub(r"\{[^}]*\}", "", script.split("[Code]", 1)[1])

    def test_v1_leaves_obsolete_source_under_inno_file_management(self):
        # Deleting or moving trees before extraction bypasses Inno's rollback.
        self.assertNotIn("[InstallDelete]", self.script)
        self.assertNotRegex(self.code, r"(?i)\b(DelTree|DeleteFile|RemoveDir|RenameFile|Exec|ShellExec)\s*\(")
        self.assertNotIn("external ", self.code.split("procedure RequireOrdinaryPath", 1)[1])

    def test_process_guard_only_uses_target_executable_paths(self):
        guard = self.code.split("Processes :=", 1)[1].split("end;", 1)[0]
        self.assertNotIn("CommandLine", guard)
        self.assertNotIn("ProcessName", guard)
        self.assertNotIn("Process.Name", guard)
        # Require every blocking predicate to be a directory-bounded prefix of
        # this installation, so null paths and other users' Python cannot match.
        condition = guard.split("    if (Pos", 1)[1].split(" then", 1)[0]
        condition = "(Pos" + condition
        predicates = [predicate.strip() for predicate in condition.split(" or")]
        prefixes = []
        for predicate in predicates:
            match = re.fullmatch(r"\(Pos\(Lowercase\(AppPath \+ '([^']+)'\), ExePath\) = 1\)", predicate)
            self.assertIsNotNone(match, predicate)
            prefixes.append(match[1])
        self.assertEqual(set(prefixes), {"backend\\python\\", "backend\\deps\\bin\\", "backend\\.portable\\yak\\"})
        self.assertEqual(guard.count("RaiseException("), 1)
        self.assertIn("ExePath := '';", guard)
        self.assertIn("if not VarIsNull(Process.ExecutablePath) then", guard)


class InnoCompileTests(unittest.TestCase):
    def test_compile_excludes_mutable_payload_and_requires_identity(self):
        compiler = os.environ.get("ISCC_EXECUTABLE") or shutil.which("ISCC.exe")
        if not compiler:
            self.skipTest("ISCC is not installed; set ISCC_EXECUTABLE to enable compile-only verification")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            packaging = root / "tools" / "packaging"
            packaging.mkdir(parents=True)
            script = packaging / "Setup.iss"
            shutil.copy2(Path(__file__).resolve().parents[1] / "packaging" / "Setup.iss", script)
            payload = root / "install"
            files = {
                ".env": "FAKE_BUILD_ENV=1",
                "build-info.json": json.dumps({"schema_version": 1}),
                "backend/app/main.py": "# fake source",
                "frontend/out/index.html": "fake frontend",
            }
            mutable_paths = (
                "backend/data", "backend/debug", "backend/assets/config", "backend/assets/data",
                "backend/assets/debug", "data", "debug",
            )
            for index, directory in enumerate(mutable_paths):
                files[f"{directory}/nested/mutable-sentinel-{index}.txt"] = "must not ship"
            for relative, content in files.items():
                target = payload / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
            result = subprocess.run([compiler, str(script)], capture_output=True, text=True)
            output = result.stdout + result.stderr
            self.assertEqual(result.returncode, 0, output)
            self.assertTrue((root / "dist" / "MaaAlibabaSupplier-v0.0.0-local-Setup.exe").is_file())
            self.assertNotIn("mutable-sentinel-", output)
            self.assertIn("main.py", output)
            self.assertIn("index.html", output)
            # Only remove a generated fixture; never touch the real install payload.
            (payload / "build-info.json").unlink()
            result = subprocess.run([compiler, str(script)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("build-info.json", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
