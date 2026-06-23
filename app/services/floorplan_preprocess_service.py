from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException
from PIL import Image, ImageFilter, ImageOps, UnidentifiedImageError

from app.schemas.run import (
    FloorplanPreprocessReport,
    ImageInspectionMetadata,
    InputManifest,
    PreprocessImageArtifact,
    RunMetadata,
)


class FloorplanPreprocessService:
    NORMALIZED_WIDTH = 1200
    NORMALIZED_HEIGHT = 1200
    BINARY_THRESHOLD = 210
    EDGE_THRESHOLD = 34

    def __init__(self, storage_dir: Path, storage_runs_dir: Path) -> None:
        self.storage_dir = storage_dir
        self.storage_runs_dir = storage_runs_dir

    def preprocess_floorplan(self, metadata: RunMetadata) -> FloorplanPreprocessReport:
        run_dir = self._safe_run_dir(metadata.run_id)
        manifest = self._load_input_manifest(run_dir)
        if manifest.floorplan is None:
            raise HTTPException(status_code=400, detail="input manifest does not contain a floorplan")

        floorplan_path = self._resolve_relative_path(manifest.floorplan.relative_path)
        if not floorplan_path.exists():
            raise HTTPException(status_code=400, detail="floorplan file is missing")

        artifacts_dir = self._artifacts_dir(metadata, run_dir)
        artifacts_dir.mkdir(parents=True, exist_ok=True)

        try:
            with Image.open(floorplan_path) as source_image:
                source_rgb = ImageOps.exif_transpose(source_image).convert("RGB")
        except UnidentifiedImageError as exc:
            raise HTTPException(status_code=400, detail="floorplan image cannot be identified") from exc
        except OSError as exc:
            raise HTTPException(status_code=400, detail="floorplan image cannot be opened") from exc

        normalized, normalization = self._normalize_contain(source_rgb)
        grayscale = ImageOps.grayscale(normalized)
        binary_mask = grayscale.point(lambda value: 0 if value < self.BINARY_THRESHOLD else 255, mode="L")
        edges = grayscale.filter(ImageFilter.FIND_EDGES)
        edge_mask = edges.point(lambda value: 255 if value > self.EDGE_THRESHOLD else 0, mode="L")
        line_preview = ImageOps.invert(edge_mask).convert("RGB")

        artifact_images = {
            "normalized_floorplan": normalized,
            "grayscale": grayscale,
            "binary_mask": binary_mask,
            "edges": edge_mask,
            "line_preview": line_preview,
        }
        artifacts = {
            name: self._save_artifact_image(artifacts_dir / f"{name}.png", image)
            for name, image in artifact_images.items()
        }

        report = FloorplanPreprocessReport(
            run_id=metadata.run_id,
            generated_at=datetime.now(timezone.utc),
            source_floorplan=manifest.floorplan,
            output_size={"width": self.NORMALIZED_WIDTH, "height": self.NORMALIZED_HEIGHT},
            normalization=normalization,
            artifacts=artifacts,
            checks={
                "input_manifest_present": True,
                "floorplan_present": True,
                "normalized_size_ok": normalized.size == (self.NORMALIZED_WIDTH, self.NORMALIZED_HEIGHT),
                "artifacts_written": all((artifacts_dir / f"{name}.png").exists() for name in artifact_images),
            },
            warnings=[],
            errors=[],
        )
        self._write_report(artifacts_dir / "floorplan_preprocess.json", report)
        return report

    def load_report(self, run_id: str) -> FloorplanPreprocessReport:
        run_dir = self._safe_run_dir(run_id)
        report_path = run_dir / "artifacts" / "floorplan_preprocess.json"
        if not report_path.exists():
            raise HTTPException(status_code=404, detail="floorplan preprocess report not found")
        try:
            return FloorplanPreprocessReport.model_validate_json(report_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=500, detail="failed to read floorplan preprocess report") from exc

    def _load_input_manifest(self, run_dir: Path) -> InputManifest:
        manifest_path = run_dir / "artifacts" / "input_manifest.json"
        if not manifest_path.exists():
            raise HTTPException(status_code=400, detail="input manifest not found; run inspection first")
        try:
            return InputManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=500, detail="failed to read input manifest") from exc

    def _normalize_contain(self, image: Image.Image) -> tuple[Image.Image, dict[str, int | float | str]]:
        source_width, source_height = image.size
        scale = min(self.NORMALIZED_WIDTH / source_width, self.NORMALIZED_HEIGHT / source_height)
        resized_width = max(1, int(round(source_width * scale)))
        resized_height = max(1, int(round(source_height * scale)))
        resized = image.resize((resized_width, resized_height), Image.Resampling.LANCZOS)
        canvas = Image.new("RGB", (self.NORMALIZED_WIDTH, self.NORMALIZED_HEIGHT), "white")
        offset_x = (self.NORMALIZED_WIDTH - resized_width) // 2
        offset_y = (self.NORMALIZED_HEIGHT - resized_height) // 2
        canvas.paste(resized, (offset_x, offset_y))
        return canvas, {
            "mode": "contain",
            "background": "white",
            "source_width": source_width,
            "source_height": source_height,
            "scale": round(scale, 6),
            "resized_width": resized_width,
            "resized_height": resized_height,
            "offset_x": offset_x,
            "offset_y": offset_y,
        }

    def _save_artifact_image(self, path: Path, image: Image.Image) -> PreprocessImageArtifact:
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(path, format="PNG")
        relative_path = self._relative_storage_path(path)
        return PreprocessImageArtifact(
            relative_path=relative_path,
            preview_url=f"/{relative_path}",
            width=image.width,
            height=image.height,
            mode=image.mode,
            size_bytes=path.stat().st_size,
        )

    def _resolve_relative_path(self, relative_path: str) -> Path:
        normalized = relative_path.replace("\\", "/").lstrip("/")
        path = (self.storage_dir.parent / normalized).resolve()
        try:
            path.relative_to(self.storage_dir.resolve())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="unsafe stored file path") from exc
        return path

    def _relative_storage_path(self, path: Path) -> str:
        return path.relative_to(self.storage_dir.parent).as_posix()

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
    def _write_report(path: Path, report: FloorplanPreprocessReport) -> None:
        try:
            with path.open("w", encoding="utf-8") as report_file:
                json.dump(report.model_dump(mode="json"), report_file, ensure_ascii=False, indent=2)
        except OSError as exc:
            raise HTTPException(status_code=500, detail="failed to write floorplan preprocess report") from exc
