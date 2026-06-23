import json
from pathlib import Path

run_id = "90c8aa9ad6cb4407a4780ca6f63b4f96"
base = Path("storage") / "runs" / run_id / "artifacts"

files = {
    "validated": base / "floorplan_analysis_validated.json",
    "initial": base / "layout_initial.json",
    "layout_validated": base / "layout_validated.json",
}

def load(path):
    return json.loads(path.read_text(encoding="utf-8"))

def get_rooms(data):
    return data.get("rooms") or data.get("normalized_analysis", {}).get("rooms") or []

def summarize_rooms(name, rooms):
    print(f"\n=== {name} ===")
    print("room_count:", len(rooms))
    print("rooms_with_bbox:", sum(1 for r in rooms if r.get("bbox")))
    print("rooms_with_approx_bbox:", sum(1 for r in rooms if r.get("approx_bbox")))
    for r in rooms[:3]:
        print(json.dumps({
            "id": r.get("id"),
            "type": r.get("type"),
            "label": r.get("label") or r.get("label_english"),
            "position": r.get("position"),
            "bbox": r.get("bbox"),
            "approx_bbox": r.get("approx_bbox"),
            "geometry_confidence": r.get("geometry_confidence"),
            "geometry_notes": r.get("geometry_notes"),
        }, ensure_ascii=False, indent=2))

for name, path in files.items():
    data = load(path)
    rooms = get_rooms(data)
    summarize_rooms(name, rooms)

validated_data = load(files["validated"])
print("\n=== geometry_summary ===")
print(json.dumps(validated_data.get("geometry_summary"), ensure_ascii=False, indent=2))