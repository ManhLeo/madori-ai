from __future__ import annotations

import json
import sys
from pathlib import Path


def _lookup_path(payload, path: str):
    current = payload
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python scripts/check_floorplan_analysis_structure.py <floorplan_analysis.json>")
        return 1

    path = Path(sys.argv[1])
    if not path.exists():
        print(f"File not found: {path}")
        return 1

    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        print("top_level_keys:", list(payload.keys()))
    else:
        print("payload_type:", type(payload).__name__)
        return 0

    candidate_paths = [
        "rooms",
        "analysis.rooms",
        "normalized_analysis.rooms",
        "floorplan.rooms",
        "result.rooms",
        "data.rooms",
    ]
    for candidate in candidate_paths:
        value = _lookup_path(payload, candidate)
        if isinstance(value, list):
            print(f"{candidate}: list[{len(value)}]")
        elif value is not None:
            print(f"{candidate}: {type(value).__name__}")
        else:
            print(f"{candidate}: <missing>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
