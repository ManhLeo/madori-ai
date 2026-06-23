from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException
from PIL import Image, UnidentifiedImageError

from app.schemas.run import (
    ImageInspectionMetadata,
    InputManifest,
    RunMetadata,
    StyleReferenceGroups,
    StyleReferenceInspectionGroups,
    UploadedFileMetadata,
)


class InputInspectionService:
    def __init__(self, storage_dir: Path, storage_runs_dir: Path) -> None:
        self.storage_dir = storage_dir
        self.storage_runs_dir = storage_runs_dir

    def inspect_run(self, metadata: RunMetadata) -> InputManifest:
        run_dir = self._safe_run_dir(metadata.run_id)
        artifacts_dir = self._artifacts_dir(metadata, run_dir)
        artifacts_dir.mkdir(parents=True, exist_ok=True)

        warnings: list[str] = []
        errors: list[str] = []
        floorplan = self._inspect_required_image(metadata.floorplan, warnings, errors)

        interior_photos = [
            inspected
            for inspected in (
                self._inspect_optional_image(item, warnings)
                for item in self._interior_photos(metadata)
            )
            if inspected is not None
        ]

        style_references = StyleReferenceInspectionGroups(
            ideal=self._inspect_optional_group(metadata.style_references.ideal, warnings),
            acceptable=self._inspect_optional_group(metadata.style_references.acceptable, warnings),
            ng=self._inspect_optional_group(metadata.style_references.ng, warnings),
        )

        checks = {
            "floorplan_present": floorplan is not None and floorplan.error is None,
            "interior_photos_present": len(interior_photos) > 0,
            "style_references_present": bool(
                style_references.ideal or style_references.acceptable or style_references.ng
            ),
            "has_errors": bool(errors),
        }

        manifest = InputManifest(
            run_id=metadata.run_id,
            generated_at=datetime.now(timezone.utc),
            floorplan=floorplan,
            interior_photos=interior_photos,
            style_references=style_references,
            checks=checks,
            warnings=warnings,
            errors=errors,
        )
        self._write_manifest(artifacts_dir / "input_manifest.json", manifest)
        return manifest

    def load_manifest(self, run_id: str) -> InputManifest:
        run_dir = self._safe_run_dir(run_id)
        manifest_path = run_dir / "artifacts" / "input_manifest.json"
        if not manifest_path.exists():
            raise HTTPException(status_code=404, detail="input manifest not found")
        try:
            return InputManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=500, detail="failed to read input manifest") from exc

    def _inspect_required_image(
        self,
        file_metadata: UploadedFileMetadata,
        warnings: list[str],
        errors: list[str],
    ) -> ImageInspectionMetadata:
        path = self._resolve_relative_path(file_metadata.relative_path)
        if not path.exists():
            errors.append(f"floorplan file is missing: {file_metadata.relative_path}")
            raise HTTPException(status_code=400, detail="floorplan file is missing")
        return self._inspect_image(path, file_metadata, warnings)

    def _inspect_optional_group(
        self,
        group: list[UploadedFileMetadata],
        warnings: list[str],
    ) -> list[ImageInspectionMetadata]:
        return [
            inspected
            for inspected in (self._inspect_optional_image(item, warnings) for item in group)
            if inspected is not None
        ]

    def _inspect_optional_image(
        self,
        file_metadata: UploadedFileMetadata,
        warnings: list[str],
    ) -> ImageInspectionMetadata | None:
        path = self._resolve_relative_path(file_metadata.relative_path)
        if not path.exists():
            warnings.append(f"optional image missing: {file_metadata.relative_path}")
            return None
        return self._inspect_image(path, file_metadata, warnings)

    def _inspect_image(
        self,
        path: Path,
        file_metadata: UploadedFileMetadata,
        warnings: list[str],
    ) -> ImageInspectionMetadata:
        try:
            with Image.open(path) as image:
                width, height = image.size
                image_format = image.format
                mode = image.mode
        except UnidentifiedImageError:
            warnings.append(f"image could not be identified: {file_metadata.relative_path}")
            return self._base_image_metadata(path, file_metadata, error="unidentified image")
        except OSError as exc:
            warnings.append(f"image could not be inspected: {file_metadata.relative_path}")
            return self._base_image_metadata(path, file_metadata, error=str(exc))

        aspect_ratio = round(width / height, 6) if height else None
        return ImageInspectionMetadata(
            width=width,
            height=height,
            format=image_format,
            mode=mode,
            aspect_ratio=aspect_ratio,
            size_bytes=path.stat().st_size,
            relative_path=file_metadata.relative_path,
            preview_url=file_metadata.preview_url,
            original_filename=file_metadata.original_filename,
            stored_filename=file_metadata.stored_filename,
            mime_type=file_metadata.mime_type,
        )

    @staticmethod
    def _base_image_metadata(
        path: Path,
        file_metadata: UploadedFileMetadata,
        *,
        error: str,
    ) -> ImageInspectionMetadata:
        size_bytes = path.stat().st_size if path.exists() else file_metadata.size_bytes
        return ImageInspectionMetadata(
            size_bytes=size_bytes,
            relative_path=file_metadata.relative_path,
            preview_url=file_metadata.preview_url,
            original_filename=file_metadata.original_filename,
            stored_filename=file_metadata.stored_filename,
            mime_type=file_metadata.mime_type,
            error=error,
        )

    def _resolve_relative_path(self, relative_path: str) -> Path:
        normalized = relative_path.replace("\\", "/").lstrip("/")
        path = (self.storage_dir.parent / normalized).resolve()
        try:
            path.relative_to(self.storage_dir.resolve())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="unsafe stored file path") from exc
        return path

    def _safe_run_dir(self, run_id: str) -> Path:
        run_dir = (self.storage_runs_dir / run_id).resolve()
        try:
            run_dir.relative_to(self.storage_runs_dir.resolve())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid run path") from exc
        if not run_dir.exists():
            raise HTTPException(status_code=404, detail="run not found")
        return run_dir

    def _artifacts_dir(self, metadata: RunMetadata, run_dir: Path) -> Path:
        if metadata.workspace and metadata.workspace.artifacts_dir:
            return self._resolve_relative_path(metadata.workspace.artifacts_dir)
        return run_dir / "artifacts"

    @staticmethod
    def _interior_photos(metadata: RunMetadata) -> list[UploadedFileMetadata]:
        if metadata.inputs and metadata.inputs.interior_photos:
            return metadata.inputs.interior_photos
        return metadata.interior_photos

    @staticmethod
    def _write_manifest(path: Path, manifest: InputManifest) -> None:
        try:
            with path.open("w", encoding="utf-8") as manifest_file:
                json.dump(manifest.model_dump(mode="json"), manifest_file, ensure_ascii=False, indent=2)
        except OSError as exc:
            raise HTTPException(status_code=500, detail="failed to write input manifest") from exc
