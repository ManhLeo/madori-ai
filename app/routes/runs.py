from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.config import get_settings
from app.schemas.run import (
    FloorplanAnalysisValidatedArtifact,
    FloorplanPreprocessReport,
    FloorplanSemanticAnalysisArtifact,
    ImageGenerationDraftArtifact,
    ImageGenerationDraftRequest,
    FurniturePlacementArtifact,
    FurniturePlacementValidationArtifact,
    InputManifest,
    ImageGenerationRequestPreviewArtifact,
    InteriorAnalysisValidatedArtifact,
    InteriorStyleAnalysisArtifact,
    LayoutInitialArtifact,
    LayoutValidationArtifact,
    PromptPackageArtifact,
    RenderPlanArtifact,
    RoomFunctionAssignmentArtifact,
    RunArtifactIndex,
    RunCreateResponse,
    RunMetadataSummary,
    RunMetadataResponse,
    StructureLockedCompositeArtifact,
)
from app.services.floorplan_analysis_service import FloorplanAnalysisService
from app.services.floorplan_analysis_validation_service import FloorplanAnalysisValidationService
from app.services.floorplan_preprocess_service import FloorplanPreprocessService
from app.services.furniture_placement_service import FurniturePlacementService
from app.services.furniture_placement_validation_service import FurniturePlacementValidationService
from app.services.interior_analysis_service import InteriorAnalysisService
from app.services.interior_analysis_validation_service import InteriorAnalysisValidationService
from app.services.input_inspection_service import InputInspectionService
from app.services.layout_creation_service import LayoutCreationService
from app.services.layout_validation_service import LayoutValidationService
from app.services.image_generation_request_preview_service import ImageGenerationRequestPreviewService
from app.services.image_generation_draft_service import ImageGenerationDraftService
from app.services.prompt_package_service import PromptPackageService
from app.services.render_plan_service import RenderPlanService
from app.services.room_function_assignment_service import RoomFunctionAssignmentService
from app.services.run_index_service import RunIndexService
from app.services.run_service import RunService, to_create_response
from app.services.structure_locked_composite_renderer import StructureLockedCompositeRenderer


router = APIRouter(prefix="/api", tags=["phase-1-runs"])


@router.post("/runs", response_model=RunCreateResponse)
def create_run(
    floorplan: UploadFile | None = File(None),
    interior_photos: list[UploadFile] | None = File(None),
    style_reference_images: list[UploadFile] | None = File(None),
    ideal_style_reference: UploadFile | None = File(None),
    acceptable_style_reference: UploadFile | None = File(None),
    ng_style_reference: UploadFile | None = File(None),
) -> RunCreateResponse:
    """Create a Phase 1 upload run without AI analysis or image generation."""
    if not floorplan:
        raise HTTPException(status_code=400, detail="floorplan file is required")

    settings = get_settings()
    run_service = RunService(settings.storage_runs_dir)
    metadata = run_service.create_run(
        floorplan,
        interior_photos,
        style_reference_images=style_reference_images,
        ideal_style_reference=ideal_style_reference,
        acceptable_style_reference=acceptable_style_reference,
        ng_style_reference=ng_style_reference,
    )
    return to_create_response(metadata)


@router.get("/runs/{run_id}/metadata", response_model=RunMetadataResponse)
def get_run_metadata(run_id: str) -> RunMetadataResponse:
    settings = get_settings()
    run_service = RunService(settings.storage_runs_dir)
    metadata = run_service.load_metadata(run_id)
    return RunMetadataResponse(status="ok", run_id=run_id, metadata=metadata)


@router.post("/runs/{run_id}/inspect", response_model=InputManifest)
def inspect_run_inputs(run_id: str) -> InputManifest:
    settings = get_settings()
    run_service = RunService(settings.storage_runs_dir)
    metadata = run_service.load_metadata(run_id)
    inspection_service = InputInspectionService(settings.storage_dir, settings.storage_runs_dir)
    manifest = inspection_service.inspect_run(metadata)
    run_service.mark_input_inspected(metadata)
    return manifest


