from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import HTTPException
from cloudinary import config as cloudinary_config
from cloudinary import uploader
from PIL import Image

from app.config import get_settings


FluxImageFormat = Literal["original", "jpg", "png"]


def convert_to_flux_safe_jpeg(image_path: Path, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with Image.open(image_path) as image:
            image.convert("RGB").save(output_path, format="JPEG", quality=95)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"failed to convert floorplan to JPEG: {exc}") from exc
    return output_path


def convert_to_flux_safe_png(image_path: Path, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with Image.open(image_path) as image:
            image.convert("RGB").save(output_path, format="PNG")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"failed to convert floorplan to PNG: {exc}") from exc
    return output_path


def upload_floorplan_to_cloudinary(
    image_path: Path,
    run_id: str,
    format_for_flux: FluxImageFormat = "original",
) -> str:
    settings = get_settings()
    if not settings.cloudinary_cloud_name or not settings.cloudinary_api_key or not settings.cloudinary_api_secret:
        raise HTTPException(
            status_code=500,
            detail=(
                "Cloudinary is not configured. Set CLOUDINARY_CLOUD_NAME, "
                "CLOUDINARY_API_KEY, and CLOUDINARY_API_SECRET."
            ),
        )

    if not image_path.exists():
        raise HTTPException(status_code=404, detail="floorplan image not found")

    upload_path = image_path
    public_id = f"{run_id}_floorplan"
    if format_for_flux == "jpg":
        upload_path = convert_to_flux_safe_jpeg(image_path, image_path.with_name(f"{image_path.stem}_fluxsafe.jpg"))
        public_id = f"{run_id}_floorplan_jpg"
    elif format_for_flux == "png":
        upload_path = convert_to_flux_safe_png(image_path, image_path.with_name(f"{image_path.stem}_fluxsafe.png"))
        public_id = f"{run_id}_floorplan_png"
    elif format_for_flux != "original":
        raise HTTPException(
            status_code=500,
            detail=f"Unsupported FLUXAPI_INPUT_IMAGE_FORMAT: {format_for_flux}. Expected original, jpg, or png.",
        )

    cloudinary_config(
        cloud_name=settings.cloudinary_cloud_name,
        api_key=settings.cloudinary_api_key,
        api_secret=settings.cloudinary_api_secret,
        secure=True,
    )

    try:
        response = uploader.upload(
            str(upload_path),
            folder="madori/floorplans",
            public_id=public_id,
            resource_type="image",
            overwrite=True,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"failed to upload floorplan to Cloudinary: {exc}") from exc

    secure_url = response.get("secure_url") if isinstance(response, dict) else None
    if not secure_url:
        raise HTTPException(status_code=502, detail="Cloudinary upload did not return secure_url")

    return secure_url


def upload_output_to_cloudinary(image_path: Path, run_id: str) -> str:
    settings = get_settings()
    if not settings.cloudinary_cloud_name or not settings.cloudinary_api_key or not settings.cloudinary_api_secret:
        raise HTTPException(
            status_code=500,
            detail=(
                "Cloudinary is not configured. Set CLOUDINARY_CLOUD_NAME, "
                "CLOUDINARY_API_KEY, and CLOUDINARY_API_SECRET."
            ),
        )

    if not image_path.exists():
        raise HTTPException(status_code=404, detail="generated output image not found")

    cloudinary_config(
        cloud_name=settings.cloudinary_cloud_name,
        api_key=settings.cloudinary_api_key,
        api_secret=settings.cloudinary_api_secret,
        secure=True,
    )

    try:
        response = uploader.upload(
            str(image_path),
            folder="madori/outputs",
            public_id=f"{run_id}_output",
            resource_type="image",
            overwrite=True,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"failed to upload output to Cloudinary: {exc}") from exc

    secure_url = response.get("secure_url") if isinstance(response, dict) else None
    if not secure_url:
        raise HTTPException(status_code=502, detail="Cloudinary output upload did not return secure_url")

    return secure_url
