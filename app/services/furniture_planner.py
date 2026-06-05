from __future__ import annotations

from collections.abc import Iterable

from app.schemas import (
    FloorplanAnalysis,
    FurnitureItem,
    FurniturePlan,
    RoomFurniturePlan,
    UserPreferences,
)


def plan_furniture(analysis: FloorplanAnalysis, preferences: UserPreferences) -> FurniturePlan:
    room_count = len(analysis.rooms)
    room_types = [room.type for room in analysis.rooms]
    has_living_space = any(room_type in {"living_room", "dining_kitchen"} for room_type in room_types)
    room_positions = {room.type: room.position for room in analysis.rooms}

    room_plans: list[RoomFurniturePlan] = []
    for room in analysis.rooms:
        items = _plan_items_for_room(
            room_type=room.type,
            room_position=room.position,
            room_count=room_count,
            has_living_space=has_living_space,
            preferences=preferences,
        )
        if items:
            room_plans.append(
                RoomFurniturePlan(
                    room_type=room.type,
                    room_name=room.room_name,
                    room_position=room.position,
                    items=items,
                )
            )

    furniture_plan = FurniturePlan(
        style=preferences.interior_style or "unspecified",
        target_user=preferences.target_user,
        budget_level=preferences.budget_level,
        room_plans=room_plans,
        global_rules=[
            "Use only existing rooms from the floorplan analysis.",
            "Preserve walls, doors, windows, room boundaries, and room locations.",
            "Keep furniture inside existing rooms only.",
            "Avoid blocking doors and circulation paths.",
            "Keep the layout top-down and suitable for a real-estate listing.",
        ],
    )
    return apply_furniture_coordinates(furniture_plan, room_positions)


def apply_furniture_coordinates(
    furniture_plan: FurniturePlan,
    analysis_or_room_positions: FloorplanAnalysis | dict[str, str | None] | None = None,
) -> FurniturePlan:
    room_positions: dict[str, str | None] = {}
    if isinstance(analysis_or_room_positions, FloorplanAnalysis):
        room_positions = {room.type: room.position for room in analysis_or_room_positions.rooms}
    elif isinstance(analysis_or_room_positions, dict):
        room_positions = analysis_or_room_positions

    normalized_room_plans: list[RoomFurniturePlan] = []
    for room_plan in furniture_plan.room_plans:
        room_position = room_plan.room_position or _lookup_room_position(room_plan.room_type, room_positions)
        normalized_items: list[FurnitureItem] = []
        for index, item in enumerate(room_plan.items):
            relative_x, relative_y = item.relative_x, item.relative_y
            rotation = item.rotation
            if relative_x is None or relative_y is None or rotation is None:
                derived_x, derived_y, derived_rotation = _derive_item_coordinates(
                    room_plan.room_type,
                    room_position,
                    item.item,
                    index,
                )
                relative_x = derived_x if relative_x is None else relative_x
                relative_y = derived_y if relative_y is None else relative_y
                rotation = derived_rotation if rotation is None else rotation
            normalized_items.append(
                FurnitureItem(
                    item=item.item,
                    room=item.room,
                    size=item.size,
                    position_hint=item.position_hint,
                    reason=item.reason,
                    relative_x=_clamp01(relative_x),
                    relative_y=_clamp01(relative_y),
                    rotation=rotation,
                )
            )
        normalized_room_plans.append(
            RoomFurniturePlan(
                room_type=room_plan.room_type,
                room_name=room_plan.room_name,
                room_position=room_position,
                items=normalized_items,
            )
        )

    return FurniturePlan(
        style=furniture_plan.style,
        target_user=furniture_plan.target_user,
        budget_level=furniture_plan.budget_level,
        room_plans=normalized_room_plans,
        global_rules=furniture_plan.global_rules,
    )


