"""Alibaba client data-directory layout: discovery and path resolution.

Pure filesystem helpers with no middleware state; the middleware keeps the
per-process selection and locking. One account may be split across instance
storage profiles named ``{ali_id}@icbu`` and ``{ali_id}@icbu_{n}``; the most
recently written profile is the active source.
"""

from __future__ import annotations

import os
import re
import string
from pathlib import Path

from backend.app.shared.crm.identities import (
    ICBU_SUFFIX,
    canonical_ali_id,
    self_sender_id,
)

# Directory name to look for at each drive root when auto-detecting candidates.
DATA_DIR_NAME = "AlibabaSupplierData"
# Relative layout below the data dir proving it is a real Alibaba client dir.
DATA_DIR_SIGNATURE = Path("IMServiceDir") / "MessageSDK"


def looks_like_data_dir(path: Path) -> bool:
    """True when *path* has the Alibaba client layout with at least one im.sqlite."""
    try:
        signature = path / DATA_DIR_SIGNATURE
        if not signature.is_dir():
            return False
        return next(signature.glob("*/database/im.sqlite"), None) is not None
    except OSError:
        return False


def _profile_pattern(ali_id: str) -> re.Pattern[str]:
    return re.compile(re.escape(ali_id) + re.escape(ICBU_SUFFIX) + r"(?:_[0-9]+)?")


def _activity_mtime(profile: Path) -> float | None:
    """Newest write observed on the profile's database pair, if any."""
    stamps = []
    for sidecar in ("database/im.sqlite", "database/im.sqlite-wal"):
        try:
            stamps.append((profile / sidecar).stat().st_mtime)
        except OSError:
            pass
    return max(stamps) if stamps else None


def profile_dir(data_dir: Path, ali_id: str) -> Path | None:
    """Resolve the account's storage profile, tolerating instance suffixes.

    Ties prefer the canonical ``{ali_id}@icbu`` name so an existing deployment
    keeps its path when no profile is clearly newer.
    """
    if not ali_id:
        return None
    root = data_dir / DATA_DIR_SIGNATURE
    pattern = _profile_pattern(ali_id)
    canonical = self_sender_id(ali_id)
    best: tuple[tuple[float, bool], Path] | None = None
    try:
        for entry in root.iterdir():
            if not pattern.fullmatch(entry.name):
                continue
            modified = _activity_mtime(entry)
            if modified is None:
                continue
            rank = (modified, entry.name == canonical)
            if best is None or rank > best[0]:
                best = (rank, entry)
    except OSError:
        return None
    return best[1] if best is not None else None


def scan_ali_ids(data_dir: Path) -> list[dict]:
    """List accounts found under *data_dir*, most-recently-written profile first.

    Instance profiles of one account collapse to a single canonical account,
    reporting the newest profile's size and timestamp.
    """
    try:
        entries = (data_dir / DATA_DIR_SIGNATURE).glob("*/database/im.sqlite")
        found: dict[str, dict] = {}
        for db_path in entries:
            try:
                stat = db_path.stat()
            except OSError:
                continue
            ali_id = canonical_ali_id(db_path.parent.parent.name)
            if not ali_id:
                continue
            current = found.get(ali_id)
            if current is None or stat.st_mtime > current["last_modified"]:
                found[ali_id] = {
                    "ali_id": ali_id,
                    "db_size": stat.st_size,
                    "last_modified": stat.st_mtime,
                }
    except OSError:
        return []
    return sorted(found.values(), key=lambda item: item["last_modified"], reverse=True)


def encrypted_db_path(data_dir: Path, ali_id: str) -> Path:
    resolved = profile_dir(data_dir, ali_id)
    profile = resolved if resolved is not None else data_dir / DATA_DIR_SIGNATURE / self_sender_id(ali_id)
    return profile / "database" / "im.sqlite"


def _drive_roots() -> list[Path]:
    """Return every present drive root (``C:/``, ``D:/``, ...)."""
    listdrives = getattr(os, "listdrives", None)
    if listdrives is None:
        return [Path(f"{letter}:/") for letter in string.ascii_uppercase[2:]]
    try:
        return [Path(drive) for drive in listdrives()]
    except OSError:
        return []


def _users_root() -> Path:
    """Return the system drive's ``Users`` directory."""
    return Path(f"{os.environ.get('SystemDrive', 'C:')}/Users")


def _search_locations() -> list[Path]:
    """Return every location checked for an Alibaba client data directory."""
    locations = [root / DATA_DIR_NAME for root in _drive_roots()]
    try:
        users = sorted(
            (entry for entry in _users_root().iterdir() if entry.is_dir()),
            key=lambda entry: entry.name.casefold(),
        )
    except OSError:
        users = []
    locations.extend(
        user / "Documents" / "AlibabaSupplier" / DATA_DIR_NAME for user in users
    )
    return locations


def find_data_dir_candidates() -> list[str]:
    """Scan drive roots and per-user Documents folders for a real data dir.

    Checks ``<drive>/AlibabaSupplierData`` for every present drive and
    ``<system drive>/Users/<user>/Documents/AlibabaSupplier/AlibabaSupplierData``
    for every user folder. Existence checks only — no recursion.
    """
    found: list[str] = []
    seen: set[str] = set()
    for candidate in _search_locations():
        try:
            if not candidate.is_dir() or not looks_like_data_dir(candidate):
                continue
            resolved = str(candidate.resolve())
        except OSError:
            continue
        key = os.path.normcase(resolved)
        if key not in seen:
            seen.add(key)
            found.append(resolved)
    return found
