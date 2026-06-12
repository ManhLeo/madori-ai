import json
import re
from pathlib import Path

from fastapi import APIRouter, Body, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from PIL import Image
import requests

from app.config import get_settings
from app.schemas import AnalyzeFloorplanResponse, GenerateResponse, UserPreferences
from app.services.auto_label_mapper import AutoLabelMapper
from app.services.auto_label_placer import AutoLabelPlacer, create_manual_labels_from_auto_suggestions
from app.services.file_service import FileService
from app.services.generation_pipeline import run_generation_pipeline
from app.services.manual_label_builder import empty_detected_label_boxes, empty_manual_labels
from app.services.ocr_label_service import OCRLabelService
from app.services.output_text_editor import apply_manual_labels
from app.services.public_image_service import upload_output_to_cloudinary
from app.services.vision_analyzer import VisionAnalyzer


router = APIRouter(prefix="/api", tags=["generation"])
RUN_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")


@router.post("/generate", response_model=GenerateResponse)
def generate_floorplan(
    floorplan: UploadFile = File(...),
    style: str = Form("japanese_watercolor"),
    target_user: str | None = Form(None),
    interior_style: str | None = Form(None),
    budget_level: str | None = Form(None),
    color_preference: str | None = Form(None),
    lifestyle: str | None = Form(None),
    special_requests: str | None = Form(None),
) -> GenerateResponse:
    if not floorplan:
        raise HTTPException(status_code=400, detail="floorplan file is required")

    preferences = UserPreferences(
        target_user=target_user,
        interior_style=interior_style,
        budget_level=budget_level,
        color_preference=color_preference,
        lifestyle=_parse_csv_field(lifestyle),
        special_requests=_parse_csv_field(special_requests),
    )

    return run_generation_pipeline(floorplan, style, preferences)


@router.post("/analyze-floorplan", response_model=AnalyzeFloorplanResponse)
def analyze_floorplan(floorplan: UploadFile = File(...)) -> AnalyzeFloorplanResponse:
    if not floorplan:
        raise HTTPException(status_code=400, detail="floorplan file is required")

    settings = get_settings()
    file_service = FileService(settings.uploads_dir, settings.outputs_dir, settings.runs_dir)
    vision_analyzer = VisionAnalyzer()

    run_id = file_service.create_run_id()
    floorplan_path = file_service.save_floorplan(run_id, floorplan)
    analysis, raw_analysis = vision_analyzer.analyze_floorplan_with_raw(Path(floorplan_path))
    analysis = vision_analyzer.normalize_floorplan_analysis(analysis)
    file_service.save_json_file(run_id, "analysis_raw.json", raw_analysis)
    file_service.save_analysis_json(run_id, analysis)

    return AnalyzeFloorplanResponse(status="analyzed", run_id=run_id, analysis=analysis)


