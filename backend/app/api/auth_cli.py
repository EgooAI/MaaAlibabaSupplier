"""Local administration: python -m backend.app.api.auth_cli revoke-all."""

import argparse

from backend.app.api.auth import SessionStore, validate_auth_config
from backend.app.shared.utils.env import load_workdir_env


def main() -> None:
    parser = argparse.ArgumentParser(description="Revoke all persistent API sessions")
    parser.add_argument("command", choices=["revoke-all"])
    parser.parse_args()
    load_workdir_env()
    try:
        count = SessionStore(validate_auth_config()).revoke()
    except Exception:
        parser.exit(1, "Could not revoke sessions; check authentication configuration and database access.\n")
    print(f"Revoked {count} session(s).")


if __name__ == "__main__":
    main()
