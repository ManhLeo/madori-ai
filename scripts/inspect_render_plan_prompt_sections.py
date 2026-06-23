from __future__ import annotations

import json
import sys
from pathlib import Path


def _format_block(title: str, value) -> None:
    print(f"\n{title}:")
    if isinstance(value, list):
        if not value:
            print("  []")
            return
        for index, item in enumerate(value, start=1):
            print(f"  {index}. {item}")
        return
    if isinstance(value, dict):
        print(json.dumps(value, ensure_ascii=False, indent=2))
        return
    if value in (None, ""):
        print("  <empty>")
        return
    print(f"  {value}")


def _count_drawable_furniture(furniture: list[dict]) -> int:
    return sum(1 for item in furniture if item.get("render_action") == "draw")


def _count_skipped_furniture(furniture: list[dict]) -> int:
    return sum(1 for item in furniture if item.get("render_action") == "skip_until_manual_placement")


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python scripts/inspect_render_plan_prompt_sections.py <render_plan.json>")
        return 1

    path = Path(sys.argv[1])
    payload = json.loads(path.read_text(encoding="utf-8"))

    prompt_sections = payload.get("prompt_sections") or {}
    render_readiness = payload.get("render_readiness") or {}
    rooms = payload.get("rooms") or []
    labels = payload.get("labels") or []
    furniture = payload.get("furniture") or []
    canvas = payload.get("canvas") or {}
    structure_lock = payload.get("structure_lock") or {}

    print("=" * 80)
    print("Render Plan Prompt Sections QA")
    print("=" * 80)
    print(f"render_plan_status: {payload.get('render_plan_status')}")
    print(f"run_id: {payload.get('run_id')}")
    print(f"canvas: {json.dumps(canvas, ensure_ascii=False)}")
    print(
        "structure_lock: "
        + json.dumps(
            {
                "enabled": structure_lock.get("enabled"),
                "preserve_original_layout": structure_lock.get("preserve_original_layout"),
                "structure_source": structure_lock.get("structure_source"),
            },
            ensure_ascii=False,
        )
    )

    _format_block("system_intent", prompt_sections.get("system_intent"))
    _format_block("layout_constraints", prompt_sections.get("layout_constraints"))
    _format_block("room_instructions", prompt_sections.get("room_instructions"))
    _format_block("furniture_instructions", prompt_sections.get("furniture_instructions"))
    _format_block("label_instructions", prompt_sections.get("label_instructions"))
    _format_block("style_instructions", prompt_sections.get("style_instructions"))
    _format_block("negative_constraints", prompt_sections.get("negative_constraints"))

    print("\nCounts:")
    print(f"  rooms: {len(rooms)}")
    print(f"  labels: {len(labels)}")
    print(f"  furniture: {len(furniture)}")
    print(f"  drawable furniture: {_count_drawable_furniture(furniture)}")
    print(f"  skipped furniture: {_count_skipped_furniture(furniture)}")
    print(f"  negative constraints: {len(prompt_sections.get('negative_constraints') or [])}")

    warnings: list[str] = []
    if not prompt_sections.get("system_intent"):
        warnings.append("system_intent is empty.")
    if not prompt_sections.get("layout_constraints"):
        warnings.append("layout_constraints is empty.")
    if not prompt_sections.get("negative_constraints"):
        warnings.append("negative_constraints is empty.")

    label_instructions = prompt_sections.get("label_instructions") or []
    has_english_label_instruction = any(
        isinstance(item, str)
        and ("English" in item or "english" in item)
        for item in label_instructions
    )
    if not has_english_label_instruction:
        warnings.append("no English label instructions exist.")

    if render_readiness.get("ready_for_prompt_building") is False:
        warnings.append("ready_for_prompt_building is false.")
    if render_readiness.get("ready_for_image_generation") is True:
        warnings.append("ready_for_image_generation is true in Phase 5A.")

    if warnings:
        print("\nWarnings:")
        for warning in warnings:
            print(f"  - {warning}")
    else:
        print("\nWarnings: none")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