@router.get("/runs/{run_id}")
def inspect_run(run_id: str) -> dict:
    run_dir = _get_safe_run_dir(run_id)

    files = {
        "output": _relative_run_path(run_id, "output.png", run_dir / "output.png"),
        "overlay": _relative_run_path(run_id, "overlay_floorplan.png", run_dir / "overlay_floorplan.png"),
        "overlay_debug": _relative_run_path(run_id, "overlay_floorplan_debug.png", run_dir / "overlay_floorplan_debug.png"),
        "prompt": _relative_run_path(run_id, "prompt.txt", run_dir / "prompt.txt"),
        "furniture_plan": _relative_run_path(run_id, "furniture_plan.json", run_dir / "furniture_plan.json"),
        "generation_debug": _relative_run_path(run_id, "generation_debug.json", run_dir / "generation_debug.json"),
        "provider_status": _relative_run_path(run_id, "provider_status.json", run_dir / "provider_status.json"),
        "quality_check": _relative_run_path(run_id, "quality_check.json", run_dir / "quality_check.json"),
        "output_label_edit": _relative_run_path(run_id, "output_label_edit.json", run_dir / "output_label_edit.json"),
        "detected_label_boxes": _relative_run_path(run_id, "detected_label_boxes.json", run_dir / "detected_label_boxes.json"),
        "detected_label_boxes_debug": _relative_run_path(
            run_id,
            "detected_label_boxes_debug.png",
            run_dir / "detected_label_boxes_debug.png",
        ),
        "detected_label_candidates": _relative_run_path(
            run_id,
            "detected_label_candidates.json",
            run_dir / "detected_label_candidates.json",
        ),
        "ocr_text_boxes": _relative_run_path(run_id, "ocr_text_boxes.json", run_dir / "ocr_text_boxes.json"),
        "auto_label_suggestions": _relative_run_path(
            run_id,
            "auto_label_suggestions.json",
            run_dir / "auto_label_suggestions.json",
        ),
        "auto_label_debug": _relative_run_path(run_id, "auto_label_debug.png", run_dir / "auto_label_debug.png"),
        "normalized_floorplan": _relative_run_path(run_id, "normalized_floorplan.png", run_dir / "normalized_floorplan.png"),
        "normalization_metadata": _relative_run_path(
            run_id,
            "normalization_metadata.json",
            run_dir / "normalization_metadata.json",
        ),
        "layout_content_bbox": _relative_run_path(
            run_id,
            "layout_content_bbox.json",
            run_dir / "layout_content_bbox.json",
        ),
        "structure_mask": _relative_run_path(run_id, "structure_mask.png", run_dir / "structure_mask.png"),
        "structure_layer": _relative_run_path(run_id, "structure_layer.png", run_dir / "structure_layer.png"),
        "structure_extraction": _relative_run_path(
            run_id,
            "structure_extraction.json",
            run_dir / "structure_extraction.json",
        ),
        "watercolor_background": _relative_run_path(
            run_id,
            "watercolor_background.png",
            run_dir / "watercolor_background.png",
        ),
        "layout_locked_render": _relative_run_path(
            run_id,
            "layout_locked_render.json",
            run_dir / "layout_locked_render.json",
        ),
        "layout_guard": _relative_run_path(run_id, "layout_guard.json", run_dir / "layout_guard.json"),
        "layout_guard_reference_crop": _relative_run_path(
            run_id,
            "layout_guard_reference_crop.png",
            run_dir / "layout_guard_reference_crop.png",
        ),
        "layout_guard_output_crop": _relative_run_path(
            run_id,
            "layout_guard_output_crop.png",
            run_dir / "layout_guard_output_crop.png",
        ),
        "layout_diff": _relative_run_path(run_id, "layout_diff.png", run_dir / "layout_diff.png"),
        "output_structure_mask": _relative_run_path(
            run_id,
            "output_structure_mask.png",
            run_dir / "output_structure_mask.png",
        ),
        "ai_draft_output": _relative_run_path(run_id, "ai_draft_output.png", run_dir / "ai_draft_output.png"),
        "manual_labels": _relative_run_path(run_id, "manual_labels.json", run_dir / "manual_labels.json"),
    }

    prompt_text = _read_text_or_none(run_dir / "prompt.txt")
    output_url = _read_text_or_none(run_dir / "output_url.txt")
    return {
        "run_id": run_id,
        "files": files,
        "output_url": output_url,
        "download_url": f"/api/runs/{run_id}/download",
        "generation_debug": _read_json_or_none(run_dir / "generation_debug.json"),
        "provider_status": _read_json_or_none(run_dir / "provider_status.json"),
        "furniture_plan": _read_json_or_none(run_dir / "furniture_plan.json"),
        "quality_check": _read_json_or_none(run_dir / "quality_check.json"),
        "output_label_edit": _read_json_or_none(run_dir / "output_label_edit.json"),
        "detected_label_boxes": _read_json_or_none(run_dir / "detected_label_boxes.json"),
        "detected_label_candidates": _read_json_or_none(run_dir / "detected_label_candidates.json"),
        "ocr_text_boxes": _read_json_or_none(run_dir / "ocr_text_boxes.json"),
        "auto_label_suggestions": _read_json_or_none(run_dir / "auto_label_suggestions.json"),
        "normalization_metadata": _read_json_or_none(run_dir / "normalization_metadata.json"),
        "layout_content_bbox": _read_json_or_none(run_dir / "layout_content_bbox.json"),
        "structure_extraction": _read_json_or_none(run_dir / "structure_extraction.json"),
        "layout_locked_render": _read_json_or_none(run_dir / "layout_locked_render.json"),
        "layout_guard": _read_json_or_none(run_dir / "layout_guard.json"),
        "manual_labels": _read_json_or_none(run_dir / "manual_labels.json"),
        "prompt_preview": prompt_text[:1000] if prompt_text else None,
    }


