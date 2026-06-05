import logging
import shutil
from pathlib import Path

from fastapi import UploadFile
from PIL import Image

from app.config import get_settings
from app.schemas import GenerationResponse, UserPreferences
from app.services.file_service import FileService
from app.services.furniture_overlay_renderer import FurnitureOverlayRenderer
from app.services.furniture_planner import plan_furniture
from app.services.image_provider import get_image_provider
from app.services.public_image_service import upload_floorplan_to_cloudinary
from app.services.prompt_builder import PromptBuilder
from app.services.vision_analyzer import VisionAnalyzer


logger = logging.getLogger(__name__)


def run_generation_pipeline(
    floorplan_file: UploadFile,
    style: str,
    preferences: UserPreferences | None = None,
) -> GenerationResponse:
    settings = get_settings()
    file_service = FileService(settings.uploads_dir, settings.outputs_dir, settings.runs_dir)
    vision_analyzer = VisionAnalyzer()
    overlay_renderer = FurnitureOverlayRenderer()
    prompt_builder = PromptBuilder()
    image_provider = get_image_provider()
    provider_name = settings.image_provider.strip().lower()
    preferences = preferences or UserPreferences()

    run_id = file_service.create_run_id()
    floorplan_path = file_service.save_floorplan(run_id, floorplan_file)
    run_dir = floorplan_path.parent

    analysis, gemini_furniture_plan, raw_analysis = vision_analyzer.analyze_floorplan_design_with_raw(Path(floorplan_path))
    analysis = vision_analyzer.normalize_floorplan_analysis(analysis)
    file_service.save_json_file(run_id, "analysis_raw.json", raw_analysis)
    file_service.save_analysis_json(run_id, analysis)

    use_gemini_furniture_plan = bool(
        gemini_furniture_plan and any(room_plan.items for room_plan in gemini_furniture_plan.room_plans)
    )
    furniture_plan = gemini_furniture_plan if use_gemini_furniture_plan else plan_furniture(analysis, preferences)
    file_service.save_json_file(run_id, "furniture_plan.json", furniture_plan)

    prompt_style = preferences.interior_style or style
    prompt = prompt_builder.build_generation_prompt(analysis, prompt_style, furniture_plan)
    file_service.save_text_file(run_id, "prompt.txt", prompt)
    file_service.save_json_file(
        run_id,
        "generation_debug.json",
        _build_generation_debug(run_id, provider_name, prompt, furniture_plan),
    )
    file_service.save_json_file(run_id, "provider_status.json", _build_provider_status(provider_name))

    # Debug-only artifact: never pass this overlay image to real image providers.
    overlay_floorplan_path = run_dir / "overlay_floorplan.png"
    try:
        overlay_renderer.render_overlay(Path(floorplan_path), furniture_plan, overlay_floorplan_path, analysis=analysis)
    except Exception as exc:
        logger.exception("overlay debug rendering failed run_id=%s", run_id)
        file_service.save_text_file(run_id, "overlay_error.txt", str(exc))
        _create_overlay_fallback(Path(floorplan_path), overlay_floorplan_path)

    public_floorplan_url = None
    if provider_name == "fluxapi":
        fluxapi_input_format = settings.fluxapi_input_image_format.strip().lower()
        if fluxapi_input_format not in {"original", "jpg", "png"}:
            from fastapi import HTTPException

            raise HTTPException(
                status_code=500,
                detail=(
                    f"Unsupported FLUXAPI_INPUT_IMAGE_FORMAT: {settings.fluxapi_input_image_format}. "
                    "Expected original, jpg, or png."
                ),
            )
        public_floorplan_url = upload_floorplan_to_cloudinary(
            Path(floorplan_path),
            run_id,
            format_for_flux=fluxapi_input_format,
        )
        file_service.save_text_file(run_id, "input_image_url.txt", public_floorplan_url)
    else:
        public_floorplan_url = None

    logger.info("generate run_id=%s", run_id)
    logger.info("generate local_floorplan_path=%s", floorplan_path)
    logger.info("generate overlay_floorplan_path=%s debug_only=true", overlay_floorplan_path)
    if public_floorplan_url:
        logger.info("generate cloudinary_secure_url=%s", public_floorplan_url)
        logger.info("generate inputImage source=original_floorplan_cloudinary")
        logger.info("generate fluxapi inputImage format=%s", settings.fluxapi_input_image_format)
        logger.info("generate fluxapi inputImage=%s", public_floorplan_url)
    else:
        logger.info("generate inputImage source=local_only")
    logger.info("generate preferences=%s", preferences.model_dump(mode="json"))
    logger.info(
        "generate furniture_plan_source=%s",
        "gemini" if use_gemini_furniture_plan else "deterministic_fallback",
    )
    logger.info("generate furniture_items_with_coordinates=%s", _count_furniture_items_with_coordinates(furniture_plan))
    logger.info("generate furniture_plan_summary=%s", _summarize_furniture_plan(furniture_plan))
    logger.info("generate prompt_preview=%s", prompt[:700])

    output_path = image_provider.generate(
        prompt,
        Path(floorplan_path),
        run_dir / "output.png",
        input_image_url=public_floorplan_url,
    )
    file_service.copy_output_to_public(run_id, output_path)

    return GenerationResponse(
        status="completed",
        run_id=run_id,
        analysis=analysis,
        prompt=prompt,
        output_url=f"/static/outputs/{run_id}_output.png",
    )


