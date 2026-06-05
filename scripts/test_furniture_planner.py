from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.schemas import FloorplanAnalysis, UserPreferences
from app.services.furniture_planner import plan_furniture


def main() -> int:
    parser = argparse.ArgumentParser(description="Test the deterministic furniture planner.")
    parser.add_argument("analysis_json", type=Path, help="Path to a saved analysis.json file")
    args = parser.parse_args()

    if not args.analysis_json.exists():
        raise SystemExit(f"analysis file not found: {args.analysis_json}")

    analysis = FloorplanAnalysis.model_validate(json.loads(args.analysis_json.read_text(encoding="utf-8")))
    preferences = UserPreferences(
        target_user="couple",
        interior_style="japanese_natural",
        budget_level="medium",
        color_preference="light_wood_beige_green",
        lifestyle=["work_from_home", "needs_storage", "likes_plants"],
        special_requests=["add small desk", "prioritize storage", "dining table for two"],
    )

    furniture_plan = plan_furniture(analysis, preferences)
    print(json.dumps(furniture_plan.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