@router.get("/runs/{run_id}/artifacts/input_manifest", response_model=InputManifest)
def get_input_manifest(run_id: str) -> InputManifest:
    settings = get_settings()
    inspection_service = InputInspectionService(settings.storage_dir, settings.storage_runs_dir)
    return inspection_service.load_manifest(run_id)


@router.post("/runs/{run_id}/preprocess-floorplan", response_model=FloorplanPreprocessReport)
def preprocess_floorplan(run_id: str) -> FloorplanPreprocessReport:
    settings = get_settings()
    run_service = RunService(settings.storage_runs_dir)
    metadata = run_service.load_metadata(run_id)
    preprocess_service = FloorplanPreprocessService(settings.storage_dir, settings.storage_runs_dir)
    report = preprocess_service.preprocess_floorplan(metadata)
    run_service.mark_floorplan_preprocessed(metadata)
    return report


@router.post("/runs/{run_id}/analyze-floorplan", response_model=FloorplanSemanticAnalysisArtifact)
def analyze_floorplan_semantics(run_id: str) -> FloorplanSemanticAnalysisArtifact:
    settings = get_settings()
    run_service = RunService(settings.storage_runs_dir)
    metadata = run_service.load_metadata(run_id)
    analysis_service = FloorplanAnalysisService(settings.storage_dir, settings.storage_runs_dir)
    artifact = analysis_service.analyze_run(metadata)
    run_service.mark_semantic_analysis_completed(metadata, analysis_provider=artifact.provider, analysis_model=artifact.model)
    return artifact


@router.get("/runs/{run_id}/artifacts/floorplan_analysis", response_model=FloorplanSemanticAnalysisArtifact)
def get_floorplan_analysis_artifact(run_id: str) -> FloorplanSemanticAnalysisArtifact:
    settings = get_settings()
    analysis_service = FloorplanAnalysisService(settings.storage_dir, settings.storage_runs_dir)
    return analysis_service.load_analysis_artifact(run_id)


@router.post("/runs/{run_id}/validate-floorplan-analysis", response_model=FloorplanAnalysisValidatedArtifact)
def validate_floorplan_analysis(run_id: str) -> FloorplanAnalysisValidatedArtifact:
    settings = get_settings()
    run_service = RunService(settings.storage_runs_dir)
    metadata = run_service.load_metadata(run_id)
    validation_service = FloorplanAnalysisValidationService(settings.storage_dir, settings.storage_runs_dir)
    artifact = validation_service.validate_run(metadata)
    run_service.mark_semantic_validation_completed(metadata)
    return artifact


@router.get("/runs/{run_id}/artifacts/floorplan_analysis_validated", response_model=FloorplanAnalysisValidatedArtifact)
def get_validated_floorplan_analysis_artifact(run_id: str) -> FloorplanAnalysisValidatedArtifact:
    settings = get_settings()
    validation_service = FloorplanAnalysisValidationService(settings.storage_dir, settings.storage_runs_dir)
    return validation_service.load_validated_artifact(run_id)


@router.post("/runs/{run_id}/index-artifacts", response_model=RunMetadataSummary)
def index_run_artifacts(run_id: str) -> RunMetadataSummary:
    settings = get_settings()
    run_service = RunService(settings.storage_runs_dir)
    metadata = run_service.load_metadata(run_id)
    index_service = RunIndexService(settings.storage_dir, settings.storage_runs_dir)
    summary = index_service.index_run(metadata)
    run_service.update_metadata_summary(metadata, summary)
    return summary


@router.get("/runs/{run_id}/artifacts/artifact_index", response_model=RunArtifactIndex)
def get_run_artifact_index(run_id: str) -> RunArtifactIndex:
    settings = get_settings()
    index_service = RunIndexService(settings.storage_dir, settings.storage_runs_dir)
    return index_service.load_artifact_index(run_id)


@router.get("/runs/{run_id}/summary", response_model=RunMetadataSummary)
def get_run_metadata_summary(run_id: str) -> RunMetadataSummary:
    settings = get_settings()
    index_service = RunIndexService(settings.storage_dir, settings.storage_runs_dir)
    return index_service.load_summary(run_id)


