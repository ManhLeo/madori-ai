from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, model_validator


class UploadedFileMetadata(BaseModel):
    role: str
    category: str | None = None
    reference_type: str | None = None
    stored_filename: str
    original_filename: str | None = None
    mime_type: str
    size_bytes: int
    relative_path: str
    preview_url: str
    filename: str | None = None
    content_type: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _backfill_file_field_names(cls, data):
        if not isinstance(data, dict):
            return data
        if "stored_filename" not in data and "filename" in data:
            data["stored_filename"] = data["filename"]
        if "filename" not in data and "stored_filename" in data:
            data["filename"] = data["stored_filename"]
        if "mime_type" not in data and "content_type" in data:
            data["mime_type"] = data["content_type"]
        if "content_type" not in data and "mime_type" in data:
            data["content_type"] = data["mime_type"]
        return data


class WorkspacePaths(BaseModel):
    root: str
    source_dir: str
    floorplan_dir: str
    interior_photos_dir: str
    style_references_dir: str
    artifacts_dir: str
    outputs_dir: str


class RunInputs(BaseModel):
    floorplan: UploadedFileMetadata
    interior_photos: list[UploadedFileMetadata] = Field(default_factory=list)
    style_references: "StyleReferenceGroups" = Field(default_factory=lambda: StyleReferenceGroups())


class StyleReferenceGroups(BaseModel):
    ideal: list[UploadedFileMetadata] = Field(default_factory=list)
    acceptable: list[UploadedFileMetadata] = Field(default_factory=list)
    ng: list[UploadedFileMetadata] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _from_legacy_list(cls, data):
        if not isinstance(data, list):
            return data
        grouped = {"ideal": [], "acceptable": [], "ng": []}
        for item in data:
            if not isinstance(item, dict):
                continue
            reference_type = item.get("reference_type")
            if reference_type in grouped:
                grouped[reference_type].append(item)
        return grouped


class ImageInspectionMetadata(BaseModel):
    width: int | None = None
    height: int | None = None
    format: str | None = None
    mode: str | None = None
    aspect_ratio: float | None = None
    size_bytes: int
    relative_path: str
    preview_url: str
    original_filename: str | None = None
    stored_filename: str | None = None
    mime_type: str | None = None
    error: str | None = None


class StyleReferenceInspectionGroups(BaseModel):
    ideal: list[ImageInspectionMetadata] = Field(default_factory=list)
    acceptable: list[ImageInspectionMetadata] = Field(default_factory=list)
    ng: list[ImageInspectionMetadata] = Field(default_factory=list)


class InputManifest(BaseModel):
    schema_version: str = "1.0"
    run_id: str
    generated_at: datetime
    floorplan: ImageInspectionMetadata | None = None
    interior_photos: list[ImageInspectionMetadata] = Field(default_factory=list)
    style_references: StyleReferenceInspectionGroups = Field(default_factory=StyleReferenceInspectionGroups)
    checks: dict[str, bool] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class PreprocessImageArtifact(BaseModel):
    relative_path: str
    preview_url: str
    width: int
    height: int
    mode: str
    format: str = "PNG"
    size_bytes: int


class FloorplanPreprocessReport(BaseModel):
    schema_version: str = "1.0"
    run_id: str
    generated_at: datetime
    source_floorplan: ImageInspectionMetadata
    output_size: dict[str, int]
    normalization: dict[str, int | float | str]
    artifacts: dict[str, PreprocessImageArtifact]
    checks: dict[str, bool] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class SemanticSourceImage(BaseModel):
    relative_path: str
    preview_url: str
    width: int | None = None
    height: int | None = None
    format: str | None = None
    mode: str | None = None


class FloorplanSemanticAnalysisArtifact(BaseModel):
    schema_version: str = "1.0"
    run_id: str
    generated_at: datetime
    provider: str
    model: str | None = None
    source_image: SemanticSourceImage
    analysis: dict
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class ValidatedRoomRecord(BaseModel):
    id: str
    type: str
    approved_label: str
    source_label: str | None = None
    position: str = "unknown"
    size: str | None = None
    bbox: "LayoutBoundingBox | None" = None
    approx_bbox: "LayoutBoundingBox | None" = None
    bounding_box: "LayoutBoundingBox | None" = None
    polygon: list[list[float]] | None = None
    confidence: float = 0.0
    geometry_confidence: float = 0.0
    geometry_notes: list[str] = Field(default_factory=list)
    connected_to: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _backfill_bbox_fields(cls, data):
        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        bbox = normalized.get("bbox") or normalized.get("bounding_box")
        approx_bbox = normalized.get("approx_bbox") or normalized.get("approximate_bbox")
        if "bbox" not in normalized:
            normalized["bbox"] = bbox
        if "bounding_box" not in normalized:
            normalized["bounding_box"] = bbox
        if "approx_bbox" not in normalized:
            normalized["approx_bbox"] = approx_bbox
        if normalized.get("geometry_notes") is None:
            normalized["geometry_notes"] = []
        return normalized


class ValidatedFixtureRecord(BaseModel):
    id: str
    type: str
    approved_label: str
    source_room_id: str | None = None
    source_room_type: str | None = None
    position: str | None = None
    bbox: "LayoutBoundingBox | None" = None
    approx_bbox: "LayoutBoundingBox | None" = None
    polygon: list[list[float]] | None = None
    confidence: float = 0.0
    geometry_confidence: float = 0.0
    geometry_notes: list[str] = Field(default_factory=list)


