import json
import re
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.config import get_settings
from app.schemas import AnalyzeFloorplanResponse, GenerateResponse, UserPreferences
from app.services.file_service import FileService
from app.services.generation_pipeline import run_generation_pipeline
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
    }

    prompt_text = _read_text_or_none(run_dir / "prompt.txt")
    return {
        "run_id": run_id,
        "files": files,
        "download_url": f"/api/runs/{run_id}/download",
        "generation_debug": _read_json_or_none(run_dir / "generation_debug.json"),
        "provider_status": _read_json_or_none(run_dir / "provider_status.json"),
        "furniture_plan": _read_json_or_none(run_dir / "furniture_plan.json"),
        "prompt_preview": prompt_text[:1000] if prompt_text else None,
    }


@router.get("/runs/{run_id}/download")
def download_run_output(run_id: str) -> FileResponse:
    return _build_output_download_response(run_id)


@router.head("/runs/{run_id}/download")
def head_run_output_download(run_id: str) -> FileResponse:
    return _build_output_download_response(run_id)


def _build_output_download_response(run_id: str) -> FileResponse:
    run_dir = _get_safe_run_dir(run_id)
    output_path = run_dir / "output.png"
    if not output_path.exists() or not output_path.is_file():
        raise HTTPException(status_code=404, detail="generated output image not found")

    return FileResponse(
        path=output_path,
        media_type="image/png",
        filename=f"madori-ai-{run_id}.png",
        content_disposition_type="attachment",
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