@router.post("/runs/{run_id}/analyze-interiors", response_model=InteriorStyleAnalysisArtifact)
def analyze_interior_style_semantics(run_id: str) -> InteriorStyleAnalysisArtifact:
    settings = get_settings()
    run_service = RunService(settings.storage_runs_dir)
    metadata = run_service.load_metadata(run_id)
    interior_service = InteriorAnalysisService(settings.storage_dir, settings.storage_runs_dir)
    artifact = interior_service.analyze_run(metadata)
    run_service.mark_interior_style_analysis_completed(
        metadata,
        interior_analysis_path=f"storage/runs/{run_id}/artifacts/interior_analysis.json",
        interior_analysis_summary=interior_service.build_summary(artifact),
    )
    return artifact


@router.get("/runs/{run_id}/artifacts/interior_analysis", response_model=InteriorStyleAnalysisArtifact)
def get_interior_style_analysis_artifact(run_id: str) -> InteriorStyleAnalysisArtifact:
    settings = get_settings()
    interior_service = InteriorAnalysisService(settings.storage_dir, settings.storage_runs_dir)
    return interior_service.load_artifact(run_id)


@router.post("/runs/{run_id}/validate-interior-analysis", response_model=InteriorAnalysisValidatedArtifact)
def validate_interior_analysis(run_id: str) -> InteriorAnalysisValidatedArtifact:
    settings = get_settings()
    run_service = RunService(settings.storage_runs_dir)
    metadata = run_service.load_metadata(run_id)
    validation_service = InteriorAnalysisValidationService(settings.storage_dir, settings.storage_runs_dir)
    artifact = validation_service.validate_run(metadata)
    metadata_updates = validation_service.build_metadata_updates(metadata, artifact)
    run_service.apply_interior_validation_updates(metadata, metadata_updates)
    return artifact


@router.get("/runs/{run_id}/artifacts/interior_analysis_validated", response_model=InteriorAnalysisValidatedArtifact)
def get_validated_interior_analysis_artifact(run_id: str) -> InteriorAnalysisValidatedArtifact:
    settings = get_settings()
    validation_service = InteriorAnalysisValidationService(settings.storage_dir, settings.storage_runs_dir)
    return validation_service.load_interior_analysis(run_id)


@router.post("/runs/{run_id}/assign-room-functions", response_model=RoomFunctionAssignmentArtifact)
def assign_room_functions(run_id: str) -> RoomFunctionAssignmentArtifact:
    settings = get_settings()
    run_service = RunService(settings.storage_runs_dir)
    metadata = run_service.load_metadata(run_id)
    assignment_service = RoomFunctionAssignmentService(settings.storage_dir, settings.storage_runs_dir)
    artifact = assignment_service.assign_room_functions(metadata)
    metadata_updates = assignment_service.build_metadata_updates(metadata, artifact)
    run_service.apply_room_function_assignment_updates(metadata, metadata_updates)
    return artifact


@router.get("/runs/{run_id}/artifacts/room_function_assignment", response_model=RoomFunctionAssignmentArtifact)
def get_room_function_assignment_artifact(run_id: str) -> RoomFunctionAssignmentArtifact:
    settings = get_settings()
    assignment_service = RoomFunctionAssignmentService(settings.storage_dir, settings.storage_runs_dir)
    return assignment_service.load_room_function_assignment(run_id)


@router.post("/runs/{run_id}/create-initial-layout", response_model=LayoutInitialArtifact)
def create_initial_layout(run_id: str) -> LayoutInitialArtifact:
    settings = get_settings()
    run_service = RunService(settings.storage_runs_dir)
    metadata = run_service.load_metadata(run_id)
    layout_service = LayoutCreationService(settings.storage_dir, settings.storage_runs_dir)
    artifact = layout_service.create_initial_layout(metadata)
    metadata_updates = layout_service.build_metadata_updates(metadata, artifact)
    run_service.apply_layout_creation_updates(metadata, metadata_updates)
    return artifact


@router.get("/runs/{run_id}/artifacts/layout_initial", response_model=LayoutInitialArtifact)
def get_layout_initial_artifact(run_id: str) -> LayoutInitialArtifact:
    settings = get_settings()
    layout_service = LayoutCreationService(settings.storage_dir, settings.storage_runs_dir)
    return layout_service.load_layout_initial(run_id)