class ValidatedDoorRecord(BaseModel):
    id: str
    position: str = "unknown"
    connects: list[str] = Field(default_factory=list)
    has_unknown_connection: bool = False
    bbox: "LayoutBoundingBox | None" = None
    approx_bbox: "LayoutBoundingBox | None" = None
    polygon: list[list[float]] | None = None
    confidence: float = 0.0
    geometry_confidence: float = 0.0
    geometry_notes: list[str] = Field(default_factory=list)


class ValidatedWindowRecord(BaseModel):
    id: str
    position: str = "unknown"
    room_id: str | None = None
    room_type: str | None = None
    approved_label: str | None = None
    bbox: "LayoutBoundingBox | None" = None
    approx_bbox: "LayoutBoundingBox | None" = None
    polygon: list[list[float]] | None = None
    confidence: float = 0.0
    geometry_confidence: float = 0.0
    geometry_notes: list[str] = Field(default_factory=list)


class ValidatedLabelRecord(BaseModel):
    id: str
    label_type: str = "room"
    source_text: str | None = None
    approved_text: str
    room_id: str | None = None
    room_type: str | None = None
    position: str | None = None


class ValidatedDimensionRecord(BaseModel):
    id: str
    room_id: str | None = None
    raw_value: str | None = None
    parsed_value: float | None = None
    unit: str | None = None
    status: str = "missing"


class AnalysisQualitySummary(BaseModel):
    room_count: int = 0
    fixture_count: int = 0
    door_count: int = 0
    window_count: int = 0
    label_count: int = 0
    dimension_count: int = 0
    approved_labels_complete: bool = False
    unknown_room_count: int = 0
    door_unknown_connection_count: int = 0
    window_unassigned_count: int = 0
    dimension_missing_count: int = 0
    needs_manual_review: bool = True
    status: str = "needs_review"


class GeometrySummary(BaseModel):
    room_count: int = 0
    rooms_with_bbox: int = 0
    rooms_missing_bbox: int = 0
    fixture_count: int = 0
    fixtures_with_bbox: int = 0
    geometry_ready_for_furniture_planning: bool = False


class FloorplanAnalysisValidatedArtifact(BaseModel):
    schema_version: str = "1.0"
    run_id: str
    generated_at: datetime
    source_analysis_path: str
    provider: str | None = None
    model: str | None = None
    source_image: SemanticSourceImage | None = None
    normalized_analysis: dict
    approved_label_map: dict[str, str] = Field(default_factory=dict)
    rooms: list[ValidatedRoomRecord] = Field(default_factory=list)
    fixtures: list[ValidatedFixtureRecord] = Field(default_factory=list)
    doors: list[ValidatedDoorRecord] = Field(default_factory=list)
    windows: list[ValidatedWindowRecord] = Field(default_factory=list)
    labels: list[ValidatedLabelRecord] = Field(default_factory=list)
    dimensions: list[ValidatedDimensionRecord] = Field(default_factory=list)
    checks: dict[str, bool] = Field(default_factory=dict)
    quality_summary: AnalysisQualitySummary = Field(default_factory=AnalysisQualitySummary)
    geometry_summary: GeometrySummary | None = None
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class InteriorObjectSemanticRecord(BaseModel):
    object_type: str
    color: str | None = None
    material: str | None = None
    count: int = 1
    notes: str | None = None
    source: str | None = None


class BedSemanticRecord(BaseModel):
    present: bool = False
    pillow_count: int | None = None
    inferred_bed_type: str | None = None
    base_color: str = "white"
    cushion_colors: list[str] = Field(default_factory=list)


class SofaSemanticRecord(BaseModel):
    present: bool = False
    base_color: str = "white"
    cushion_colors: list[str] = Field(default_factory=list)


class InteriorPhotoSemanticRecord(BaseModel):
    source_image: ImageInspectionMetadata
    room_context: str = "unknown"
    floor_color_category: str = "unknown"
    detected_objects: list[InteriorObjectSemanticRecord] = Field(default_factory=list)
    dominant_colors: list[str] = Field(default_factory=list)
    dominant_materials: list[str] = Field(default_factory=list)
    bed: BedSemanticRecord | None = None
    sofa: SofaSemanticRecord | None = None
    notes: list[str] = Field(default_factory=list)


class StyleReferenceSemanticRecord(BaseModel):
    source_image: ImageInspectionMetadata
    reference_type: str
    watercolor_strength: str = "medium"
    linework_style: str = "clean"
    palette_keywords: list[str] = Field(default_factory=list)
    positive_cues: list[str] = Field(default_factory=list)
    avoid_cues: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class InteriorStyleReferenceAnalysisGroups(BaseModel):
    ideal: list[StyleReferenceSemanticRecord] = Field(default_factory=list)
    acceptable: list[StyleReferenceSemanticRecord] = Field(default_factory=list)
    ng: list[StyleReferenceSemanticRecord] = Field(default_factory=list)


class DerivedInteriorStyleProfile(BaseModel):
    preferred_floor_color: str = "unknown"
    bed_base_color: str = "white"
    sofa_base_color: str = "white"
    inferred_bed_type: str | None = None
    accent_colors: list[str] = Field(default_factory=list)
    cushion_colors: list[str] = Field(default_factory=list)
    preferred_materials: list[str] = Field(default_factory=list)
    style_positive_cues: list[str] = Field(default_factory=list)
    style_acceptable_cues: list[str] = Field(default_factory=list)
    style_avoid_cues: list[str] = Field(default_factory=list)


