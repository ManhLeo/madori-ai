from __future__ import annotations

from app.schemas import FloorplanAnalysis, FurniturePlan


def trim_prompt(prompt: str, max_chars: int = 2500) -> str:
    if len(prompt) <= max_chars:
        return prompt

    trimmed = prompt[:max_chars].rstrip()
    if "\n" in trimmed:
        trimmed = trimmed.rsplit("\n", 1)[0].rstrip()
    return trimmed


def build_generation_prompt(
    analysis: FloorplanAnalysis,
    style: str,
    furniture_plan: FurniturePlan | None = None,
) -> str:
    _ = analysis

    style_name = _normalize_style(style)
    style_details = _style_instruction(style_name)
    furniture_lines = _build_furniture_plan_lines(furniture_plan)

    prompt = "\n".join(
        [
            "Image editing task.",
            "",
            "Use the uploaded floorplan as a strict architectural reference.",
            "",
            "Preserve the original apartment layout exactly:",
            "- keep all walls in the same positions",
            "- keep all room boundaries unchanged",
            "- keep the entrance, balcony, kitchen, bathroom, washroom, toilet, doors and windows unchanged",
            "- do not add, remove, move, resize, split, merge or redesign any room",
            "- do not convert the image to 3D",
            "- keep a clean top-down 2D floorplan view",
            "",
            "Add realistic top-down furniture inside the existing rooms only.",
            "Furniture should be proportional to each room size.",
            "Furniture must stay inside the correct room.",
            "Furniture must not cross walls, doors, windows, labels, or room boundaries.",
            "Keep clear walking space.",
            "",
            "Interior design:",
            "Living room: compact sofa facing a low coffee table, TV stand against one wall, small dining table near the kitchen side, and one or two small plants if space allows.",
            "Bedroom: bed clearly centered in the bedroom, wardrobe along one wall, small nightstand beside the bed, and a small work desk only if space allows.",
            "Entrance: shoe cabinet near the entrance and a small entrance rug.",
            "Kitchen: keep kitchen fixtures in their original position; add only a compact refrigerator if space allows.",
            "Bathroom, toilet, washroom: keep all fixtures in place; do not add large furniture; add only tiny storage details if space allows.",
            "",
            "Furniture plan:",
            *furniture_lines,
            "",
            "Style:",
            f"{_style_label(style_name)}.",
            f"Use {style_details}.",
            "",
            "Rendering style:",
            "Japanese watercolor real-estate floorplan illustration. Soft colors, clean outlines, top-down furniture, bright and warm residential atmosphere.",
            "",
            "The final image should look like a furnished Japanese apartment floorplan illustration while preserving the original floorplan layout.",
        ]
    )

    return trim_prompt(prompt, max_chars=2500)


def _build_furniture_plan_lines(furniture_plan: FurniturePlan | None) -> list[str]:
    if furniture_plan is None or not furniture_plan.room_plans:
        return _default_furniture_plan_lines()

    lines: list[str] = []
    for room_plan in furniture_plan.room_plans:
        if not room_plan.items:
            continue
        room_name = _room_display_name(room_plan.room_type)
        item_names = _dedupe_items(_item_display_name(item.item) for item in room_plan.items)
        if not item_names:
            continue
        lines.append(f"- {room_name}: {', '.join(item_names[:6])}")
        if len(lines) >= 8:
            break

    return lines or _default_furniture_plan_lines()


def _default_furniture_plan_lines() -> list[str]:
    return [
        "- living room: compact sofa, coffee table, TV stand, small dining table",
        "- bedroom: centered bed, wardrobe, nightstand, optional small desk",
        "- entrance: shoe cabinet, entrance rug",
        "- kitchen: compact refrigerator if space allows",
    ]


def _dedupe_items(values) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _room_display_name(room_type: str | None) -> str:
    normalized = (room_type or "room").strip().lower().replace("-", "_").replace(" ", "_")
    labels = {
        "living_room": "living room",
        "dining_kitchen": "dining kitchen",
        "walk_in_closet": "walk-in closet",
    }
    return labels.get(normalized, normalized.replace("_", " "))


def _item_display_name(item_name: str | None) -> str:
    normalized = (item_name or "").strip().lower().replace("-", "_").replace(" ", "_")
    labels = {
        "tv_stand": "TV stand",
        "coffee_table": "coffee table",
        "dining_table": "small dining table",
        "small_dining_table": "small dining table",
        "single_bed": "bed",
        "semi_double_bed": "bed",
        "double_bed": "bed",
        "compact_work_desk": "small work desk",
        "small_desk": "small work desk",
        "shoe_cabinet": "shoe cabinet",
        "small_rug": "entrance rug",
        "entrance_rug": "entrance rug",
        "plant_pot": "small plant",
        "small_plant": "small plant",
    }
    return labels.get(normalized, normalized.replace("_", " "))


def _normalize_style(style: str | None) -> str:
    normalized = (style or "").strip().lower().replace("-", "_").replace(" ", "_")
    if normalized in {"japanese_natural", "scandinavian", "modern_minimal"}:
        return normalized
    return "japanese_natural"


def _style_label(style: str) -> str:
    labels = {
        "japanese_natural": "Japanese natural interior",
        "scandinavian": "Scandinavian interior",
        "modern_minimal": "Modern minimal interior",
    }
    return labels.get(style, labels["japanese_natural"])


def _style_instruction(style: str) -> str:
    normalized = _normalize_style(style)
    if normalized == "japanese_natural":
        return "light wood furniture, beige fabric, soft neutral colors, subtle green plants, and a clean minimalist Japanese apartment style"
    if normalized == "scandinavian":
        return "pale wood furniture, white and beige fabric, simple modern furniture, and a calm bright atmosphere"
    if normalized == "modern_minimal":
        return "clean lines, neutral colors, minimal furniture, and an uncluttered layout"
    return _style_instruction("japanese_natural")


class PromptBuilder:
    def build_generation_prompt(
        self,
        analysis: FloorplanAnalysis,
        style: str,
        furniture_plan: FurniturePlan | None = None,
    ) -> str:
        return build_generation_prompt(analysis, style, furniture_plan)