@router.post("/runs/{run_id}/validate-layout", response_model=LayoutValidationArtifact)
def validate_layout(run_id: str) -> LayoutValidationArtifact:
    settings = get_settings()
    run_service = RunService(settings.storage_runs_dir)
    metadata = run_service.load_metadata(run_id)
    validation_service = LayoutValidationService(settings.storage_dir, settings.storage_runs_dir)
    artifact = validation_service.validate_layout(metadata)
    metadata_updates = validation_service.build_metadata_updates(metadata, artifact)
    run_service.apply_layout_validation_updates(metadata, metadata_updates)
    return artifact


@router.get("/runs/{run_id}/artifacts/layout_validated", response_model=LayoutValidationArtifact)
def get_layout_validated_artifact(run_id: str) -> LayoutValidationArtifact:
    settings = get_settings()
    validation_service = LayoutValidationService(settings.storage_dir, settings.storage_runs_dir)
    return validation_service.load_layout_validated(run_id)


@router.post("/runs/{run_id}/plan-furniture-placement", response_model=FurniturePlacementArtifact)
def plan_furniture_placement(run_id: str) -> FurniturePlacementArtifact:
    settings = get_settings()
    run_service = RunService(settings.storage_runs_dir)
    metadata = run_service.load_metadata(run_id)
    placement_service = FurniturePlacementService(settings.storage_dir, settings.storage_runs_dir)
    artifact = placement_service.plan_furniture_placement(metadata)
    metadata_updates = placement_service.build_metadata_updates(metadata, artifact)
    run_service.apply_furniture_placement_updates(metadata, metadata_updates)
    return artifact


@router.get("/runs/{run_id}/artifacts/layout_furniture_planned", response_model=FurniturePlacementArtifact)
def get_layout_furniture_planned_artifact(run_id: str) -> FurniturePlacementArtifact:
    settings = get_settings()
    placement_service = FurniturePlacementService(settings.storage_dir, settings.storage_runs_dir)
    return placement_service.load_layout_furniture_planned(run_id)


@router.post("/runs/{run_id}/validate-furniture-placement", response_model=FurniturePlacementValidationArtifact)
def validate_furniture_placement(run_id: str) -> FurniturePlacementValidationArtifact:
    settings = get_settings()
    run_service = RunService(settings.storage_runs_dir)
    metadata = run_service.load_metadata(run_id)
    validation_service = FurniturePlacementValidationService(settings.storage_dir, settings.storage_runs_dir)
    artifact = validation_service.validate_furniture_placement(metadata)
    metadata_updates = validation_service.build_metadata_updates(metadata, artifact)
    run_service.apply_furniture_placement_validation_updates(metadata, metadata_updates)
    return artifact


@router.get("/runs/{run_id}/artifacts/layout_furniture_validated", response_model=FurniturePlacementValidationArtifact)
def get_layout_furniture_validated_artifact(run_id: str) -> FurniturePlacementValidationArtifact:
    settings = get_settings()
    validation_service = FurniturePlacementValidationService(settings.storage_dir, settings.storage_runs_dir)
    return validation_service.load_layout_furniture_validated(run_id)


@router.post("/runs/{run_id}/create-render-plan", response_model=RenderPlanArtifact)
def create_render_plan(run_id: str) -> RenderPlanArtifact:
    settings = get_settings()
    run_service = RunService(settings.storage_runs_dir)
    metadata = run_service.load_metadata(run_id)
    render_plan_service = RenderPlanService(settings.storage_dir, settings.storage_runs_dir)
    artifact = render_plan_service.create_render_plan(metadata)
    metadata_updates = render_plan_service.build_metadata_updates(metadata, artifact)
    run_service.apply_render_plan_updates(metadata, metadata_updates)
    return artifact


@router.get("/runs/{run_id}/artifacts/render_plan", response_model=RenderPlanArtifact)
def get_render_plan_artifact(run_id: str) -> RenderPlanArtifact:
    settings = get_settings()
    render_plan_service = RenderPlanService(settings.storage_dir, settings.storage_runs_dir)
    return render_plan_service.load_render_plan(run_id)