def _summarize_furniture_plan(furniture_plan) -> str:
    if not furniture_plan.room_plans:
        return "no room plans"

    summary_parts = []
    for room_plan in furniture_plan.room_plans:
        item_names = ", ".join(item.item for item in room_plan.items)
        summary_parts.append(f"{room_plan.room_type}@{room_plan.room_position or 'unknown'}: {item_names}")
    return "; ".join(summary_parts)


def _count_furniture_items_with_coordinates(furniture_plan) -> int:
    count = 0
    for room_plan in furniture_plan.room_plans:
        for item in room_plan.items:
            if item.relative_x is not None and item.relative_y is not None:
                count += 1
    return count


def _count_furniture_items(furniture_plan) -> int:
    return sum(len(room_plan.items) for room_plan in furniture_plan.room_plans)


def _build_generation_debug(run_id: str, provider_name: str, prompt: str, furniture_plan) -> dict:
    normalized_prompt = prompt.lower()
    return {
        "run_id": run_id,
        "image_provider": provider_name,
        "input_image_mode": "original_floorplan",
        "overlay_used_for_provider": False,
        "overlay_created_for_debug": True,
        "prompt_length": len(prompt),
        "prompt_contains_layout_preservation": _contains_all(
            normalized_prompt,
            ("preserve", "unchanged", "layout", "walls", "room boundaries"),
        ),
        "prompt_contains_room_by_room_furniture": (
            ("living room" in normalized_prompt and "furniture" in normalized_prompt)
            or ("bedroom" in normalized_prompt and "furniture" in normalized_prompt)
        ),
        "prompt_contains_top_down": "top-down" in normalized_prompt or "2d" in normalized_prompt,
        "prompt_contains_no_3d": "do not convert the image to 3d" in normalized_prompt or "no 3d" in normalized_prompt,
        "furniture_plan_room_count": len(furniture_plan.room_plans),
        "furniture_plan_item_count": _count_furniture_items(furniture_plan),
    }


def _contains_all(text: str, terms: tuple[str, ...]) -> bool:
    return all(term in text for term in terms)


def _build_provider_status(provider_name: str) -> dict:
    if provider_name == "stub":
        return {
            "image_provider": "stub",
            "external_generation_enabled": False,
            "reason": "FluxAPI disabled for development",
            "output_mode": "local_preview",
        }

    return {
        "image_provider": provider_name,
        "external_generation_enabled": True,
        "output_mode": "provider_generation",
        "input_image_mode": "original_floorplan",
        "overlay_used_for_provider": False,
    }


def _create_overlay_fallback(floorplan_path: Path, overlay_floorplan_path: Path) -> None:
    overlay_floorplan_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with Image.open(floorplan_path) as image:
            image.convert("RGB").save(overlay_floorplan_path, format="PNG")
            image.convert("RGB").save(overlay_floorplan_path.with_name("overlay_floorplan_debug.png"), format="PNG")
    except Exception:
        logger.exception("failed to create PNG overlay fallback; copying source bytes")
        try:
            shutil.copyfile(floorplan_path, overlay_floorplan_path)
            shutil.copyfile(floorplan_path, overlay_floorplan_path.with_name("overlay_floorplan_debug.png"))
        except OSError:
            logger.exception("failed to copy overlay fallback")
