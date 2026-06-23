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
STORAGE_DIR = BASE_DIR / "storage"
STORAGE_RUNS_DIR = STORAGE_DIR / "runs"
STORAGE_OUTPUTS_DIR = STORAGE_DIR / "outputs"


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
    storage_dir: Path = STORAGE_DIR
    storage_runs_dir: Path = STORAGE_RUNS_DIR
    storage_outputs_dir: Path = STORAGE_OUTPUTS_DIR
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
    room_zone_detection_enabled: bool = Field(default=True, validation_alias="ROOM_ZONE_DETECTION_ENABLED")
    room_zone_min_area: int = Field(default=1500, validation_alias="ROOM_ZONE_MIN_AREA")
    room_zone_max_area_ratio: float = Field(default=0.65, validation_alias="ROOM_ZONE_MAX_AREA_RATIO")
    room_zone_morph_close_iterations: int = Field(default=2, validation_alias="ROOM_ZONE_MORPH_CLOSE_ITERATIONS")
    room_zone_padding: int = Field(default=4, validation_alias="ROOM_ZONE_PADDING")
    room_zone_debug_enabled: bool = Field(default=True, validation_alias="ROOM_ZONE_DEBUG_ENABLED")
    interior_mask_enabled: bool = Field(default=True, validation_alias="INTERIOR_MASK_ENABLED")
    furniture_placement_use_room_zones: bool = Field(default=True, validation_alias="FURNITURE_PLACEMENT_USE_ROOM_ZONES")
    label_auto_apply_enabled: bool = Field(default=False, validation_alias="LABEL_AUTO_APPLY_ENABLED")
    label_auto_apply_confidence_threshold: float = Field(default=0.85, validation_alias="LABEL_AUTO_APPLY_CONFIDENCE_THRESHOLD")
    openai_api_key: str | None = Field(default=None, validation_alias="OPENAI_API_KEY")
    vision_provider: str = Field(default="openai", validation_alias="VISION_PROVIDER")
    openai_analysis_model: str = Field(default="gpt-5.4", validation_alias="OPENAI_ANALYSIS_MODEL")
    openai_analysis_timeout_seconds: int = Field(default=90, validation_alias="OPENAI_ANALYSIS_TIMEOUT_SECONDS")
    openai_analysis_max_output_tokens: int = Field(default=4096, validation_alias="OPENAI_ANALYSIS_MAX_OUTPUT_TOKENS")
    openai_image_model: str = Field(default="gpt-image-1", validation_alias="OPENAI_IMAGE_MODEL")
    openai_image_output_size: str = Field(default="1200x1200", validation_alias="OPENAI_IMAGE_OUTPUT_SIZE")
    openai_image_provider_size: str = Field(default="1024x1024", validation_alias="OPENAI_IMAGE_PROVIDER_SIZE")
    openai_image_final_output_size: str = Field(default="1200x1200", validation_alias="OPENAI_IMAGE_FINAL_OUTPUT_SIZE")
    enable_openai_image_generation: bool = Field(default=False, validation_alias="ENABLE_OPENAI_IMAGE_GENERATION")
    openai_image_dry_run: bool = Field(default=True, validation_alias="OPENAI_IMAGE_DRY_RUN")
    openai_image_max_images: int = Field(default=1, validation_alias="OPENAI_IMAGE_MAX_IMAGES")
    openai_image_max_input_images: int = Field(default=5, validation_alias="OPENAI_IMAGE_MAX_INPUT_IMAGES")
    openai_image_output_format: str = Field(default="png", validation_alias="OPENAI_IMAGE_OUTPUT_FORMAT")
    openai_image_quality: str = Field(default="auto", validation_alias="OPENAI_IMAGE_QUALITY")
    openai_image_prompt_mode: str = Field(default="default", validation_alias="OPENAI_IMAGE_PROMPT_MODE")
    openai_image_require_structure_reference: bool = Field(default=True, validation_alias="OPENAI_IMAGE_REQUIRE_STRUCTURE_REFERENCE")
    openai_image_allow_prompt_only: bool = Field(default=False, validation_alias="OPENAI_IMAGE_ALLOW_PROMPT_ONLY")
    cloudinary_enabled: bool = Field(default=False, validation_alias="CLOUDINARY_ENABLED")
    cloudinary_cloud_name: str | None = Field(default=None, validation_alias="CLOUDINARY_CLOUD_NAME")
    cloudinary_api_key: str | None = Field(default=None, validation_alias="CLOUDINARY_API_KEY")
    cloudinary_api_secret: str | None = Field(default=None, validation_alias="CLOUDINARY_API_SECRET")
    cloudinary_folder: str = Field(default="madori/runs", validation_alias="CLOUDINARY_FOLDER")
    cloudinary_upload_drafts: bool = Field(default=True, validation_alias="CLOUDINARY_UPLOAD_DRAFTS")
    cloudinary_upload_finals: bool = Field(default=True, validation_alias="CLOUDINARY_UPLOAD_FINALS")
    cloudinary_secure_url: bool = Field(default=True, validation_alias="CLOUDINARY_SECURE_URL")
    cloudinary_upload_required: bool = Field(default=False, validation_alias="CLOUDINARY_UPLOAD_REQUIRED")
    gemini_api_key: str | None = Field(default=None, validation_alias="GEMINI_API_KEY")
    gemini_model: str = Field(default="gemini-2.5-flash", validation_alias="GEMINI_MODEL")
    use_gemini_analysis: bool = Field(default=False, validation_alias="USE_GEMINI_ANALYSIS")
    gemini_retry_attempts: int = Field(default=3, validation_alias="GEMINI_RETRY_ATTEMPTS")
    gemini_retry_delay_seconds: float = Field(default=2.0, validation_alias="GEMINI_RETRY_DELAY_SECONDS")

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
        settings.storage_dir = runtime_dir / "storage"
        settings.storage_runs_dir = settings.storage_dir / "runs"
        settings.storage_outputs_dir = settings.storage_dir / "outputs"

    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    settings.outputs_dir.mkdir(parents=True, exist_ok=True)
    settings.runs_dir.mkdir(parents=True, exist_ok=True)
    settings.storage_runs_dir.mkdir(parents=True, exist_ok=True)
    settings.storage_outputs_dir.mkdir(parents=True, exist_ok=True)
    settings.static_dir.mkdir(parents=True, exist_ok=True)
    return settings
