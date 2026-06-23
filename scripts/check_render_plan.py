from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python scripts/check_render_plan.py <render_plan.json>")
        return 1

    path = Path(sys.argv[1])
    payload = json.loads(path.read_text(encoding="utf-8"))
    furniture = payload.get("furniture", [])
    print(
        {
            "render_plan_status": payload.get("render_plan_status"),
            "canvas": payload.get("canvas"),
            "room_count": len(payload.get("rooms", [])),
            "furniture_count": len(furniture),
            "drawable_furniture_count": sum(1 for item in furniture if item.get("render_action") == "draw"),
            "skipped_furniture_count": sum(1 for item in furniture if item.get("render_action") == "skip_until_manual_placement"),
            "label_count": len(payload.get("labels", [])),
            "negative_constraints_count": len(payload.get("prompt_sections", {}).get("negative_constraints", [])),
            "ready_for_prompt_building": payload.get("render_readiness", {}).get("ready_for_prompt_building"),
            "ready_for_image_generation": payload.get("render_readiness", {}).get("ready_for_image_generation"),
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
