import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from backend.app.shared.utils import app_config


def test_failed_replace_keeps_old_config_and_cleans_temporary_file(monkeypatch):
    app_config.write_app_config({"self_ali_id": "10001", "other": "retained"})
    path = app_config.config_file_path()
    before = path.read_bytes()

    def fail_replace(source, destination):
        assert Path(destination) == path
        assert path.read_bytes() == before
        assert json.loads(Path(source).read_text(encoding="utf-8"))["self_ali_id"] == "10002"
        raise OSError("replace failed")

    monkeypatch.setattr(app_config.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        app_config.write_app_config({"self_ali_id": "10002"})
    assert path.read_bytes() == before
    assert list(path.parent.iterdir()) == [path]


def test_concurrent_updates_preserve_each_patch():
    patches = [{f"setting_{i}": "x" * 4096} for i in range(12)]
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(app_config.write_app_config, patches))
    assert app_config.read_app_config() == {key: value for patch in patches for key, value in patch.items()}