class InteriorAnalysisSummary(BaseModel):
    provider: str | None = None
    model: str | None = None
    interior_photo_count: int = 0
    style_reference_count: int = 0
    preferred_floor_color: str = "unknown"
    inferred_bed_type: str | None = None
    accent_colors: list[str] = Field(default_factory=list)
    style_positive_cues: list[str] = Field(default_factory=list)
    style_avoid_cues: list[str] = Field(default_factory=list)


class InteriorValidationSummary(BaseModel):
    validation_status: str = "failed"
    floor_tone: str = "unknown"
    suggested_sofa_type: str = "unknown"
    suggested_bed_type: str = "unknown"
    furniture_planning_ready: bool = False
    overall_confidence: float = 0.0
    needs_human_review: bool = True
    warnings_count: int = 0
    errors_count: int = 0


class RoomFunctionAssignmentRoomRecord(BaseModel):
    room_id: str
    semantic_type: str
    label: str
    functional_role: str
    confidence: float = 0.0
    reasons: list[str] = Field(default_factory=list)


class RoomFunctionAssignmentSummary(BaseModel):
    assignment_status: str = "failed"
    western_room_count: int = 0
    media_lounge_room_id: str | None = None
    main_bedroom_room_id: str | None = None
    dining_zone_assigned: bool = False
    allowed_furniture_count: int = 0
    suppressed_furniture_count: int = 0
    role_conflict_count: int = 0
    needs_human_review: bool = True
    warnings_count: int = 0
    errors_count: int = 0


class FurnitureCleanupSummary(BaseModel):
    allowed_furniture_count: int = 0
    suppressed_furniture_count: int = 0
    conditional_allowed_furniture_count: int = 0
    role_conflict_count: int = 0
    by_functional_role: dict[str, dict[str, int]] = Field(default_factory=dict)
    suppression_reasons: list[str] = Field(default_factory=list)
    warnings_count: int = 0
    errors_count: int = 0


class InteriorStyleAnalysisArtifact(BaseModel):
    schema_version: str = "1.0"
    run_id: str
    generated_at: datetime
    provider: str
    model: str | None = None
    interior_photos: list[InteriorPhotoSemanticRecord] = Field(default_factory=list)
    style_references: InteriorStyleReferenceAnalysisGroups = Field(default_factory=InteriorStyleReferenceAnalysisGroups)
    derived_profile: DerivedInteriorStyleProfile = Field(default_factory=DerivedInteriorStyleProfile)
    summary: InteriorAnalysisSummary = Field(default_factory=InteriorAnalysisSummary)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class InteriorAnalysisValidatedArtifact(BaseModel):
    schema_version: str = "interior_analysis_validated.v1"
    run_id: str
    generated_at: datetime
    validation_status: str
    source: dict[str, str | None] = Field(default_factory=dict)
    provider: dict[str, str | None] = Field(default_factory=dict)
    interior_summary: dict = Field(default_factory=dict)
    room_observations: dict[str, dict] = Field(default_factory=dict)
    furniture_signals: dict[str, list[str]] = Field(default_factory=dict)
    style_reference_analysis: dict[str, list[dict]] = Field(default_factory=dict)
    customer_rules: dict = Field(default_factory=dict)
    recommendations_for_next_phase: dict = Field(default_factory=dict)
    quality: dict = Field(default_factory=dict)
    validation: dict = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class RoomFunctionAssignmentArtifact(BaseModel):
    schema_version: str = "room_function_assignment.v1"
    run_id: str
    assignment_status: str = "failed"
    generated_at: datetime
    rooms: list[RoomFunctionAssignmentRoomRecord] = Field(default_factory=list)
    assignment_rules_applied: list[str] = Field(default_factory=list)
    furniture_cleanup_summary: FurnitureCleanupSummary | None = None
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class LayoutBoundingBox(BaseModel):
    x_min: float | int | None = None
    y_min: float | int | None = None
    x_max: float | int | None = None
    y_max: float | int | None = None

    @model_validator(mode="before")
    @classmethod
    def _coerce_from_list_or_aliases(cls, data):
        if isinstance(data, (list, tuple)) and len(data) == 4:
            return {
                "x_min": data[0],
                "y_min": data[1],
                "x_max": data[2],
                "y_max": data[3],
            }
        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        alias_map = {
            "left": "x_min",
            "top": "y_min",
            "right": "x_max",
            "bottom": "y_max",
        }
        for alias, canonical in alias_map.items():
            if canonical not in normalized and alias in normalized:
                normalized[canonical] = normalized[alias]
        return normalized


class LayoutLayerConfig(BaseModel):
    visible: bool = True
    locked: bool = True
    editable: bool = False
    opacity: float | None = None
    preview_url: str | None = None


