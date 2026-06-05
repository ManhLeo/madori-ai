from pydantic import BaseModel, Field


class RoomInfo(BaseModel):
    type: str
    room_name: str | None = None
    position: str | None = None
    size: str | None = None
    bounding_box: list[float] | None = None
    connected_to: list[str]


class DoorInfo(BaseModel):
    position: str | None = None
    connects: list[str]


class WindowInfo(BaseModel):
    position: str | None = None
    room: str | None = None


class BalconyInfo(BaseModel):
    exists: bool
    position: str | None = None


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
