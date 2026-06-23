import json
from pathlib import Path

run_id = "90c8aa9ad6cb4407a4780ca6f63b4f96"
base = Path("storage") / "runs" / run_id / "artifacts"

for name in ["floorplan_analysis.json", "floorplan_analysis_validated.json", "layout_initial.json"]:
    path = base / name
    print("\n===", name, "===")
    data = json.loads(path.read_text(encoding="utf-8"))
    rooms = data.get("rooms") or data.get("normalized_analysis", {}).get("rooms") or []
    print("room_count:", len(rooms))
    for r in rooms[:3]:
        print(json.dumps({
            "id": r.get("id"),
            "type": r.get("type"),
            "label": r.get("label"),
            "position": r.get("position"),
            "bbox": r.get("bbox"),
            "approx_bbox": r.get("approx_bbox"),
            "polygon": r.get("polygon"),
        }, ensure_ascii=False, indent=2))