class LayoutRoomObject(BaseModel):
    id: str
    type: str
    label: str
    functional_role: str | None = None
    source_label_original: str | None = None
    bbox: LayoutBoundingBox | None = None
    approx_bbox: LayoutBoundingBox | None = None
    polygon: list[list[float]] | None = None
    position: str | None = None
    connected_to: list[str] = Field(default_factory=list)
    floor_tone: str = "unknown"
    locked: bool = True
    editable: bool = False
    confidence: float = 0.0
    geometry_confidence: float = 0.0
    geometry_notes: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class LayoutFixtureObject(BaseModel):
    id: str
    type: str
    bbox: LayoutBoundingBox | None = None
    approx_bbox: LayoutBoundingBox | None = None
    polygon: list[list[float]] | None = None
    position: str | None = None
    locked: bool = True
    editable: bool = False
    confidence: float = 0.0
    geometry_confidence: float = 0.0
    geometry_notes: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class LayoutConnectionObject(BaseModel):
    id: str
    type: str
    bbox: LayoutBoundingBox | None = None
    approx_bbox: LayoutBoundingBox | None = None
    polygon: list[list[float]] | None = None
    position: str | None = None
    connects: list[str] = Field(default_factory=list)
    room_id: str | None = None
    room_type: str | None = None
    approved_label: str | None = None
    exists: bool | None = None
    locked: bool = True
    editable: bool = False
    confidence: float = 0.0
    geometry_confidence: float = 0.0
    geometry_notes: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class LayoutLabelObject(BaseModel):
    id: str
    text: str
    text_original: str | None = None
    room_id: str | None = None
    bbox: LayoutBoundingBox | None = None
    position: str | None = None
    font_family: str = "default"
    font_size: int = 24
    locked: bool = False
    editable: bool = True
    confidence: float = 0.0


class LayoutFurnitureObject(BaseModel):
    id: str
    type: str
    room_type: str
    room_functional_role: str | None = None
    functional_role: str | None = None
    room_id: str | None = None
    bbox: LayoutBoundingBox | None = None
    position_hint: str | None = None
    rotation: float = 0.0
    base_color: str | None = None
    observed_color: str | None = None
    accent_colors: list[str] = Field(default_factory=list)
    source: str
    placement_status: str = "suggested_unplaced"
    placement_method: str | None = None
    placement_confidence: float = 0.0
    placement_notes: list[str] = Field(default_factory=list)
    compatibility_status: str | None = None
    suppression_reason: str | None = None
    render_action: str | None = None
    prompt_action: str | None = None
    target_room_bbox: LayoutBoundingBox | None = None
    room_geometry_confidence: float = 0.0
    x: int | None = None
    y: int | None = None
    width: int | None = None
    height: int | None = None
    locked: bool = False
    editable: bool = True
    confidence: float = 0.0
    notes: list[str] = Field(default_factory=list)


class LayoutStyleObject(BaseModel):
    floor_tone: str = "unknown"
    bed_base_color: str = "white"
    sofa_base_color: str = "white"
    dominant_colors: list[str] = Field(default_factory=list)
    accent_colors: list[str] = Field(default_factory=list)
    material_keywords: list[str] = Field(default_factory=list)
    style_keywords: list[str] = Field(default_factory=list)
    avoid_keywords: list[str] = Field(default_factory=list)


class LayoutQualitySummary(BaseModel):
    needs_human_review: bool = True
    structure_locked: bool = True
    semantic_layout_only: bool = True
    pixel_perfect_geometry: bool = False
    furniture_placement_done: bool = False
    image_generation_done: bool = False
    watercolor_rendering_done: bool = False
    room_count: int = 0
    fixture_count: int = 0
    label_count: int = 0
    furniture_suggestion_count: int = 0


class LayoutSummary(BaseModel):
    layout_status: str = "created"
    room_count: int = 0
    fixture_count: int = 0
    label_count: int = 0
    furniture_suggestion_count: int = 0
    structure_locked: bool = True
    needs_human_review: bool = True


class LayoutInitialArtifact(BaseModel):
    schema_version: str = "layout_initial.v1"
    run_id: str
    generated_at: datetime
    layout_status: str = "created"
    source: dict[str, str | None] = Field(default_factory=dict)
    canvas: dict = Field(default_factory=dict)
    layers: dict[str, LayoutLayerConfig] = Field(default_factory=dict)
    rooms: list[LayoutRoomObject] = Field(default_factory=list)
    fixtures: list[LayoutFixtureObject] = Field(default_factory=list)
    doors: list[LayoutConnectionObject] = Field(default_factory=list)
    windows: list[LayoutConnectionObject] = Field(default_factory=list)
    balcony: list[LayoutConnectionObject] = Field(default_factory=list)
    labels: list[LayoutLabelObject] = Field(default_factory=list)
    furniture: list[LayoutFurnitureObject] = Field(default_factory=list)
    style: LayoutStyleObject = Field(default_factory=LayoutStyleObject)
    constraints: list[str] = Field(default_factory=list)
    quality: LayoutQualitySummary = Field(default_factory=LayoutQualitySummary)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class LayoutValidationQualitySummary(BaseModel):
    needs_human_review: bool = True
    structure_locked: bool = True
    semantic_layout_only: bool = True
    pixel_perfect_geometry: bool = False
    furniture_placement_done: bool = False
    image_generation_done: bool = False
    watercolor_rendering_done: bool = False
    room_count: int = 0
    fixture_count: int = 0
    label_count: int = 0
    furniture_suggestion_count: int = 0
    canvas_valid: bool = True
    structure_lock_valid: bool = True
    editable_object_rules_valid: bool = True
    style_valid: bool = True


class LayoutValidationSummary(BaseModel):
    validation_status: str = "failed"
    room_count: int = 0
    fixture_count: int = 0
    label_count: int = 0
    furniture_count: int = 0
    structure_locked: bool = True
    furniture_placement_done: bool = False
    needs_human_review: bool = True
    warnings_count: int = 0
    errors_count: int = 0


