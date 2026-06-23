from __future__ import annotations

import json
import sys
from pathlib import Path


def _safe_filenames(items) -> list[str]:
    result: list[str] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        for key in ("original_filename", "stored_filename", "filename", "relative_path"):
            value = item.get(key)
            if value:
                result.append(Path(str(value)).name)
                break
    return result


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python scripts/check_openai_reference_selection.py <openai_reference_selection.json>")
        return 1

    path = Path(sys.argv[1])
    payload = json.loads(path.read_text(encoding="utf-8"))

    selected_images = payload.get("selected_images") or []
    style_reference = next((item for item in selected_images if isinstance(item, dict) and item.get("role") == "style_reference"), {})
    structure_reference = next((item for item in selected_images if isinstance(item, dict) and item.get("role") == "structure_reference"), {})
    selected_interiors = [item for item in selected_images if isinstance(item, dict) and item.get("role") == "interior_photo"]
    interior_filenames = [str(item.get("filename") or Path(str(item.get("relative_path") or "")).name) for item in selected_interiors]
    interior_original_filenames = [str(item.get("original_filename") or "") for item in selected_interiors]
    excluded_images = payload.get("excluded_images") or []
    excluded_filenames = _safe_filenames(excluded_images)
    scoring_details = payload.get("scoring_details") or {}
    candidate_scores = scoring_details.get("candidate_scores") or []

    print(
        {
            "prompt_mode": payload.get("selection_mode"),
            "selected_reference_count": len(selected_images),
            "primary_structure_reference": structure_reference.get("relative_path") or structure_reference.get("preview_url"),
            "selected_style_reference": style_reference.get("relative_path") or style_reference.get("preview_url"),
            "selected_interior_filenames": interior_filenames,
            "selected_original_filenames": interior_original_filenames,
            "excluded_interior_filenames": excluded_filenames,
            "interior_guidance_summary": payload.get("interior_guidance_summary"),
            "furniture_arrangement_rules_applied": payload.get("furniture_arrangement_rules_applied"),
            "living_room_arrangement_rule": payload.get("living_room_arrangement_rule"),
            "bedroom_bed_count_rule_applied": payload.get("bedroom_bed_count_rule_applied"),
            "bedroom_bed_count_rule": payload.get("bedroom_bed_count_rule"),
            "candidate_scores": candidate_scores,
            "warnings_count": len(payload.get("warnings") or []),
            "errors_count": len(payload.get("errors") or []),
        }
    )
    print("\n--- warnings ---\n")
    print(json.dumps(payload.get("warnings", []), ensure_ascii=False, indent=2))
    print("\n--- errors ---\n")
    print(json.dumps(payload.get("errors", []), ensure_ascii=False, indent=2))
    print("\n--- scoring_details ---\n")
    print(json.dumps(scoring_details, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
