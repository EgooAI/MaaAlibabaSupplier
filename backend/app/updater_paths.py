"""Filesystem contract between the updater, its helper and diagnostics."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

LOCALAPPDATA_DIRNAME = "MaaAlibabaSupplierUpdater"

RESULT_FILENAME = "last-result.json"
NOTICE_FILENAME = "installation-notice.json"
ACKNOWLEDGED_FILENAME = "acknowledged-installation.json"
BOOTSTRAP_FILENAME = "bootstrap.json"
REQUEST_FILENAME = "request.json"
ACCEPTED_FILENAME = "accepted.json"
GO_FILENAME = "go.json"
CANCEL_FILENAME = "cancel.json"
HELPER_LOG_FILENAME = "helper.log"
HELPER_SCRIPT_FILENAME = "update-helper.ps1"
INSTALLER_LOG_FILENAME = "installer.log"
ARTIFACT_FILENAME = "artifact.zip"
ARTIFACT_PART_SUFFIX = ".part"


def staging_root(install: Path) -> Path:
    """Compute the per-install staging root without enforcing the install boundary."""
    local = os.environ.get("LOCALAPPDATA")
    if not local or not Path(local).is_absolute():
        raise ValueError("LOCALAPPDATA is unavailable.")
    identity = hashlib.sha256(str(install.resolve()).casefold().encode("utf-8")).hexdigest()[:24]
    return (Path(local) / LOCALAPPDATA_DIRNAME / identity).resolve()


def staging_base(install: Path) -> Path:
    base = staging_root(install)
    if base.is_relative_to(install.resolve()):
        raise ValueError("Updater staging must be outside the installation.")
    return base
