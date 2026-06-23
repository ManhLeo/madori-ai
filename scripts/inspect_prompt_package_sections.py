from __future__ import annotations

import json
import sys
from pathlib import Path


SECTION_ORDER = [
    "system_prompt",
    "primary_generation_prompt",
    "structure_lock_prompt",
    "room_prompt",
    "furniture_prompt",
    "label_prompt",
    "style_prompt",
    "negative_prompt",
]

COMBINED_HEADERS = [
    "## System Prompt",
    "## Primary Generation Prompt",
    "## Structure Lock Prompt",
    "## Room Prompt",
    "## Furniture Prompt",
    "## Label Prompt",
    "## Style Prompt",
    "## Negative Prompt",
]


def _print_json_block(title: str, payload) -> None:
    print(f"\n=== {title} ===\n")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _print_text_block(title: str, text: str) -> None:
    print(f"\n=== {title} ===\n")
    print(text if text else "(empty)")


def _collect_warnings(payload: dict) -> list[str]:
    prompts = payload.get("prompts", {})
    provider_readiness = payload.get("provider_readiness", {})
    combined_prompt = str(prompts.get("combined_prompt") or "")
    negative_prompt = str(prompts.get("negative_prompt") or "")
    structure_lock_prompt = str(prompts.get("structure_lock_prompt") or "")
    label_prompt = str(prompts.get("label_prompt") or "")

    warnings: list[str] = []
    if not combined_prompt.strip():
        warnings.append("combined_prompt is empty.")
    if not negative_prompt.strip():
        warnings.append("negative_prompt is empty.")
    if not structure_lock_prompt.strip():
        warnings.append("structure_lock_prompt is empty.")
    if "English labels" not in label_prompt and "Use English labels only" not in label_prompt:
        warnings.append("label_prompt does not mention English labels.")
    if "Do not change the floorplan structure" not in combined_prompt:
        warnings.append("combined_prompt is missing the core structure lock phrase.")
    if "Do not use Japanese labels" not in negative_prompt:
        warnings.append("negative_prompt is missing the Japanese label exclusion.")
    if provider_readiness.get("ready_for_openai_image_api") is True:
        warnings.append("ready_for_openai_image_api is true before Phase 5C.")
    if provider_readiness.get("image_generation_done") is True:
        warnings.append("image_generation_done is true before Phase 5C.")
    if provider_readiness.get("watercolor_rendering_done") is True:
        warnings.append("watercolor_rendering_done is true before Phase 5C.")
    return warnings


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python scripts/inspect_prompt_package_sections.py <prompt_package.json>")
        return 1

    path = Path(sys.argv[1])
    payload = json.loads(path.read_text(encoding="utf-8"))
    prompts = payload.get("prompts", {})
    combined_prompt = str(prompts.get("combined_prompt") or "")
    detected_headers = [header for header in COMBINED_HEADERS if header in combined_prompt]
    prompt_quality = payload.get("prompt_quality", {})
    warnings = _collect_warnings(payload)

    print(f"prompt_package_status: {payload.get('prompt_package_status')}")
    print(f"run_id: {payload.get('run_id')}")
    _print_json_block("target_output", payload.get("target_output", {}))
    _print_json_block("provider_readiness", payload.get("provider_readiness", {}))
    _print_json_block("prompt_quality", prompt_quality)

    for section_name in SECTION_ORDER:
        _print_text_block(section_name, str(prompts.get(section_name) or ""))

    _print_json_block("combined_prompt_section_headers_detected", detected_headers)
    _print_json_block("artifact_warnings", payload.get("warnings", []))
    _print_json_block("artifact_errors", payload.get("errors", []))
    _print_json_block("validation_warnings", warnings)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
