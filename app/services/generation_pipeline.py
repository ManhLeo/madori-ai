import logging
import shutil
from pathlib import Path

from fastapi import HTTPException, UploadFile
from PIL import Image

from app.config import get_settings, is_vercel_runtime
from app.schemas import GenerationResponse, UserPreferences
from app.services.file_service import FileService
from app.services.furniture_overlay_renderer import FurnitureOverlayRenderer
from app.services.furniture_planner import plan_furniture
from app.services.image_postprocessor import match_image_size
from app.services.image_provider import get_image_provider
from app.services.label_box_detector import LabelBoxDetector
from app.services.layout_lock.floorplan_normalizer import FloorplanNormalizer
from app.services.layout_lock.layout_guard import LayoutGuard
from app.services.layout_lock.layout_locked_renderer import LayoutLockedRenderer
from app.services.layout_lock.structure_extractor import StructureExtractor
from app.services.manual_label_builder import build_manual_labels_from_detected_boxes
from app.services.auto_label_mapper import AutoLabelMapper
from app.services.auto_label_placer import AutoLabelPlacer, can_auto_apply_labels, create_manual_labels_from_auto_suggestions
from app.services.ocr_label_service import OCRLabelService
from app.services.output_text_editor import apply_manual_labels
from app.services.public_image_service import upload_floorplan_to_cloudinary, upload_output_to_cloudinary
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
    label_box_detector = LabelBoxDetector()
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
    file_service.save_json_file(run_id, "provider_status.json", _build_provider_status(provider_name))

    # Debug-only artifact: never pass this overlay image to real image providers.
    overlay_floorplan_path = run_dir / "overlay_floorplan.png"
    try:
        overlay_renderer.render_overlay(Path(floorplan_path), furniture_plan, overlay_floorplan_path, analysis=analysis)
    except Exception as exc:
        logger.exception("overlay debug rendering failed run_id=%s", run_id)
        file_service.save_text_file(run_id, "overlay_error.txt", str(exc))
        _create_overlay_fallback(Path(floorplan_path), overlay_floorplan_path)

    layout_lock_enabled = bool(settings.layout_locked_rendering_enabled)
    public_floorplan_url = None
    if provider_name == "fluxapi" and (not layout_lock_enabled or settings.layout_lock_create_ai_draft):
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

    layout_lock_metadata = None
    if layout_lock_enabled:
        output_path, layout_lock_metadata = _run_layout_locked_generation(
            file_service=file_service,
            run_id=run_id,
            run_dir=run_dir,
            floorplan_path=Path(floorplan_path),
            prompt=prompt,
            image_provider=image_provider,
            public_floorplan_url=public_floorplan_url,
            furniture_plan=furniture_plan,
        )
    else:
        output_path = image_provider.generate(
            prompt,
            Path(floorplan_path),
            run_dir / "output.png",
            input_image_url=public_floorplan_url,
        )
    image_postprocess_metadata = _postprocess_output_image(file_service, run_id, Path(output_path), Path(floorplan_path))
    detected_label_boxes = _detect_label_boxes(file_service, run_id, label_box_detector, Path(output_path))
    output_label_edit_metadata = _initial_manual_label_review_metadata(detected_label_boxes)

    manual_labels = build_manual_labels_from_detected_boxes(detected_label_boxes)
    ocr_workflow = None
    if settings.label_ocr_enabled:
        ocr_workflow = _run_ocr_label_workflow(file_service, run_id, Path(output_path))
        if ocr_workflow.get("manual_labels") and ocr_workflow["manual_labels"].get("labels"):
            manual_labels = ocr_workflow["manual_labels"]
            output_label_edit_metadata = _auto_label_review_metadata(ocr_workflow["auto_label_suggestions"])
            if settings.label_auto_apply_enabled and ocr_workflow.get("can_auto_apply"):
                file_service.save_json_file(run_id, "manual_labels.json", manual_labels)
                edit_metadata = apply_manual_labels(Path(output_path), run_dir / "manual_labels.json")
                output_label_edit_metadata = _auto_label_apply_metadata(edit_metadata)
        elif ocr_workflow.get("warnings"):
            output_label_edit_metadata["warnings"].extend(ocr_workflow["warnings"])

    file_service.save_json_file(run_id, "manual_labels.json", manual_labels)
    file_service.save_json_file(run_id, "output_label_edit.json", output_label_edit_metadata)
    quality_check = _build_quality_check(settings, image_postprocess_metadata, output_label_edit_metadata, layout_lock_metadata)
    file_service.save_json_file(run_id, "quality_check.json", quality_check)
    file_service.save_json_file(
        run_id,
        "generation_debug.json",
        _build_generation_debug(
            run_id,
            provider_name,
            prompt,
            furniture_plan,
            image_postprocess_metadata,
            output_label_edit_metadata,
            ocr_workflow,
            layout_lock_metadata,
        ),
    )
    file_service.copy_output_to_public(run_id, output_path)
    output_url = f"/static/outputs/{run_id}_output.png"

    if _should_persist_output_to_cloudinary(provider_name):
        try:
            output_url = upload_output_to_cloudinary(output_path, run_id)
            file_service.save_text_file(run_id, "output_url.txt", output_url)
            logger.info("generate cloudinary_output_url=%s", output_url)
        except HTTPException:
            if is_vercel_runtime():
                raise
            logger.exception("failed to persist output to Cloudinary; falling back to local output URL")

    return GenerationResponse(
        status="completed",
        run_id=run_id,
        analysis=analysis,
        prompt=prompt,
        output_url=output_url,
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


def _build_generation_debug(
    run_id: str,
    provider_name: str,
    prompt: str,
    furniture_plan,
    image_postprocess_metadata: dict | None = None,
    output_label_edit_metadata: dict | None = None,
    ocr_workflow: dict | None = None,
    layout_lock_metadata: dict | None = None,
) -> dict:
    normalized_prompt = prompt.lower()
    debug_payload = {
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
    debug_payload.update(
        {
            "output_match_input_size": bool(image_postprocess_metadata and image_postprocess_metadata.get("output_match_input_size")),
            "output_size_mode": (image_postprocess_metadata or {}).get("output_size_mode"),
            "output_resize_mode": (image_postprocess_metadata or {}).get("resize_mode"),
            "input_width": (image_postprocess_metadata or {}).get("reference_width"),
            "input_height": (image_postprocess_metadata or {}).get("reference_height"),
            "provider_output_width_before_resize": (image_postprocess_metadata or {}).get("original_output_width"),
            "provider_output_height_before_resize": (image_postprocess_metadata or {}).get("original_output_height"),
            "output_width": (image_postprocess_metadata or {}).get("final_output_width"),
            "output_height": (image_postprocess_metadata or {}).get("final_output_height"),
            "output_label_edit_enabled": bool(output_label_edit_metadata and output_label_edit_metadata.get("enabled")),
            "output_label_mode": (output_label_edit_metadata or {}).get("mode"),
            "quality_check_path": "quality_check.json",
            "manual_review_required": True,
        }
    )
    if ocr_workflow:
        ocr_result = ocr_workflow.get("ocr_result") or {}
        debug_payload.update(
            {
                "ocr_provider": ocr_result.get("provider"),
                "ocr_text_count": len(ocr_result.get("texts", [])),
                "ocr_warnings": ocr_workflow.get("warnings", []),
                "ocr_credentials_diagnostics": ocr_result.get("diagnostics"),
            }
        )
    if layout_lock_metadata:
        debug_payload.update(
            {
                "layout_locked_render": layout_lock_metadata.get("layout_locked_render"),
                "normalization_metadata": layout_lock_metadata.get("normalization_metadata"),
                "structure_extraction": layout_lock_metadata.get("structure_extraction"),
                "layout_guard": layout_lock_metadata.get("layout_guard"),
                "layout_content_bbox": layout_lock_metadata.get("layout_content_bbox"),
                "watercolor_background_config": layout_lock_metadata.get("watercolor_background_config"),
                "layout_guard_compare_region": (layout_lock_metadata.get("layout_guard") or {}).get("compare_region"),
                "layout_lock_warnings": layout_lock_metadata.get("warnings", []),
            }
        )
    if image_postprocess_metadata and image_postprocess_metadata.get("postprocess_error"):
        debug_payload["image_postprocess_error"] = image_postprocess_metadata["postprocess_error"]
    return debug_payload


def _contains_all(text: str, terms: tuple[str, ...]) -> bool:
    return all(term in text for term in terms)


def _build_provider_status(provider_name: str) -> dict:
    settings = get_settings()
    if settings.layout_locked_rendering_enabled and not settings.layout_lock_create_ai_draft:
        return {
            "image_provider": provider_name,
            "external_generation_enabled": False,
            "reason": "Layout-locked rendering is enabled; image provider is not used for final output.",
            "output_mode": "layout_locked_rendering",
            "input_image_mode": "original_floorplan",
            "overlay_used_for_provider": False,
        }

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


def _run_layout_locked_generation(
    file_service: FileService,
    run_id: str,
    run_dir: Path,
    floorplan_path: Path,
    prompt: str,
    image_provider,
    public_floorplan_url: str | None,
    furniture_plan,
) -> tuple[Path, dict]:
    settings = get_settings()
    warnings: list[str] = []
    if settings.layout_lock_mode.strip().lower() != "structure_overlay":
        raise HTTPException(
            status_code=500,
            detail=f"Unsupported LAYOUT_LOCK_MODE: {settings.layout_lock_mode}. Expected structure_overlay.",
        )

    output_name = settings.layout_lock_output_name or "output.png"
    if Path(output_name).name != output_name:
        raise HTTPException(status_code=500, detail="LAYOUT_LOCK_OUTPUT_NAME must be a filename, not a path.")

    normalized_floorplan_path = run_dir / "normalized_floorplan.png"
    normalization_metadata = FloorplanNormalizer().normalize_to_canvas(
        floorplan_path,
        normalized_floorplan_path,
        width=settings.output_width,
        height=settings.output_height,
        mode="contain",
    )
    file_service.save_json_file(run_id, "normalization_metadata.json", normalization_metadata)
    layout_content_bbox = normalization_metadata.get("content_bbox") or {}
    file_service.save_json_file(run_id, "layout_content_bbox.json", layout_content_bbox)

    structure_mask_path = run_dir / "structure_mask.png"
    structure_layer_path = run_dir / "structure_layer.png"
    if not settings.structure_extraction_enabled:
        raise HTTPException(status_code=500, detail="STRUCTURE_EXTRACTION_ENABLED must be true for layout-locked rendering.")
    structure_extraction = StructureExtractor().extract_structure(
        normalized_floorplan_path,
        structure_mask_path,
        structure_layer_path,
    )
    file_service.save_json_file(run_id, "structure_extraction.json", structure_extraction)

    ai_draft_metadata = None
    if settings.layout_lock_create_ai_draft:
        try:
            ai_draft_path = image_provider.generate(
                prompt,
                floorplan_path,
                run_dir / "ai_draft_output.png",
                input_image_url=public_floorplan_url,
            )
            ai_draft_metadata = {"path": ai_draft_path.name, "created": True}
        except HTTPException as exc:
            warnings.append(f"AI draft generation failed: {exc.detail}")
            ai_draft_metadata = {"created": False, "error": str(exc.detail)}

    output_path = run_dir / output_name
    render_metadata = LayoutLockedRenderer().render(
        run_dir=run_dir,
        normalized_floorplan_path=normalized_floorplan_path,
        structure_layer_path=structure_layer_path,
        output_path=output_path,
        furniture_layout=furniture_plan.model_dump(mode="json") if hasattr(furniture_plan, "model_dump") else None,
        manual_labels=None,
    )
    file_service.save_json_file(run_id, "layout_locked_render.json", render_metadata)

    layout_guard = LayoutGuard().compare_structure(
        reference_mask_path=structure_mask_path,
        final_output_path=output_path,
        output_diff_path=run_dir / "layout_diff.png",
        content_bbox_path=run_dir / "layout_content_bbox.json",
    )
    file_service.save_json_file(run_id, "layout_guard.json", layout_guard)

    metadata = {
        "enabled": True,
        "mode": settings.layout_lock_mode,
        "output_name": output_name,
        "normalization_metadata": normalization_metadata,
        "structure_extraction": structure_extraction,
        "layout_locked_render": render_metadata,
        "layout_guard": layout_guard,
        "layout_content_bbox": layout_content_bbox,
        "watercolor_background_config": {
            "enabled": bool(settings.watercolor_background_enabled),
            "mode": settings.watercolor_background_mode,
            "draw_frame": bool(settings.watercolor_draw_frame),
            "strength": float(settings.watercolor_background_strength),
        },
        "ai_draft": ai_draft_metadata,
        "warnings": warnings
        + normalization_metadata.get("warnings", [])
        + structure_extraction.get("warnings", [])
        + render_metadata.get("warnings", [])
        + layout_guard.get("warnings", []),
    }
    return output_path, metadata


def _should_persist_output_to_cloudinary(provider_name: str) -> bool:
    settings = get_settings()
    has_cloudinary_config = bool(
        settings.cloudinary_cloud_name and settings.cloudinary_api_key and settings.cloudinary_api_secret
    )
    return has_cloudinary_config and (is_vercel_runtime() or provider_name != "stub")


def _postprocess_output_image(
    file_service: FileService,
    run_id: str,
    output_path: Path,
    floorplan_path: Path,
) -> dict:
    settings = get_settings()
    output_size_mode = settings.output_size_mode.strip().lower()
    resize_mode = settings.output_resize_mode.strip().lower()
    metadata = {
        "output_match_input_size": output_size_mode == "match_input" and bool(settings.output_match_input_size),
        "output_size_mode": output_size_mode,
        "resize_mode": resize_mode,
        "required_width": settings.output_width if output_size_mode == "fixed" else None,
        "required_height": settings.output_height if output_size_mode == "fixed" else None,
    }
    if output_size_mode not in {"match_input", "fixed"}:
        raise HTTPException(
            status_code=500,
            detail=f"Unsupported OUTPUT_SIZE_MODE: {settings.output_size_mode}. Expected match_input or fixed.",
        )

    if output_size_mode == "match_input" and not settings.output_match_input_size:
        input_width, input_height = _read_image_size(floorplan_path)
        output_width, output_height = _read_image_size(output_path)
        metadata.update(
            {
                "required_width": input_width,
                "required_height": input_height,
                "reference_width": input_width,
                "reference_height": input_height,
                "original_output_width": output_width,
                "original_output_height": output_height,
                "final_output_width": output_width,
                "final_output_height": output_height,
            }
        )
        file_service.save_json_file(run_id, "image_postprocess.json", metadata)
        return metadata

    try:
        match_metadata = match_image_size(
            output_image_path=output_path,
            reference_image_path=floorplan_path,
            mode=resize_mode,
            output_size_mode=output_size_mode,
            required_width=settings.output_width,
            required_height=settings.output_height,
        )
        metadata.update(match_metadata)
        file_service.save_json_file(run_id, "image_postprocess.json", metadata)
        return metadata
    except HTTPException as exc:
        logger.exception("image post-processing failed run_id=%s", run_id)
        metadata["postprocess_error"] = str(exc.detail)
        if output_path.exists():
            input_width, input_height = _read_image_size(floorplan_path)
            output_width, output_height = _safe_read_image_size(output_path)
            metadata.update(
                {
                    "reference_width": input_width,
                    "reference_height": input_height,
                    "original_output_width": output_width,
                    "original_output_height": output_height,
                    "final_output_width": output_width,
                    "final_output_height": output_height,
                }
            )
            file_service.save_text_file(run_id, "image_postprocess_error.txt", str(exc.detail))
            file_service.save_json_file(run_id, "image_postprocess.json", metadata)
            return metadata
        raise


def _build_quality_check(
    settings,
    image_postprocess_metadata: dict,
    output_label_edit_metadata: dict,
    layout_lock_metadata: dict | None = None,
) -> dict:
    required_width = image_postprocess_metadata.get("required_width") or settings.output_width
    required_height = image_postprocess_metadata.get("required_height") or settings.output_height
    actual_width = image_postprocess_metadata.get("final_output_width")
    actual_height = image_postprocess_metadata.get("final_output_height")
    label_status = output_label_edit_metadata.get("status") or "needs_review"
    if settings.output_label_edit_enabled and label_status == "skipped":
        label_status = "needs_review"
    layout_guard = (layout_lock_metadata or {}).get("layout_guard") or {}
    quality = {
        "output_size_required": f"{required_width}x{required_height}",
        "output_size_actual": f"{actual_width}x{actual_height}" if actual_width and actual_height else "unknown",
        "english_labels_required": bool(settings.output_label_edit_enabled),
        "english_labels_status": label_status,
        "layout_accuracy_required": "100%",
        "layout_accuracy_status": "manual_review_required",
        "watercolor_quality_status": "manual_review_required",
        "needs_manual_review": True,
    }
    quality.update(
        {
            "layout_lock_enabled": bool(settings.layout_locked_rendering_enabled),
            "layout_lock_status": "active" if settings.layout_locked_rendering_enabled else "disabled",
            "layout_guard_status": layout_guard.get("status") or ("not_run" if not settings.layout_locked_rendering_enabled else "needs_review"),
            "layout_guard_score": layout_guard.get("score"),
            "layout_guard_compare_region": layout_guard.get("compare_region") or settings.layout_guard_compare_region,
        }
    )
    return quality


def _detect_label_boxes(
    file_service: FileService,
    run_id: str,
    label_box_detector: LabelBoxDetector,
    output_path: Path,
) -> dict:
    try:
        detected_label_boxes = label_box_detector.detect(output_path)
    except HTTPException as exc:
        logger.exception("label box detection failed run_id=%s", run_id)
        detected_label_boxes = {
            "method": "opencv_label_rectangle_detection",
            "image_width": None,
            "image_height": None,
            "boxes": [],
            "warnings": [str(exc.detail)],
        }
    file_service.save_json_file(run_id, "detected_label_boxes.json", detected_label_boxes)
    return detected_label_boxes


def _run_ocr_label_workflow(file_service: FileService, run_id: str, output_path: Path) -> dict:
    settings = get_settings()
    warnings = []
    try:
        ocr_result = OCRLabelService().extract_text_boxes(output_path)
        file_service.save_json_file(run_id, "ocr_text_boxes.json", ocr_result)

        mapped = AutoLabelMapper().map_ocr_texts(ocr_result)
        auto_suggestions = AutoLabelPlacer().place_labels(
            output_path,
            mapped,
            ocr_result=ocr_result,
            confidence_threshold=settings.label_auto_apply_confidence_threshold,
        )
        file_service.save_json_file(run_id, "auto_label_suggestions.json", auto_suggestions)
        manual_labels = create_manual_labels_from_auto_suggestions(
            auto_suggestions,
            confidence_threshold=settings.label_auto_apply_confidence_threshold,
        )
        return {
            "ocr_result": ocr_result,
            "auto_label_suggestions": auto_suggestions,
            "manual_labels": manual_labels,
            "can_auto_apply": can_auto_apply_labels(
                auto_suggestions,
                confidence_threshold=settings.label_auto_apply_confidence_threshold,
            ),
            "warnings": list(ocr_result.get("warnings", [])) + list(auto_suggestions.get("warnings", [])),
        }
    except Exception as exc:
        logger.exception("OCR label workflow failed run_id=%s", run_id)
        warnings.append(f"OCR label workflow failed: {exc}")
        empty_ocr = {
            "provider": settings.label_ocr_provider,
            "image_width": None,
            "image_height": None,
            "texts": [],
            "warnings": warnings,
            "diagnostics": {
                "ocr_provider": settings.label_ocr_provider,
                "ocr_enabled": bool(settings.label_ocr_enabled),
                "explicit_credentials_path_configured": bool(getattr(settings, "GOOGLE_APPLICATION_CREDENTIALS", None)),
                "credentials_file_exists": bool(
                    getattr(settings, "GOOGLE_APPLICATION_CREDENTIALS", None)
                    and Path(getattr(settings, "GOOGLE_APPLICATION_CREDENTIALS")).exists()
                ),
                "initialization_mode": None,
            },
        }
        empty_suggestions = {
            "version": "1.0",
            "source": "ocr_auto_label_placement",
            "labels": [],
            "unmapped_texts": [],
            "warnings": warnings,
            "needs_manual_review": True,
        }
        file_service.save_json_file(run_id, "ocr_text_boxes.json", empty_ocr)
        file_service.save_json_file(run_id, "auto_label_suggestions.json", empty_suggestions)
        try:
            AutoLabelPlacer().save_debug_image(output_path, empty_suggestions, empty_ocr)
        except Exception:
            logger.exception("failed to save auto label debug fallback run_id=%s", run_id)
        return {
            "ocr_result": empty_ocr,
            "auto_label_suggestions": empty_suggestions,
            "manual_labels": {"version": "1.0", "source": "auto_ocr", "needs_manual_review": True, "labels": []},
            "can_auto_apply": False,
            "warnings": warnings,
        }


def _initial_manual_label_review_metadata(detected_label_boxes: dict) -> dict:
    box_count = len(detected_label_boxes.get("boxes", [])) if isinstance(detected_label_boxes, dict) else 0
    warnings = []
    if box_count == 0:
        warnings.append("No label boxes were detected; manual label placement is required.")
    else:
        warnings.append("Manual English text entry is required before labels can be marked done.")
    return {
        "enabled": True,
        "mode": "manual",
        "language": "en",
        "status": "needs_review",
        "edited_labels": [],
        "warnings": warnings,
    }


def _auto_label_review_metadata(auto_suggestions: dict) -> dict:
    labels = auto_suggestions.get("labels", []) if isinstance(auto_suggestions, dict) else []
    warnings = list(auto_suggestions.get("warnings", [])) if isinstance(auto_suggestions, dict) else []
    if not labels:
        warnings.append("OCR did not produce mapped English label suggestions.")
    else:
        warnings.append("OCR label suggestions were created but still require manual review before completion.")
    if auto_suggestions.get("unmapped_texts"):
        warnings.append("Some OCR text was not mapped to English room labels.")
    return {
        "enabled": True,
        "mode": "ocr_manual_review",
        "language": "en",
        "status": "needs_review",
        "edited_labels": [],
        "warnings": warnings,
        "auto_label_suggestions_count": len(labels),
    }


def _auto_label_apply_metadata(edit_metadata: dict) -> dict:
    processed = edit_metadata.get("labels_processed", 0)
    skipped = edit_metadata.get("labels_skipped", 0)
    return {
        "enabled": True,
        "mode": "ocr_auto_apply",
        "language": "en",
        "status": "done" if processed > 0 and skipped == 0 else "needs_review",
        "edited_labels": [],
        "warnings": edit_metadata.get("warnings", []),
        "manual_labels_result": edit_metadata,
    }


def _read_image_size(image_path: Path) -> tuple[int, int]:
    try:
        with Image.open(image_path) as image:
            return image.size
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"failed to read image size from {image_path.name}: {exc}") from exc


def _safe_read_image_size(image_path: Path) -> tuple[int | None, int | None]:
    try:
        return _read_image_size(image_path)
    except HTTPException:
        return None, None


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
