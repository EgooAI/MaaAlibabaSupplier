"""Run all install stages in order: frontend -> backend -> maafw -> yak.

Usage:
  python tools/install_all.py [--only frontend,backend,maafw,yak] [--use-system] [--dev]

`--use-system` and `--dev` are forwarded to the stages that accept them. The
Yak stage never recompiles backend/yak_mitm.yakc (use tools/install_4_yak.py
--compile to refresh the tracked artifact explicitly).
"""

from __future__ import annotations

import argparse
import sys

import _common as common

STAGES = {
    "frontend": "install_1_frontend.py",
    "backend": "install_2_backend.py",
    "maafw": "install_3_maafw.py",
    "yak": "install_4_yak.py",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", help="Comma-separated stage names to run (default: all)")
    parser.add_argument("--use-system", action="store_true", help="Use the interpreter/toolchain from PATH instead of the portable runtimes")
    parser.add_argument("--dev", action="store_true", help="Forwarded to the backend stage (install pytest)")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    selected = list(STAGES)
    if args.only:
        selected = [stage.strip() for stage in args.only.split(",")]
        unknown = [stage for stage in selected if stage not in STAGES]
        if unknown:
            common.fail(f"Unknown stage(s): {', '.join(unknown)}; known: {', '.join(STAGES)}")

    for stage in selected:
        script = common.TOOLS_DIR / STAGES[stage]
        command = [sys.executable, str(script)]
        if args.use_system and stage == "frontend":
            command.append("--use-system")
        if args.use_system and stage == "backend":
            command.append("--into")
        if args.dev and stage == "backend":
            command.append("--dev")
        common.run(command, cwd=common.REPO_ROOT)
    common.log("All stages done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