def _plan_items_for_room(
    room_type: str,
    room_position: str | None,
    room_count: int,
    has_living_space: bool,
    preferences: UserPreferences,
) -> list[FurnitureItem]:
    target_user = (preferences.target_user or "").strip().lower()
    lifestyle = {item.strip().lower() for item in preferences.lifestyle if item.strip()}
    special_requests = {item.strip().lower() for item in preferences.special_requests if item.strip()}

    items: list[FurnitureItem] = []

    if room_type in {"bedroom"}:
        _add_bedroom_items(items, target_user, room_count)
        if "work_from_home" in lifestyle or _contains_request(special_requests, "desk"):
            _add_unique_item(
                items,
                item="compact_work_desk",
                room=room_type,
                size="small",
                position_hint="against wall",
                reason="supports work from home use",
            )
        if "likes_plants" in lifestyle:
            _add_unique_item(
                items,
                item="small_plant",
                room=room_type,
                size="small",
                position_hint="corner",
                reason="adds a soft residential touch",
            )
        if "needs_storage" in lifestyle or _contains_request(special_requests, "storage"):
            _add_unique_item(
                items,
                item="storage_shelf",
                room=room_type,
                size="small",
                position_hint="against wall",
                reason="adds storage capacity",
            )

    elif room_type in {"living_room", "dining_kitchen"}:
        _add_unique_item(
            items,
            item="sofa",
            room=room_type,
            size="medium",
            position_hint="center or wall-facing",
            reason="creates the main seating area",
        )
        _add_unique_item(
            items,
            item="coffee_table",
            room=room_type,
            size="small",
            position_hint="in front of sofa",
            reason="supports a natural living room arrangement",
        )
        _add_unique_item(
            items,
            item="tv_stand",
            room=room_type,
            size="small",
            position_hint="against wall",
            reason="creates a standard real-estate listing setup",
        )
        if target_user in {"couple", "family_with_child"} or _contains_request(special_requests, "dining"):
            _add_unique_item(
                items,
                item="dining_table",
                room=room_type,
                size="small",
                position_hint="near kitchen or window side",
                reason="supports shared meals",
            )
        if "work_from_home" in lifestyle or _contains_request(special_requests, "desk"):
            _add_unique_item(
                items,
                item="compact_work_desk",
                room=room_type,
                size="small",
                position_hint="against wall",
                reason="supports work from home use",
            )
        if "likes_plants" in lifestyle:
            _add_unique_item(
                items,
                item="small_plant",
                room=room_type,
                size="small",
                position_hint="corner",
                reason="adds a soft residential touch",
            )
        if "needs_storage" in lifestyle or _contains_request(special_requests, "storage"):
            _add_unique_item(
                items,
                item="storage_shelf",
                room=room_type,
                size="small",
                position_hint="against wall",
                reason="adds storage capacity",
            )

    elif room_type == "kitchen":
        if has_living_space and (target_user in {"couple", "family_with_child"} or _contains_request(special_requests, "dining")):
            _add_unique_item(
                items,
                item="dining_table",
                room=room_type,
                size="small",
                position_hint="adjacent to open space",
                reason="uses available living and dining space efficiently",
            )

    elif room_type in {"bathroom", "toilet", "washroom"}:
        if "needs_storage" in lifestyle or _contains_request(special_requests, "storage"):
            _add_unique_item(
                items,
                item="small_storage",
                room=room_type,
                size="small",
                position_hint="against wall",
                reason="adds compact utility storage",
            )
        if room_type != "toilet" and "likes_plants" in lifestyle:
            _add_unique_item(
                items,
                item="small_plant",
                room=room_type,
                size="small",
                position_hint="corner",
                reason="adds a gentle decorative accent",
            )

    elif room_type in {"closet", "walk_in_closet"}:
        _add_unique_item(
            items,
            item="storage_boxes",
            room=room_type,
            size="small",
            position_hint="inside storage area",
            reason="organizes clothing and accessories",
        )

    elif room_type == "entrance":
        _add_unique_item(
            items,
            item="shoe_cabinet",
            room=room_type,
            size="small",
            position_hint="against wall near entry",
            reason="keeps the entrance tidy",
        )
        _add_unique_item(
            items,
            item="small_rug",
            room=room_type,
            size="small",
            position_hint="entry floor",
            reason="makes the entrance more inviting",
        )

    elif room_type == "balcony":
        _add_unique_item(
            items,
            item="small_plant",
            room=room_type,
            size="small",
            position_hint="corner or railing side",
            reason="adds a modest balcony accent",
        )
        if target_user in {"couple", "family_with_child"} or "likes_plants" in lifestyle:
            _add_unique_item(
                items,
                item="outdoor_chair",
                room=room_type,
                size="small",
                position_hint="balcony side",
                reason="provides a simple outdoor seating accent",
            )

    elif room_type == "hallway":
        if "needs_storage" in lifestyle or _contains_request(special_requests, "storage"):
            _add_unique_item(
                items,
                item="storage_shelf",
                room=room_type,
                size="small",
                position_hint="against wall",
                reason="adds storage without blocking circulation",
            )

    if room_type in {"bathroom", "toilet", "washroom"} and "likes_plants" not in lifestyle:
        # Keep wet-area furnishing minimal.
        items = [item for item in items if item.item in {"small_storage", "small_plant"}]

    if room_type == "unknown":
        return []

    return items


