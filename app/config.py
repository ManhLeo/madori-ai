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
    openai_api_key: str | None = Field(default=None, validation_alias="OPENAI_API_KEY")
    openai_vision_model: str = Field(default="gpt-4o-mini", validation_alias="OPENAI_VISION_MODEL")
    openai_image_model: str = Field(default="gpt-image-1", validation_alias="OPENAI_IMAGE_MODEL")
    fal_api_key: str | None = Field(default=None, validation_alias="FAL_API_KEY")
    flux_model: str = Field(default="fal-ai/flux-pro/kontext", validation_alias="FLUX_MODEL")
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
    use_openai_analysis: bool = Field(default=False, validation_alias="USE_OPENAI_ANALYSIS")
    openrouter_api_key: str | None = Field(default=None, validation_alias="OPENROUTER_API_KEY")
    openrouter_vision_model: str = Field(
        default="nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
        validation_alias="OPENROUTER_VISION_MODEL",
    )
    openrouter_vision_models: str | None = Field(default=None, validation_alias="OPENROUTER_VISION_MODELS")
    use_openrouter_analysis: bool = Field(default=False, validation_alias="USE_OPENROUTER_ANALYSIS")
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