class FurniturePlacementQualitySummary(BaseModel):
    needs_human_review: bool = True
    structure_locked: bool = True
    semantic_layout_only: bool = True
    pixel_perfect_geometry: bool = False
    furniture_placement_done: bool = False
    image_generation_done: bool = False
    watercolor_rendering_done: bool = False
    room_count: int = 0
    fixture_count: int = 0
    label_count: int = 0
    furniture_count: int = 0
    furniture_placed_count: int = 0
    furniture_unplaced_count: int = 0
    placement_confidence_avg: float = 0.0


class FurniturePlacementSummary(BaseModel):
    planning_status: str = "failed"
    furniture_count: int = 0
    furniture_placed_count: int = 0
    furniture_unplaced_count: int = 0
    placement_confidence_avg: float = 0.0
    needs_human_review: bool = True
    warnings_count: int = 0
    errors_count: int = 0


class FurniturePlacementValidationQualitySummary(BaseModel):
    needs_human_review: bool = True
    structure_locked: bool = True
    semantic_layout_only: bool = True
    pixel_perfect_geometry: bool = False
    furniture_placement_done: bool = False
    furniture_placement_validated: bool = True
    image_generation_done: bool = False
    watercolor_rendering_done: bool = False
    room_count: int = 0
    fixture_count: int = 0
    label_count: int = 0
    furniture_count: int = 0
    furniture_placed_count: int = 0
    furniture_unplaced_count: int = 0
    placement_confidence_avg: float = 0.0


class FurniturePlacementValidationSummary(BaseModel):
    validation_status: str = "failed"
    furniture_count: int = 0
    auto_placed_count: int = 0
    suggested_unplaced_count: int = 0
    invalid_count: int = 0
    inside_room_count: int = 0
    outside_room_count: int = 0
    overlap_warning_count: int = 0
    fixture_overlap_warning_count: int = 0
    needs_human_review: bool = True
    warnings_count: int = 0
    errors_count: int = 0


class RenderReadinessSummary(BaseModel):
    ready_for_prompt_building: bool = True
    ready_for_image_generation: bool = False
    requires_human_review: bool = True
    has_validated_layout: bool = True
    has_validated_furniture: bool = True
    has_style_profile: bool = False
    auto_placed_furniture_count: int = 0
    unplaced_furniture_count: int = 0
    warnings_count: int = 0
    errors_count: int = 0


class RenderPlanSummary(BaseModel):
    render_plan_status: str = "failed"
    ready_for_prompt_building: bool = False
    ready_for_image_generation: bool = False
    auto_placed_furniture_count: int = 0
    unplaced_furniture_count: int = 0
    label_count: int = 0
    room_count: int = 0
    needs_human_review: bool = True
    warnings_count: int = 0
    errors_count: int = 0


class PromptQualitySummary(BaseModel):
    has_system_prompt: bool = False
    has_structure_lock_prompt: bool = False
    has_room_prompt: bool = False
    has_furniture_prompt: bool = False
    has_label_prompt: bool = False
    has_style_prompt: bool = False
    has_negative_prompt: bool = False
    combined_prompt_char_count: int = 0
    negative_prompt_char_count: int = 0
    drawable_furniture_count: int = 0
    skipped_furniture_count: int = 0
    label_count: int = 0
    room_count: int = 0


class PromptPackageSummary(BaseModel):
    prompt_package_status: str = "failed"
    ready_for_openai_image_api: bool = False
    ready_for_manual_review: bool = True
    combined_prompt_char_count: int = 0
    negative_prompt_char_count: int = 0
    drawable_furniture_count: int = 0
    skipped_furniture_count: int = 0
    room_count: int = 0
    label_count: int = 0
    needs_human_review: bool = True
    warnings_count: int = 0
    errors_count: int = 0


class ImageGenerationRequestPreviewQualitySummary(BaseModel):
    has_prompt: bool = False
    has_normalized_floorplan: bool = False
    has_style_reference: bool = False
    has_interior_photos: bool = False
    prompt_char_count: int = 0
    input_image_count: int = 0
    ready_for_generation_after_manual_approval: bool = False


class ImageGenerationRequestPreviewSummary(BaseModel):
    preview_status: str = "failed"
    provider_name: str = "openai"
    model: str = "gpt-image-1"
    request_will_be_sent: bool = False
    api_call_performed: bool = False
    requires_manual_approval: bool = True
    input_image_count: int = 0
    prompt_char_count: int = 0
    provider_requested_size: str = "1024x1024"
    final_delivery_size: str = "1200x1200"
    provider_size_supported: bool = True
    postprocess_required: bool = True
    preview_warnings_count: int = 0
    upstream_warnings_count: int = 0
    warnings_count: int = 0
    errors_count: int = 0


class ImageGenerationDraftRequest(BaseModel):
    confirm_generation: bool = False
    provider: str = "openai"
    output_format: str = "png"
    use_reference_images: bool = True
    max_reference_images: int | None = None


class ImageGenerationDraftSummary(BaseModel):
    draft_status: str = "failed"
    provider_name: str = "openai"
    model: str = "gpt-image-1"
    api_call_performed: bool = False
    provider_size: str = "1024x1024"
    final_delivery_size: str = "1200x1200"
    draft_image_preview_url: str | None = None
    needs_human_review: bool = True
    ready_for_visual_qa: bool = False
    warnings_count: int = 0
    errors_count: int = 0


