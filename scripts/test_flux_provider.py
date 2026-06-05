from __future__ import annotations

import argparse
import sys
from pathlib import Path

from fastapi import HTTPException


DEFAULT_PROMPT = (
    "Create a clean Japanese watercolor real-estate illustration from this floorplan. "
    "Preserve the layout, walls, doors, windows, and balcony."
)


def _add_repo_root_to_path() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    repo_root_str = str(repo_root)
    if repo_root_str not in sys.path:
        sys.path.insert(0, repo_root_str)


def main() -> int:
    _add_repo_root_to_path()

    from app.services.image_provider import get_image_provider

    parser = argparse.ArgumentParser(description="Test the Flux image provider.")
    parser.add_argument("floorplan_path", help="Path to the floorplan image.")
    parser.add_argument("prompt", nargs="?", default=DEFAULT_PROMPT, help="Optional image prompt.")
    args = parser.parse_args()

    floorplan_path = Path(args.floorplan_path)
    if not floorplan_path.exists():
        print(f"Error: floorplan path does not exist: {floorplan_path}", file=sys.stderr)
        return 1

    output_path = Path("outputs") / "test_image_provider_output.png"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        provider = get_image_provider()
        result_path = provider.generate(args.prompt, floorplan_path, output_path)
    except HTTPException as exc:
        print(f"Error: provider failed ({exc.status_code}): {exc.detail}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error: unexpected failure during image generation: {exc}", file=sys.stderr)
        return 1

    print(str(result_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
