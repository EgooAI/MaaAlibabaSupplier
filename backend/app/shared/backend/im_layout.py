"""Alibaba client data-directory layout: discovery and path resolution.

Pure filesystem helpers with no middleware state; the middleware keeps the
per-process selection and locking.
"""

from __future__ import annotations

from pathlib import Path

from backend.app.shared.crm.identities import self_sender_id, strip_icbu_suffix

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


def scan_ali_ids(data_dir: Path) -> list[dict]:
    """List accounts found under *data_dir*, most-recent first."""
    try:
        entries = (data_dir / DATA_DIR_SIGNATURE).glob("*/database/im.sqlite")
        found = []
        for db_path in entries:
            try:
                stat = db_path.stat()
            except OSError:
                continue
            found.append({
                "ali_id": strip_icbu_suffix(db_path.parent.parent.name),
                "db_size": stat.st_size,
                "last_modified": stat.st_mtime,
            })
    except OSError:
        return []
    found.sort(key=lambda item: item["last_modified"], reverse=True)
    return found


def encrypted_db_path(data_dir: Path, ali_id: str) -> Path:
    return data_dir / DATA_DIR_SIGNATURE / self_sender_id(ali_id) / "database" / "im.sqlite"


def find_data_dir_candidates() -> list[str]:
    """Scan drive roots (C:..Z:) for an ``AlibabaSupplierData`` dir with IM layout.

    Existence checks only — no recursion, completes in milliseconds.
    """
    import string

    found: list[str] = []
    for letter in string.ascii_uppercase:
        if letter < "C":
            continue
        candidate = Path(f"{letter}:/{DATA_DIR_NAME}")
        try:
            if not candidate.is_dir():
                continue
        except OSError:
            continue
        if looks_like_data_dir(candidate):
            found.append(str(candidate.resolve()))
    return found