class FurniturePlacementArtifact(BaseModel):
    schema_version: str = "layout_furniture_planned.v1"
    run_id: str
    generated_at: datetime
    planning_status: str = "failed"
    source: dict[str, str | None] = Field(default_factory=dict)
    canvas: dict = Field(default_factory=dict)
    layers: dict[str, LayoutLayerConfig] = Field(default_factory=dict)
    rooms: list[LayoutRoomObject] = Field(default_factory=list)
    fixtures: list[LayoutFixtureObject] = Field(default_factory=list)
    doors: list[LayoutConnectionObject] = Field(default_factory=list)
    windows: list[LayoutConnectionObject] = Field(default_factory=list)
    balcony: list[LayoutConnectionObject] = Field(default_factory=list)
    labels: list[LayoutLabelObject] = Field(default_factory=list)
    furniture: list[LayoutFurnitureObject] = Field(default_factory=list)
    style: LayoutStyleObject = Field(default_factory=LayoutStyleObject)
    constraints: list[str] = Field(default_factory=list)
    placement: dict = Field(default_factory=dict)
    quality: FurniturePlacementQualitySummary = Field(default_factory=FurniturePlacementQualitySummary)
    validation: dict[str, bool | int] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class FurniturePlacementValidationArtifact(BaseModel):
    schema_version: str = "layout_furniture_validated.v1"
    run_id: str
    generated_at: datetime
    validation_status: str = "failed"
    source: dict[str, str | None] = Field(default_factory=dict)
    canvas: dict = Field(default_factory=dict)
    layers: dict[str, LayoutLayerConfig] = Field(default_factory=dict)
    rooms: list[LayoutRoomObject] = Field(default_factory=list)
    fixtures: list[LayoutFixtureObject] = Field(default_factory=list)
    doors: list[LayoutConnectionObject] = Field(default_factory=list)
    windows: list[LayoutConnectionObject] = Field(default_factory=list)
    balcony: list[LayoutConnectionObject] = Field(default_factory=list)
    labels: list[LayoutLabelObject] = Field(default_factory=list)
    furniture: list[LayoutFurnitureObject] = Field(default_factory=list)
    style: LayoutStyleObject = Field(default_factory=LayoutStyleObject)
    constraints: list[str] = Field(default_factory=list)
    placement_validation: dict = Field(default_factory=dict)
    quality: FurniturePlacementValidationQualitySummary = Field(default_factory=FurniturePlacementValidationQualitySummary)
    validation: dict[str, bool | int] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class RenderPlanArtifact(BaseModel):
    schema_version: str = "render_plan.v1"
    run_id: str
    generated_at: datetime
    render_plan_status: str = "failed"
    source: dict[str, str | None] = Field(default_factory=dict)
    canvas: dict = Field(default_factory=dict)
    render_mode: dict = Field(default_factory=dict)
    structure_lock: dict = Field(default_factory=dict)
    rooms: list[dict] = Field(default_factory=list)
    fixtures: list[dict] = Field(default_factory=list)
    doors: list[dict] = Field(default_factory=list)
    windows: list[dict] = Field(default_factory=list)
    balcony: list[dict] = Field(default_factory=list)
    furniture: list[dict] = Field(default_factory=list)
    labels: list[dict] = Field(default_factory=list)
    style: dict = Field(default_factory=dict)
    prompt_sections: dict = Field(default_factory=dict)
    render_readiness: RenderReadinessSummary = Field(default_factory=RenderReadinessSummary)
    quality: dict = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class PromptPackageArtifact(BaseModel):
    schema_version: str = "prompt_package.v1"
    run_id: str
    generated_at: datetime
    prompt_package_status: str = "failed"
    source: dict[str, str | None] = Field(default_factory=dict)
    target_output: dict = Field(default_factory=dict)
    provider_readiness: dict = Field(default_factory=dict)
    prompts: dict[str, str] = Field(default_factory=dict)
    reference_manifest: dict = Field(default_factory=dict)
    prompt_constraints: dict[str, bool] = Field(default_factory=dict)
    prompt_quality: PromptQualitySummary = Field(default_factory=PromptQualitySummary)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class ImageGenerationRequestPreviewArtifact(BaseModel):
    schema_version: str = "image_generation_request_preview.v1"
    run_id: str
    generated_at: datetime
    preview_status: str = "failed"
    provider: dict = Field(default_factory=dict)
    source: dict = Field(default_factory=dict)
    target_output: dict = Field(default_factory=dict)
    request_payload_preview: dict = Field(default_factory=dict)
    postprocess_plan: dict = Field(default_factory=dict)
    provider_size_policy: dict = Field(default_factory=dict)
    reference_inputs: dict = Field(default_factory=dict)
    safety_and_cost_controls: dict = Field(default_factory=dict)
    request_quality: ImageGenerationRequestPreviewQualitySummary = Field(default_factory=ImageGenerationRequestPreviewQualitySummary)
    reference_selection_path: str | None = None
    selected_reference_images: list[dict] = Field(default_factory=list)
    excluded_reference_images: list[dict] = Field(default_factory=list)
    reference_scoring_details: dict = Field(default_factory=dict)
    interior_reference_count: int = 0
    selected_interior_filenames: list[str] = Field(default_factory=list)
    interior_guidance_summary: dict = Field(default_factory=dict)
    preview_warnings: list[str] = Field(default_factory=list)
    upstream_warnings: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class ImageGenerationDraftArtifact(BaseModel):
    schema_version: str = "image_generation_draft.v1"
    run_id: str
    generated_at: datetime
    draft_status: str = "failed"
    provider: dict = Field(default_factory=dict)
    source: dict = Field(default_factory=dict)
    request: dict = Field(default_factory=dict)
    outputs: dict = Field(default_factory=dict)
    postprocess: dict = Field(default_factory=dict)
    usage: dict = Field(default_factory=dict)
    quality: dict = Field(default_factory=dict)
    reference_selection_path: str | None = None
    selected_reference_images: list[dict] = Field(default_factory=list)
    interior_reference_count: int = 0
    selected_interior_filenames: list[str] = Field(default_factory=list)
    interior_guidance_summary: dict = Field(default_factory=dict)
    cloudinary: dict = Field(default_factory=dict)
    public_output_url: str | None = None
    cloudinary_url: str | None = None
    output_url: str | None = None
    preview_url: str | None = None
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class StructureLockedCompositeSummary(BaseModel):
    composite_status: str = "failed"
    composite_image_preview_url: str | None = None
    width: int = 1200
    height: int = 1200
    ai_provider_used: bool = False
    structure_overlay_applied: bool = False
    furniture_drawn_count: int = 0
    furniture_skipped_count: int = 0
    needs_human_review: bool = True
    warnings_count: int = 0
    errors_count: int = 0


