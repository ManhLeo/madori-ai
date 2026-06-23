from __future__ import annotations

import json
import struct
import sys
from pathlib import Path


def read_png_size(path: Path) -> tuple[int | None, int | None]:
    if not path.exists():
        return None, None
    try:
        with path.open("rb") as file_obj:
            header = file_obj.read(24)
    except OSError:
        return None, None
    if len(header) < 24 or header[:8] != b"\x89PNG\r\n\x1a\n":
        return None, None
    width, height = struct.unpack(">II", header[16:24])
    return width, height


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python scripts/check_openai_strict_layout_test.py <image_generation_draft.json>")
        return 1

    artifact_path = Path(sys.argv[1])
    if not artifact_path.exists():
        print(f"Artifact not found: {artifact_path}")
        return 1

    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    request = artifact.get("request") or {}
    provider = artifact.get("provider") or {}
    outputs = artifact.get("outputs") or {}
    warnings = artifact.get("warnings") or []
    errors = artifact.get("errors") or []

    draft_image_path = outputs.get("output_image_path") or outputs.get("draft_image_path")
    if draft_image_path:
        output_path = Path(draft_image_path)
    else:
        output_path = Path()
    width, height = read_png_size(output_path) if draft_image_path else (None, None)

    selected_reference_images = request.get("selected_reference_images") or []
    primary_reference = selected_reference_images[0] if selected_reference_images else {}
    primary_ok = (
        str(primary_reference.get("role") or "") == "structure_reference"
        and str(primary_reference.get("relative_path") or "").replace("\\", "/").endswith("artifacts/normalized_floorplan.png")
    )

    print(f"prompt_mode: {request.get('prompt_mode')}")
    print(f"strict_layout_test_enabled: {request.get('strict_layout_test_enabled')}")
    print(f"normalized_floorplan_primary_reference: {primary_ok}")
    print(f"primary_structure_reference: {request.get('primary_structure_reference')}")
    print(f"long_prompt_disabled: {request.get('long_prompt_disabled')}")
    print(f"selected_reference_images_count: {len(selected_reference_images)}")
    print("selected_reference_images:")
    for image in selected_reference_images:
        print(f"  - role={image.get('role')} reference_type={image.get('reference_type')} relative_path={image.get('relative_path')}")
    print(f"api_call_performed: {provider.get('api_call_performed')}")
    print(f"output_image_path: {draft_image_path}")
    print(f"output_path_exists: {bool(draft_image_path and output_path.exists())}")
    print(f"output_size: {width}x{height}" if width and height else "output_size: unknown")
    print(f"warnings_count: {len(warnings)}")
    for warning in warnings:
        print(f"  warning: {warning}")
    print(f"errors_count: {len(errors)}")
    for error in errors:
        print(f"  error: {error}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
