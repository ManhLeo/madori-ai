from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from fastapi import HTTPException


def _add_repo_root_to_path() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    repo_root_str = str(repo_root)
    if repo_root_str not in sys.path:
        sys.path.insert(0, repo_root_str)


def main() -> int:
    _add_repo_root_to_path()

    from app.services.vision_analyzer import VisionAnalyzer

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="Run floorplan analysis on a local image.")
    parser.add_argument("image_path", help="Path to the floorplan image.")
    args = parser.parse_args()

    use_gemini = os.getenv("USE_GEMINI_ANALYSIS", "").lower() == "true"
    if not use_gemini:
        print("Error: set USE_GEMINI_ANALYSIS=true before running this script.", file=sys.stderr)
        return 1
    if not os.getenv("GEMINI_API_KEY"):
        print("Error: GEMINI_API_KEY is missing. Set it before running the Gemini analysis test.", file=sys.stderr)
        return 1

    image_path = Path(args.image_path)
    if not image_path.exists():
        print(f"Error: image path does not exist: {image_path}", file=sys.stderr)
        return 1

    try:
        analyzer = VisionAnalyzer()
        analysis, furniture_plan, _ = analyzer.analyze_floorplan_design_with_raw(image_path)
        analysis = analyzer.normalize_floorplan_analysis(analysis)
    except HTTPException as exc:
        print(f"Error: analysis failed ({exc.status_code}): {exc.detail}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error: unexpected failure during analysis: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(analysis.model_dump(mode="json"), indent=2, ensure_ascii=False))
    if furniture_plan is not None:
        print(json.dumps(furniture_plan.model_dump(mode="json"), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
