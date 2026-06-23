from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.config import get_settings


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit readiness for Phase 5C.1 image generation draft.")
    parser.add_argument("--run-id", required=True, help="Run ID to inspect")
    return parser.parse_args()


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _size_value(value: object) -> str:
    return str(value or "").strip()


def _select_reference_images(preview: dict, max_reference_images: int) -> list[dict]:
    input_images = list(preview.get("request_payload_preview", {}).get("input_images") or [])
    if not input_images:
        return []

    selected: list[dict] = []

    def add_first_match(predicate) -> None:
        for image in input_images:
            if predicate(image) and image not in selected:
                selected.append(image)
                return

    add_first_match(lambda image: image.get("role") == "structure_reference")
    add_first_match(lambda image: image.get("reference_type") == "ideal")
    add_first_match(lambda image: image.get("reference_type") == "ng")

    interior_count = 0
    for image in input_images:
        if image.get("role") != "interior_photo":
            continue
        if image in selected:
            continue
        selected.append(image)
        interior_count += 1
        if interior_count >= 2:
            break

    return selected[: max(0, max_reference_images)]


def main() -> int:
    args = _parse_args()
    settings = get_settings()
    run_id = args.run_id
    run_dir = settings.storage_runs_dir / run_id
    metadata_path = run_dir / "run_metadata.json"
    preview_path = run_dir / "artifacts" / "image_generation_request_preview.json"
    outputs_dir = run_dir / "outputs"

    print("This script does not call OpenAI and does not generate an image.")
    print(f"run_id: {run_id}")
    print(f"run_dir_exists: {run_dir.exists()}")
    print(f"run_metadata_exists: {metadata_path.exists()}")
    print(f"preview_artifact_exists: {preview_path.exists()}")

    readiness_status = "blocked"
    preview: dict | None = None
    preview_valid = False
    outputs_ready = False
    selected_images: list[dict] = []
    provider_size = ""
    final_delivery_size = ""

    if preview_path.exists():
        try:
            preview = _load_json(preview_path)
        except Exception as exc:  # noqa: BLE001
            print(f"preview_load_error: {exc.__class__.__name__}")
            preview = None

    if preview is not None:
        provider = preview.get("provider", {})
        request_payload = preview.get("request_payload_preview", {})
        target_output = preview.get("target_output", {})
        postprocess_plan = preview.get("postprocess_plan", {})
        provider_size_policy = preview.get("provider_size_policy", {})
        safety = preview.get("safety_and_cost_controls", {})
        request_quality = preview.get("request_quality", {})

        provider_name = str(provider.get("provider_name") or "")
        prompt = str(request_payload.get("prompt") or "")
        provider_size = _size_value(request_payload.get("size") or provider_size_policy.get("provider_requested_size"))
        final_delivery_size = _size_value(target_output.get("final_delivery_size") or postprocess_plan.get("final_delivery_size"))
        provider_size_supported = _truthy(request_payload.get("provider_size_supported"))
        input_image_count = int(request_quality.get("input_image_count") or 0)
        preview_valid = bool(provider_name == "openai" and prompt.strip() and provider_size and final_delivery_size and provider_size_supported and postprocess_plan)
        selected_images = _select_reference_images(preview, settings.openai_image_max_input_images)

        print(f"preview.provider.provider_name: {provider_name}")
        print(f"preview.request_payload_preview.prompt_empty: {not bool(prompt.strip())}")
        print(f"preview.request_payload_preview.size: {provider_size or None}")
        print(f"preview.request_payload_preview.provider_size_supported: {provider_size_supported}")
        print(f"preview.target_output.final_delivery_size: {final_delivery_size or None}")
        print(f"preview.postprocess_plan_exists: {bool(postprocess_plan)}")
        print(f"preview.input_image_count: {input_image_count}")
        print(f"preview.selected_reference_images_count: {len(selected_images)}")
        print(f"preview.selected_reference_images_roles: {[image.get('role') for image in selected_images]}")
        print(f"preview.safety_and_cost_controls.allow_provider_call: {safety.get('allow_provider_call')}")

    print(f"outputs_dir_exists: {outputs_dir.exists()}")
    if not outputs_dir.exists():
        try:
            outputs_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            print(f"outputs_dir_create_error: {exc.__class__.__name__}")
        outputs_ready = outputs_dir.exists()
        print(f"outputs_dir_created_or_exists: {outputs_ready}")
    else:
        outputs_ready = True
        print("outputs_dir_created_or_exists: True")

    print(f"ENABLE_OPENAI_IMAGE_GENERATION: {_truthy(settings.enable_openai_image_generation)}")
    print(f"OPENAI_IMAGE_DRY_RUN: {_truthy(settings.openai_image_dry_run)}")
    print(f"OPENAI_API_KEY_exists: {bool(settings.openai_api_key)}")
    print(f"OPENAI_IMAGE_MODEL: {settings.openai_image_model}")
    print(f"OPENAI_IMAGE_PROVIDER_SIZE: {settings.openai_image_provider_size}")
    print(f"OPENAI_IMAGE_FINAL_OUTPUT_SIZE: {settings.openai_image_final_output_size}")
    print(f"OPENAI_IMAGE_OUTPUT_FORMAT: {settings.openai_image_output_format}")
    print(f"OPENAI_IMAGE_QUALITY: {settings.openai_image_quality}")

    if preview is None:
        readiness_status = "blocked"
    elif not preview_valid or not outputs_ready:
        readiness_status = "blocked"
    elif not _truthy(settings.enable_openai_image_generation) or _truthy(settings.openai_image_dry_run) or not settings.openai_api_key:
        readiness_status = "ready_but_generation_disabled"
    else:
        readiness_status = "ready_for_real_provider_test"

    print(f"readiness_status: {readiness_status}")

    if preview is not None:
        warnings = []
        if not preview_valid:
            warnings.append("preview validation failed")
        if not provider_size:
            warnings.append("provider size is missing")
        if not final_delivery_size:
            warnings.append("final delivery size is missing")
        if not _truthy(preview.get("request_payload_preview", {}).get("provider_size_supported")):
            warnings.append("provider_size_supported is false")
        if int(preview.get("request_quality", {}).get("input_image_count") or 0) > 5:
            warnings.append("input_image_count exceeds 5")
        if warnings:
            print(f"warnings: {warnings}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