@router.get("/runs/{run_id}/label-boxes")
def get_run_label_boxes(run_id: str) -> dict:
    run_dir = _get_safe_run_dir(run_id)
    return _read_json_or_none(run_dir / "detected_label_boxes.json") or empty_detected_label_boxes()


@router.get("/runs/{run_id}/ocr-text-boxes")
def get_run_ocr_text_boxes(run_id: str) -> dict:
    run_dir = _get_safe_run_dir(run_id)
    return _read_json_or_none(run_dir / "ocr_text_boxes.json") or _empty_ocr_text_boxes()


@router.get("/runs/{run_id}/auto-label-suggestions")
def get_run_auto_label_suggestions(run_id: str) -> dict:
    run_dir = _get_safe_run_dir(run_id)
    return _read_json_or_none(run_dir / "auto_label_suggestions.json") or _empty_auto_label_suggestions()


@router.post("/runs/{run_id}/auto-detect-labels")
def auto_detect_run_labels(run_id: str) -> dict:
    run_dir = _get_safe_run_dir(run_id)
    output_path = run_dir / "output.png"
    if not output_path.exists():
        raise HTTPException(status_code=404, detail="generated output image not found")

    result = _run_auto_label_detection(run_dir, output_path)
    if result["manual_labels"].get("labels"):
        _write_json(run_dir / "manual_labels.json", _validate_manual_labels(result["manual_labels"], _read_output_bounds(run_dir)))

    quality_check = _mark_quality_labels_need_review(run_dir, result)
    _write_json(run_dir / "quality_check.json", quality_check)

    return {
        "status": "auto_detected",
        "run_id": run_id,
        "ocr_text_count": len(result["ocr_text_boxes"].get("texts", [])),
        "auto_label_suggestion_count": len(result["auto_label_suggestions"].get("labels", [])),
        "manual_labels": result["manual_labels"],
        "auto_label_suggestions": result["auto_label_suggestions"],
        "quality_check": quality_check,
    }


@router.get("/runs/{run_id}/manual-labels")
def get_run_manual_labels(run_id: str) -> dict:
    run_dir = _get_safe_run_dir(run_id)
    return _read_json_or_none(run_dir / "manual_labels.json") or empty_manual_labels()


@router.put("/runs/{run_id}/manual-labels")
def update_run_manual_labels(run_id: str, payload: dict = Body(...)) -> dict:
    run_dir = _get_safe_run_dir(run_id)
    manual_labels = _validate_manual_labels(payload, _read_output_bounds(run_dir))
    _write_json(run_dir / "manual_labels.json", manual_labels)
    return manual_labels


@router.post("/runs/{run_id}/apply-manual-labels")
def apply_run_manual_labels(run_id: str) -> dict:
    run_dir = _get_safe_run_dir(run_id)
    output_path = run_dir / "output.png"
    manual_labels = _read_json_or_none(run_dir / "manual_labels.json") or empty_manual_labels()
    manual_labels = _validate_manual_labels(manual_labels, _read_output_bounds(run_dir))
    _write_json(run_dir / "manual_labels.json", manual_labels)

    edit_metadata = apply_manual_labels(output_path, run_dir / "manual_labels.json")
    output_label_edit = _build_manual_label_edit_metadata(edit_metadata)
    _write_json(run_dir / "output_label_edit.json", output_label_edit)
    quality_check = _update_quality_check_after_manual_labels(run_dir, edit_metadata, _empty_text_label_count(manual_labels))
    _write_json(run_dir / "quality_check.json", quality_check)
    _copy_output_to_public(run_id, output_path)
    _refresh_cloudinary_output_if_needed(run_id, run_dir, output_path)

    return {
        "status": "labels_applied",
        "run_id": run_id,
        "output_url": _read_text_or_none(run_dir / "output_url.txt"),
        "output_label_edit": output_label_edit,
        "quality_check": quality_check,
    }