class StructureLockedCompositeArtifact(BaseModel):
    schema_version: str = "structure_locked_composite.v1"
    run_id: str
    generated_at: datetime
    composite_status: str = "failed"
    source: dict = Field(default_factory=dict)
    outputs: dict = Field(default_factory=dict)
    rendering: dict = Field(default_factory=dict)
    quality: dict = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class LayoutValidationArtifact(BaseModel):
    schema_version: str = "layout_validated.v1"
    run_id: str
    generated_at: datetime
    validation_status: str = "failed"
    source: dict[str, str | None] = Field(default_factory=dict)
    canvas: dict = Field(default_factory=dict)
    layers: dict[str, LayoutLayerConfig] = Field(default_factory=dict)
    rooms: list[LayoutRoomObject] = Field(default_factory=list)
    fixtures: list[LayoutFixtureObject] = Field(default_factory=list)
    doors: list[LayoutConnectionObject] = Field(default_factory=list)
    windows: list[LayoutConnectionObject] = Field(default_factory=list)
    balcony: list[LayoutConnectionObject] = Field(default_factory=list)
    labels: list[LayoutLabelObject] = Field(default_factory=list)
    furniture: list[LayoutFurnitureObject] = Field(default_factory=list)
    style: LayoutStyleObject = Field(default_factory=LayoutStyleObject)
    constraints: list[str] = Field(default_factory=list)
    quality: LayoutValidationQualitySummary = Field(default_factory=LayoutValidationQualitySummary)
    validation: dict[str, bool | int] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class ArtifactIndexEntry(BaseModel):
    key: str
    filename: str
    relative_path: str
    preview_url: str | None = None
    external_url: str | None = None
    cloudinary_secure_url: str | None = None
    size_bytes: int | None = None
    content_type: str | None = None
    type: str = "unknown"
    category: str
    exists: bool = True


class InputSummary(BaseModel):
    floorplan_present: bool = False
    interior_photo_count: int = 0
    style_reference_ideal_count: int = 0
    style_reference_acceptable_count: int = 0
    style_reference_ng_count: int = 0
    total_style_reference_count: int = 0


class PipelineSummary(BaseModel):
    current_run_status: str
    phase_1_uploaded: bool = False
    phase_2a_inspected: bool = False
    phase_2b_preprocessed: bool = False
    phase_2c_analyzed: bool = False
    phase_2d_validated: bool = False
    phase_3a_interior_style_analyzed: bool = False
    phase_3a_interior_semantic_analysis: bool = False
    phase_3b_interior_analysis_validation: bool = False
    phase_3c_room_function_assignment: bool = False
    phase_4a_layout_object_creation: bool = False
    phase_4b_layout_validation: bool = False
    phase_4c_furniture_placement_planning: bool = False
    phase_4d_furniture_placement_validation: bool = False
    phase_5a_render_plan_creation: bool = False
    phase_5b_prompt_package_creation: bool = False
    phase_5c0_image_generation_request_preview: bool = False
    phase_5c1_image_generation_draft: bool = False
    phase_6a_structure_locked_composite_renderer: bool = False
    artifact_count: int = 0
    completed_steps: list[str] = Field(default_factory=list)
    pending_steps: list[str] = Field(default_factory=list)
    next_recommended_step: str | None = None


class AnalysisSummary(BaseModel):
    provider: str | None = None
    model: str | None = None
    apartment_type: str | None = None
    room_count: int = 0
    room_types: list[str] = Field(default_factory=list)
    approved_labels_complete: bool = False
    needs_manual_review: bool = True
    warning_count: int = 0
    error_count: int = 0
    validation_status: str | None = None
    geometry_summary: GeometrySummary | None = None


