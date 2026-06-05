from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
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
