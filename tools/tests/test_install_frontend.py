"""The frontend build identity must identify code, not build attempts."""

import importlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import _common

with patch.dict(sys.modules, {"_common": _common}):
    install_frontend = importlib.import_module("tools.install_1_frontend")


class FrontendBuildIdentityTests(unittest.TestCase):
    def test_clean_tree_is_stable_and_dirty_tree_is_unique(self):
        with patch.object(install_frontend.subprocess, "check_output", side_effect=[
            b"a" * 40 + b"\n", b"",
            b"a" * 40 + b"\n", b"",
        ]):
            first = install_frontend.resolve_build_id()
            second = install_frontend.resolve_build_id()
        self.assertEqual(first, "a" * 40)
        self.assertEqual(second, first)

        with patch.object(install_frontend.subprocess, "check_output", side_effect=[
            b"a" * 40 + b"\n", b" M app/page.tsx\n",
            b"a" * 40 + b"\n", b" M app/page.tsx\n",
        ]):
            first = install_frontend.resolve_build_id()
            second = install_frontend.resolve_build_id()
        self.assertRegex(first, r"^a{40}-[0-9a-f]{12}$")
        self.assertNotEqual(first, second)

    def test_missing_git_falls_back_to_random_identity(self):
        with patch.object(install_frontend.subprocess, "check_output", side_effect=FileNotFoundError):
            first = install_frontend.resolve_build_id()
            second = install_frontend.resolve_build_id()
        self.assertRegex(first, r"^[0-9a-f]{32}$")
        self.assertNotEqual(first, second)

    def test_build_id_file_matches_the_frontend_contract(self):
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary) / "out"
            install_frontend.write_build_id(out, "build-1")
            self.assertEqual(json.loads((out / "build-id.json").read_text(encoding="utf-8")),
                             {"schema_version": 1, "build_id": "build-1"})

    def test_build_identity_must_be_embedded_in_exported_javascript(self):
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary)
            (out / "chunk.js").write_text("const build = 'build-1';", encoding="utf-8")
            install_frontend.verify_build_id(out, "build-1")
            with self.assertRaises(SystemExit):
                install_frontend.verify_build_id(out, "missing")