def _add_bedroom_items(items: list[FurnitureItem], target_user: str, room_count: int) -> None:
    if target_user == "couple":
        _add_unique_item(
            items,
            item="double_bed",
            room="bedroom",
            size="large",
            position_hint="center or wall-facing",
            reason="fits a couple's primary sleeping setup",
        )
        _add_unique_item(
            items,
            item="wardrobe",
            room="bedroom",
            size="medium",
            position_hint="against wall",
            reason="provides clothes storage",
        )
        _add_unique_item(
            items,
            item="nightstand",
            room="bedroom",
            size="small",
            position_hint="bed side",
            reason="supports bedside storage",
        )
        _add_unique_item(
            items,
            item="nightstand",
            room="bedroom",
            size="small",
            position_hint="other bed side",
            reason="balances the couple setup",
        )
        return

    if target_user == "family_with_child":
        _add_unique_item(
            items,
            item="bed",
            room="bedroom",
            size="medium",
            position_hint="center or wall-facing",
            reason="keeps the sleeping area simple and flexible",
        )
        _add_unique_item(
            items,
            item="wardrobe",
            room="bedroom",
            size="medium",
            position_hint="against wall",
            reason="provides clothes storage",
        )
        if room_count >= 2:
            _add_unique_item(
                items,
                item="study_desk",
                room="bedroom",
                size="small",
                position_hint="against wall",
                reason="supports child study or parent use",
            )
        return

    _add_unique_item(
        items,
        item="single_bed",
        room="bedroom",
        size="medium",
        position_hint="center or wall-facing",
        reason="fits a single-occupant bedroom setup",
    )
    _add_unique_item(
        items,
        item="wardrobe",
        room="bedroom",
        size="medium",
        position_hint="against wall",
        reason="provides clothes storage",
    )
    _add_unique_item(
        items,
        item="nightstand",
        room="bedroom",
        size="small",
        position_hint="bed side",
        reason="adds bedside storage",
    )


def _add_unique_item(
    items: list[FurnitureItem],
    *,
    item: str,
    room: str,
    size: str | None,
    position_hint: str | None,
    reason: str | None,
) -> None:
    if any(existing.item == item for existing in items):
        return
    items.append(
        FurnitureItem(
            item=item,
            room=room,
            size=size,
            position_hint=position_hint,
            reason=reason,
        )
    )


def _contains_request(requests: Iterable[str], needle: str) -> bool:
    needle = needle.lower()
    return any(needle in request for request in requests)


def _lookup_room_position(room_type: str, room_positions: dict[str, str | None]) -> str | None:
    return room_positions.get(room_type)


