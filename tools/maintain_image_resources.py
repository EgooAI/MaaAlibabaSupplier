"""Migrates legacy image bytes or backs up a CRM database with its resources."""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.shared.crm.image_attachment_store import ImageAttachmentStore
from backend.app.shared.crm.resource_backup import backup_resources, verify_resource_backup


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    migrate = commands.add_parser("migrate")
    migrate.add_argument("--database", type=Path, required=True)
    migrate.add_argument("--release-legacy", action="store_true", help="Releases verified legacy BLOBs after migration")
    backup = commands.add_parser("backup")
    backup.add_argument("--database", type=Path, required=True)
    backup.add_argument("destination", type=Path)
    verify = commands.add_parser("verify-backup")
    verify.add_argument("destination", type=Path)
    args = parser.parse_args()
    if args.command == "verify-backup":
        print(json.dumps({"verified_resources": verify_resource_backup(args.destination)}))
    elif args.command == "backup":
        print(json.dumps(backup_resources(args.database, args.destination)))
    else:
        if not args.database.is_file():
            parser.error("Database does not exist")
        store = ImageAttachmentStore(args.database)
        total = 0
        while count := store.migrate(release_legacy=args.release_legacy):
            total += count
        print(json.dumps({"migrated": total, "released_legacy": args.release_legacy}))


if __name__ == "__main__":
    main()
