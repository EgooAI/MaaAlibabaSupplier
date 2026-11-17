"""Backs up one CRM database and its referenced local files as a consistent set."""

from contextlib import closing
import json
from pathlib import Path
import shutil
import sqlite3

from backend.app.shared.crm.sdk import ResourceManager


def backup_resources(database_path, destination):
    """Blocks writers and GC while taking a database snapshot and verified file copies."""
    source = ResourceManager(database_path)
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=False)
    target = ResourceManager(destination / source.database_path.name)
    with closing(sqlite3.connect(source.database_path.as_uri() + "?mode=rw", uri=True, isolation_level=None)) as guard:
        guard.execute("BEGIN IMMEDIATE")
        try:
            with closing(sqlite3.connect(source.database_path.as_uri() + "?mode=ro", uri=True)) as reader:
                with closing(sqlite3.connect(target.database_path)) as backup:
                    reader.backup(backup)
            exists = guard.execute("SELECT 1 FROM sqlite_master WHERE name='resource'").fetchone()
            identifiers = [row[0] for row in guard.execute("SELECT resource_id FROM resource WHERE state='ready'")] if exists else []
            for identifier in identifiers:
                path = target.path(identifier)
                path.parent.mkdir(parents=True, exist_ok=True)
                with source.open(identifier, transaction=guard) as incoming, path.open("xb") as outgoing:
                    shutil.copyfileobj(incoming, outgoing)
            # The manifest is the completion marker; partial backups have no manifest.
            (destination / "resources-backup.json").write_text(json.dumps({
                "version": 1, "database": target.database_path.name, "resources": len(identifiers),
            }), encoding="utf-8")
            return {"database": str(target.database_path), "resources": len(identifiers)}
        finally:
            guard.rollback()


def verify_resource_backup(destination):
    """Checks an independent backup without changing its schema or data."""
    destination = Path(destination).resolve()
    manifest = json.loads((destination / "resources-backup.json").read_text(encoding="utf-8"))
    name = manifest["database"]
    if Path(name).name != name or manifest["version"] != 1:
        raise ValueError("Invalid backup manifest")
    resources = ResourceManager(destination / name)
    with resources.transaction() as conn:
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Invalid backup database")
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE name='resource'").fetchone()
        identifiers = [row[0] for row in conn.execute("SELECT resource_id FROM resource WHERE state='ready'")] if exists else []
        for identifier in identifiers:
            with resources.open(identifier, transaction=conn):
                pass
    if len(identifiers) != manifest["resources"]:
        raise ValueError("Backup resource count changed")
    return len(identifiers)