def _base_coordinates_for_room(room_position: str | None) -> tuple[float, float]:
    mapping = {
        "top_left": (0.26, 0.26),
        "top_right": (0.74, 0.26),
        "bottom_left": (0.26, 0.74),
        "bottom_right": (0.74, 0.74),
        "left": (0.24, 0.5),
        "right": (0.76, 0.5),
        "top": (0.5, 0.24),
        "bottom": (0.5, 0.76),
        "center": (0.5, 0.5),
        "unknown": (0.5, 0.5),
        None: (0.5, 0.5),
    }
    return mapping.get(room_position, (0.5, 0.5))


def _derive_item_coordinates(
    room_type: str,
    room_position: str | None,
    item_name: str,
    index: int,
) -> tuple[float, float, float]:
    base_x, base_y = _base_coordinates_for_room(room_position)
    room_offsets = {
        "living_room": {
            "sofa": (-0.07, 0.04),
            "coffee_table": (0.02, 0.12),
            "tv_stand": (0.16, 0.03),
            "dining_table": (0.05, -0.1),
            "compact_work_desk": (0.17, 0.1),
            "small_plant": (-0.15, -0.14),
            "storage_shelf": (0.16, -0.1),
        },
        "dining_kitchen": {
            "sofa": (-0.05, 0.05),
            "coffee_table": (0.02, 0.12),
            "tv_stand": (0.16, 0.03),
            "dining_table": (0.05, -0.1),
            "compact_work_desk": (0.17, 0.1),
            "small_plant": (-0.14, -0.12),
            "storage_shelf": (0.16, -0.1),
        },
        "bedroom": {
            "single_bed": (-0.04, 0.03),
            "semi_double_bed": (-0.04, 0.03),
            "double_bed": (-0.04, 0.03),
            "wardrobe": (0.16, -0.04),
            "nightstand": (0.1, 0.13),
            "study_desk": (0.12, -0.12),
            "compact_work_desk": (0.12, -0.12),
            "small_plant": (-0.14, -0.14),
            "storage_shelf": (0.15, -0.12),
        },
        "entrance": {
            "shoe_cabinet": (0.08, 0.0),
            "small_rug": (0.0, 0.14),
        },
        "closet": {
            "storage_boxes": (0.0, 0.0),
            "wardrobe": (0.0, 0.0),
            "shelves": (0.0, 0.0),
        },
        "walk_in_closet": {
            "storage_boxes": (0.0, 0.0),
            "wardrobe": (0.0, 0.0),
            "shelves": (0.0, 0.0),
        },
        "bathroom": {
            "small_storage": (0.08, 0.08),
            "small_plant": (-0.1, -0.1),
        },
        "toilet": {
            "small_storage": (0.08, 0.08),
        },
        "washroom": {
            "small_storage": (0.08, 0.08),
            "small_plant": (-0.1, -0.1),
        },
        "balcony": {
            "small_plant": (-0.12, -0.12),
            "outdoor_chair": (0.1, 0.1),
        },
        "hallway": {
            "storage_shelf": (0.1, 0.0),
        },
        "kitchen": {
            "dining_table": (0.08, 0.08),
        },
    }
    offsets = room_offsets.get(room_type, {})
    offset_x, offset_y = offsets.get(item_name, _default_item_offset(index))
    rotation = _rotation_for_item(room_type, item_name)
    return base_x + offset_x, base_y + offset_y, rotation


def _default_item_offset(index: int) -> tuple[float, float]:
    offsets = [
        (-0.04, 0.0),
        (0.06, 0.05),
        (0.14, -0.02),
        (0.0, -0.12),
    ]
    return offsets[index % len(offsets)]


def _rotation_for_item(room_type: str, item_name: str) -> float:
    if item_name in {"wardrobe", "tv_stand", "storage_shelf", "shoe_cabinet"}:
        return 90.0
    if item_name in {"desk", "study_desk", "compact_work_desk"}:
        return 0.0
    if item_name in {"bed", "single_bed", "semi_double_bed", "double_bed"}:
        return 0.0 if room_type != "hallway" else 90.0
    return 0.0


def _clamp01(value: float | None) -> float | None:
    if value is None:
        return None
    return max(0.08, min(0.92, float(value)))