@router.get("/runs/{run_id}/labels")
def get_run_labels(run_id: str) -> dict:
    return get_run_manual_labels(run_id)


@router.put("/runs/{run_id}/labels")
def update_run_labels(run_id: str, payload: dict = Body(...)) -> dict:
    return update_run_manual_labels(run_id, payload)


@router.post("/runs/{run_id}/apply-labels")
def apply_run_labels(run_id: str) -> dict:
    return apply_run_manual_labels(run_id)


@router.get("/runs/{run_id}/download")
def download_run_output(run_id: str) -> FileResponse:
    return _build_output_download_response(run_id)


@router.head("/runs/{run_id}/download")
def head_run_output_download(run_id: str) -> Response:
    run_dir = _get_safe_run_dir(run_id)
    output_path = run_dir / "output.png"
    if output_path.exists() and output_path.is_file():
        return _build_output_download_response(run_id)

    output_url = _read_text_or_none(run_dir / "output_url.txt")
    if not output_url:
        raise HTTPException(status_code=404, detail="generated output image not found")

    return Response(
        media_type="image/png",
        headers={"Content-Disposition": f'attachment; filename="madori-ai-{run_id}.png"'},
    )


def _build_output_download_response(run_id: str) -> FileResponse | StreamingResponse:
    run_dir = _get_safe_run_dir(run_id)
    output_path = run_dir / "output.png"
    if output_path.exists() and output_path.is_file():
        return FileResponse(
            path=output_path,
            media_type="image/png",
            filename=f"madori-ai-{run_id}.png",
            content_disposition_type="attachment",
        )

    output_url = _read_text_or_none(run_dir / "output_url.txt")
    if not output_url:
        raise HTTPException(status_code=404, detail="generated output image not found")

    try:
        response = requests.get(output_url, stream=True, timeout=30)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"failed to fetch generated output image: {exc}") from exc

    return StreamingResponse(
        response.iter_content(chunk_size=1024 * 64),
        media_type="image/png",
        headers={"Content-Disposition": f'attachment; filename="madori-ai-{run_id}.png"'},
    )


def _parse_csv_field(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _relative_run_path(run_id: str, filename: str, path: Path) -> str | None:
    if not path.exists():
        return None
    return f"runs/{run_id}/{filename}"


def _get_safe_run_dir(run_id: str) -> Path:
    if not RUN_ID_PATTERN.fullmatch(run_id):
        raise HTTPException(status_code=400, detail="invalid run_id")

    settings = get_settings()
    run_dir = settings.runs_dir / run_id
    resolved_runs_dir = settings.runs_dir.resolve()
    resolved_run_dir = run_dir.resolve()
    if resolved_runs_dir not in resolved_run_dir.parents and resolved_run_dir != resolved_runs_dir:
        raise HTTPException(status_code=400, detail="invalid run_id")

    if not run_dir.exists() or not run_dir.is_dir():
        raise HTTPException(status_code=404, detail="run not found")

    return run_dir


def _read_json_or_none(path: Path) -> dict | list | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _read_text_or_none(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _write_json(path: Path, payload: dict) -> None:
    try:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"failed to save {path.name}") from exc


def _validate_manual_labels(payload: dict, image_bounds: tuple[int, int] | None = None) -> dict:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="manual labels payload must be a JSON object")
    version = str(payload.get("version") or "1.0")
    source = str(payload.get("source") or "manual")
    needs_manual_review = bool(payload.get("needs_manual_review", True))
    labels = payload.get("labels", [])
    if not isinstance(labels, list):
        raise HTTPException(status_code=422, detail="manual_labels.labels must be a list")

    normalized_labels = []
    for index, label in enumerate(labels):
        if not isinstance(label, dict):
            raise HTTPException(status_code=422, detail=f"manual label at index {index} must be an object")
        text = str(label.get("text") or "").strip()
        bbox = _coerce_label_bbox(label, index, image_bounds)
        normalized_labels.append(
            {
                "id": str(label.get("id") or f"label_{index + 1}"),
                "text": text,
                "bbox": bbox,
                "locked": bool(label.get("locked", False)),
                "needs_text": bool(label.get("needs_text", not text)),
            }
        )

    return {"version": version, "source": source, "needs_manual_review": needs_manual_review, "labels": normalized_labels}