class RunArtifactIndex(BaseModel):
    schema_version: str = "1.0"
    run_id: str
    generated_at: datetime
    artifacts: list[ArtifactIndexEntry] = Field(default_factory=list)
    checks: dict[str, bool] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class RunMetadataSummary(BaseModel):
    schema_version: str = "1.0"
    run_id: str
    generated_at: datetime
    artifact_index_path: str | None = None
    artifacts: list[ArtifactIndexEntry] = Field(default_factory=list)
    input_summary: InputSummary | None = None
    pipeline_summary: PipelineSummary
    analysis_summary: AnalysisSummary | None = None
    interior_analysis_summary: InteriorAnalysisSummary | None = None
    interior_validation_summary: InteriorValidationSummary | None = None
    room_function_assignment_summary: RoomFunctionAssignmentSummary | None = None
    layout_summary: LayoutSummary | None = None
    layout_validation_summary: LayoutValidationSummary | None = None
    furniture_placement_summary: FurniturePlacementSummary | None = None
    furniture_placement_validation_summary: FurniturePlacementValidationSummary | None = None
    render_plan_summary: RenderPlanSummary | None = None
    prompt_package_summary: PromptPackageSummary | None = None
    image_generation_request_preview_summary: ImageGenerationRequestPreviewSummary | None = None
    image_generation_draft_summary: ImageGenerationDraftSummary | None = None
    structure_locked_composite_summary: StructureLockedCompositeSummary | None = None
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class ProcessingFlags(BaseModel):
    input_inspection: bool = False
    floorplan_preprocess: bool = False
    semantic_analysis: bool = False
    semantic_validation: bool = False
    interior_style_analysis: bool = False
    interior_analysis_validation: bool = False
    room_function_assignment: bool = False
    layout_initial_creation: bool = False
    layout_validation: bool = False
    furniture_placement_planning: bool = False
    furniture_placement_validation: bool = False
    render_plan_creation: bool = False
    prompt_package_creation: bool = False
    image_generation_request_preview: bool = False
    image_generation_draft: bool = False
    structure_locked_composite_rendering: bool = False
    ai_analysis: bool = False
    ocr: bool = False
    image_generation: bool = False
    watercolor_rendering: bool = False


class OutputRequirements(BaseModel):
    preserve_layout_accuracy: str = "100%"
    label_language: str = "en"
    final_output_width: int = 1200
    final_output_height: int = 1200
    final_output_formats: list[str] = Field(default_factory=lambda: ["png", "jpeg"])


class RunMetadata(BaseModel):
    schema_version: str = "1.1"
    phase: str = "phase_1_upload_workspace"
    run_id: str
    run_status: str = "uploaded"
    status: str = "uploaded"
    created_at: datetime
    updated_at: datetime | None = None
    workspace_path: str
    workspace: WorkspacePaths | None = None
    inputs: RunInputs | None = None
    floorplan: UploadedFileMetadata
    interior_photos: list[UploadedFileMetadata] = Field(default_factory=list)
    style_references: StyleReferenceGroups = Field(default_factory=StyleReferenceGroups)
    processing: ProcessingFlags = Field(default_factory=ProcessingFlags)
    requirements: OutputRequirements = Field(default_factory=OutputRequirements)
    artifact_index_path: str | None = None
    pipeline_summary: PipelineSummary | None = None
    pipeline: dict | None = None
    analysis_summary: AnalysisSummary | None = None
    analysis_provider: str | None = None
    analysis_model: str | None = None
    interior_analysis_path: str | None = None
    interior_analysis_summary: InteriorAnalysisSummary | None = None
    interior_analysis_validated_path: str | None = None
    interior_validation_summary: InteriorValidationSummary | None = None
    room_function_assignment_path: str | None = None
    room_function_assignment_summary: RoomFunctionAssignmentSummary | None = None
    layout_initial_path: str | None = None
    layout_summary: LayoutSummary | None = None
    layout_validated_path: str | None = None
    layout_validation_summary: LayoutValidationSummary | None = None
    layout_furniture_planned_path: str | None = None
    furniture_placement_summary: FurniturePlacementSummary | None = None
    layout_furniture_validated_path: str | None = None
    furniture_placement_validation_summary: FurniturePlacementValidationSummary | None = None
    render_plan_path: str | None = None
    render_plan_summary: RenderPlanSummary | None = None
    prompt_package_path: str | None = None
    prompt_package_summary: PromptPackageSummary | None = None
    image_generation_request_preview_path: str | None = None
    image_generation_request_preview_summary: ImageGenerationRequestPreviewSummary | None = None
    image_generation_draft_path: str | None = None
    image_generation_draft_summary: ImageGenerationDraftSummary | None = None
    cloudinary_summary: dict = Field(default_factory=dict)
    public_output_url: str | None = None
    structure_locked_composite_path: str | None = None
    structure_locked_composite_summary: StructureLockedCompositeSummary | None = None
    last_indexed_at: datetime | None = None
    metadata_path: str

    @model_validator(mode="before")
    @classmethod
    def _backfill_run_status_and_grouped_references(cls, data):
        if not isinstance(data, dict):
            return data
        if "run_status" not in data and "status" in data:
            data["run_status"] = data["status"]
        if "status" not in data and "run_status" in data:
            data["status"] = data["run_status"]
        if isinstance(data.get("style_references"), list):
            data["style_references"] = StyleReferenceGroups.model_validate(data["style_references"]).model_dump(mode="json")
        if isinstance(data.get("inputs"), dict) and isinstance(data["inputs"].get("style_references"), list):
            data["inputs"]["style_references"] = StyleReferenceGroups.model_validate(
                data["inputs"]["style_references"]
            ).model_dump(mode="json")
        return data


class RunCreateResponse(BaseModel):
    status: str
    run_status: str = "uploaded"
    run_id: str
    workspace_path: str
    metadata_path: str
    floorplan: UploadedFileMetadata
    interior_photos: list[UploadedFileMetadata] = Field(default_factory=list)
    style_references: StyleReferenceGroups = Field(default_factory=StyleReferenceGroups)
    inputs: RunInputs | None = None


class RunMetadataResponse(BaseModel):
    status: str
    run_id: str
    metadata: RunMetadata
