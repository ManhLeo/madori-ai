from __future__ import annotations

import json
import sys
from pathlib import Path


def _inside(inner: dict | None, outer: dict | None) -> bool:
    if not inner or not outer:
        return False
    return (
        inner["x_min"] >= outer["x_min"]
        and inner["y_min"] >= outer["y_min"]
        and inner["x_max"] <= outer["x_max"]
        and inner["y_max"] <= outer["y_max"]
    )


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python scripts/check_furniture_placement.py <layout_furniture_planned.json>")
        return 1

    path = Path(sys.argv[1])
    payload = json.loads(path.read_text(encoding="utf-8"))
    for item in payload.get("furniture", []):
        bbox = item.get("bbox")
        target_room_bbox = item.get("target_room_bbox")
        print(
            {
                "furniture_id": item.get("id"),
                "type": item.get("type"),
                "room_id": item.get("room_id"),
                "room_type": item.get("room_type"),
                "placement_status": item.get("placement_status"),
                "bbox": bbox,
                "target_room_bbox": target_room_bbox,
                "inside_target_room_bbox": _inside(bbox, target_room_bbox),
            }
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
