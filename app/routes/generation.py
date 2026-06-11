import json
import re
from pathlib import Path

from fastapi import APIRouter, Body, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
import requests

from app.config import get_settings
from app.schemas import AnalyzeFloorplanResponse, GenerateResponse, UserPreferences
from app.services.file_service import FileService
from app.services.generation_pipeline import run_generation_pipeline
from app.services.manual_label_builder import empty_manual_labels
from app.services.output_text_editor import apply_manual_labels_to_output
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
        "manual_labels": _read_json_or_none(run_dir / "manual_labels.json"),
        "prompt_preview": prompt_text[:1000] if prompt_text else None,
    }


@router.get("/runs/{run_id}/labels")
def get_run_labels(run_id: str) -> dict:
    run_dir = _get_safe_run_dir(run_id)
    return _read_json_or_none(run_dir / "manual_labels.json") or empty_manual_labels()


@router.put("/runs/{run_id}/labels")
def update_run_labels(run_id: str, payload: dict = Body(...)) -> dict:
    run_dir = _get_safe_run_dir(run_id)
    manual_labels = _validate_manual_labels(payload)
    _write_json(run_dir / "manual_labels.json", manual_labels)
    return manual_labels


@router.post("/runs/{run_id}/apply-labels")
def apply_run_labels(run_id: str) -> dict:
    run_dir = _get_safe_run_dir(run_id)
    output_path = run_dir / "output.png"
    manual_labels = _read_json_or_none(run_dir / "manual_labels.json") or empty_manual_labels()
    manual_labels = _validate_manual_labels(manual_labels)

    edit_metadata = apply_manual_labels_to_output(output_path, manual_labels)
    _write_json(run_dir / "output_label_edit.json", edit_metadata)
    quality_check = _update_quality_check_after_manual_labels(run_dir, edit_metadata, bool(manual_labels.get("labels")))
    _write_json(run_dir / "quality_check.json", quality_check)
    _copy_output_to_public(run_id, output_path)
    _refresh_cloudinary_output_if_needed(run_id, run_dir, output_path)

    return {
        "status": "labels_applied",
        "run_id": run_id,
        "output_url": _read_text_or_none(run_dir / "output_url.txt"),
        "output_label_edit": edit_metadata,
        "quality_check": quality_check,
    }


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


def _validate_manual_labels(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="manual labels payload must be a JSON object")
    version = str(payload.get("version") or "1.0")
    labels = payload.get("labels", [])
    if not isinstance(labels, list):
        raise HTTPException(status_code=422, detail="manual_labels.labels must be a list")

    normalized_labels = []
    for index, label in enumerate(labels):
        if not isinstance(label, dict):
            raise HTTPException(status_code=422, detail=f"manual label at index {index} must be an object")
        text = str(label.get("text") or "").strip()
        if not text:
            raise HTTPException(status_code=422, detail=f"manual label at index {index} requires text")
        normalized_labels.append(
            {
                "id": str(label.get("id") or f"label_{index + 1}"),
                "text": text,
                "x": _coerce_number(label.get("x"), f"label {index + 1} x"),
                "y": _coerce_number(label.get("y"), f"label {index + 1} y"),
                "width": _coerce_positive_number(label.get("width"), f"label {index + 1} width"),
                "height": _coerce_positive_number(label.get("height"), f"label {index + 1} height"),
                "font_size": int(_coerce_positive_number(label.get("font_size", 28), f"label {index + 1} font_size")),
                "align": _validate_align(label.get("align", "center")),
            }
        )

    return {"version": version, "labels": normalized_labels}


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


def _validate_align(value) -> str:
    align = str(value or "center").strip().lower()
    if align not in {"left", "center", "right"}:
        raise HTTPException(status_code=422, detail="label align must be left, center, or right")
    return align


def _update_quality_check_after_manual_labels(run_dir: Path, edit_metadata: dict, has_labels: bool) -> dict:
    quality_check = _read_json_or_none(run_dir / "quality_check.json") or {}
    quality_check.setdefault("output_size_required", "1200x1200")
    quality_check.setdefault("output_size_actual", "unknown")
    quality_check["english_labels_required"] = True
    quality_check["english_labels_status"] = "done" if has_labels and edit_metadata.get("status") == "done" else "needs_review"
    quality_check.setdefault("layout_accuracy_required", "100%")
    quality_check["layout_accuracy_status"] = "manual_review_required"
    quality_check["watercolor_quality_status"] = "manual_review_required"
    quality_check["needs_manual_review"] = True
    return quality_check


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
