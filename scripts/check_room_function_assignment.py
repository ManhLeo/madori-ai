from __future__ import annotations

import json
import sys
from pathlib import Path


MEDIA_LIVING_TYPES = {
    "sofa",
    "sofa_1_seater",
    "sofa_2_seater",
    "sofa_3_seater",
    "tv",
    "tv_stand",
    "coffee_table",
    "rug",
    "curtain",
    "wall_art",
    "potted_plant",
    "floor_lamp",
    "shelf",
}
BED_TYPES = {"bed", "two_single_beds", "single_bed", "semi_double_bed", "double_bed", "pillow", "blanket"}
DINING_TYPES = {"dining_table", "chair"}
KITCHEN_TYPES = {"kitchen_counter", "sink", "stove", "cabinet", "refrigerator"}
WET_TYPES = {"bathtub", "shower", "toilet", "washbasin", "towel"}


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _artifact_dir(path: Path) -> Path:
    if path.is_dir():
        if (path / "artifacts").exists():
            return path / "artifacts"
        return path
    return path.parent


def _furniture_summary(items: list[dict], active_only: bool = False) -> dict[str, list[str]]:
    by_role: dict[str, list[str]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        if active_only and item.get("render_action") not in {"draw", None}:
            continue
        role = str(item.get("functional_role") or item.get("room_functional_role") or item.get("room_type") or "unknown")
        item_type = str(item.get("type") or "unknown")
        by_role.setdefault(role, [])
        if item_type not in by_role[role]:
            by_role[role].append(item_type)
    return by_role


def _has_conflict(items: list[dict], role: str, forbidden: set[str], *, active_only: bool = False) -> bool:
    for item in items:
        if not isinstance(item, dict):
            continue
        if active_only and item.get("render_action") != "draw":
            continue
        item_role = str(item.get("functional_role") or item.get("room_functional_role") or item.get("room_type") or "unknown")
        if item_role != role:
            continue
        if str(item.get("type") or "") in forbidden and item.get("placement_status") != "suppressed_by_functional_role":
            return True
    return False


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python scripts/check_room_function_assignment.py <artifact_path_or_run_dir>")
        return 1

    path = Path(sys.argv[1])
    if not path.exists():
        print(f"artifact not found: {path}")
        return 1

    artifacts_dir = _artifact_dir(path)
    room_assignment_path = artifacts_dir / "room_function_assignment.json"
    room_assignment = _load_json(room_assignment_path)
    if not room_assignment:
        room_assignment = _load_json(path if path.name == "room_function_assignment.json" else room_assignment_path)
    if not room_assignment:
        print(f"room function assignment artifact not found: {room_assignment_path}")
        return 1

    rooms = room_assignment.get("rooms") or []
    western_rooms = [room for room in rooms if str(room.get("semantic_type") or "") in {"bedroom", "bed_room"}]
    media_lounge = next((room for room in rooms if room.get("functional_role") == "media_lounge"), None)
    main_bedroom = next((room for room in rooms if room.get("functional_role") == "main_bedroom"), None)
    dining_assigned = any(room.get("functional_role") in {"living_dining", "dining_zone"} for room in rooms)
    cleanup = room_assignment.get("furniture_cleanup_summary") or {}

    print(f"assignment_status: {room_assignment.get('assignment_status')}")
    print(f"western_room_count: {len(western_rooms)}")
    print(f"media_lounge_room: {media_lounge.get('room_id') if media_lounge else None}")
    print(f"main_bedroom_room: {main_bedroom.get('room_id') if main_bedroom else None}")
    print(f"dining_zone_assigned: {dining_assigned}")
    print(f"allowed_furniture_count: {cleanup.get('allowed_furniture_count', 0)}")
    print(f"suppressed_furniture_count: {cleanup.get('suppressed_furniture_count', 0)}")
    print(f"role_conflict_count: {cleanup.get('role_conflict_count', 0)}")
    print(f"applied_rules: {room_assignment.get('assignment_rules_applied') or []}")
    print(f"warnings: {room_assignment.get('warnings') or []}")
    print(f"errors: {room_assignment.get('errors') or []}")

    layout_path = next(
        (candidate for candidate in (
            artifacts_dir / "render_plan.json",
            artifacts_dir / "layout_furniture_planned.json",
            artifacts_dir / "layout_validated.json",
            artifacts_dir / "layout_initial.json",
        ) if candidate.exists()),
        None,
    )
    layout = _load_json(layout_path) if layout_path else {}
    furniture = layout.get("furniture") or []

    if furniture:
        allowed_by_role = _furniture_summary(furniture, active_only=True)
        suppressed_by_role = {}
        suppression_reasons: list[str] = []
        for item in furniture:
            if not isinstance(item, dict):
                continue
            role = str(item.get("functional_role") or item.get("room_functional_role") or item.get("room_type") or "unknown")
            if item.get("placement_status") == "suppressed_by_functional_role":
                suppressed_by_role.setdefault(role, [])
                item_type = str(item.get("type") or "unknown")
                if item_type not in suppressed_by_role[role]:
                    suppressed_by_role[role].append(item_type)
                if item.get("suppression_reason"):
                    suppression_reasons.append(str(item.get("suppression_reason")))

        print(f"allowed_furniture_by_role: {allowed_by_role}")
        print(f"suppressed_furniture_by_role: {suppressed_by_role}")
        print(f"suppression_reasons: {list(dict.fromkeys(suppression_reasons))}")
        print(f"bedroom_contains_media_or_dining: {_has_conflict(furniture, 'main_bedroom', MEDIA_LIVING_TYPES | DINING_TYPES)}")
        print(f"media_lounge_contains_bed: {_has_conflict(furniture, 'media_lounge', BED_TYPES)}")
        print(f"living_dining_contains_bed: {_has_conflict(furniture, 'living_dining', BED_TYPES)}")

        active_prompt_items = [item for item in furniture if item.get("render_action") == "draw"]
        active_prompt_summary = [
            f"{item.get('type')}@{item.get('functional_role') or item.get('room_functional_role') or item.get('room_type')}"
            for item in active_prompt_items
        ]
        print(f"active_prompt_furniture_summary: {active_prompt_summary}")
        active_conflict = any(
            str(item.get("type") or "") in MEDIA_LIVING_TYPES | DINING_TYPES
            for item in active_prompt_items
            if str(item.get("functional_role") or item.get("room_functional_role") or item.get("room_type") or "") == "main_bedroom"
        ) or any(
            str(item.get("type") or "") in BED_TYPES
            for item in active_prompt_items
            if str(item.get("functional_role") or item.get("room_functional_role") or item.get("room_type") or "") == "media_lounge"
        ) or any(
            str(item.get("type") or "") in BED_TYPES
            for item in active_prompt_items
            if str(item.get("functional_role") or item.get("room_functional_role") or item.get("room_type") or "") == "living_dining"
        )
        print(f"prompt_clean_by_functional_role: {not active_conflict}")
    else:
        print("allowed_furniture_by_role: {}")
        print("suppressed_furniture_by_role: {}")
        print("suppression_reasons: []")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
