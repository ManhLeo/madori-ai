from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException

from app.config import get_settings


class CloudinaryStorageService:
    SUPPORTED_ASSET_KINDS = {"draft", "raw_draft", "final"}

    def __init__(self) -> None:
        self.settings = get_settings()

    def is_cloudinary_enabled(self) -> bool:
        return bool(self.settings.cloudinary_enabled)

    def upload_run_image(
        self,
        run_id: str,
        local_path: Path,
        asset_kind: str,
        public_id_suffix: str | None = None,
    ) -> dict:
        normalized_kind = str(asset_kind or "").strip().lower()
        if not self.is_cloudinary_enabled():
            return {
                "enabled": False,
                "uploaded": False,
                "reason": "cloudinary_disabled",
                "asset_kind": normalized_kind or asset_kind,
            }
        if normalized_kind not in self.SUPPORTED_ASSET_KINDS:
            raise HTTPException(status_code=400, detail=f"unsupported Cloudinary asset_kind: {asset_kind}")
        self._require_configuration()

        if not local_path.exists():
            raise HTTPException(status_code=404, detail=f"local output image not found for Cloudinary upload: {local_path.name}")

        from cloudinary import config as cloudinary_config
        from cloudinary import uploader

        cloudinary_config(
            cloud_name=self.settings.cloudinary_cloud_name,
            api_key=self.settings.cloudinary_api_key,
            api_secret=self.settings.cloudinary_api_secret,
            secure=bool(self.settings.cloudinary_secure_url),
        )

        folder = self._build_folder(run_id)
        public_id = self._build_public_id(run_id, normalized_kind, public_id_suffix)
        try:
            response = uploader.upload(
                str(local_path),
                folder=folder,
                public_id=public_id,
                resource_type="image",
                overwrite=True,
                use_filename=False,
                unique_filename=False,
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Cloudinary upload failed: {exc}") from exc

        secure_url = response.get("secure_url") if isinstance(response, dict) else None
        url = response.get("url") if isinstance(response, dict) else None
        return {
            "enabled": True,
            "uploaded": True,
            "provider": "cloudinary",
            "asset_kind": normalized_kind,
            "cloud_name": self.settings.cloudinary_cloud_name,
            "public_id": response.get("public_id"),
            "asset_id": response.get("asset_id"),
            "version": response.get("version"),
            "format": response.get("format"),
            "resource_type": response.get("resource_type"),
            "bytes": response.get("bytes"),
            "width": response.get("width"),
            "height": response.get("height"),
            "secure_url": secure_url,
            "url": url,
            "folder": response.get("folder") or folder,
            "created_at": response.get("created_at") or datetime.now(timezone.utc).isoformat(),
            "original_filename": local_path.name,
        }

    def _require_configuration(self) -> None:
        missing = [
            name
            for name, value in (
                ("CLOUDINARY_CLOUD_NAME", self.settings.cloudinary_cloud_name),
                ("CLOUDINARY_API_KEY", self.settings.cloudinary_api_key),
                ("CLOUDINARY_API_SECRET", self.settings.cloudinary_api_secret),
            )
            if not str(value or "").strip()
        ]
        if missing:
            raise HTTPException(
                status_code=500,
                detail=f"Cloudinary upload is enabled but required configuration is missing: {', '.join(missing)}",
            )

    def _build_folder(self, run_id: str) -> str:
        base_folder = str(self.settings.cloudinary_folder or "madori/runs").strip().strip("/")
        return f"{base_folder}/{run_id}"

    @staticmethod
    def _build_public_id(run_id: str, asset_kind: str, public_id_suffix: str | None) -> str:
        suffix = str(public_id_suffix or "").strip().strip("_")
        if suffix:
            return f"{run_id}_{asset_kind}_{suffix}"
        return f"{run_id}_{asset_kind}"