def _coerce_label_bbox(label: dict, index: int, image_bounds: tuple[int, int] | None) -> list[int]:
    bbox = label.get("bbox")
    if isinstance(bbox, list) and len(bbox) == 4:
        try:
            x0, y0, x1, y1 = [float(value) for value in bbox]
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=f"label {index + 1} bbox values must be numbers") from exc
        if x1 <= x0 or y1 <= y0:
            raise HTTPException(status_code=422, detail=f"label {index + 1} bbox must have positive width and height")
        return _validate_bbox_bounds([int(round(x0)), int(round(y0)), int(round(x1)), int(round(y1))], index, image_bounds)

    x = _coerce_number(label.get("x"), f"label {index + 1} x")
    y = _coerce_number(label.get("y"), f"label {index + 1} y")
    width = _coerce_positive_number(label.get("width"), f"label {index + 1} width")
    height = _coerce_positive_number(label.get("height"), f"label {index + 1} height")
    return _validate_bbox_bounds([int(round(x)), int(round(y)), int(round(x + width)), int(round(y + height))], index, image_bounds)


def _validate_bbox_bounds(bbox: list[int], index: int, image_bounds: tuple[int, int] | None) -> list[int]:
    if not image_bounds:
        return bbox
    image_width, image_height = image_bounds
    x0, y0, x1, y1 = bbox
    if x0 < 0 or y0 < 0 or x1 > image_width or y1 > image_height:
        raise HTTPException(status_code=422, detail=f"label {index + 1} bbox must be inside output image bounds")
    return bbox


def _coerce_number(value, field_name: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"{field_name} must be a number") from exc


def _coerce_positive_number(value, field_name: str) -> float:
    number = _coerce_number(value, field_name)
    if number <= 0:
        raise HTTPException(status_code=422, detail=f"{field_name} must be greater than 0")
    return number


def _empty_text_label_count(manual_labels: dict) -> int:
    labels = manual_labels.get("labels", []) if isinstance(manual_labels, dict) else []
    return sum(1 for label in labels if not str(label.get("text") or "").strip())


def _build_manual_label_edit_metadata(edit_metadata: dict) -> dict:
    return {
        "enabled": True,
        "mode": "manual",
        "language": "en",
        "status": "done" if edit_metadata.get("labels_processed", 0) > 0 and edit_metadata.get("labels_skipped", 0) == 0 else "needs_review",
        "edited_labels": [],
        "warnings": edit_metadata.get("warnings", []),
        "manual_labels_result": edit_metadata,
    }


def _update_quality_check_after_manual_labels(run_dir: Path, edit_metadata: dict, empty_text_count: int) -> dict:
    quality_check = _read_json_or_none(run_dir / "quality_check.json") or {}
    quality_check.setdefault("output_size_required", "1200x1200")
    quality_check.setdefault("output_size_actual", "unknown")
    quality_check["english_labels_required"] = True
    quality_check["english_labels_status"] = (
        "done" if edit_metadata.get("labels_processed", 0) > 0 and empty_text_count == 0 else "needs_review"
    )
    quality_check.setdefault("layout_accuracy_required", "100%")
    quality_check["layout_accuracy_status"] = "manual_review_required"
    quality_check["watercolor_quality_status"] = "manual_review_required"
    quality_check["needs_manual_review"] = True
    return quality_check


