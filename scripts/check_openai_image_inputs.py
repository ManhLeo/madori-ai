from __future__ import annotations

import json
import sys
from pathlib import Path


def detect_image_mime_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".png":
        return "image/png"
    if suffix == ".webp":
        return "image/webp"
    return "application/octet-stream"


def resolve_preview_path(input_path: Path) -> Path:
    if input_path.is_dir():
        artifacts_child = input_path / "artifacts" / "image_generation_request_preview.json"
        if artifacts_child.exists():
            return artifacts_child
        direct_child = input_path / "image_generation_request_preview.json"
        if direct_child.exists():
            return direct_child
    return input_path


def resolve_storage_path(run_dir: Path, relative_path: str) -> Path:
    normalized = str(relative_path or "").replace("\\", "/").strip()
    if normalized.startswith("storage/"):
        return run_dir.parent.parent / normalized
    return run_dir / normalized


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python scripts/check_openai_image_inputs.py <run_dir|image_generation_request_preview.json>")
        return 1

    input_path = Path(sys.argv[1]).resolve()
    preview_path = resolve_preview_path(input_path)
    if not preview_path.exists():
        print(f"preview_not_found: {preview_path}")
        return 1

    try:
        payload = json.loads(preview_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"failed_to_read_preview: {exc}")
        return 1

    run_dir = preview_path.parent.parent
    selected_images = list((payload.get("request_payload_preview") or {}).get("selected_reference_images") or [])

    print(f"selected_reference_count: {len(selected_images)}")
    for index, image in enumerate(selected_images, start=1):
        relative_path = str(image.get("relative_path") or "")
        resolved_path = resolve_storage_path(run_dir, relative_path)
        mime_type = detect_image_mime_type(resolved_path)
        supported = mime_type in {"image/png", "image/jpeg", "image/webp"}
        print(f"[{index}] role: {image.get('role')}")
        print(f"    path: {resolved_path}")
        print(f"    filename: {resolved_path.name}")
        print(f"    exists: {resolved_path.exists()}")
        print(f"    detected_mime_type: {mime_type}")
        print(f"    supported_by_openai: {supported}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