@router.post("/runs/{run_id}/create-prompt-package", response_model=PromptPackageArtifact)
def create_prompt_package(run_id: str) -> PromptPackageArtifact:
    settings = get_settings()
    run_service = RunService(settings.storage_runs_dir)
    metadata = run_service.load_metadata(run_id)
    prompt_package_service = PromptPackageService(settings.storage_dir, settings.storage_runs_dir)
    artifact = prompt_package_service.create_prompt_package(metadata)
    metadata_updates = prompt_package_service.build_metadata_updates(metadata, artifact)
    run_service.apply_prompt_package_updates(metadata, metadata_updates)
    return artifact


@router.get("/runs/{run_id}/artifacts/prompt_package", response_model=PromptPackageArtifact)
def get_prompt_package_artifact(run_id: str) -> PromptPackageArtifact:
    settings = get_settings()
    prompt_package_service = PromptPackageService(settings.storage_dir, settings.storage_runs_dir)
    return prompt_package_service.load_prompt_package(run_id)


@router.post("/runs/{run_id}/preview-image-generation-request", response_model=ImageGenerationRequestPreviewArtifact)
def preview_image_generation_request(run_id: str) -> ImageGenerationRequestPreviewArtifact:
    settings = get_settings()
    run_service = RunService(settings.storage_runs_dir)
    metadata = run_service.load_metadata(run_id)
    preview_service = ImageGenerationRequestPreviewService(settings.storage_dir, settings.storage_runs_dir)
    artifact = preview_service.create_image_generation_request_preview(metadata)
    metadata_updates = preview_service.build_metadata_updates(metadata, artifact)
    run_service.apply_image_generation_request_preview_updates(metadata, metadata_updates)
    return artifact


@router.get("/runs/{run_id}/artifacts/image_generation_request_preview", response_model=ImageGenerationRequestPreviewArtifact)
def get_image_generation_request_preview(run_id: str) -> ImageGenerationRequestPreviewArtifact:
    settings = get_settings()
    preview_service = ImageGenerationRequestPreviewService(settings.storage_dir, settings.storage_runs_dir)
    return preview_service.load_image_generation_request_preview(run_id)


@router.post("/runs/{run_id}/generate-image-draft", response_model=ImageGenerationDraftArtifact)
def generate_image_draft(run_id: str, request: ImageGenerationDraftRequest) -> ImageGenerationDraftArtifact:
    settings = get_settings()
    run_service = RunService(settings.storage_runs_dir)
    metadata = run_service.load_metadata(run_id)
    draft_service = ImageGenerationDraftService(settings.storage_dir, settings.storage_runs_dir)
    artifact = draft_service.generate_image_draft(metadata, request)
    metadata_updates = draft_service.build_metadata_updates(metadata, artifact)
    run_service.apply_image_generation_draft_updates(metadata, metadata_updates)
    return artifact


@router.get("/runs/{run_id}/artifacts/image_generation_draft", response_model=ImageGenerationDraftArtifact)
def get_image_generation_draft(run_id: str) -> ImageGenerationDraftArtifact:
    settings = get_settings()
    draft_service = ImageGenerationDraftService(settings.storage_dir, settings.storage_runs_dir)
    return draft_service.load_image_generation_draft(run_id)


@router.post("/runs/{run_id}/render-structure-locked-composite", response_model=StructureLockedCompositeArtifact)
def render_structure_locked_composite(run_id: str) -> StructureLockedCompositeArtifact:
    settings = get_settings()
    run_service = RunService(settings.storage_runs_dir)
    metadata = run_service.load_metadata(run_id)
    composite_service = StructureLockedCompositeRenderer(settings.storage_dir, settings.storage_runs_dir)
    artifact = composite_service.render_structure_locked_composite(metadata)
    metadata_updates = composite_service.build_metadata_updates(metadata, artifact)
    run_service.apply_structure_locked_composite_updates(metadata, metadata_updates)
    return artifact


@router.get("/runs/{run_id}/artifacts/structure_locked_composite", response_model=StructureLockedCompositeArtifact)
def get_structure_locked_composite(run_id: str) -> StructureLockedCompositeArtifact:
    settings = get_settings()
    composite_service = StructureLockedCompositeRenderer(settings.storage_dir, settings.storage_runs_dir)
    return composite_service.load_structure_locked_composite(run_id)
