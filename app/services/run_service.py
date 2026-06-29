from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException, UploadFile

from app.schemas.run import (
    AnalysisSummary,
    FinalOutputSummary,
    FurniturePlacementSummary,
    FurniturePlacementValidationSummary,
    ImageGenerationDraftSummary,
    RegenerationSummary,
    InteriorAnalysisSummary,
    InteriorValidationSummary,
    LayoutSummary,
    LayoutValidationSummary,
    ImageGenerationRequestPreviewSummary,
    OutputRequirements,
    PipelineSummary,
    PromptPackageSummary,
    QAFeedbackSummary,
    ProcessingFlags,
    RoomFunctionAssignmentSummary,
    RenderPlanSummary,
    RunCreateResponse,
    RunInputs,
    RunMetadata,
    RunMetadataSummary,
    StructureLockedCompositeSummary,
    StyleReferenceGroups,
    UploadedFileMetadata,
    VisualQASummary,
    WorkspacePaths,
)
from app.services.file_service import FileService, SavedUpload


RUN_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")


class RunService:
    MAX_IMAGE_BYTES = 20 * 1024 * 1024
    ALLOWED_IMAGE_MIME_TYPES = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
    }

    def __init__(self, storage_runs_dir: Path) -> None:
        self.storage_runs_dir = storage_runs_dir
        self.file_service = FileService(storage_runs_dir, storage_runs_dir, storage_runs_dir)

    def create_run(
        self,
        floorplan: UploadFile,
        interior_photos: list[UploadFile] | None = None,
        style_reference_images: list[UploadFile] | None = None,
        ideal_style_reference: UploadFile | None = None,
        acceptable_style_reference: UploadFile | None = None,
        ng_style_reference: UploadFile | None = None,
    ) -> RunMetadata:
        if not floorplan:
            raise HTTPException(status_code=400, detail="floorplan file is required")
        self._require_upload_filename(floorplan, "floorplan")

        run_id = self.file_service.create_run_id()
        run_dir = self._run_dir(run_id)
        source_dir = run_dir / "source"
        floorplan_dir = source_dir / "floorplan"
        interior_dir = source_dir / "interior_photos"
        style_reference_dir = source_dir / "style_references"
        ideal_style_reference_dir = style_reference_dir / "ideal"
        acceptable_style_reference_dir = style_reference_dir / "acceptable"
        ng_style_reference_dir = style_reference_dir / "ng"
        artifacts_dir = run_dir / "artifacts"
        outputs_dir = run_dir / "outputs"
        run_dir.mkdir(parents=True, exist_ok=False)
        source_dir.mkdir(parents=True, exist_ok=True)
        floorplan_dir.mkdir(parents=True, exist_ok=True)
        interior_dir.mkdir(parents=True, exist_ok=True)
        style_reference_dir.mkdir(parents=True, exist_ok=True)
        ideal_style_reference_dir.mkdir(parents=True, exist_ok=True)
        acceptable_style_reference_dir.mkdir(parents=True, exist_ok=True)
        ng_style_reference_dir.mkdir(parents=True, exist_ok=True)
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        outputs_dir.mkdir(parents=True, exist_ok=True)

        floorplan_ext = self._extension_for_upload(floorplan)
        floorplan_saved = self.file_service.save_upload_file(
            floorplan,
            floorplan_dir / f"floorplan{floorplan_ext}",
            allowed_mime_types=self.ALLOWED_IMAGE_MIME_TYPES,
            max_bytes=self.MAX_IMAGE_BYTES,
        )

        interior_metadata: list[UploadedFileMetadata] = []
        for index, photo in enumerate(interior_photos or [], start=1):
            self._require_upload_filename(photo, f"interior_photos[{index}]")
            photo_ext = self._extension_for_upload(photo)
            saved = self.file_service.save_upload_file(
                photo,
                interior_dir / f"interior_{index:03d}{photo_ext}",
                allowed_mime_types=self.ALLOWED_IMAGE_MIME_TYPES,
                max_bytes=self.MAX_IMAGE_BYTES,
            )
            interior_metadata.append(self._uploaded_file_metadata(run_id, saved, "interior_photo"))

        style_reference_groups = StyleReferenceGroups()
        named_style_references = [
            ("ideal", ideal_style_reference, ideal_style_reference_dir),
            ("acceptable", acceptable_style_reference, acceptable_style_reference_dir),
            ("ng", ng_style_reference, ng_style_reference_dir),
        ]
        for reference_type, image, destination_dir in named_style_references:
            if image is None:
                continue
            self._require_upload_filename(image, f"{reference_type}_style_reference")
            group = getattr(style_reference_groups, reference_type)
            group.append(self._save_style_reference(destination_dir, image, reference_type, len(group) + 1))
        for index, image in enumerate(style_reference_images or [], start=1):
            self._require_upload_filename(image, f"style_reference_images[{index}]")
            style_reference_groups.acceptable.append(
                self._save_style_reference(
                    acceptable_style_reference_dir,
                    image,
                    "acceptable",
                    len(style_reference_groups.acceptable) + 1,
                )
            )

        metadata_path = run_dir / "run_metadata.json"
        floorplan_metadata = self._uploaded_file_metadata(run_id, floorplan_saved, "floorplan")
        workspace = WorkspacePaths(
            root=self._relative_storage_path(run_dir),
            source_dir=self._relative_storage_path(source_dir),
            floorplan_dir=self._relative_storage_path(floorplan_dir),
            interior_photos_dir=self._relative_storage_path(interior_dir),
            style_references_dir=self._relative_storage_path(style_reference_dir),
            artifacts_dir=self._relative_storage_path(artifacts_dir),
            outputs_dir=self._relative_storage_path(outputs_dir),
        )
        inputs = RunInputs(
            floorplan=floorplan_metadata,
            interior_photos=interior_metadata,
            style_references=style_reference_groups,
        )
        now = datetime.now(timezone.utc)
        metadata = RunMetadata(
            schema_version="1.1",
            phase="phase_1_upload_workspace",
            run_id=run_id,
            run_status="uploaded",
            status="uploaded",
            created_at=now,
            updated_at=now,
            workspace_path=self._relative_storage_path(run_dir),
            workspace=workspace,
            inputs=inputs,
            floorplan=floorplan_metadata,
            interior_photos=interior_metadata,
            style_references=style_reference_groups,
            processing=ProcessingFlags(),
            requirements=OutputRequirements(),
            metadata_path=self._relative_storage_path(metadata_path),
        )
        self._write_metadata(metadata_path, metadata)
        return metadata

    def _save_style_reference(
        self,
        style_reference_dir: Path,
        image: UploadFile,
        reference_type: str,
        index: int,
    ) -> UploadedFileMetadata:
        image_ext = self._extension_for_upload(image)
        saved = self.file_service.save_upload_file(
            image,
            style_reference_dir / f"style_reference_{reference_type}_{index:03d}{image_ext}",
            allowed_mime_types=self.ALLOWED_IMAGE_MIME_TYPES,
            max_bytes=self.MAX_IMAGE_BYTES,
        )
        metadata = self._uploaded_file_metadata("unused", saved, "style_reference")
        return metadata.model_copy(update={"reference_type": reference_type})

    @staticmethod
    def _require_upload_filename(upload: UploadFile | None, field_name: str) -> None:
        if upload is None or not upload.filename:
            raise HTTPException(status_code=400, detail=f"{field_name} file is empty or missing")

    def load_metadata(self, run_id: str) -> RunMetadata:
        run_dir = self._safe_run_dir(run_id)
        metadata_path = run_dir / "run_metadata.json"
        if not metadata_path.exists():
            raise HTTPException(status_code=404, detail="run metadata not found")
        try:
            return RunMetadata.model_validate_json(metadata_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=500, detail="failed to read run metadata") from exc

    def mark_input_inspected(self, metadata: RunMetadata) -> RunMetadata:
        now = datetime.now(timezone.utc)
        processing = metadata.processing.model_copy(update={"input_inspection": True})
        updated_metadata = metadata.model_copy(
            update={
                "run_status": "inspected",
                "status": "inspected",
                "updated_at": now,
                "processing": processing,
            }
        )
        metadata_path = self._safe_run_dir(metadata.run_id) / "run_metadata.json"
        self._write_metadata(metadata_path, updated_metadata)
        return updated_metadata

    def mark_floorplan_preprocessed(self, metadata: RunMetadata) -> RunMetadata:
        now = datetime.now(timezone.utc)
        processing = metadata.processing.model_copy(
            update={
                "input_inspection": True,
                "floorplan_preprocess": True,
            }
        )
        updated_metadata = metadata.model_copy(
            update={
                "run_status": "preprocessed",
                "status": "preprocessed",
                "updated_at": now,
                "processing": processing,
            }
        )
        metadata_path = self._safe_run_dir(metadata.run_id) / "run_metadata.json"
        self._write_metadata(metadata_path, updated_metadata)
        return updated_metadata

    def mark_semantic_analysis_completed(
        self,
        metadata: RunMetadata,
        *,
        analysis_provider: str | None = None,
        analysis_model: str | None = None,
    ) -> RunMetadata:
        now = datetime.now(timezone.utc)
        processing = metadata.processing.model_copy(
            update={
                "input_inspection": True,
                "floorplan_preprocess": True,
                "semantic_analysis": True,
            }
        )
        updated_metadata = metadata.model_copy(
            update={
                "run_status": "analyzed",
                "status": "analyzed",
                "updated_at": now,
                "processing": processing,
                "analysis_provider": analysis_provider or metadata.analysis_provider,
                "analysis_model": analysis_model or metadata.analysis_model,
            }
        )
        metadata_path = self._safe_run_dir(metadata.run_id) / "run_metadata.json"
        self._write_metadata(metadata_path, updated_metadata)
        return updated_metadata

    def mark_semantic_validation_completed(self, metadata: RunMetadata) -> RunMetadata:
        now = datetime.now(timezone.utc)
        processing = metadata.processing.model_copy(
            update={
                "input_inspection": True,
                "floorplan_preprocess": True,
                "semantic_analysis": True,
                "semantic_validation": True,
            }
        )
        updated_metadata = metadata.model_copy(
            update={
                "run_status": "validated",
                "status": "validated",
                "updated_at": now,
                "processing": processing,
            }
        )
        metadata_path = self._safe_run_dir(metadata.run_id) / "run_metadata.json"
        self._write_metadata(metadata_path, updated_metadata)
        return updated_metadata

    def mark_interior_style_analysis_completed(
        self,
        metadata: RunMetadata,
        *,
        interior_analysis_path: str | None,
        interior_analysis_summary: InteriorAnalysisSummary | None,
    ) -> RunMetadata:
        now = datetime.now(timezone.utc)
        processing = metadata.processing.model_copy(
            update={
                "input_inspection": True,
                "interior_style_analysis": True,
                "floorplan_preprocess": metadata.processing.floorplan_preprocess,
                "semantic_analysis": metadata.processing.semantic_analysis,
                "semantic_validation": metadata.processing.semantic_validation,
            }
        )
        updated_metadata = metadata.model_copy(
            update={
                "run_status": "interior_analyzed",
                "status": "interior_analyzed",
                "updated_at": now,
                "processing": processing,
                "interior_analysis_path": interior_analysis_path,
                "interior_analysis_summary": interior_analysis_summary,
                "analysis_provider": (interior_analysis_summary.provider if interior_analysis_summary else metadata.analysis_provider),
                "analysis_model": (interior_analysis_summary.model if interior_analysis_summary else metadata.analysis_model),
            }
        )
        metadata_path = self._safe_run_dir(metadata.run_id) / "run_metadata.json"
        self._write_metadata(metadata_path, updated_metadata)
        return updated_metadata

    def update_metadata_summary(self, metadata: RunMetadata, summary: RunMetadataSummary) -> RunMetadata:
        now = datetime.now(timezone.utc)
        updated_metadata = metadata.model_copy(
            update={
                "updated_at": now,
                "artifact_index_path": summary.artifact_index_path,
                "pipeline_summary": summary.pipeline_summary if isinstance(summary.pipeline_summary, PipelineSummary) else PipelineSummary.model_validate(summary.pipeline_summary),
                "analysis_summary": summary.analysis_summary if summary.analysis_summary is None or isinstance(summary.analysis_summary, AnalysisSummary) else AnalysisSummary.model_validate(summary.analysis_summary),
                "interior_analysis_summary": summary.interior_analysis_summary if summary.interior_analysis_summary is None or isinstance(summary.interior_analysis_summary, InteriorAnalysisSummary) else InteriorAnalysisSummary.model_validate(summary.interior_analysis_summary),
                "interior_validation_summary": summary.interior_validation_summary if summary.interior_validation_summary is None or isinstance(summary.interior_validation_summary, InteriorValidationSummary) else InteriorValidationSummary.model_validate(summary.interior_validation_summary),
                "room_function_assignment_summary": summary.room_function_assignment_summary if summary.room_function_assignment_summary is None or isinstance(summary.room_function_assignment_summary, RoomFunctionAssignmentSummary) else RoomFunctionAssignmentSummary.model_validate(summary.room_function_assignment_summary),
                "layout_summary": summary.layout_summary if summary.layout_summary is None or isinstance(summary.layout_summary, LayoutSummary) else LayoutSummary.model_validate(summary.layout_summary),
                "layout_validation_summary": summary.layout_validation_summary if summary.layout_validation_summary is None or isinstance(summary.layout_validation_summary, LayoutValidationSummary) else LayoutValidationSummary.model_validate(summary.layout_validation_summary),
                "furniture_placement_summary": summary.furniture_placement_summary if summary.furniture_placement_summary is None or isinstance(summary.furniture_placement_summary, FurniturePlacementSummary) else FurniturePlacementSummary.model_validate(summary.furniture_placement_summary),
                "furniture_placement_validation_summary": summary.furniture_placement_validation_summary if summary.furniture_placement_validation_summary is None or isinstance(summary.furniture_placement_validation_summary, FurniturePlacementValidationSummary) else FurniturePlacementValidationSummary.model_validate(summary.furniture_placement_validation_summary),
                "render_plan_summary": summary.render_plan_summary if summary.render_plan_summary is None or isinstance(summary.render_plan_summary, RenderPlanSummary) else RenderPlanSummary.model_validate(summary.render_plan_summary),
                "prompt_package_summary": summary.prompt_package_summary if summary.prompt_package_summary is None or isinstance(summary.prompt_package_summary, PromptPackageSummary) else PromptPackageSummary.model_validate(summary.prompt_package_summary),
                "image_generation_request_preview_summary": summary.image_generation_request_preview_summary if summary.image_generation_request_preview_summary is None or isinstance(summary.image_generation_request_preview_summary, ImageGenerationRequestPreviewSummary) else ImageGenerationRequestPreviewSummary.model_validate(summary.image_generation_request_preview_summary),
                "image_generation_draft_summary": summary.image_generation_draft_summary if summary.image_generation_draft_summary is None or isinstance(summary.image_generation_draft_summary, ImageGenerationDraftSummary) else ImageGenerationDraftSummary.model_validate(summary.image_generation_draft_summary),
                "visual_qa_summary": summary.visual_qa_summary if summary.visual_qa_summary is None or isinstance(summary.visual_qa_summary, VisualQASummary) else VisualQASummary.model_validate(summary.visual_qa_summary),
                "qa_feedback_path": summary.qa_feedback_path,
                "qa_feedback_summary": summary.qa_feedback_summary if summary.qa_feedback_summary is None or isinstance(summary.qa_feedback_summary, QAFeedbackSummary) else QAFeedbackSummary.model_validate(summary.qa_feedback_summary),
                "final_output_path": summary.final_output_path,
                "final_output_summary": summary.final_output_summary if summary.final_output_summary is None or isinstance(summary.final_output_summary, FinalOutputSummary) else FinalOutputSummary.model_validate(summary.final_output_summary),
                "latest_regeneration_path": summary.latest_regeneration_path,
                "regeneration_summary": summary.regeneration_summary if summary.regeneration_summary is None or isinstance(summary.regeneration_summary, RegenerationSummary) else RegenerationSummary.model_validate(summary.regeneration_summary),
                "public_output_url": summary.public_output_url or metadata.public_output_url,
                "last_indexed_at": summary.generated_at,
            }
        )
        metadata_path = self._safe_run_dir(metadata.run_id) / "run_metadata.json"
        self._write_metadata(metadata_path, updated_metadata)
        return updated_metadata

    def apply_layout_validation_updates(
        self,
        metadata: RunMetadata,
        updates: dict,
    ) -> RunMetadata:
        now = datetime.now(timezone.utc)
        updated_metadata = metadata.model_copy(
            update={
                **updates,
                "updated_at": updates.get("updated_at", now),
                "processing": updates.get("processing", metadata.processing),
                "pipeline": updates.get("pipeline", metadata.pipeline),
                "layout_validated_path": updates.get("layout_validated_path"),
                "layout_validation_summary": updates.get("layout_validation_summary"),
            }
        )
        metadata_path = self._safe_run_dir(metadata.run_id) / "run_metadata.json"
        self._write_metadata(metadata_path, updated_metadata)
        return updated_metadata

    def apply_furniture_placement_updates(
        self,
        metadata: RunMetadata,
        updates: dict,
    ) -> RunMetadata:
        now = datetime.now(timezone.utc)
        updated_metadata = metadata.model_copy(
            update={
                **updates,
                "updated_at": updates.get("updated_at", now),
                "processing": updates.get("processing", metadata.processing),
                "pipeline": updates.get("pipeline", metadata.pipeline),
                "layout_furniture_planned_path": updates.get("layout_furniture_planned_path"),
                "furniture_placement_summary": updates.get("furniture_placement_summary"),
            }
        )
        metadata_path = self._safe_run_dir(metadata.run_id) / "run_metadata.json"
        self._write_metadata(metadata_path, updated_metadata)
        return updated_metadata

    def apply_furniture_placement_validation_updates(
        self,
        metadata: RunMetadata,
        updates: dict,
    ) -> RunMetadata:
        now = datetime.now(timezone.utc)
        updated_metadata = metadata.model_copy(
            update={
                **updates,
                "updated_at": updates.get("updated_at", now),
                "processing": updates.get("processing", metadata.processing),
                "pipeline": updates.get("pipeline", metadata.pipeline),
                "layout_furniture_validated_path": updates.get("layout_furniture_validated_path"),
                "furniture_placement_validation_summary": updates.get("furniture_placement_validation_summary"),
            }
        )
        metadata_path = self._safe_run_dir(metadata.run_id) / "run_metadata.json"
        self._write_metadata(metadata_path, updated_metadata)
        return updated_metadata

    def apply_render_plan_updates(
        self,
        metadata: RunMetadata,
        updates: dict,
    ) -> RunMetadata:
        now = datetime.now(timezone.utc)
        updated_metadata = metadata.model_copy(
            update={
                **updates,
                "updated_at": updates.get("updated_at", now),
                "processing": updates.get("processing", metadata.processing),
                "pipeline": updates.get("pipeline", metadata.pipeline),
                "render_plan_path": updates.get("render_plan_path"),
                "render_plan_summary": updates.get("render_plan_summary"),
            }
        )
        metadata_path = self._safe_run_dir(metadata.run_id) / "run_metadata.json"
        self._write_metadata(metadata_path, updated_metadata)
        return updated_metadata

    def apply_prompt_package_updates(
        self,
        metadata: RunMetadata,
        updates: dict,
    ) -> RunMetadata:
        now = datetime.now(timezone.utc)
        updated_metadata = metadata.model_copy(
            update={
                **updates,
                "updated_at": updates.get("updated_at", now),
                "processing": updates.get("processing", metadata.processing),
                "pipeline": updates.get("pipeline", metadata.pipeline),
                "prompt_package_path": updates.get("prompt_package_path"),
                "prompt_package_summary": updates.get("prompt_package_summary"),
            }
        )
        metadata_path = self._safe_run_dir(metadata.run_id) / "run_metadata.json"
        self._write_metadata(metadata_path, updated_metadata)
        return updated_metadata

    def apply_image_generation_request_preview_updates(
        self,
        metadata: RunMetadata,
        updates: dict,
    ) -> RunMetadata:
        now = datetime.now(timezone.utc)
        updated_metadata = metadata.model_copy(
            update={
                **updates,
                "updated_at": updates.get("updated_at", now),
                "processing": updates.get("processing", metadata.processing),
                "pipeline": updates.get("pipeline", metadata.pipeline),
                "image_generation_request_preview_path": updates.get("image_generation_request_preview_path"),
                "image_generation_request_preview_summary": updates.get("image_generation_request_preview_summary"),
            }
        )
        metadata_path = self._safe_run_dir(metadata.run_id) / "run_metadata.json"
        self._write_metadata(metadata_path, updated_metadata)
        return updated_metadata

    def apply_image_generation_draft_updates(
        self,
        metadata: RunMetadata,
        updates: dict,
    ) -> RunMetadata:
        now = datetime.now(timezone.utc)
        updated_metadata = metadata.model_copy(
            update={
                **updates,
                "updated_at": updates.get("updated_at", now),
                "processing": updates.get("processing", metadata.processing),
                "pipeline": updates.get("pipeline", metadata.pipeline),
                "image_generation_draft_path": updates.get("image_generation_draft_path"),
                "image_generation_draft_summary": updates.get("image_generation_draft_summary"),
                "cloudinary_summary": updates.get("cloudinary_summary", metadata.cloudinary_summary),
                "public_output_url": updates.get("public_output_url", metadata.public_output_url),
            }
        )
        metadata_path = self._safe_run_dir(metadata.run_id) / "run_metadata.json"
        self._write_metadata(metadata_path, updated_metadata)
        return updated_metadata

    def apply_structure_locked_composite_updates(
        self,
        metadata: RunMetadata,
        updates: dict,
    ) -> RunMetadata:
        now = datetime.now(timezone.utc)
        updated_metadata = metadata.model_copy(
            update={
                **updates,
                "updated_at": updates.get("updated_at", now),
                "processing": updates.get("processing", metadata.processing),
                "pipeline": updates.get("pipeline", metadata.pipeline),
                "structure_locked_composite_path": updates.get("structure_locked_composite_path"),
                "structure_locked_composite_summary": updates.get("structure_locked_composite_summary"),
            }
        )
        metadata_path = self._safe_run_dir(metadata.run_id) / "run_metadata.json"
        self._write_metadata(metadata_path, updated_metadata)
        return updated_metadata

    def apply_visual_qa_updates(
        self,
        metadata: RunMetadata,
        updates: dict,
    ) -> RunMetadata:
        now = datetime.now(timezone.utc)
        updated_metadata = metadata.model_copy(
            update={
                **updates,
                "updated_at": updates.get("updated_at", now),
                "processing": updates.get("processing", metadata.processing),
                "visual_qa_report_path": updates.get("visual_qa_report_path"),
                "visual_qa_summary": updates.get("visual_qa_summary"),
            }
        )
        metadata_path = self._safe_run_dir(metadata.run_id) / "run_metadata.json"
        self._write_metadata(metadata_path, updated_metadata)
        return updated_metadata

    def apply_final_output_updates(
        self,
        metadata: RunMetadata,
        updates: dict,
    ) -> RunMetadata:
        now = datetime.now(timezone.utc)
        updated_metadata = metadata.model_copy(
            update={
                **updates,
                "updated_at": updates.get("updated_at", now),
                "processing": updates.get("processing", metadata.processing),
                "pipeline": updates.get("pipeline", metadata.pipeline),
                "final_output_path": updates.get("final_output_path"),
                "final_output_summary": updates.get("final_output_summary"),
                "public_output_url": updates.get("public_output_url", metadata.public_output_url),
            }
        )
        metadata_path = self._safe_run_dir(metadata.run_id) / "run_metadata.json"
        self._write_metadata(metadata_path, updated_metadata)
        return updated_metadata

    def apply_qa_feedback_updates(
        self,
        metadata: RunMetadata,
        updates: dict,
    ) -> RunMetadata:
        now = datetime.now(timezone.utc)
        updated_metadata = metadata.model_copy(
            update={
                **updates,
                "updated_at": updates.get("updated_at", now),
                "processing": updates.get("processing", metadata.processing),
                "pipeline": updates.get("pipeline", metadata.pipeline),
                "qa_feedback_path": updates.get("qa_feedback_path"),
                "qa_feedback_summary": updates.get("qa_feedback_summary"),
            }
        )
        metadata_path = self._safe_run_dir(metadata.run_id) / "run_metadata.json"
        self._write_metadata(metadata_path, updated_metadata)
        return updated_metadata

    def apply_regeneration_updates(
        self,
        metadata: RunMetadata,
        updates: dict,
    ) -> RunMetadata:
        now = datetime.now(timezone.utc)
        updated_metadata = metadata.model_copy(
            update={
                **updates,
                "updated_at": updates.get("updated_at", now),
                "processing": updates.get("processing", metadata.processing),
                "pipeline": updates.get("pipeline", metadata.pipeline),
                "latest_regeneration_path": updates.get("latest_regeneration_path"),
                "regeneration_summary": updates.get("regeneration_summary"),
                "public_output_url": updates.get("public_output_url", metadata.public_output_url),
            }
        )
        metadata_path = self._safe_run_dir(metadata.run_id) / "run_metadata.json"
        self._write_metadata(metadata_path, updated_metadata)
        return updated_metadata

    def apply_layout_creation_updates(
        self,
        metadata: RunMetadata,
        updates: dict,
    ) -> RunMetadata:
        now = datetime.now(timezone.utc)
        updated_metadata = metadata.model_copy(
            update={
                **updates,
                "updated_at": updates.get("updated_at", now),
                "processing": updates.get("processing", metadata.processing),
                "pipeline": updates.get("pipeline", metadata.pipeline),
                "room_function_assignment_path": updates.get("room_function_assignment_path", metadata.room_function_assignment_path),
                "room_function_assignment_summary": updates.get("room_function_assignment_summary", metadata.room_function_assignment_summary),
                "layout_initial_path": updates.get("layout_initial_path"),
                "layout_summary": updates.get("layout_summary"),
            }
        )
        metadata_path = self._safe_run_dir(metadata.run_id) / "run_metadata.json"
        self._write_metadata(metadata_path, updated_metadata)
        return updated_metadata

    def apply_room_function_assignment_updates(
        self,
        metadata: RunMetadata,
        updates: dict,
    ) -> RunMetadata:
        now = datetime.now(timezone.utc)
        updated_metadata = metadata.model_copy(
            update={
                **updates,
                "updated_at": updates.get("updated_at", now),
                "processing": updates.get("processing", metadata.processing),
                "pipeline": updates.get("pipeline", metadata.pipeline),
                "room_function_assignment_path": updates.get("room_function_assignment_path"),
                "room_function_assignment_summary": updates.get("room_function_assignment_summary"),
            }
        )
        metadata_path = self._safe_run_dir(metadata.run_id) / "run_metadata.json"
        self._write_metadata(metadata_path, updated_metadata)
        return updated_metadata

    def apply_interior_validation_updates(
        self,
        metadata: RunMetadata,
        updates: dict,
    ) -> RunMetadata:
        now = datetime.now(timezone.utc)
        updated_metadata = metadata.model_copy(
            update={
                **updates,
                "updated_at": updates.get("updated_at", now),
                "processing": updates.get("processing", metadata.processing),
                "interior_validation_summary": updates.get("interior_validation_summary"),
                "pipeline": updates.get("pipeline"),
                "interior_analysis_validated_path": updates.get("interior_analysis_validated_path"),
            }
        )
        metadata_path = self._safe_run_dir(metadata.run_id) / "run_metadata.json"
        self._write_metadata(metadata_path, updated_metadata)
        return updated_metadata

    def _safe_run_dir(self, run_id: str) -> Path:
        if not RUN_ID_PATTERN.fullmatch(run_id):
            raise HTTPException(status_code=400, detail="invalid run_id")
        run_dir = self._run_dir(run_id)
        try:
            run_dir.resolve().relative_to(self.storage_runs_dir.resolve())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid run path") from exc
        return run_dir

    def _run_dir(self, run_id: str) -> Path:
        return self.storage_runs_dir / run_id

    def _extension_for_upload(self, upload: UploadFile) -> str:
        content_type = upload.content_type or ""
        if content_type not in self.ALLOWED_IMAGE_MIME_TYPES:
            inferred_content_type = self.file_service._infer_content_type_from_filename(
                upload.filename,
                self.ALLOWED_IMAGE_MIME_TYPES,
            )
            if inferred_content_type:
                content_type = inferred_content_type
        if content_type not in self.ALLOWED_IMAGE_MIME_TYPES:
            raise HTTPException(
                status_code=415,
                detail="unsupported image MIME type; allowed types are image/png, image/jpeg, and image/webp",
            )
        return self.ALLOWED_IMAGE_MIME_TYPES[content_type]

    def _uploaded_file_metadata(self, run_id: str, saved: SavedUpload, role: str) -> UploadedFileMetadata:
        relative_path = self._relative_storage_path(saved.path)
        category = "input" if role in {"floorplan", "interior_photo"} else "reference"
        return UploadedFileMetadata(
            role=role,
            category=category,
            stored_filename=saved.filename,
            mime_type=saved.content_type,
            filename=saved.filename,
            original_filename=saved.original_filename,
            content_type=saved.content_type,
            size_bytes=saved.size_bytes,
            relative_path=relative_path,
            preview_url=f"/{relative_path}",
        )

    def _relative_storage_path(self, path: Path) -> str:
        return path.relative_to(self.storage_runs_dir.parent.parent).as_posix()

    @staticmethod
    def _write_metadata(path: Path, metadata: RunMetadata) -> None:
        try:
            payload = json.dumps(metadata.model_dump(mode="json"), ensure_ascii=False, indent=2)
            temp_path = path.with_suffix(f"{path.suffix}.tmp")
            temp_path.write_text(payload, encoding="utf-8")
            temp_path.replace(path)
        except OSError as exc:
            raise HTTPException(status_code=500, detail="failed to write run metadata") from exc


def to_create_response(metadata: RunMetadata) -> RunCreateResponse:
    return RunCreateResponse(
        status=metadata.status,
        run_status=metadata.run_status,
        run_id=metadata.run_id,
        workspace_path=metadata.workspace_path,
        metadata_path=metadata.metadata_path,
        floorplan=metadata.floorplan,
        interior_photos=metadata.interior_photos,
        style_references=metadata.style_references,
        inputs=metadata.inputs,
    )
