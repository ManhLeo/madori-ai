from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException

from app.schemas.run import (
    ImageGenerationDraftArtifact,
    RunMetadata,
    VisualQAReportResponse,
    VisualQARequest,
    VisualQASummary,
)


class VisualQAService:
    VALID_QA_STATUSES = {"passed", "needs_fix", "failed"}
    VALID_CHECK_STATUSES = {"pass", "needs_review", "fail"}
    VALID_SEVERITIES = {"low", "medium", "high"}

    def __init__(self, storage_dir: Path, storage_runs_dir: Path) -> None:
        self.storage_dir = storage_dir
        self.storage_runs_dir = storage_runs_dir

    def create_visual_qa_report(self, metadata: RunMetadata, request: VisualQARequest) -> VisualQAReportResponse:
        self._validate_request(request)
        warnings: list[str] = []
        errors: list[str] = []

        draft_artifact = self._load_optional_draft_artifact(metadata.run_id)
        source_draft = self._build_source_draft(metadata.run_id, draft_artifact, warnings)

        artifact = VisualQAReportResponse(
            run_id=metadata.run_id,
            generated_at=datetime.now(timezone.utc),
            qa_status=request.qa_status,
            source_draft=source_draft,
            checks={
                "layout_preserved": request.layout_preserved,
                "english_labels_correct": request.english_labels_correct,
                "room_roles_correct": request.room_roles_correct,
                "furniture_arrangement_correct": request.furniture_arrangement_correct,
                "bedroom_bed_count_correct": request.bedroom_bed_count_correct,
                "dining_location_correct": request.dining_location_correct,
                "sofa_tv_arrangement_correct": request.sofa_tv_arrangement_correct,
            },
            final_usable_for_demo=request.final_usable_for_demo,
            notes=request.notes,
            issues=request.issues,
            warnings=warnings,
            errors=errors,
        )
        self.write_visual_qa_report(metadata.run_id, artifact)
        return artifact

    def build_metadata_updates(self, metadata: RunMetadata, artifact: VisualQAReportResponse) -> dict:
        now = datetime.now(timezone.utc)
        qa_status_to_run_status = {
            "passed": "visual_qa_passed",
            "needs_fix": "visual_qa_needs_fix",
            "failed": "visual_qa_failed",
        }
        return {
            "status": qa_status_to_run_status[artifact.qa_status],
            "run_status": qa_status_to_run_status[artifact.qa_status],
            "updated_at": now,
            "processing": metadata.processing.model_copy(update={"visual_qa": True}),
            "pipeline": {
                "current_phase": "phase_7a1_visual_qa",
                "next_phase": "phase_7b_finalize_output",
            },
            "visual_qa_report_path": self._relative_artifact_path(metadata.run_id, "visual_qa_report.json"),
            "visual_qa_summary": VisualQASummary(
                qa_status=artifact.qa_status,
                final_usable_for_demo=artifact.final_usable_for_demo,
                issues_count=len(artifact.issues),
                warnings_count=len(artifact.warnings),
                errors_count=len(artifact.errors),
            ),
        }

    def write_visual_qa_report(self, run_id: str, artifact: VisualQAReportResponse) -> None:
        path = self._artifacts_dir(run_id) / "visual_qa_report.json"
        try:
            with path.open("w", encoding="utf-8") as output_file:
                json.dump(artifact.model_dump(mode="json"), output_file, ensure_ascii=False, indent=2)
        except OSError as exc:
            raise HTTPException(status_code=500, detail="failed to write visual_qa_report artifact") from exc

    def load_visual_qa_report(self, run_id: str) -> VisualQAReportResponse:
        path = self._artifacts_dir(run_id) / "visual_qa_report.json"
        if not path.exists():
            raise HTTPException(status_code=404, detail="visual_qa_report artifact not found")
        try:
            return VisualQAReportResponse.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=500, detail="failed to read visual_qa_report artifact") from exc

    def _validate_request(self, request: VisualQARequest) -> None:
        if request.qa_status not in self.VALID_QA_STATUSES:
            raise HTTPException(status_code=400, detail="qa_status must be one of: passed, needs_fix, failed")

        checks = {
            "layout_preserved": request.layout_preserved,
            "english_labels_correct": request.english_labels_correct,
            "room_roles_correct": request.room_roles_correct,
            "furniture_arrangement_correct": request.furniture_arrangement_correct,
            "bedroom_bed_count_correct": request.bedroom_bed_count_correct,
            "dining_location_correct": request.dining_location_correct,
            "sofa_tv_arrangement_correct": request.sofa_tv_arrangement_correct,
        }
        invalid_checks = [name for name, value in checks.items() if value not in self.VALID_CHECK_STATUSES]
        if invalid_checks:
            raise HTTPException(
                status_code=400,
                detail=f"invalid visual QA check value for: {', '.join(invalid_checks)}",
            )

        invalid_severities = [issue.severity for issue in request.issues if issue.severity not in self.VALID_SEVERITIES]
        if invalid_severities:
            raise HTTPException(status_code=400, detail="issue severity must be one of: low, medium, high")

    def _build_source_draft(
        self,
        run_id: str,
        draft_artifact: ImageGenerationDraftArtifact | None,
        warnings: list[str],
    ) -> dict:
        if draft_artifact is None:
            warnings.append("No image_generation_draft artifact found.")
            return {
                "draft_artifact_path": self._relative_artifact_path(run_id, "image_generation_draft.json"),
                "draft_image_path": None,
                "draft_image_preview_url": None,
                "public_output_url": None,
                "cloudinary_url": None,
            }

        outputs = draft_artifact.outputs if isinstance(draft_artifact.outputs, dict) else {}
        return {
            "draft_artifact_path": self._relative_artifact_path(run_id, "image_generation_draft.json"),
            "draft_image_path": outputs.get("draft_image_path") or outputs.get("output_image_path"),
            "draft_image_preview_url": outputs.get("draft_image_preview_url") or draft_artifact.preview_url,
            "public_output_url": draft_artifact.public_output_url,
            "cloudinary_url": draft_artifact.cloudinary_url,
        }

    def _load_optional_draft_artifact(self, run_id: str) -> ImageGenerationDraftArtifact | None:
        path = self._artifacts_dir(run_id) / "image_generation_draft.json"
        if not path.exists():
            return None
        try:
            return ImageGenerationDraftArtifact.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=500, detail="failed to read image_generation_draft artifact") from exc

    def _artifacts_dir(self, run_id: str) -> Path:
        return self._safe_run_dir(run_id) / "artifacts"

    def _safe_run_dir(self, run_id: str) -> Path:
        run_dir = (self.storage_runs_dir / run_id).resolve()
        try:
            run_dir.relative_to(self.storage_runs_dir.resolve())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid run path") from exc
        if not run_dir.exists():
            raise HTTPException(status_code=404, detail="run not found")
        return run_dir

    @staticmethod
    def _relative_artifact_path(run_id: str, filename: str) -> str:
        return f"storage/runs/{run_id}/artifacts/{filename}"
