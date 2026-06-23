from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException

from app.schemas.run import (
    ImageInspectionMetadata,
    InputManifest,
    InteriorAnalysisSummary,
    InteriorStyleAnalysisArtifact,
    RunMetadata,
)
from app.services.vision_analyzer import VisionAnalyzer


class InteriorAnalysisService:
    def __init__(self, storage_dir: Path, storage_runs_dir: Path) -> None:
        self.storage_dir = storage_dir
        self.storage_runs_dir = storage_runs_dir
        self.vision_analyzer = VisionAnalyzer()

    def analyze_run(self, metadata: RunMetadata) -> InteriorStyleAnalysisArtifact:
        run_dir = self._safe_run_dir(metadata.run_id)
        manifest = self._load_input_manifest(run_dir)

        warnings: list[str] = []
        errors: list[str] = []
        interior_images = self._resolve_manifest_images(manifest.interior_photos, "interior photo", warnings)
        style_images = {
            "ideal": self._resolve_manifest_images(manifest.style_references.ideal, "ideal style reference", warnings),
            "acceptable": self._resolve_manifest_images(manifest.style_references.acceptable, "acceptable style reference", warnings),
            "ng": self._resolve_manifest_images(manifest.style_references.ng, "ng style reference", warnings),
        }
        if not interior_images:
            warnings.append("No interior photos are available for semantic analysis.")
        if not any(style_images.values()):
            warnings.append("No style reference images are available for semantic analysis.")

        artifact, raw_payload = self.vision_analyzer.analyze_interior_style_semantic_with_raw(interior_images, style_images)
        generated_at = datetime.now(timezone.utc)
        summary = artifact.summary.model_copy(
            update={
                "provider": artifact.provider,
                "model": artifact.model,
            }
        )
        artifact = artifact.model_copy(
            update={
                "run_id": metadata.run_id,
                "generated_at": generated_at,
                "summary": summary,
                "warnings": [*artifact.warnings, *warnings],
                "errors": [*artifact.errors, *errors],
            }
        )

        artifacts_dir = self._artifacts_dir(metadata, run_dir)
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        self._write_json(artifacts_dir / "interior_analysis.json", artifact.model_dump(mode="json"))
        self._write_json(
            artifacts_dir / "interior_analysis_raw.json",
            {
                **raw_payload,
                "run_id": metadata.run_id,
                "generated_at": generated_at.isoformat().replace("+00:00", "Z"),
                "warnings": warnings,
                "errors": errors,
            },
        )
        return artifact

    def load_artifact(self, run_id: str) -> InteriorStyleAnalysisArtifact:
        run_dir = self._safe_run_dir(run_id)
        artifact_path = run_dir / "artifacts" / "interior_analysis.json"
        if not artifact_path.exists():
            raise HTTPException(status_code=404, detail="interior analysis artifact not found")
        try:
            return InteriorStyleAnalysisArtifact.model_validate_json(artifact_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=500, detail="failed to read interior analysis artifact") from exc

    @staticmethod
    def build_summary(artifact: InteriorStyleAnalysisArtifact) -> InteriorAnalysisSummary:
        return artifact.summary.model_copy(
            update={
                "provider": artifact.provider,
                "model": artifact.model,
            }
        )

    def _resolve_manifest_images(
        self,
        items: list[ImageInspectionMetadata],
        label: str,
        warnings: list[str],
    ) -> list[tuple[Path, str, dict]]:
        resolved: list[tuple[Path, str, dict]] = []
        for item in items:
            path = self._resolve_relative_path(item.relative_path)
            if not path.exists():
                warnings.append(f"missing {label}: {item.relative_path}")
                continue
            resolved.append((path, item.mime_type or self._mime_type_for_suffix(path), item.model_dump(mode="json")))
        return resolved

    def _load_input_manifest(self, run_dir: Path) -> InputManifest:
        manifest_path = run_dir / "artifacts" / "input_manifest.json"
        if not manifest_path.exists():
            raise HTTPException(status_code=400, detail="input manifest not found; run inspection first")
        try:
            return InputManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=500, detail="failed to read input manifest") from exc

    def _safe_run_dir(self, run_id: str) -> Path:
        run_dir = (self.storage_runs_dir / run_id).resolve()
        try:
            run_dir.relative_to(self.storage_runs_dir.resolve())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid run path") from exc
        if not run_dir.exists():
            raise HTTPException(status_code=404, detail="run not found")
        return run_dir

    def _resolve_relative_path(self, relative_path: str) -> Path:
        normalized = relative_path.replace("\\", "/").lstrip("/")
        path = (self.storage_dir.parent / normalized).resolve()
        try:
            path.relative_to(self.storage_dir.resolve())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="unsafe stored file path") from exc
        return path

    def _artifacts_dir(self, metadata: RunMetadata, run_dir: Path) -> Path:
        if metadata.workspace and metadata.workspace.artifacts_dir:
            return self._resolve_relative_path(metadata.workspace.artifacts_dir)
        return run_dir / "artifacts"

    @staticmethod
    def _mime_type_for_suffix(path: Path) -> str:
        return {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
        }.get(path.suffix.lower(), "application/octet-stream")

    @staticmethod
    def _write_json(path: Path, payload: dict) -> None:
        try:
            with path.open("w", encoding="utf-8") as output_file:
                json.dump(payload, output_file, ensure_ascii=False, indent=2)
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"failed to write {path.name}") from exc
