"""Copy the portable Refero skill into a local agent's discovery directory."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path
from typing import Optional, Sequence


def main(argv: Optional[Sequence[str]] = None) -> None:
    """Install for compatible local agents, preserving existing files by default.

    Args:
        argv: Optional CLI arguments; otherwise read the process arguments.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--agent", required=True, choices=("agents", "codex", "claude-code"),
        help="Use the shared .agents directory (agents/codex) or .claude directory",
    )
    parser.add_argument(
        "--project", type=Path,
        help="Install in this project's discovery directory instead of your home",
    )
    parser.add_argument(
        "--replace", action="store_true",
        help="Overwrite shipped skill files; preserve additional local files",
    )
    args = parser.parse_args(argv)
    root = args.project.expanduser().resolve() if args.project else Path.home()
    directory = ".claude" if args.agent == "claude-code" else ".agents"
    destination = root / directory / "skills" / "refero"
    source = Path(__file__).resolve().parents[1] / "skills" / "refero"
    try:
        shutil.copytree(source, destination, dirs_exist_ok=args.replace)
    except FileExistsError:
        parser.exit(
            1, f"Skill already exists at {destination}; "
            "review changes before using --replace.\n",
        )
    except OSError as exc:
        parser.exit(1, f"Could not install skill: {exc}\n")
    print(f"Installed Refero skill at {destination}")


if __name__ == "__main__":
    main()
