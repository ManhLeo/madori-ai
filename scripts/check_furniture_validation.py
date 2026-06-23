from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python scripts/check_furniture_validation.py <layout_furniture_validated.json>")
        return 1

    path = Path(sys.argv[1])
    payload = json.loads(path.read_text(encoding="utf-8"))
    for item in payload.get("furniture", []):
        print(
            {
                "furniture_id": item.get("id"),
                "type": item.get("type"),
                "placement_status": item.get("placement_status"),
                "bbox": item.get("bbox"),
                "target_room_bbox": item.get("target_room_bbox"),
                "inside_target_room_bbox": (
                    bool(item.get("bbox") and item.get("target_room_bbox"))
                    and item["bbox"]["x_min"] >= item["target_room_bbox"]["x_min"]
                    and item["bbox"]["y_min"] >= item["target_room_bbox"]["y_min"]
                    and item["bbox"]["x_max"] <= item["target_room_bbox"]["x_max"]
                    and item["bbox"]["y_max"] <= item["target_room_bbox"]["y_max"]
                ),
                "bbox_consistent": (
                    item.get("bbox") is not None
                    and item.get("x") == item["bbox"]["x_min"]
                    and item.get("y") == item["bbox"]["y_min"]
                    and item.get("width") == item["bbox"]["x_max"] - item["bbox"]["x_min"]
                    and item.get("height") == item["bbox"]["y_max"] - item["bbox"]["y_min"]
                ),
                "placement_notes": item.get("placement_notes", []),
            }
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
