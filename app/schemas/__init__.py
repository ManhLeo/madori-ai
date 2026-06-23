from pydantic import BaseModel, Field

from app.schemas.run import (
    AnalysisQualitySummary,
    AnalysisSummary,
    ArtifactIndexEntry,
    BedSemanticRecord,
    DerivedInteriorStyleProfile,
    InputSummary,
    InteriorAnalysisValidatedArtifact,
    InteriorAnalysisSummary,
    InteriorValidationSummary,
    InteriorObjectSemanticRecord,
    InteriorPhotoSemanticRecord,
    InteriorStyleAnalysisArtifact,
    InteriorStyleReferenceAnalysisGroups,
    OutputRequirements,
    ProcessingFlags,
    PipelineSummary,
    PromptPackageArtifact,
    PromptPackageSummary,
    PromptQualitySummary,
    RenderPlanArtifact,
    RenderPlanSummary,
    RenderReadinessSummary,
    LayoutBoundingBox,
    LayoutConnectionObject,
    LayoutFixtureObject,
    LayoutFurnitureObject,
    LayoutInitialArtifact,
    LayoutLabelObject,
    LayoutLayerConfig,
    LayoutQualitySummary,
    LayoutRoomObject,
    LayoutStyleObject,
    LayoutSummary,
    LayoutValidationArtifact,
    LayoutValidationQualitySummary,
    LayoutValidationSummary,
    FloorplanAnalysisValidatedArtifact,
    FloorplanSemanticAnalysisArtifact,
    FloorplanPreprocessReport,
    FurniturePlacementArtifact,
    FurniturePlacementQualitySummary,
    FurniturePlacementSummary,
    FurniturePlacementValidationArtifact,
    FurniturePlacementValidationQualitySummary,
    FurniturePlacementValidationSummary,
    GeometrySummary,
    ImageInspectionMetadata,
    ImageGenerationRequestPreviewArtifact,
    ImageGenerationDraftArtifact,
    ImageGenerationDraftRequest,
    ImageGenerationDraftSummary,
    ImageGenerationRequestPreviewQualitySummary,
    ImageGenerationRequestPreviewSummary,
    StructureLockedCompositeArtifact,
    StructureLockedCompositeSummary,
    InputManifest,
    PreprocessImageArtifact,
    RunArtifactIndex,
    SofaSemanticRecord,
    StyleReferenceSemanticRecord,
    ValidatedDimensionRecord,
    ValidatedDoorRecord,
    ValidatedFixtureRecord,
    ValidatedLabelRecord,
    RunMetadataSummary,
    ValidatedRoomRecord,
    ValidatedWindowRecord,
    SemanticSourceImage,
    RunCreateResponse,
    RunInputs,
    RunMetadata,
    RunMetadataResponse,
    StyleReferenceGroups,
    StyleReferenceInspectionGroups,
    UploadedFileMetadata,
    WorkspacePaths,
)


class RoomInfo(BaseModel):
    type: str
    room_name: str | None = None
    position: str | None = None
    size: str | None = None
    bounding_box: LayoutBoundingBox | None = None
    approx_bbox: LayoutBoundingBox | None = None
    polygon: list[list[float]] | None = None
    confidence: float = 0.0
    geometry_confidence: float = 0.0
    geometry_notes: list[str] = Field(default_factory=list)
    connected_to: list[str] = Field(default_factory=list)


class DoorInfo(BaseModel):
    position: str | None = None
    connects: list[str] = Field(default_factory=list)
    bounding_box: LayoutBoundingBox | None = None
    approx_bbox: LayoutBoundingBox | None = None
    polygon: list[list[float]] | None = None
    confidence: float = 0.0
    geometry_confidence: float = 0.0
    geometry_notes: list[str] = Field(default_factory=list)


class WindowInfo(BaseModel):
    position: str | None = None
    room: str | None = None
    bounding_box: LayoutBoundingBox | None = None
    approx_bbox: LayoutBoundingBox | None = None
    polygon: list[list[float]] | None = None
    confidence: float = 0.0
    geometry_confidence: float = 0.0
    geometry_notes: list[str] = Field(default_factory=list)


class BalconyInfo(BaseModel):
    exists: bool
    position: str | None = None
    bounding_box: LayoutBoundingBox | None = None
    approx_bbox: LayoutBoundingBox | None = None
    polygon: list[list[float]] | None = None
    confidence: float = 0.0
    geometry_confidence: float = 0.0
    geometry_notes: list[str] = Field(default_factory=list)


class FloorplanAnalysis(BaseModel):
    apartment_type: str | None = None
    layout_description: str
    rooms: list[RoomInfo]
    doors: list[DoorInfo]
    windows: list[WindowInfo]
    balcony: BalconyInfo | None = None
    constraints: list[str]


class UserPreferences(BaseModel):
    target_user: str | None = None
    interior_style: str | None = None
    budget_level: str | None = None
    color_preference: str | None = None
    lifestyle: list[str] = Field(default_factory=list)
    special_requests: list[str] = Field(default_factory=list)


class FurnitureItem(BaseModel):
    item: str
    room: str
    size: str | None = None
    position_hint: str | None = None
    reason: str | None = None
    relative_x: float | None = None
    relative_y: float | None = None
    rotation: float | None = None


class RoomFurniturePlan(BaseModel):
    room_type: str
    room_name: str | None = None
    room_position: str | None = None
    items: list[FurnitureItem] = Field(default_factory=list)


class FurniturePlan(BaseModel):
    style: str
    target_user: str | None = None
    budget_level: str | None = None
    room_plans: list[RoomFurniturePlan] = Field(default_factory=list)
    global_rules: list[str] = Field(default_factory=list)


class FloorplanDesignAnalysis(BaseModel):
    analysis: FloorplanAnalysis
    furniture_plan: FurniturePlan | None = None


class AnalyzeFloorplanResponse(BaseModel):
    status: str
    run_id: str
    analysis: FloorplanAnalysis


class GenerateResponse(BaseModel):
    status: str
    run_id: str
    analysis: FloorplanAnalysis
    prompt: str
    output_url: str


class GenerateRequest(BaseModel):
    image_filename: str


GenerationResponse = GenerateResponse
