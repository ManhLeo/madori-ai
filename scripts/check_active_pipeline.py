from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.config import get_settings


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _scan_files(paths: list[Path], tokens: list[str]) -> list[str]:
    matches: list[str] = []
    for path in paths:
        text = _read_text(path)
        if not text:
            continue
        lowered = text.lower()
        if any(token in lowered for token in tokens):
            matches.append(str(path).replace("\\", "/"))
    return matches


def _collect_repo_files() -> list[Path]:
    allowed_suffixes = {".py", ".js", ".html", ".css", ".md", ".example"}
    files: list[Path] = []
    self_path = Path(__file__).resolve()
    for path in ROOT_DIR.rglob("*"):
        if not path.is_file():
            continue
        if path.resolve() == self_path:
            continue
        if "__pycache__" in path.parts or "storage" in path.parts or ".git" in path.parts:
            continue
        if path.suffix.lower() not in allowed_suffixes and path.name != ".env.example":
            continue
        files.append(path)
    return files


def _active_app_files() -> list[Path]:
    files: list[Path] = []
    for root in (ROOT_DIR / "app", ROOT_DIR / "app" / "static"):
        if root.exists():
            for path in root.rglob("*"):
                if path.is_file() and path.suffix.lower() in {".py", ".js", ".html", ".css"}:
                    files.append(path)
    return files


def main() -> int:
    settings = get_settings()

    try:
        from app.main import app
    except Exception as exc:
        print(f"failed_to_import_app: {exc}")
        return 1

    route_paths = {getattr(route, "path", "") for route in app.routes}
    staged_route_paths = [
        "/api/runs",
        "/api/runs/{run_id}/inspect",
        "/api/runs/{run_id}/preprocess-floorplan",
        "/api/runs/{run_id}/analyze-floorplan",
        "/api/runs/{run_id}/validate-floorplan-analysis",
        "/api/runs/{run_id}/analyze-interiors",
        "/api/runs/{run_id}/validate-interior-analysis",
        "/api/runs/{run_id}/assign-room-functions",
        "/api/runs/{run_id}/create-initial-layout",
        "/api/runs/{run_id}/validate-layout",
        "/api/runs/{run_id}/plan-furniture-placement",
        "/api/runs/{run_id}/validate-furniture-placement",
        "/api/runs/{run_id}/create-render-plan",
        "/api/runs/{run_id}/create-prompt-package",
        "/api/runs/{run_id}/preview-image-generation-request",
        "/api/runs/{run_id}/generate-image-draft",
    ]

    repo_files = _collect_repo_files()
    active_app_files = _active_app_files()

    api_generate_route_present = "/api/generate" in route_paths
    frontend_references_api_generate = "/api/generate" in _read_text(ROOT_DIR / "app" / "static" / "app.js")

    analysis_provider_openai_only = str(settings.vision_provider).strip().lower() == "openai" and not bool(
        getattr(settings, "use_gemini_analysis", False)
    )
    image_provider_openai_only = True
    gemini_active = str(getattr(settings, "vision_provider", "")).strip().lower() == "gemini" or bool(
        getattr(settings, "use_gemini_analysis", False)
    )

    active_flux_tokens = ["api.fluxapi.ai", "flux-kontext", "provider=\"flux\"", "provider='flux'"]
    active_fal_tokens = ["fal.ai", "provider=\"fal\"", "provider='fal'"]
    active_ocr_tokens = ["label_ocr_provider", "google vision ocr", "tesseract", "pytesseract", "easyocr", "paddleocr"]

    reference_flux_tokens = ["fluxapi", "flux-kontext", "provider=\"flux\"", "provider='flux'"]
    reference_fal_tokens = ["fal.ai", "fal_", "provider=\"fal\"", "provider='fal'"]
    reference_ocr_tokens = ["label_ocr", "tesseract", "pytesseract", "easyocr", "paddleocr", "google vision ocr"]

    fluxapi_active_matches = _scan_files(active_app_files, active_flux_tokens)
    fal_active_matches = _scan_files(active_app_files, active_fal_tokens)
    ocr_active_matches = _scan_files(active_app_files, active_ocr_tokens)

    fluxapi_reference_files = _scan_files(repo_files, reference_flux_tokens)
    fal_reference_files = _scan_files(repo_files, reference_fal_tokens)
    ocr_reference_files = _scan_files(repo_files, reference_ocr_tokens)

    unsupported_provider_rejected = "unsupported_provider" in _read_text(
        ROOT_DIR / "app" / "services" / "image_generation_draft_service.py"
    ).lower()

    print(f"api_generate_route_present: {api_generate_route_present}")
    print(f"frontend_references_api_generate: {frontend_references_api_generate}")
    print(f"vision_provider_effective: {str(settings.vision_provider).strip().lower() or 'openai'}")
    print(f"analysis_provider_openai_only: {analysis_provider_openai_only}")
    print(f"image_provider_openai_only: {image_provider_openai_only}")
    print(f"gemini_active: {gemini_active}")
    print(f"fluxapi_active: {bool(fluxapi_active_matches)}")
    print(f"fal_active: {bool(fal_active_matches)}")
    print(f"ocr_active: {bool(ocr_active_matches)}")
    print(f"staged_routes_present: {all(path in route_paths for path in staged_route_paths)}")
    print(f"openai_draft_route_present: {'/api/runs/{run_id}/generate-image-draft' in route_paths}")
    print(f"unsupported_provider_rejected: {unsupported_provider_rejected}")
    print(f"fluxapi_reference_files: {fluxapi_reference_files[:10]}")
    print(f"fal_reference_files: {fal_reference_files[:10]}")
    print(f"ocr_reference_files: {ocr_reference_files[:10]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
