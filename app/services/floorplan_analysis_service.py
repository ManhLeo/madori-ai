from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException

from app.schemas import FloorplanAnalysis
from app.schemas.run import (
    FloorplanPreprocessReport,
    FloorplanSemanticAnalysisArtifact,
    RunMetadata,
    SemanticSourceImage,
)
from app.services.vision_analyzer import OpenAIJSONParseError, VisionAnalyzer


class FloorplanAnalysisService:
    def __init__(self, storage_dir: Path, storage_runs_dir: Path) -> None:
        self.storage_dir = storage_dir
        self.storage_runs_dir = storage_runs_dir
        self.vision_analyzer = VisionAnalyzer()

    def analyze_run(self, metadata: RunMetadata) -> FloorplanSemanticAnalysisArtifact:
        run_dir = self._safe_run_dir(metadata.run_id)
        preprocess_report = self._load_preprocess_report(run_dir)
        normalized_artifact = preprocess_report.artifacts.get("normalized_floorplan")
        if normalized_artifact is None:
            raise HTTPException(status_code=400, detail="normalized_floorplan artifact is missing")

        normalized_path = self._resolve_relative_path(normalized_artifact.relative_path)
        if not normalized_path.exists():
            raise HTTPException(status_code=400, detail="normalized_floorplan image is missing")

        artifacts_dir = self._artifacts_dir(metadata, run_dir)
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        try:
            analysis, raw_payload = self.vision_analyzer.analyze_floorplan_semantic_with_raw(
                normalized_path,
                run_id=metadata.run_id,
                artifacts_dir=artifacts_dir,
            )
        except OpenAIJSONParseError as exc:
            raw_payload = exc.raw_payload or {
                "run_id": metadata.run_id,
                "provider": "openai",
                "model": None,
                "mode": "semantic_only",
                "analysis_type": "floorplan_semantic",
                "attempts": getattr(exc, "attempts", []),
                "warnings": [],
                "errors": [
                    {
                        "error": "openai_invalid_json",
                        "message": str(exc),
                        "details": {
                            "attempts": len(getattr(exc, "attempts", [])),
                            "likely_truncated": getattr(exc, "likely_truncated", False),
                        },
                    }
                ],
            }
            raw_payload["run_id"] = metadata.run_id
            self._write_json(artifacts_dir / "floorplan_analysis_raw.json", raw_payload)
            raise HTTPException(
                status_code=502,
                detail={
                    "error": "openai_invalid_json",
                    "message": "OpenAI floorplan semantic analysis returned invalid JSON after retry.",
                    "details": {
                        "attempts": len(raw_payload.get("attempts") or []),
                        "likely_truncated": any(
                            bool(item.get("likely_truncated")) for item in (raw_payload.get("attempts") or [])
                        ),
                    },
                },
            ) from exc
        provider = str(raw_payload.get("provider") or "unknown")
        model = raw_payload.get("model")
        warnings: list[str] = []
        errors: list[str] = []
        raw_payload = dict(raw_payload)
        raw_payload["run_id"] = metadata.run_id

        artifact = FloorplanSemanticAnalysisArtifact(
            run_id=metadata.run_id,
            generated_at=datetime.now(timezone.utc),
            provider=provider,
            model=str(model) if model else None,
            source_image=SemanticSourceImage(
                relative_path=normalized_artifact.relative_path,
                preview_url=normalized_artifact.preview_url,
                width=normalized_artifact.width,
                height=normalized_artifact.height,
                format=normalized_artifact.format,
                mode=normalized_artifact.mode,
            ),
            analysis=analysis.model_dump(mode="json"),
            warnings=warnings,
            errors=errors,
        )

        self._write_json(artifacts_dir / "floorplan_analysis.json", artifact.model_dump(mode="json"))
        self._write_json(artifacts_dir / "floorplan_analysis_raw.json", raw_payload)
        return artifact

    def load_analysis_artifact(self, run_id: str) -> FloorplanSemanticAnalysisArtifact:
        run_dir = self._safe_run_dir(run_id)
        artifact_path = run_dir / "artifacts" / "floorplan_analysis.json"
        if not artifact_path.exists():
            raise HTTPException(status_code=404, detail="floorplan analysis artifact not found")
        try:
            return FloorplanSemanticAnalysisArtifact.model_validate_json(artifact_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=500, detail="failed to read floorplan analysis artifact") from exc

    @staticmethod
    def load_normalized_analysis(run_id: str, storage_runs_dir: Path) -> FloorplanAnalysis:
        analysis_path = storage_runs_dir / run_id / "artifacts" / "floorplan_analysis.json"
        if not analysis_path.exists():
            raise HTTPException(status_code=404, detail="floorplan analysis artifact not found")
        try:
            payload = json.loads(analysis_path.read_text(encoding="utf-8"))
            return FloorplanAnalysis.model_validate(payload["analysis"])
        except (OSError, KeyError, ValueError) as exc:
            raise HTTPException(status_code=500, detail="failed to parse normalized floorplan analysis") from exc

    def _load_preprocess_report(self, run_dir: Path) -> FloorplanPreprocessReport:
        report_path = run_dir / "artifacts" / "floorplan_preprocess.json"
        if not report_path.exists():
            raise HTTPException(status_code=400, detail="floorplan preprocess report not found; run preprocessing first")
        try:
            return FloorplanPreprocessReport.model_validate_json(report_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=500, detail="failed to read floorplan preprocess report") from exc

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
    def _write_json(path: Path, payload: dict) -> None:
        try:
            with path.open("w", encoding="utf-8") as output_file:
                json.dump(payload, output_file, ensure_ascii=False, indent=2)
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"failed to write {path.name}") from exc
