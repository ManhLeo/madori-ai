import os
import tempfile
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parent.parent
UPLOADS_DIR = BASE_DIR / "uploads"
OUTPUTS_DIR = BASE_DIR / "outputs"
RUNS_DIR = BASE_DIR / "runs"
STATIC_DIR = Path(__file__).resolve().parent / "static"
VERCEL_RUNTIME_DIR = Path("/tmp/madori-ai")


def is_vercel_runtime() -> bool:
    return os.getenv("VERCEL") == "1" or os.getenv("VERCEL_ENV") is not None


def serverless_runtime_dir() -> Path:
    if os.name == "nt":
        return Path(tempfile.gettempdir()) / "madori-ai"
    return VERCEL_RUNTIME_DIR


class Settings(BaseSettings):
    app_name: str = "Madori AI MVP"
    debug: bool = False
    environment: str = "development"
    uploads_dir: Path = UPLOADS_DIR
    outputs_dir: Path = OUTPUTS_DIR
    runs_dir: Path = RUNS_DIR
    static_dir: Path = STATIC_DIR
    image_provider: str = Field(default="stub", validation_alias="IMAGE_PROVIDER")
    output_match_input_size: bool = Field(default=True, validation_alias="OUTPUT_MATCH_INPUT_SIZE")
    output_size_mode: str = Field(default="fixed", validation_alias="OUTPUT_SIZE_MODE")
    output_width: int = Field(default=1200, validation_alias="OUTPUT_WIDTH")
    output_height: int = Field(default=1200, validation_alias="OUTPUT_HEIGHT")
    output_resize_mode: str = Field(default="contain", validation_alias="OUTPUT_RESIZE_MODE")
    output_label_edit_enabled: bool = Field(default=True, validation_alias="OUTPUT_LABEL_EDIT_ENABLED")
    output_label_mode: str = Field(default="translate", validation_alias="OUTPUT_LABEL_MODE")
    output_label_language: str = Field(default="en", validation_alias="OUTPUT_LABEL_LANGUAGE")
    layout_locked_rendering_enabled: bool = Field(default=True, validation_alias="LAYOUT_LOCKED_RENDERING_ENABLED")
    layout_lock_mode: str = Field(default="structure_overlay", validation_alias="LAYOUT_LOCK_MODE")
    layout_lock_output_name: str = Field(default="output.png", validation_alias="LAYOUT_LOCK_OUTPUT_NAME")
    layout_lock_create_ai_draft: bool = Field(default=False, validation_alias="LAYOUT_LOCK_CREATE_AI_DRAFT")
    structure_extraction_enabled: bool = Field(default=True, validation_alias="STRUCTURE_EXTRACTION_ENABLED")
    structure_line_dark_threshold: int = Field(default=180, validation_alias="STRUCTURE_LINE_DARK_THRESHOLD")
    structure_min_component_area: int = Field(default=20, validation_alias="STRUCTURE_MIN_COMPONENT_AREA")
    structure_dilate_iterations: int = Field(default=1, validation_alias="STRUCTURE_DILATE_ITERATIONS")
    watercolor_background_enabled: bool = Field(default=True, validation_alias="WATERCOLOR_BACKGROUND_ENABLED")
    watercolor_background_mode: str = Field(default="soft_paper", validation_alias="WATERCOLOR_BACKGROUND_MODE")
    watercolor_draw_frame: bool = Field(default=False, validation_alias="WATERCOLOR_DRAW_FRAME")
    watercolor_background_strength: float = Field(default=0.35, validation_alias="WATERCOLOR_BACKGROUND_STRENGTH")
    layout_lock_blend_normalized_floorplan: bool = Field(default=True, validation_alias="LAYOUT_LOCK_BLEND_NORMALIZED_FLOORPLAN")
    layout_lock_normalized_floorplan_opacity: float = Field(default=1.0, validation_alias="LAYOUT_LOCK_NORMALIZED_FLOORPLAN_OPACITY")
    layout_guard_compare_region: str = Field(default="content_bbox", validation_alias="LAYOUT_GUARD_COMPARE_REGION")
    layout_guard_ignore_canvas_border: bool = Field(default=True, validation_alias="LAYOUT_GUARD_IGNORE_CANVAS_BORDER")
    layout_content_bbox_padding: int = Field(default=8, validation_alias="LAYOUT_CONTENT_BBOX_PADDING")
    layout_lock_reapply_structure: bool = Field(default=True, validation_alias="LAYOUT_LOCK_REAPPLY_STRUCTURE")
    label_ocr_provider: str = Field(default="none", validation_alias="LABEL_OCR_PROVIDER")
    label_ocr_enabled: bool = Field(default=False, validation_alias="LABEL_OCR_ENABLED")
    label_auto_apply_enabled: bool = Field(default=False, validation_alias="LABEL_AUTO_APPLY_ENABLED")
    label_auto_apply_confidence_threshold: float = Field(default=0.85, validation_alias="LABEL_AUTO_APPLY_CONFIDENCE_THRESHOLD")
    GOOGLE_APPLICATION_CREDENTIALS: str | None = Field(
        default_factory=lambda: os.getenv("GOOGLE_APPLICATION_CREDENTIALS"),
        validation_alias="GOOGLE_APPLICATION_CREDENTIALS",
    )
    google_cloud_project: str | None = Field(default=None, validation_alias="GOOGLE_CLOUD_PROJECT")
    label_ocr_language_hints: str = Field(default="ja,en", validation_alias="LABEL_OCR_LANGUAGE_HINTS")
    fluxapi_api_key: str | None = Field(default=None, validation_alias="FLUXAPI_API_KEY")
    fluxapi_model: str = Field(default="flux-kontext-pro", validation_alias="FLUXAPI_MODEL")
    fluxapi_input_image_url: str | None = Field(default=None, validation_alias="FLUXAPI_INPUT_IMAGE_URL")
    fluxapi_input_image_format: str = Field(default="jpg", validation_alias="FLUXAPI_INPUT_IMAGE_FORMAT")
    fluxapi_enable_translation: bool = Field(default=False, validation_alias="FLUXAPI_ENABLE_TRANSLATION")
    fluxapi_timeout_seconds: int = Field(default=600, validation_alias="FLUXAPI_TIMEOUT_SECONDS")
    fluxapi_poll_interval_seconds: int = Field(default=5, validation_alias="FLUXAPI_POLL_INTERVAL_SECONDS")
    cloudinary_cloud_name: str | None = Field(default=None, validation_alias="CLOUDINARY_CLOUD_NAME")
    cloudinary_api_key: str | None = Field(default=None, validation_alias="CLOUDINARY_API_KEY")
    cloudinary_api_secret: str | None = Field(default=None, validation_alias="CLOUDINARY_API_SECRET")
    gemini_api_key: str | None = Field(default=None, validation_alias="GEMINI_API_KEY")
    gemini_model: str = Field(default="gemini-2.5-flash", validation_alias="GEMINI_MODEL")
    use_gemini_analysis: bool = Field(default=False, validation_alias="USE_GEMINI_ANALYSIS")

    model_config = SettingsConfigDict(
        env_prefix="MADORI_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    if is_vercel_runtime():
        runtime_dir = serverless_runtime_dir()
        settings.uploads_dir = runtime_dir / "uploads"
        settings.outputs_dir = runtime_dir / "outputs"
        settings.runs_dir = runtime_dir / "runs"

    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    settings.outputs_dir.mkdir(parents=True, exist_ok=True)
    settings.runs_dir.mkdir(parents=True, exist_ok=True)
    settings.static_dir.mkdir(parents=True, exist_ok=True)
    return settings
