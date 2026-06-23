import json
from pathlib import Path

run_id = "90c8aa9ad6cb4407a4780ca6f63b4f96"
base = Path("storage") / "runs" / run_id / "artifacts"

initial = json.loads((base / "layout_initial.json").read_text(encoding="utf-8"))
validated = json.loads((base / "layout_validated.json").read_text(encoding="utf-8"))

print("=== Canvas ===")
print("initial:", initial.get("canvas"))
print("validated:", validated.get("canvas"))

print("\n=== Counts ===")
for key in ["rooms", "fixtures", "doors", "windows", "balcony", "labels", "furniture"]:
    print(key, len(initial.get(key, [])), "->", len(validated.get(key, [])))

print("\n=== Quality ===")
print(json.dumps(validated.get("quality", {}), ensure_ascii=False, indent=2))

print("\n=== Validation ===")
print(json.dumps(validated.get("validation", {}), ensure_ascii=False, indent=2))

print("\n=== Warnings ===")
for w in validated.get("warnings", []):
    print("-", w)

print("\n=== First furniture ===")
fi = (initial.get("furniture") or [None])[0]
fv = (validated.get("furniture") or [None])[0]
print("initial:", json.dumps(fi, ensure_ascii=False, indent=2))
print("validated:", json.dumps(fv, ensure_ascii=False, indent=2))