from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException

from app.schemas.run import (
    AnalysisSummary,
    ArtifactIndexEntry,
    FloorplanAnalysisValidatedArtifact,
    FloorplanSemanticAnalysisArtifact,
    FurniturePlacementArtifact,
    FurniturePlacementSummary,
    FurniturePlacementValidationArtifact,
    FurniturePlacementValidationSummary,
    InputSummary,
    ImageGenerationDraftArtifact,
    ImageGenerationDraftSummary,
    InteriorAnalysisSummary,
    InteriorStyleAnalysisArtifact,
    InteriorValidationSummary,
    ImageGenerationRequestPreviewArtifact,
    ImageGenerationRequestPreviewSummary,
    LayoutInitialArtifact,
    LayoutSummary,
    LayoutValidationArtifact,
    LayoutValidationSummary,
    PipelineSummary,
    PromptPackageArtifact,
    PromptPackageSummary,
    RenderPlanArtifact,
    RenderPlanSummary,
    RoomFunctionAssignmentArtifact,
    RoomFunctionAssignmentSummary,
    RunArtifactIndex,
    RunMetadata,
    RunMetadataSummary,
    StructureLockedCompositeArtifact,
    StructureLockedCompositeSummary,
)


class RunIndexService:
    ARTIFACT_CONTENT_TYPES = {
        ".json": "application/json",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".txt": "text/plain",
    }
    KNOWN_ARTIFACT_SPECS = {
        "input_manifest": {
            "filename": "input_manifest.json",
            "category": "input",
            "type": "json",
        },
        "floorplan_preprocess": {
            "filename": "floorplan_preprocess.json",
            "category": "preprocess",
            "type": "json",
        },
        "normalized_floorplan": {
            "filename": "normalized_floorplan.png",
            "category": "preprocess_image",
            "type": "image",
        },
        "grayscale": {
            "filename": "grayscale.png",
            "category": "preprocess_image",
            "type": "image",
        },
        "binary_mask": {
            "filename": "binary_mask.png",
            "category": "preprocess_image",
            "type": "image",
        },
        "edges": {
            "filename": "edges.png",
            "category": "preprocess_image",
            "type": "image",
        },
        "line_preview": {
            "filename": "line_preview.png",
            "category": "preprocess_image",
            "type": "image",
        },
        "floorplan_analysis": {
            "filename": "floorplan_analysis.json",
            "category": "floorplan_analysis",
            "type": "json",
        },
        "floorplan_analysis_raw": {
            "filename": "floorplan_analysis_raw.json",
            "category": "floorplan_analysis_raw",
            "type": "json",
        },
        "floorplan_analysis_validated": {
            "filename": "floorplan_analysis_validated.json",
            "category": "floorplan_analysis_validated",
            "type": "json",
        },
        "interior_analysis": {
            "filename": "interior_analysis.json",
            "category": "interior_analysis",
            "type": "json",
        },
        "interior_analysis_raw": {
            "filename": "interior_analysis_raw.json",
            "category": "interior_analysis_raw",
            "type": "json",
        },
        "interior_analysis_validated": {
            "filename": "interior_analysis_validated.json",
            "category": "interior_analysis_validated",
            "type": "json",
        },
        "room_function_assignment": {
            "filename": "room_function_assignment.json",
            "category": "room_function_assignment",
            "type": "json",
        },
        "layout_initial": {
            "filename": "layout_initial.json",
            "category": "layout",
            "type": "json",
        },
        "layout_validated": {
            "filename": "layout_validated.json",
            "category": "layout",
            "type": "json",
        },
        "layout_furniture_planned": {
            "filename": "layout_furniture_planned.json",
            "category": "layout",
            "type": "json",
        },
        "layout_furniture_validated": {
            "filename": "layout_furniture_validated.json",
            "category": "layout",
            "type": "json",
        },
        "render_plan": {
            "filename": "render_plan.json",
            "category": "render",
            "type": "json",
        },
        "prompt_package": {
            "filename": "prompt_package.json",
            "category": "render",
            "type": "json",
        },
        "image_generation_request_preview": {
            "filename": "image_generation_request_preview.json",
            "category": "render",
            "type": "json",
        },
        "image_generation_draft": {
            "filename": "image_generation_draft.json",
            "category": "render",
            "type": "json",
        },
        "structure_locked_composite": {
            "filename": "structure_locked_composite.json",
            "category": "render",
            "type": "json",
        },
        "generated_draft_raw": {
            "filename": "generated_draft_raw.png",
            "category": "render",
            "type": "image",
        },
        "artifact_index": {
            "filename": "artifact_index.json",
            "category": "index",
            "type": "json",
        },
        "run_metadata_summary": {
            "filename": "run_metadata_summary.json",
            "category": "summary",
            "type": "json",
        },
    }

    def __init__(self, storage_dir: Path, storage_runs_dir: Path) -> None:
        self.storage_dir = storage_dir
        self.storage_runs_dir = storage_runs_dir

    def index_run(self, metadata: RunMetadata) -> RunMetadataSummary:
        run_dir = self._safe_run_dir(metadata.run_id)
        artifact_entries = self._collect_artifacts(metadata, run_dir)
        artifact_index = RunArtifactIndex(
            run_id=metadata.run_id,
            generated_at=datetime.now(timezone.utc),
            artifacts=artifact_entries,
            checks={
                "run_metadata_present": True,
                "floorplan_present": self._artifact_exists(artifact_entries, "floorplan_source"),
                "artifact_index_written": True,
            },
            warnings=[],
            errors=[],
        )

        artifacts_dir = self._artifacts_dir(metadata, run_dir)
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        artifact_index_path = artifacts_dir / "artifact_index.json"
        self._write_json(artifact_index_path, artifact_index.model_dump(mode="json"))

        validated_artifact = self._load_optional_validated_artifact(run_dir)
        analysis_artifact = self._load_optional_analysis_artifact(run_dir)
        interior_analysis_artifact = self._load_optional_interior_analysis_artifact(run_dir)
        interior_validation_artifact = self._load_optional_interior_validation_artifact(run_dir)
        room_function_assignment_artifact = self._load_optional_room_function_assignment_artifact(run_dir)
        layout_initial_artifact = self._load_optional_layout_initial_artifact(run_dir)
        layout_validated_artifact = self._load_optional_layout_validated_artifact(run_dir)
        layout_furniture_planned_artifact = self._load_optional_layout_furniture_planned_artifact(run_dir)
        layout_furniture_validated_artifact = self._load_optional_layout_furniture_validated_artifact(run_dir)
        render_plan_artifact = self._load_optional_render_plan_artifact(run_dir)
        prompt_package_artifact = self._load_optional_prompt_package_artifact(run_dir)
        image_generation_request_preview_artifact = self._load_optional_image_generation_request_preview_artifact(run_dir)
        image_generation_draft_artifact = self._load_optional_image_generation_draft_artifact(run_dir)
        structure_locked_composite_artifact = self._load_optional_structure_locked_composite_artifact(run_dir)
        input_summary = self._build_input_summary(metadata)
        pipeline_summary = self._build_pipeline_summary(metadata, artifact_entries)
        analysis_summary = self._build_analysis_summary(validated_artifact, analysis_artifact)
        interior_analysis_summary = self._build_interior_analysis_summary(metadata, interior_analysis_artifact)
        interior_validation_summary = self._build_interior_validation_summary(metadata, interior_validation_artifact)
        room_function_assignment_summary = self._build_room_function_assignment_summary(metadata, room_function_assignment_artifact)
        layout_summary = self._build_layout_summary(metadata, layout_initial_artifact)
        layout_validation_summary = self._build_layout_validation_summary(metadata, layout_validated_artifact)
        furniture_placement_summary = self._build_furniture_placement_summary(metadata, layout_furniture_planned_artifact)
        furniture_placement_validation_summary = self._build_furniture_placement_validation_summary(metadata, layout_furniture_validated_artifact)
        render_plan_summary = self._build_render_plan_summary(metadata, render_plan_artifact)
        prompt_package_summary = self._build_prompt_package_summary(metadata, prompt_package_artifact)
        image_generation_request_preview_summary = self._build_image_generation_request_preview_summary(metadata, image_generation_request_preview_artifact)
        image_generation_draft_summary = self._build_image_generation_draft_summary(metadata, image_generation_draft_artifact)
        structure_locked_composite_summary = self._build_structure_locked_composite_summary(metadata, structure_locked_composite_artifact)

        summary = RunMetadataSummary(
            run_id=metadata.run_id,
            generated_at=datetime.now(timezone.utc),
            artifact_index_path=self._relative_storage_path(artifact_index_path),
            artifacts=artifact_entries,
            input_summary=input_summary,
            pipeline_summary=pipeline_summary,
            analysis_summary=analysis_summary,
            interior_analysis_summary=interior_analysis_summary,
            interior_validation_summary=interior_validation_summary,
            room_function_assignment_summary=room_function_assignment_summary,
            layout_summary=layout_summary,
            layout_validation_summary=layout_validation_summary,
            furniture_placement_summary=furniture_placement_summary,
            furniture_placement_validation_summary=furniture_placement_validation_summary,
            render_plan_summary=render_plan_summary,
            prompt_package_summary=prompt_package_summary,
            image_generation_request_preview_summary=image_generation_request_preview_summary,
            image_generation_draft_summary=image_generation_draft_summary,
            structure_locked_composite_summary=structure_locked_composite_summary,
            warnings=[],
            errors=[],
        )
        self._write_json(artifacts_dir / "run_metadata_summary.json", summary.model_dump(mode="json"))
        return summary

    def load_artifact_index(self, run_id: str) -> RunArtifactIndex:
        run_dir = self._safe_run_dir(run_id)
        artifact_path = run_dir / "artifacts" / "artifact_index.json"
        if not artifact_path.exists():
            raise HTTPException(status_code=404, detail="artifact index not found")
        try:
            return RunArtifactIndex.model_validate_json(artifact_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=500, detail="failed to read artifact index") from exc

    def load_summary(self, run_id: str) -> RunMetadataSummary:
        run_dir = self._safe_run_dir(run_id)
        summary_path = run_dir / "artifacts" / "run_metadata_summary.json"
        if not summary_path.exists():
            raise HTTPException(status_code=404, detail="run metadata summary not found")
        try:
            return RunMetadataSummary.model_validate_json(summary_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=500, detail="failed to read run metadata summary") from exc

    def _collect_artifacts(self, metadata: RunMetadata, run_dir: Path) -> list[ArtifactIndexEntry]:
        entries: list[ArtifactIndexEntry] = []
        floorplan_meta = metadata.inputs.floorplan if metadata.inputs else metadata.floorplan
        entries.append(self._entry_from_relative_path("floorplan_source", floorplan_meta.relative_path, "source"))

        for index, photo in enumerate((metadata.inputs.interior_photos if metadata.inputs else metadata.interior_photos), start=1):
            entries.append(self._entry_from_relative_path(f"interior_photo_{index:03d}", photo.relative_path, "source"))

        style_groups = metadata.inputs.style_references if metadata.inputs else metadata.style_references
        for group_name in ("ideal", "acceptable", "ng"):
            for index, item in enumerate(getattr(style_groups, group_name), start=1):
                entries.append(
                    self._entry_from_relative_path(
                        f"style_reference_{group_name}_{index:03d}",
                        item.relative_path,
                        "source",
                    )
                )

        artifacts_dir = self._artifacts_dir(metadata, run_dir)
        known_artifact_entries = self._collect_known_artifact_entries(artifacts_dir)
        entries.extend(known_artifact_entries)

        if artifacts_dir.exists():
            known_filenames = {spec["filename"] for spec in self.KNOWN_ARTIFACT_SPECS.values()}
            for path in sorted(artifacts_dir.iterdir(), key=lambda item: item.name):
                if not path.is_file() or path.name in known_filenames:
                    continue
                entries.append(self._entry_from_path(path, "artifact"))

        outputs_dir = self._outputs_dir(metadata, run_dir)
        draft_output_path = outputs_dir / f"{metadata.run_id}_draft.png"
        entries.append(self._entry_from_path(draft_output_path, "output", key="draft_output", entry_type="image"))
        composite_output_path = outputs_dir / f"{metadata.run_id}_structure_locked_composite.png"
        entries.append(self._entry_from_path(composite_output_path, "output", key="structure_locked_composite_output", entry_type="image"))
        if outputs_dir.exists():
            for path in sorted(outputs_dir.iterdir(), key=lambda item: item.name):
                if not path.is_file() or path in {draft_output_path, composite_output_path}:
                    continue
                entries.append(self._entry_from_path(path, "output"))

        return entries

    def _collect_known_artifact_entries(self, artifacts_dir: Path) -> list[ArtifactIndexEntry]:
        entries: list[ArtifactIndexEntry] = []
        for key, spec in self.KNOWN_ARTIFACT_SPECS.items():
            path = artifacts_dir / spec["filename"]
            entries.append(
                self._entry_from_path(
                    path,
                    spec["category"],
                    key=key,
                    entry_type=spec["type"],
                )
            )
        return entries

    def _build_pipeline_summary(
        self,
        metadata: RunMetadata,
        artifact_entries: list[ArtifactIndexEntry],
    ) -> PipelineSummary:
        completed_steps: list[str] = ["phase_1_upload_workspace"]
        pending_steps: list[str] = []
        processing = metadata.processing

        if processing.input_inspection:
            completed_steps.append("phase_2a_input_inspection")
        else:
            pending_steps.append("phase_2a_input_inspection")

        if processing.floorplan_preprocess:
            completed_steps.append("phase_2b_floorplan_preprocess")
        else:
            pending_steps.append("phase_2b_floorplan_preprocess")

        if processing.semantic_analysis:
            completed_steps.append("phase_2c_semantic_analysis")
        else:
            pending_steps.append("phase_2c_semantic_analysis")

        if processing.semantic_validation:
            completed_steps.append("phase_2d_semantic_validation")
        else:
            pending_steps.append("phase_2d_semantic_validation")

        interior_artifact_exists = self._artifact_exists(artifact_entries, "interior_analysis")
        interior_validation_artifact_exists = self._artifact_exists(artifact_entries, "interior_analysis_validated")
        if processing.interior_style_analysis or interior_artifact_exists:
            completed_steps.append("phase_3a_interior_style_analysis")
        else:
            pending_steps.append("phase_3a_interior_style_analysis")
        phase_3a_completed = processing.interior_style_analysis or interior_artifact_exists

        if processing.interior_analysis_validation or interior_validation_artifact_exists:
            completed_steps.append("phase_3b_interior_analysis_validation")
        else:
            pending_steps.append("phase_3b_interior_analysis_validation")
        phase_3b_completed = processing.interior_analysis_validation or interior_validation_artifact_exists

        room_function_assignment_exists = self._artifact_exists(artifact_entries, "room_function_assignment")
        if processing.room_function_assignment or room_function_assignment_exists:
            completed_steps.append("phase_3c_room_function_assignment")
        else:
            pending_steps.append("phase_3c_room_function_assignment")
        phase_3c_completed = processing.room_function_assignment or room_function_assignment_exists

        layout_initial_exists = self._artifact_exists(artifact_entries, "layout_initial")
        if processing.layout_initial_creation or layout_initial_exists:
            completed_steps.append("phase_4a_layout_object_creation")
        else:
            pending_steps.append("phase_4a_layout_object_creation")
        phase_4a_completed = processing.layout_initial_creation or layout_initial_exists

        layout_validated_exists = self._artifact_exists(artifact_entries, "layout_validated")
        if processing.layout_validation or layout_validated_exists:
            completed_steps.append("phase_4b_layout_validation")
        else:
            pending_steps.append("phase_4b_layout_validation")
        phase_4b_completed = processing.layout_validation or layout_validated_exists

        layout_furniture_planned_exists = self._artifact_exists(artifact_entries, "layout_furniture_planned")
        if processing.furniture_placement_planning or layout_furniture_planned_exists:
            completed_steps.append("phase_4c_furniture_placement_planning")
        else:
            pending_steps.append("phase_4c_furniture_placement_planning")
        phase_4c_completed = processing.furniture_placement_planning or layout_furniture_planned_exists

        layout_furniture_validated_exists = self._artifact_exists(artifact_entries, "layout_furniture_validated")
        if processing.furniture_placement_validation or layout_furniture_validated_exists:
            completed_steps.append("phase_4d_furniture_placement_validation")
        else:
            pending_steps.append("phase_4d_furniture_placement_validation")
        phase_4d_completed = processing.furniture_placement_validation or layout_furniture_validated_exists

        render_plan_exists = self._artifact_exists(artifact_entries, "render_plan")
        if processing.render_plan_creation or render_plan_exists:
            completed_steps.append("phase_5a_render_plan_creation")
        else:
            pending_steps.append("phase_5a_render_plan_creation")
        phase_5a_completed = processing.render_plan_creation or render_plan_exists

        prompt_package_exists = self._artifact_exists(artifact_entries, "prompt_package")
        if processing.prompt_package_creation or prompt_package_exists:
            completed_steps.append("phase_5b_prompt_package_creation")
        else:
            pending_steps.append("phase_5b_prompt_package_creation")
        phase_5b_completed = processing.prompt_package_creation or prompt_package_exists

        image_generation_request_preview_exists = self._artifact_exists(artifact_entries, "image_generation_request_preview")
        if processing.image_generation_request_preview or image_generation_request_preview_exists:
            completed_steps.append("phase_5c0_image_generation_request_preview")
        else:
            pending_steps.append("phase_5c0_image_generation_request_preview")
        phase_5c0_completed = processing.image_generation_request_preview or image_generation_request_preview_exists

        image_generation_draft_exists = self._artifact_exists(artifact_entries, "image_generation_draft")
        if processing.image_generation_draft or image_generation_draft_exists:
            completed_steps.append("phase_5c1_image_generation_draft")
        else:
            pending_steps.append("phase_5c1_image_generation_draft")
        phase_5c1_completed = processing.image_generation_draft or image_generation_draft_exists

        structure_locked_composite_exists = self._artifact_exists(artifact_entries, "structure_locked_composite")
        if processing.structure_locked_composite_rendering or structure_locked_composite_exists:
            completed_steps.append("phase_6a_structure_locked_composite_renderer")
        else:
            pending_steps.append("phase_6a_structure_locked_composite_renderer")
        phase_6a_completed = processing.structure_locked_composite_rendering or structure_locked_composite_exists

        next_step = pending_steps[0] if pending_steps else None
        return PipelineSummary(
            current_run_status=metadata.run_status,
            phase_1_uploaded=True,
            phase_2a_inspected=processing.input_inspection,
            phase_2b_preprocessed=processing.floorplan_preprocess,
            phase_2c_analyzed=processing.semantic_analysis,
            phase_2d_validated=processing.semantic_validation,
            phase_3a_interior_style_analyzed=phase_3a_completed,
            phase_3a_interior_semantic_analysis=phase_3a_completed,
            phase_3b_interior_analysis_validation=phase_3b_completed,
            phase_3c_room_function_assignment=phase_3c_completed,
            phase_4a_layout_object_creation=phase_4a_completed,
            phase_4b_layout_validation=phase_4b_completed,
            phase_4c_furniture_placement_planning=phase_4c_completed,
            phase_4d_furniture_placement_validation=phase_4d_completed,
            phase_5a_render_plan_creation=phase_5a_completed,
            phase_5b_prompt_package_creation=phase_5b_completed,
            phase_5c0_image_generation_request_preview=phase_5c0_completed,
            phase_5c1_image_generation_draft=phase_5c1_completed,
            phase_6a_structure_locked_composite_renderer=phase_6a_completed,
            artifact_count=len(artifact_entries),
            completed_steps=completed_steps,
            pending_steps=pending_steps,
            next_recommended_step=next_step,
        )

    def _build_input_summary(self, metadata: RunMetadata) -> InputSummary:
        interior_photos = metadata.inputs.interior_photos if metadata.inputs else metadata.interior_photos
        style_groups = metadata.inputs.style_references if metadata.inputs else metadata.style_references
        ideal_count = len(style_groups.ideal)
        acceptable_count = len(style_groups.acceptable)
        ng_count = len(style_groups.ng)
        return InputSummary(
            floorplan_present=bool((metadata.inputs.floorplan if metadata.inputs else metadata.floorplan).relative_path),
            interior_photo_count=len(interior_photos),
            style_reference_ideal_count=ideal_count,
            style_reference_acceptable_count=acceptable_count,
            style_reference_ng_count=ng_count,
            total_style_reference_count=ideal_count + acceptable_count + ng_count,
        )

    def _build_analysis_summary(
        self,
        validated_artifact: FloorplanAnalysisValidatedArtifact | None,
        analysis_artifact: FloorplanSemanticAnalysisArtifact | None,
    ) -> AnalysisSummary | None:
        if validated_artifact is not None:
            quality = validated_artifact.quality_summary
            room_types = [room.type for room in validated_artifact.rooms]
            unique_room_types = list(dict.fromkeys(room_types))
            return AnalysisSummary(
                provider=validated_artifact.provider,
                model=validated_artifact.model,
                apartment_type=validated_artifact.normalized_analysis.get("apartment_type"),
                room_count=quality.room_count,
                room_types=unique_room_types,
                approved_labels_complete=quality.approved_labels_complete,
                needs_manual_review=quality.needs_manual_review,
                warning_count=len(validated_artifact.warnings),
                error_count=len(validated_artifact.errors),
                validation_status=quality.status,
                geometry_summary=validated_artifact.geometry_summary,
            )

        if analysis_artifact is not None:
            analysis = analysis_artifact.analysis or {}
            rooms = analysis.get("rooms") or []
            room_types = []
            for room in rooms:
                if isinstance(room, dict):
                    room_type = room.get("type")
                    if room_type:
                        room_types.append(str(room_type))
            unique_room_types = list(dict.fromkeys(room_types))
            return AnalysisSummary(
                provider=analysis_artifact.provider,
                model=analysis_artifact.model,
                apartment_type=analysis.get("apartment_type"),
                room_count=len(rooms),
                room_types=unique_room_types,
                approved_labels_complete=False,
                needs_manual_review=True,
                warning_count=len(analysis_artifact.warnings),
                error_count=len(analysis_artifact.errors),
                validation_status="unvalidated",
                geometry_summary=None,
            )

        return None

    def _build_interior_analysis_summary(
        self,
        metadata: RunMetadata,
        interior_analysis_artifact: InteriorStyleAnalysisArtifact | None,
    ) -> InteriorAnalysisSummary | None:
        if metadata.interior_analysis_summary is not None:
            return metadata.interior_analysis_summary
        if interior_analysis_artifact is not None:
            return interior_analysis_artifact.summary.model_copy(
                update={
                    "provider": interior_analysis_artifact.provider,
                    "model": interior_analysis_artifact.model,
                }
            )
        return None

    def _build_interior_validation_summary(
        self,
        metadata: RunMetadata,
        interior_validation_artifact,
    ) -> InteriorValidationSummary | None:
        if metadata.interior_validation_summary is not None:
            return metadata.interior_validation_summary
        if interior_validation_artifact is not None:
            quality = interior_validation_artifact.quality
            recommendations = interior_validation_artifact.recommendations_for_next_phase
            return InteriorValidationSummary(
                validation_status=interior_validation_artifact.validation_status,
                floor_tone=str(recommendations.get("suggested_floor_tone") or "unknown"),
                suggested_sofa_type=str(recommendations.get("suggested_sofa_type") or "unknown"),
                suggested_bed_type=str(recommendations.get("suggested_bed_type") or "unknown"),
                furniture_planning_ready=bool(recommendations.get("furniture_planning_ready")),
                overall_confidence=float(quality.get("overall_confidence") or 0.0),
                needs_human_review=bool(quality.get("needs_human_review", True)),
                warnings_count=len(interior_validation_artifact.warnings),
                errors_count=len(interior_validation_artifact.errors),
                )
        return None

    def _build_room_function_assignment_summary(
        self,
        metadata: RunMetadata,
        artifact: RoomFunctionAssignmentArtifact | None,
    ) -> RoomFunctionAssignmentSummary | None:
        if metadata.room_function_assignment_summary is not None:
            return metadata.room_function_assignment_summary
        if artifact is not None:
            media_lounge_room_id = next((room.room_id for room in artifact.rooms if room.functional_role == "media_lounge"), None)
            main_bedroom_room_id = next((room.room_id for room in artifact.rooms if room.functional_role == "main_bedroom"), None)
            western_room_count = sum(1 for room in artifact.rooms if room.semantic_type in {"bedroom", "bed_room"})
            cleanup = artifact.furniture_cleanup_summary
            return RoomFunctionAssignmentSummary(
                assignment_status=artifact.assignment_status,
                western_room_count=western_room_count,
                media_lounge_room_id=media_lounge_room_id,
                main_bedroom_room_id=main_bedroom_room_id,
                dining_zone_assigned=any(room.functional_role in {"living_dining", "dining_zone"} for room in artifact.rooms),
                allowed_furniture_count=int(getattr(cleanup, "allowed_furniture_count", 0) or 0),
                suppressed_furniture_count=int(getattr(cleanup, "suppressed_furniture_count", 0) or 0),
                role_conflict_count=int(getattr(cleanup, "role_conflict_count", 0) or 0),
                needs_human_review=True,
                warnings_count=len(artifact.warnings),
                errors_count=len(artifact.errors),
            )
        return None

    def _build_layout_summary(
        self,
        metadata: RunMetadata,
        layout_initial_artifact: LayoutInitialArtifact | None,
    ) -> LayoutSummary | None:
        if metadata.layout_summary is not None:
            return metadata.layout_summary
        if layout_initial_artifact is not None:
            quality = layout_initial_artifact.quality
            return LayoutSummary(
                layout_status=layout_initial_artifact.layout_status,
                room_count=quality.room_count,
                fixture_count=quality.fixture_count,
                label_count=quality.label_count,
                furniture_suggestion_count=quality.furniture_suggestion_count,
                structure_locked=quality.structure_locked,
                needs_human_review=quality.needs_human_review,
            )
        return None

    def _build_layout_validation_summary(
        self,
        metadata: RunMetadata,
        layout_validated_artifact: LayoutValidationArtifact | None,
    ) -> LayoutValidationSummary | None:
        if metadata.layout_validation_summary is not None:
            return metadata.layout_validation_summary
        if layout_validated_artifact is not None:
            quality = layout_validated_artifact.quality
            return LayoutValidationSummary(
                validation_status=layout_validated_artifact.validation_status,
                room_count=quality.room_count,
                fixture_count=quality.fixture_count,
                label_count=quality.label_count,
                furniture_count=quality.furniture_suggestion_count,
                structure_locked=quality.structure_locked,
                furniture_placement_done=quality.furniture_placement_done,
                needs_human_review=quality.needs_human_review,
                warnings_count=len(layout_validated_artifact.warnings),
                errors_count=len(layout_validated_artifact.errors),
            )
        return None

    def _build_furniture_placement_summary(
        self,
        metadata: RunMetadata,
        artifact: FurniturePlacementArtifact | None,
    ) -> FurniturePlacementSummary | None:
        if metadata.furniture_placement_summary is not None:
            return metadata.furniture_placement_summary
        if artifact is not None:
            quality = artifact.quality
            return FurniturePlacementSummary(
                planning_status=artifact.planning_status,
                furniture_count=quality.furniture_count,
                furniture_placed_count=quality.furniture_placed_count,
                furniture_unplaced_count=quality.furniture_unplaced_count,
                placement_confidence_avg=quality.placement_confidence_avg,
                needs_human_review=quality.needs_human_review,
                warnings_count=len(artifact.warnings),
                errors_count=len(artifact.errors),
            )
        return None

    def _build_furniture_placement_validation_summary(
        self,
        metadata: RunMetadata,
        artifact: FurniturePlacementValidationArtifact | None,
    ) -> FurniturePlacementValidationSummary | None:
        if metadata.furniture_placement_validation_summary is not None:
            return metadata.furniture_placement_validation_summary
        if artifact is not None:
            placement_validation = artifact.placement_validation
            return FurniturePlacementValidationSummary(
                validation_status=artifact.validation_status,
                furniture_count=artifact.quality.furniture_count,
                auto_placed_count=int(placement_validation.get("auto_placed_count", 0)),
                suggested_unplaced_count=int(placement_validation.get("suggested_unplaced_count", 0)),
                invalid_count=int(placement_validation.get("invalid_count", 0)),
                inside_room_count=int(placement_validation.get("inside_room_count", 0)),
                outside_room_count=int(placement_validation.get("outside_room_count", 0)),
                overlap_warning_count=int(placement_validation.get("overlap_warning_count", 0)),
                fixture_overlap_warning_count=int(placement_validation.get("fixture_overlap_warning_count", 0)),
                needs_human_review=artifact.quality.needs_human_review,
                warnings_count=len(artifact.warnings),
                errors_count=len(artifact.errors),
            )
        return None

    def _build_render_plan_summary(
        self,
        metadata: RunMetadata,
        artifact: RenderPlanArtifact | None,
    ) -> RenderPlanSummary | None:
        if metadata.render_plan_summary is not None:
            return metadata.render_plan_summary
        if artifact is not None:
            readiness = artifact.render_readiness
            return RenderPlanSummary(
                render_plan_status=artifact.render_plan_status,
                ready_for_prompt_building=readiness.ready_for_prompt_building,
                ready_for_image_generation=readiness.ready_for_image_generation,
                auto_placed_furniture_count=readiness.auto_placed_furniture_count,
                unplaced_furniture_count=readiness.unplaced_furniture_count,
                label_count=len(artifact.labels),
                room_count=len(artifact.rooms),
                needs_human_review=readiness.requires_human_review,
                warnings_count=len(artifact.warnings),
                errors_count=len(artifact.errors),
            )
        return None

    def _build_prompt_package_summary(
        self,
        metadata: RunMetadata,
        artifact: PromptPackageArtifact | None,
    ) -> PromptPackageSummary | None:
        if metadata.prompt_package_summary is not None:
            return metadata.prompt_package_summary
        if artifact is not None:
            quality = artifact.prompt_quality
            provider_readiness = artifact.provider_readiness
            return PromptPackageSummary(
                prompt_package_status=artifact.prompt_package_status,
                ready_for_openai_image_api=bool(provider_readiness.get("ready_for_openai_image_api")),
                ready_for_manual_review=bool(provider_readiness.get("ready_for_manual_review")),
                combined_prompt_char_count=quality.combined_prompt_char_count,
                negative_prompt_char_count=quality.negative_prompt_char_count,
                drawable_furniture_count=quality.drawable_furniture_count,
                skipped_furniture_count=quality.skipped_furniture_count,
                room_count=quality.room_count,
                label_count=quality.label_count,
                needs_human_review=bool(provider_readiness.get("requires_human_review_before_generation", True)),
                warnings_count=len(artifact.warnings),
                errors_count=len(artifact.errors),
            )
        return None

    def _build_image_generation_request_preview_summary(
        self,
        metadata: RunMetadata,
        artifact: ImageGenerationRequestPreviewArtifact | None,
    ) -> ImageGenerationRequestPreviewSummary | None:
        if metadata.image_generation_request_preview_summary is not None:
            return metadata.image_generation_request_preview_summary
        if artifact is not None:
            quality = artifact.request_quality
            provider = artifact.provider
            safety = artifact.safety_and_cost_controls
            return ImageGenerationRequestPreviewSummary(
                preview_status=artifact.preview_status,
                provider_name=str(provider.get("provider_name") or "openai"),
                model=str(provider.get("model") or "gpt-image-1"),
                request_will_be_sent=bool(provider.get("request_will_be_sent")),
                api_call_performed=bool(provider.get("api_call_performed")),
                requires_manual_approval=bool(safety.get("requires_manual_approval", True)),
                input_image_count=quality.input_image_count,
                prompt_char_count=quality.prompt_char_count,
                provider_requested_size=str(
                    artifact.request_payload_preview.get("size")
                    or artifact.provider_size_policy.get("provider_requested_size")
                    or "1024x1024"
                ),
                final_delivery_size=str(
                    artifact.target_output.get("final_delivery_size")
                    or artifact.postprocess_plan.get("final_delivery_size")
                    or "1200x1200"
                ),
                provider_size_supported=bool(artifact.request_payload_preview.get("provider_size_supported", True)),
                postprocess_required=bool(artifact.postprocess_plan.get("required", True)),
                preview_warnings_count=len(artifact.preview_warnings),
                upstream_warnings_count=len(artifact.upstream_warnings),
                warnings_count=len(artifact.warnings),
                errors_count=len(artifact.errors),
            )
        return None

    def _build_image_generation_draft_summary(
        self,
        metadata: RunMetadata,
        artifact: ImageGenerationDraftArtifact | None,
    ) -> ImageGenerationDraftSummary | None:
        if metadata.image_generation_draft_summary is not None:
            return metadata.image_generation_draft_summary
        if artifact is not None:
            return ImageGenerationDraftSummary(
                draft_status=artifact.draft_status,
                provider_name=str(artifact.provider.get("provider_name") or "openai"),
                model=str(artifact.provider.get("model") or "gpt-image-1"),
                api_call_performed=bool(artifact.provider.get("api_call_performed")),
                provider_size=str(artifact.request.get("provider_size") or "1024x1024"),
                final_delivery_size=str(artifact.request.get("final_delivery_size") or "1200x1200"),
                draft_image_preview_url=artifact.outputs.get("draft_image_preview_url"),
                needs_human_review=bool(artifact.quality.get("needs_human_review", True)),
                ready_for_visual_qa=bool(artifact.quality.get("ready_for_visual_qa", False)),
                warnings_count=len(artifact.warnings),
                errors_count=len(artifact.errors),
            )
        return None

    def _build_structure_locked_composite_summary(
        self,
        metadata: RunMetadata,
        artifact: StructureLockedCompositeArtifact | None,
    ) -> StructureLockedCompositeSummary | None:
        if metadata.structure_locked_composite_summary is not None:
            return metadata.structure_locked_composite_summary
        if artifact is not None:
            return StructureLockedCompositeSummary(
                composite_status=artifact.composite_status,
                composite_image_preview_url=artifact.outputs.get("composite_image_preview_url"),
                width=int(artifact.outputs.get("width") or 1200),
                height=int(artifact.outputs.get("height") or 1200),
                ai_provider_used=bool(artifact.rendering.get("ai_provider_used", False)),
                structure_overlay_applied=bool(artifact.rendering.get("structure_overlay_applied", False)),
                furniture_drawn_count=int(artifact.rendering.get("furniture_drawn_count", 0)),
                furniture_skipped_count=int(artifact.rendering.get("furniture_skipped_count", 0)),
                needs_human_review=bool(artifact.quality.get("needs_human_review", True)),
                warnings_count=len(artifact.warnings),
                errors_count=len(artifact.errors),
            )
        return None

    @staticmethod
    def _artifact_exists(entries: list[ArtifactIndexEntry], key: str) -> bool:
        return any(entry.key == key and entry.exists for entry in entries)

    def _entry_from_relative_path(self, key: str, relative_path: str, category: str) -> ArtifactIndexEntry:
        path = self._resolve_relative_path(relative_path)
        return self._entry_from_path(path, category, key=key)

    def _entry_from_path(
        self,
        path: Path,
        category: str,
        key: str | None = None,
        entry_type: str | None = None,
    ) -> ArtifactIndexEntry:
        relative_path = self._relative_storage_path(path)
        suffix = path.suffix.lower()
        exists = path.exists()
        inferred_type = entry_type or self._infer_entry_type_from_suffix(suffix)
        preview_url = f"/{relative_path}" if exists and suffix in {".png", ".jpg", ".jpeg", ".webp", ".json", ".txt"} else None
        size_bytes = path.stat().st_size if exists else None
        resolved_key = key or path.stem
        resolved_category = category
        return ArtifactIndexEntry(
            key=resolved_key,
            filename=path.name,
            relative_path=relative_path,
            preview_url=preview_url,
            size_bytes=size_bytes,
            content_type=self.ARTIFACT_CONTENT_TYPES.get(suffix),
            type=inferred_type,
            category=resolved_category,
            exists=exists,
        )

    @staticmethod
    def _infer_entry_type_from_suffix(suffix: str) -> str:
        if suffix == ".json":
            return "json"
        if suffix in {".png", ".jpg", ".jpeg", ".webp"}:
            return "image"
        return "unknown"

    def _load_optional_analysis_artifact(self, run_dir: Path) -> FloorplanSemanticAnalysisArtifact | None:
        path = run_dir / "artifacts" / "floorplan_analysis.json"
        if not path.exists():
            return None
        try:
            return FloorplanSemanticAnalysisArtifact.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _load_optional_validated_artifact(self, run_dir: Path) -> FloorplanAnalysisValidatedArtifact | None:
        path = run_dir / "artifacts" / "floorplan_analysis_validated.json"
        if not path.exists():
            return None
        try:
            return FloorplanAnalysisValidatedArtifact.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _load_optional_interior_analysis_artifact(self, run_dir: Path) -> InteriorStyleAnalysisArtifact | None:
        path = run_dir / "artifacts" / "interior_analysis.json"
        if not path.exists():
            return None
        try:
            return InteriorStyleAnalysisArtifact.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _load_optional_interior_validation_artifact(self, run_dir: Path):
        from app.schemas.run import InteriorAnalysisValidatedArtifact

        path = run_dir / "artifacts" / "interior_analysis_validated.json"
        if not path.exists():
            return None
        try:
            return InteriorAnalysisValidatedArtifact.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _load_optional_room_function_assignment_artifact(self, run_dir: Path) -> RoomFunctionAssignmentArtifact | None:
        path = run_dir / "artifacts" / "room_function_assignment.json"
        if not path.exists():
            return None
        try:
            return RoomFunctionAssignmentArtifact.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _load_optional_layout_initial_artifact(self, run_dir: Path) -> LayoutInitialArtifact | None:
        path = run_dir / "artifacts" / "layout_initial.json"
        if not path.exists():
            return None
        try:
            return LayoutInitialArtifact.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _load_optional_layout_validated_artifact(self, run_dir: Path) -> LayoutValidationArtifact | None:
        path = run_dir / "artifacts" / "layout_validated.json"
        if not path.exists():
            return None
        try:
            return LayoutValidationArtifact.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _load_optional_layout_furniture_planned_artifact(self, run_dir: Path) -> FurniturePlacementArtifact | None:
        path = run_dir / "artifacts" / "layout_furniture_planned.json"
        if not path.exists():
            return None
        try:
            return FurniturePlacementArtifact.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _load_optional_layout_furniture_validated_artifact(self, run_dir: Path) -> FurniturePlacementValidationArtifact | None:
        path = run_dir / "artifacts" / "layout_furniture_validated.json"
        if not path.exists():
            return None
        try:
            return FurniturePlacementValidationArtifact.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _load_optional_render_plan_artifact(self, run_dir: Path) -> RenderPlanArtifact | None:
        path = run_dir / "artifacts" / "render_plan.json"
        if not path.exists():
            return None
        try:
            return RenderPlanArtifact.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _load_optional_prompt_package_artifact(self, run_dir: Path) -> PromptPackageArtifact | None:
        path = run_dir / "artifacts" / "prompt_package.json"
        if not path.exists():
            return None
        try:
            return PromptPackageArtifact.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _load_optional_image_generation_request_preview_artifact(self, run_dir: Path) -> ImageGenerationRequestPreviewArtifact | None:
        path = run_dir / "artifacts" / "image_generation_request_preview.json"
        if not path.exists():
            return None
        try:
            return ImageGenerationRequestPreviewArtifact.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _load_optional_image_generation_draft_artifact(self, run_dir: Path) -> ImageGenerationDraftArtifact | None:
        path = run_dir / "artifacts" / "image_generation_draft.json"
        if not path.exists():
            return None
        try:
            return ImageGenerationDraftArtifact.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _load_optional_structure_locked_composite_artifact(self, run_dir: Path) -> StructureLockedCompositeArtifact | None:
        path = run_dir / "artifacts" / "structure_locked_composite.json"
        if not path.exists():
            return None
        try:
            return StructureLockedCompositeArtifact.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

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

    def _relative_storage_path(self, path: Path) -> str:
        return path.relative_to(self.storage_dir.parent).as_posix()

    def _artifacts_dir(self, metadata: RunMetadata, run_dir: Path) -> Path:
        if metadata.workspace and metadata.workspace.artifacts_dir:
            return self._resolve_relative_path(metadata.workspace.artifacts_dir)
        return run_dir / "artifacts"

    def _outputs_dir(self, metadata: RunMetadata, run_dir: Path) -> Path:
        if metadata.workspace and metadata.workspace.outputs_dir:
            return self._resolve_relative_path(metadata.workspace.outputs_dir)
        return run_dir / "outputs"

    @staticmethod
    def _write_json(path: Path, payload: dict) -> None:
        try:
            with path.open("w", encoding="utf-8") as output_file:
                json.dump(payload, output_file, ensure_ascii=False, indent=2)
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"failed to write {path.name}") from exc
