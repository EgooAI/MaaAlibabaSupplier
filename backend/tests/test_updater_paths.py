from pathlib import Path

from backend.app import updater_paths

HELPER = Path(__file__).resolve().parents[1] / "app" / "update-helper.ps1"


def test_helper_uses_shared_updater_filenames():
    script = HELPER.read_text(encoding="utf-8")
    for name in (
        updater_paths.BOOTSTRAP_FILENAME,
        updater_paths.REQUEST_FILENAME,
        updater_paths.ACCEPTED_FILENAME,
        updater_paths.GO_FILENAME,
        updater_paths.CANCEL_FILENAME,
        updater_paths.NOTICE_FILENAME,
        "build-info.json",
    ):
        assert name in script