def _run_auto_label_detection(run_dir: Path, output_path: Path) -> dict:
    settings = get_settings()
    try:
        ocr_text_boxes = OCRLabelService().extract_text_boxes(output_path)
        _write_json(run_dir / "ocr_text_boxes.json", ocr_text_boxes)

        mapped = AutoLabelMapper().map_ocr_texts(ocr_text_boxes)
        auto_label_suggestions = AutoLabelPlacer().place_labels(
            output_path,
            mapped,
            ocr_result=ocr_text_boxes,
            confidence_threshold=settings.label_auto_apply_confidence_threshold,
        )
        _write_json(run_dir / "auto_label_suggestions.json", auto_label_suggestions)
        manual_labels = create_manual_labels_from_auto_suggestions(
            auto_label_suggestions,
            confidence_threshold=settings.label_auto_apply_confidence_threshold,
        )
        return {
            "ocr_text_boxes": ocr_text_boxes,
            "auto_label_suggestions": auto_label_suggestions,
            "manual_labels": manual_labels,
        }
    except Exception as exc:
        empty_ocr = _empty_ocr_text_boxes(f"OCR auto detection failed: {exc}")
        empty_suggestions = _empty_auto_label_suggestions(f"OCR auto detection failed: {exc}")
        _write_json(run_dir / "ocr_text_boxes.json", empty_ocr)
        _write_json(run_dir / "auto_label_suggestions.json", empty_suggestions)
        try:
            AutoLabelPlacer().save_debug_image(output_path, empty_suggestions, empty_ocr)
        except Exception:
            pass
        return {
            "ocr_text_boxes": empty_ocr,
            "auto_label_suggestions": empty_suggestions,
            "manual_labels": empty_manual_labels(),
        }


def _mark_quality_labels_need_review(run_dir: Path, auto_detect_result: dict) -> dict:
    quality_check = _read_json_or_none(run_dir / "quality_check.json") or {}
    suggestions = auto_detect_result.get("auto_label_suggestions", {})
    labels = suggestions.get("labels", []) if isinstance(suggestions, dict) else []
    quality_check.setdefault("output_size_required", "1200x1200")
    quality_check.setdefault("output_size_actual", "unknown")
    quality_check["english_labels_required"] = True
    quality_check["english_labels_status"] = "needs_review"
    quality_check["ocr_text_count"] = len(auto_detect_result.get("ocr_text_boxes", {}).get("texts", []))
    quality_check["auto_label_suggestion_count"] = len(labels)
    quality_check["layout_accuracy_required"] = "100%"
    quality_check["layout_accuracy_status"] = "manual_review_required"
    quality_check["watercolor_quality_status"] = "manual_review_required"
    quality_check["needs_manual_review"] = True
    return quality_check


def _empty_ocr_text_boxes(warning: str | None = None) -> dict:
    settings = get_settings()
    warnings = [warning] if warning else []
    return {
        "provider": settings.label_ocr_provider,
        "image_width": None,
        "image_height": None,
        "texts": [],
        "warnings": warnings,
    }


def _empty_auto_label_suggestions(warning: str | None = None) -> dict:
    warnings = [warning] if warning else []
    return {
        "version": "1.0",
        "source": "ocr_auto_label_placement",
        "labels": [],
        "unmapped_texts": [],
        "warnings": warnings,
        "needs_manual_review": True,
    }


def _copy_output_to_public(run_id: str, output_path: Path) -> None:
    settings = get_settings()
    file_service = FileService(settings.uploads_dir, settings.outputs_dir, settings.runs_dir)
    file_service.copy_output_to_public(run_id, output_path)


def _refresh_cloudinary_output_if_needed(run_id: str, run_dir: Path, output_path: Path) -> None:
    output_url_path = run_dir / "output_url.txt"
    if not output_url_path.exists():
        return
    try:
        output_url = upload_output_to_cloudinary(output_path, run_id)
        output_url_path.write_text(output_url, encoding="utf-8")
    except HTTPException:
        raise


def _read_output_bounds(run_dir: Path) -> tuple[int, int] | None:
    output_path = run_dir / "output.png"
    if not output_path.exists():
        return None
    try:
        with Image.open(output_path) as image:
            return image.size
    except OSError:
        return None
