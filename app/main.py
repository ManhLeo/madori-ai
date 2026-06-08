from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings, is_vercel_runtime
from app.routes.generation import router as generation_router


settings = get_settings()

app = FastAPI(title=settings.app_name, debug=settings.debug)

app.include_router(generation_router)
app.mount("/static/outputs", StaticFiles(directory=settings.outputs_dir), name="outputs")
app.mount("/runs", StaticFiles(directory=settings.runs_dir), name="runs")
app.mount("/assets", StaticFiles(directory=settings.static_dir), name="assets")


@app.get("/", tags=["frontend"])
def landing_page() -> FileResponse:
    return FileResponse(settings.static_dir / "index.html")


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> FileResponse:
    return FileResponse(settings.static_dir / "favicon.ico", media_type="image/x-icon")


@app.head("/favicon.ico", include_in_schema=False)
def favicon_head() -> FileResponse:
    return FileResponse(settings.static_dir / "favicon.ico", media_type="image/x-icon")


@app.get("/health", tags=["health"])
def health_check() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/deployment-check", tags=["deployment"])
def deployment_check() -> dict[str, object]:
    return {
        "is_vercel": is_vercel_runtime(),
        "runs_dir": str(settings.runs_dir),
        "uploads_dir": str(settings.uploads_dir),
        "outputs_dir": str(settings.outputs_dir),
        "runs_dir_writable": _is_directory_writable(settings.runs_dir),
        "uploads_dir_writable": _is_directory_writable(settings.uploads_dir),
        "outputs_dir_writable": _is_directory_writable(settings.outputs_dir),
        "image_provider": settings.image_provider,
        "has_gemini_key": bool(settings.gemini_api_key),
        "has_fluxapi_key": bool(settings.fluxapi_api_key),
        "has_cloudinary_config": bool(
            settings.cloudinary_cloud_name
            and settings.cloudinary_api_key
            and settings.cloudinary_api_secret
        ),
    }


def _is_directory_writable(directory) -> bool:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe_path = directory / ".write-test"
        probe_path.write_text("ok", encoding="utf-8")
        probe_path.unlink(missing_ok=True)
        return True
    except OSError:
        return False
