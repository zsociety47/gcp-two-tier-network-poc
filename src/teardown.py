"""Delete the proof.

    python -m src.teardown
    python -m src.teardown --resources-only

The full delete asks you to type the project id. A wrong name deletes nothing.
--resources-only keeps the project and deletes what is inside it.
"""

from __future__ import annotations

import argparse
import sys

from src.lib.config import ConfigError, load_settings
from src.lib.orchestrate import confirm_teardown


def main(argv: list[str] | None = None, typed: str | None = None) -> int:
    parser = argparse.ArgumentParser(description="Delete the two-tier proof.")
    parser.add_argument(
        "--resources-only",
        action="store_true",
        help="Keep the project. Delete the resources inside it.",
    )
    args = parser.parse_args(argv)
    try:
        settings = load_settings()
    except ConfigError as error:
        print(str(error), file=sys.stderr)
        return 1
    if settings.project_id is None:
        print("Set GCP_PROJECT_ID in .env.", file=sys.stderr)
        return 1
    project_id = settings.project_id
    if typed is None:
        typed = input(f"Type {project_id} to continue: ")
    if typed != project_id:
        print("Wrong name. Nothing was deleted.")
        return 1
    from src.lib.live import LiveCloud

    result = confirm_teardown(
        LiveCloud(settings),
        typed,
        project_id,
        resources_only=args.resources_only,
    )
    if result == "refused":
        print("Wrong name. Nothing was deleted.")
        return 1
    print("Deleted." if result == "project" else "Resources deleted. The project is still there.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
