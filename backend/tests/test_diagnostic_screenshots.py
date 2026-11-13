"""Checks saved error-image exports without a desktop or native writer."""

import io
import json
import os
from pathlib import Path
import stat
from types import SimpleNamespace
from unittest.mock import Mock
import zipfile

from PIL import Image, PngImagePlugin
import pytest
from fastapi.testclient import TestClient

from backend.app import diagnostics as diag
from backend.app.api import main
from backend.tests.auth_client import authenticate
from backend.tests.test_diagnostics import APPLICATION, NATIVE, URL, unpack, write
from tools import pull_diagnostics as pull


def screenshot(path, *, color=(17, 31, 47, 127), size=(32, 24)):
    path.parent.mkdir(parents=True, exist_ok=True)
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("private-key", "private-file-metadata")
    with Image.new("RGBA", size, color) as image:
        image.save(path, format="PNG", pnginfo=metadata)
    return path


def session():
    root = diag.resolve_backend_root() / "data/logs/native" / ("session-" + "a" * 32)
    write(root / ".owner", "maa-native-session-v1\n")
    write(root / "maa.log", NATIVE)
    return root


def image_entries(content):
    return [item for item in json.loads(content["manifest.json"])["files"]
            if item["source"] == "native-error-screenshot"]


def test_authenticated_export_preserves_error_pixels_and_correlates_session(tmp_path, monkeypatch):
    root = session()
    image = screenshot(root / "on_error/2026-11-13-09-03-56-978_ChatInput.png")
    screenshot(root / "debug/on_error/private-filename_ChatInput_SendOnly.png")
    for path in (root / "draw/ignored.png", root / "arbitrary.png",
                 root.parent / ("session-" + "b" * 32) / "on_error/unowned.png"):
        screenshot(path)
    monkeypatch.setattr(diag, "runtime_observations", lambda: {"native_active_session": root.name})
    monkeypatch.setattr(main, "get_account_context", Mock(side_effect=AssertionError("No live account access")))
    with TestClient(main.create_app()) as client:
        assert client.get(URL).status_code == 401
        authenticate(client)
        response = client.get(URL)
    assert response.status_code == 200
    content = unpack(response.content)
    entries = image_entries(content)
    assert len(entries) == 2
    assert {entry["node"] for entry in entries} == {"ChatInput", "ChatInput_SendOnly"}
    assert all(entry["native_log_session_id"] == root.name and entry["session_state"] == "active" for entry in entries)
    assert all(entry["status"] == "included" for entry in entries)
    assert next(item for item in json.loads(content["manifest.json"])["files"]
                if item["source"] == "native-session-maa")["native_log_session_id"] == root.name
    for entry in entries:
        with Image.open(io.BytesIO(content[entry["file"]])) as exported:
            assert exported.size == (32, 24) and exported.getpixel((0, 0)) == (17, 31, 47, 127)
            assert not exported.info
    assert next(entry for entry in entries if entry["node"] == "ChatInput")["modified_at"] == image.stat().st_mtime
    assert b"private-" not in b"\n".join(content.values())
    assert image.exists() and not (root / ".closed").exists()
    downloaded = tmp_path / "diagnostics.zip"
    downloaded.write_bytes(response.content)
    pull.validate_archive(downloaded)


def test_recent_images_and_core_logs_precede_rotations_with_shared_limits(monkeypatch):
    root = session()
    for index in range(6):
        image = screenshot(root / f"on_error/{index}_ChatInput.png", color=(index, 0, 0, 255))
        os.utime(image, (100 + index, 100 + index))
    legacy = screenshot(diag.resolve_backend_root() / "debug/on_error/legacy_ChatInput.png", color=(99, 0, 0, 255))
    os.utime(legacy, (200, 200))
    for index in range(30):
        write(diag.resolve_backend_root() / f"data/logs/api.{index}.log", APPLICATION)
    monkeypatch.setattr(diag, "MAX_FILES", 8)
    content = unpack(diag.build_bundle())
    manifest = json.loads(content["manifest.json"])
    assert len(content) == 8
    assert {entry["source"] for entry in manifest["files"]} == {"api", "native-session-maa", "native-error-screenshot"}
    assert manifest["omitted_screenshots_file_limit"] == 3
    pixels = []
    for entry in image_entries(content):
        with Image.open(io.BytesIO(content[entry["file"]])) as image:
            pixels.append(image.getpixel((0, 0))[0])
    assert pixels == [99, 5, 4, 3]

    # A whole-image budget cannot be spent on a truncated PNG or consume core logs.
    monkeypatch.setattr(diag, "MAX_SCREENSHOT_TOTAL_BYTES", legacy.stat().st_size)
    content = unpack(diag.build_bundle())
    entries = image_entries(content)
    assert [entry["status"] for entry in entries] == ["included"] + ["omitted_size_limit"] * 3
    assert sum(max(entry["read_bytes"], entry["exported_bytes"]) for entry in entries) <= legacy.stat().st_size
    assert any(name.startswith("logs/") for name in content)


@pytest.mark.parametrize("failure", ["truncated", "non_png", "oversized", "pixels", "reencoded_size", "changed", "deadline"])
def test_bad_or_incomplete_images_are_omitted_without_losing_logs(monkeypatch, failure):
    root = session()
    path = screenshot(root / "on_error/error_ChatInput.png")
    if failure == "truncated":
        path.write_bytes(path.read_bytes()[:-12])
    elif failure == "non_png":
        path.write_bytes(b"private-not-a-png")
    elif failure == "oversized":
        with path.open("ab") as stream:
            stream.write(b"x" * diag.MAX_FILE_BYTES)
    elif failure == "pixels":
        monkeypatch.setattr(diag, "MAX_SCREENSHOT_PIXELS", 100)
    elif failure == "reencoded_size":
        with Image.new("1", (100, 100)) as image:
            image.save(path, format="PNG")
        monkeypatch.setattr(diag, "MAX_SCREENSHOT_TOTAL_BYTES", path.stat().st_size)
    elif failure in ("changed", "deadline"):
        original = diag._open_checked
        clock = [100.0]
        monkeypatch.setattr(diag.time, "monotonic", lambda: clock[0])

        def open_image(candidate, info):
            stream = original(candidate, info)
            if candidate == path:
                if failure == "changed":
                    with path.open("ab") as writer:
                        writer.write(b"private-appended")
                else:
                    clock[0] = 111.0
            return stream

        monkeypatch.setattr(diag, "_open_checked", open_image)
    content = unpack(diag.build_bundle())
    entry, = image_entries(content)
    assert entry["file"] not in content
    expected = {"truncated": "invalid_image", "non_png": "invalid_image", "oversized": "omitted_size_limit",
                "pixels": "omitted_pixel_limit", "reencoded_size": "omitted_size_limit",
                "changed": "unavailable_or_rotated", "deadline": "omitted_deadline"}
    assert entry["status"] == expected[failure]
    assert any(name.startswith("logs/") for name in content)
    if failure == "deadline":
        assert json.loads(content["manifest.json"])["deadline_exceeded"]


@pytest.mark.parametrize("kind", ["hardlink", "reparse_parent", "replacement"])
def test_error_images_use_existing_checked_file_boundary(tmp_path, monkeypatch, kind):
    root = session()
    path = screenshot(root / "on_error/error_ChatInput.png")
    outside = screenshot(tmp_path / "outside.png", color=(99, 99, 99, 255))
    if kind == "hardlink":
        path.unlink()
        os.link(outside, path)
    elif kind == "reparse_parent":
        original = Path.lstat

        def marked(candidate):
            return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400) if candidate == path.parent else original(candidate)

        monkeypatch.setattr(Path, "lstat", marked)
    else:
        original = diag._open_checked

        def replaced(candidate, info):
            if candidate == path:
                candidate.rename(candidate.with_suffix(".old"))
                candidate.write_bytes(outside.read_bytes())
            return original(candidate, info)

        monkeypatch.setattr(diag, "_open_checked", replaced)
    content = unpack(diag.build_bundle())
    assert not any(name.startswith("screenshots/") for name in content)
    assert any(name.startswith("logs/") for name in content)


@pytest.mark.parametrize("failure", [None, "path", "payload", "missing_manifest", "dimensions", "count", "total_bytes"])
def test_downloader_checks_image_contract_before_accepting_zip(tmp_path, monkeypatch, failure):
    root = session()
    screenshot(root / "on_error/error_ChatInput.png")
    content = unpack(diag.build_bundle())
    entry, = image_entries(content)
    name = entry["file"]
    manifest = json.loads(content["manifest.json"])
    if failure == "path":
        content["screenshots/../../escape.png"] = content.pop(name)
    elif failure == "payload":
        content[name] = b"not an image"
    elif failure in ("missing_manifest", "dimensions"):
        for item in manifest["files"]:
            if item["file"] == name:
                item["file" if failure == "missing_manifest" else "width"] = "wrong"
        content["manifest.json"] = json.dumps(manifest).encode()
    elif failure == "count":
        for i in range(90, 94):
            content[f"screenshots/{i}-native-error.png"] = content[name]
    elif failure == "total_bytes":
        monkeypatch.setattr(pull, "MAX_SCREENSHOT_TOTAL_BYTES", len(content[name]) - 1)
    target = tmp_path / "download.zip"
    with zipfile.ZipFile(target, "w") as archive:
        for filename, data in content.items():
            archive.writestr(filename, data)
    if failure:
        with pytest.raises((ValueError, OSError)):
            pull.validate_archive(target)
    else:
        pull.validate_archive(target)
