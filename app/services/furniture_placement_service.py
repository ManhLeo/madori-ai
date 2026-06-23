from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException

from app.schemas.run import (
    FurniturePlacementArtifact,
    FurniturePlacementQualitySummary,
    FurniturePlacementSummary,
    LayoutBoundingBox,
    LayoutFurnitureObject,
    LayoutValidationArtifact,
    RunMetadata,
)


class FurniturePlacementService:
    ROOM_TYPE_ALIASES = {
        "bedroom": "bed_room",
        "bed room": "bed_room",
        "living": "living_room",
        "ldk": "living_room",
        "dining": "dining",
        "dining_kitchen": "dining_kitchen",
        "dining kitchen": "dining_kitchen",
        "bath": "bath_room",
        "bathroom": "bath_room",
    }
    FURNITURE_ROOM_FALLBACKS = {
        "sofa_1_seater": "living_room",
        "sofa_2_seater": "living_room",
        "sofa_3_seater": "living_room",
        "sectional_sofa": "living_room",
        "sofa_bed": "living_room",
        "coffee_table": "living_room",
        "tv": "living_room",
        "tv_stand": "living_room",
        "rug": "living_room",
        "curtain": "living_room",
        "potted_plant": "living_room",
        "floor_lamp": "living_room",
        "wall_art": "living_room",
        "pillow": "bed_room",
        "blanket": "bed_room",
        "single_bed": "bed_room",
        "semi_double_bed": "bed_room",
        "double_bed": "bed_room",
        "two_single_beds": "bed_room",
        "dining_table": "dining_kitchen",
        "chair": "dining_kitchen",
        "kitchen_counter": "kitchen",
        "stove": "kitchen",
        "sink": "kitchen",
        "cabinet": "kitchen",
        "bathtub": "bath_room",
        "shower": "bath_room",
        "towel": "bath_room",
    }
    LARGE_LIVING_ROOM_FURNITURE = {"sofa_3_seater", "coffee_table", "tv_stand", "rug", "dining_table", "chair"}
    WALL_SIDE_FURNITURE = {"tv", "curtain", "wall_art"}
    KITCHEN_FURNITURE = {"kitchen_counter", "sink", "stove", "cabinet"}
    BATH_FURNITURE = {"bathtub", "shower", "towel"}
    BEDROOM_DETAIL_FURNITURE = {"pillow", "blanket"}
    SUPPRESSED_PLACEMENT_STATUSES = {"suppressed_by_functional_role"}
    LIVING_ROOM_EXCLUDED_ROOM_TYPES = {"kitchen", "bath_room", "toilet", "wash_area", "closet", "entrance"}
    UNKNOWN_TYPE_RECOVERY_BY_ROOM = {
        "living_room": ["sofa_3_seater", "coffee_table", "tv", "tv_stand", "floor_lamp", "curtain", "wall_art", "potted_plant", "rug"],
        "dining": ["dining_table", "chair"],
        "bed_room": ["two_single_beds", "bed", "pillow", "blanket", "side_table", "curtain"],
        "kitchen": ["kitchen_counter", "sink", "stove", "cabinet"],
        "bath_room": ["bathtub", "shower", "towel"],
    }

    def __init__(self, storage_dir: Path, storage_runs_dir: Path) -> None:
        self.storage_dir = storage_dir
        self.storage_runs_dir = storage_runs_dir

    def plan_furniture_placement(self, metadata: RunMetadata) -> FurniturePlacementArtifact:
        run_id = metadata.run_id
        layout = self.load_layout_validated(run_id)
        artifact = self._build_planned_artifact(layout)
        self.write_layout_furniture_planned(run_id, artifact)
        return artifact

    def load_layout_validated(self, run_id: str) -> LayoutValidationArtifact:
        path = self._artifacts_dir(run_id) / "layout_validated.json"
        if not path.exists():
            raise HTTPException(status_code=400, detail="Run layout validation before furniture placement planning")
        try:
            return LayoutValidationArtifact.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"invalid layout_validated.json: {exc}") from exc

    def load_layout_furniture_planned(self, run_id: str) -> FurniturePlacementArtifact:
        path = self._artifacts_dir(run_id) / "layout_furniture_planned.json"
        if not path.exists():
            raise HTTPException(status_code=404, detail="layout_furniture_planned artifact not found")
        try:
            return FurniturePlacementArtifact.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=500, detail="failed to read layout_furniture_planned artifact") from exc

    def build_room_lookup(self, layout: LayoutValidationArtifact) -> dict[str, list]:
        room_ids: dict[str, object] = {}
        room_types: dict[str, list] = {}
        room_roles: dict[str, list] = {}
        for room in layout.rooms:
            room_ids[room.id] = room
            normalized_type = self._normalize_room_type(room.type)
            room_types.setdefault(normalized_type, []).append(room)
            normalized_role = self._normalize_room_role(getattr(room, "functional_role", None))
            if normalized_role != "unknown":
                room_roles.setdefault(normalized_role, []).append(room)
        return {"by_id": room_ids, "by_type": room_types, "by_role": room_roles}

    def plan_all_furniture(
        self,
        layout: LayoutValidationArtifact,
    ) -> tuple[list[LayoutFurnitureObject], dict, list[str], list[str]]:
        warnings = list(layout.warnings)
        errors = list(layout.errors)
        room_lookup = self.build_room_lookup(layout)
        planned_by_room: dict[str, list[LayoutFurnitureObject]] = {}
        planned: list[LayoutFurnitureObject] = []
        recovered_types = self._build_unknown_type_recovery_map(layout)

        for furniture in layout.furniture:
            if str(furniture.placement_status or "") in self.SUPPRESSED_PLACEMENT_STATUSES:
                planned.append(
                    furniture.model_copy(
                        update={
                            "locked": False,
                            "editable": True,
                            "placement_method": None,
                            "placement_confidence": 0.0,
                            "placement_notes": list(furniture.placement_notes or []) or ["Suppressed by functional role cleanup."],
                            "bbox": None,
                            "target_room_bbox": None,
                            "x": None,
                            "y": None,
                            "width": None,
                            "height": None,
                        }
                    )
                )
                continue
            recovered_type = recovered_types.get(furniture.id)
            if recovered_type:
                furniture = furniture.model_copy(update={"type": recovered_type})
            room, resolve_notes = self._resolve_target_room(furniture, room_lookup)
            updated, item_warnings = self.plan_single_furniture(
                furniture,
                room,
                layout,
                planned_by_room.get(room.id if room else "", []),
                planned,
            )
            if room and updated.bbox is not None:
                planned_by_room.setdefault(room.id, []).append(updated)
            planned.append(updated)
            warnings.extend(resolve_notes)
            warnings.extend(item_warnings)

        placed_count = sum(1 for item in planned if item.placement_status == "auto_placed" and item.bbox is not None)
        unplaced_count = sum(1 for item in planned if item.placement_status == "suggested_unplaced")
        invalid_count = sum(1 for item in planned if item.placement_status == "invalid")

        placement = {
            "algorithm": "deterministic_room_bbox_v1",
            "auto_place_enabled": True,
            "room_bbox_required": True,
            "collision_check_enabled": True,
            "structure_modification_allowed": False,
            "placed_count": placed_count,
            "unplaced_count": unplaced_count,
            "invalid_count": invalid_count,
        }
        return planned, placement, self._dedupe_keep_order(warnings), self._dedupe_keep_order(errors)

    def plan_single_furniture(
        self,
        furniture: LayoutFurnitureObject,
        room,
        layout: LayoutValidationArtifact,
        placed_items: list[LayoutFurnitureObject],
        all_planned_items: list[LayoutFurnitureObject],
    ) -> tuple[LayoutFurnitureObject, list[str]]:
        warnings: list[str] = []
        update = {
            "locked": False,
            "editable": True,
            "placement_method": "deterministic_room_bbox_v1",
            "placement_confidence": 0.0,
            "placement_notes": [],
            "target_room_bbox": room.bbox if room and room.bbox else None,
            "room_geometry_confidence": float(room.geometry_confidence) if room else 0.0,
            "room_id": getattr(room, "id", None) if room else furniture.room_id,
            "room_type": getattr(room, "type", None) if room else furniture.room_type,
            "room_functional_role": getattr(room, "functional_role", None) if room else getattr(furniture, "room_functional_role", None),
        }
        if furniture.type.startswith("sofa") and not furniture.base_color:
            update["base_color"] = "white"
        if "bed" in furniture.type and not furniture.base_color:
            update["base_color"] = "white"

        if str(furniture.placement_status or "") in self.SUPPRESSED_PLACEMENT_STATUSES:
            note = "Suppressed by functional role cleanup; do not place."
            update.update(
                {
                    "placement_status": "suppressed_by_functional_role",
                    "placement_method": None,
                    "placement_confidence": 0.0,
                    "placement_notes": [note],
                    "bbox": None,
                    "target_room_bbox": None,
                    "x": None,
                    "y": None,
                    "width": None,
                    "height": None,
                }
            )
            return furniture.model_copy(update=update), warnings

        if room is None:
            note = "No valid target room found."
            warnings.append(f"Furniture {furniture.id}: {note}")
            update["placement_status"] = "suggested_unplaced"
            update["placement_notes"] = [note]
            return furniture.model_copy(update=update), warnings
        if room.bbox is None:
            note = "No valid target room bbox available."
            warnings.append(f"Furniture {furniture.id}: {note}")
            update["placement_status"] = "suggested_unplaced"
            update["placement_notes"] = [note]
            return furniture.model_copy(update=update), warnings

        if furniture.type in self.BEDROOM_DETAIL_FURNITURE:
            supporting_bed = self._find_supporting_bed(placed_items)
            if supporting_bed is None or supporting_bed.bbox is None:
                note = "Associated bed is not safely placed, so bedding detail stays suggested_unplaced."
                warnings.append(f"Furniture {furniture.id}: {note}")
                update["placement_status"] = "suggested_unplaced"
                update["placement_notes"] = [note]
                return furniture.model_copy(update=update), warnings

        size = self.estimate_furniture_size(furniture.type, room.bbox)
        if size is None:
            note = "Room is too small for conservative placement."
            warnings.append(f"Furniture {furniture.id}: {note}")
            update["placement_status"] = "suggested_unplaced"
            update["placement_notes"] = [note]
            return furniture.model_copy(update=update), warnings

        exclusion_zones = self.build_exclusion_zones(layout, room, furniture.type, all_planned_items)
        candidates = self.select_candidate_positions(furniture.type, room.bbox, size)
        collision_adjustment_used = False
        for candidate in candidates:
            candidate_bbox = self._candidate_bbox_from_anchor(candidate, size)
            is_valid, notes = self.validate_candidate_bbox(
                candidate_bbox,
                room.bbox,
                placed_items,
                furniture.type,
                exclusion_zones,
            )
            if is_valid:
                placement_confidence = 0.60
                if float(room.geometry_confidence or 0.0) >= 0.8:
                    placement_confidence += 0.10
                if furniture.room_id:
                    placement_confidence += 0.10
                if not collision_adjustment_used:
                    placement_confidence += 0.05
                placement_confidence = max(0.0, min(0.85, placement_confidence))
                bbox_payload = candidate_bbox.model_dump(mode="json")
                update["bbox"] = candidate_bbox
                update["x"] = int(bbox_payload["x_min"])
                update["y"] = int(bbox_payload["y_min"])
                update["width"] = int(bbox_payload["x_max"] - bbox_payload["x_min"])
                update["height"] = int(bbox_payload["y_max"] - bbox_payload["y_min"])
                update["placement_status"] = "auto_placed"
                update["placement_confidence"] = placement_confidence
                update["placement_notes"] = notes
                return furniture.model_copy(update=update), warnings
            if notes:
                collision_adjustment_used = True

        note = "No safe placement candidate fit inside the target room bbox."
        warnings.append(f"Furniture {furniture.id}: {note}")
        update["placement_status"] = "suggested_unplaced"
        update["placement_notes"] = [note]
        return furniture.model_copy(update=update), warnings

    def estimate_furniture_size(self, furniture_type: str, room_bbox: LayoutBoundingBox) -> dict | None:
        room_width = int(room_bbox.x_max - room_bbox.x_min)
        room_height = int(room_bbox.y_max - room_bbox.y_min)
        margin = self._room_margin(room_bbox)
        usable_width = room_width - (margin * 2)
        usable_height = room_height - (margin * 2)
        if usable_width < 20 or usable_height < 20:
            return None

        defs = {
            "sofa_1_seater": (0.25, 90, 0.18, 70),
            "sofa_2_seater": (0.38, 150, 0.20, 80),
            "sofa_3_seater": (0.50, 220, 0.22, 90),
            "sectional_sofa": (0.55, 240, 0.30, 120),
            "sofa_bed": (0.48, 210, 0.25, 110),
            "single_bed": (0.35, 120, 0.45, 200),
            "semi_double_bed": (0.42, 150, 0.48, 210),
            "double_bed": (0.50, 180, 0.50, 220),
            "two_single_beds": (0.65, 260, 0.45, 200),
            "coffee_table": (0.22, 90, 0.14, 60),
            "dining_table": (0.32, 130, 0.22, 90),
            "chair": (0.12, 45, 0.12, 45),
            "tv": (0.25, 120, 0.08, 35),
            "tv_stand": (0.30, 140, 0.10, 45),
            "rug": (0.45, 200, 0.30, 130),
            "curtain": (0.35, 160, 0.05, 30),
            "potted_plant": (0.10, 45, 0.12, 55),
            "floor_lamp": (0.08, 35, 0.16, 70),
            "wall_art": (0.20, 90, 0.10, 45),
            "pillow": (0.12, 40, 0.08, 28),
            "blanket": (0.28, 110, 0.18, 70),
            "shelf": (0.25, 110, 0.16, 70),
            "desk": (0.28, 120, 0.18, 75),
            "kitchen_counter": (0.60, 180, 0.22, 70),
            "stove": (0.18, 55, 0.18, 55),
            "sink": (0.20, 60, 0.16, 50),
            "cabinet": (0.24, 90, 0.18, 70),
            "bathtub": (0.42, 140, 0.25, 85),
            "shower": (0.20, 65, 0.20, 65),
            "towel": (0.10, 30, 0.12, 36),
            "unknown": (0.20, 80, 0.15, 60),
        }
        width_ratio, width_cap, height_ratio, height_cap = defs.get(furniture_type, defs["unknown"])
        width = max(20, min(int(room_width * width_ratio), width_cap, usable_width))
        height = max(20, min(int(room_height * height_ratio), height_cap, usable_height))
        if width > usable_width or height > usable_height:
            return None
        return {"width": width, "height": height}

    def select_candidate_positions(self, furniture_type: str, room_bbox: LayoutBoundingBox, size: dict) -> list[dict]:
        margin = self._room_margin(room_bbox)
        x_min = int(room_bbox.x_min + margin)
        y_min = int(room_bbox.y_min + margin)
        x_max = int(room_bbox.x_max - margin)
        y_max = int(room_bbox.y_max - margin)
        center_x = (x_min + x_max) // 2
        center_y = (y_min + y_max) // 2

        anchors = {
            "bottom-left": {"x": x_min, "y": y_max - size["height"]},
            "left-center": {"x": x_min, "y": center_y - size["height"] // 2},
            "bottom-center": {"x": center_x - size["width"] // 2, "y": y_max - size["height"]},
            "top-left": {"x": x_min, "y": y_min},
            "center": {"x": center_x - size["width"] // 2, "y": center_y - size["height"] // 2},
            "center-bottom": {"x": center_x - size["width"] // 2, "y": y_max - size["height"]},
            "top-right": {"x": x_max - size["width"], "y": y_min},
            "right-center": {"x": x_max - size["width"], "y": center_y - size["height"] // 2},
            "top-center": {"x": center_x - size["width"] // 2, "y": y_min},
            "bottom-right": {"x": x_max - size["width"], "y": y_max - size["height"]},
            "right-center-bed": {"x": x_max - size["width"], "y": center_y - size["height"] // 2},
            "left-edge": {"x": x_min, "y": center_y - size["height"] // 2},
            "bottom-edge": {"x": center_x - size["width"] // 2, "y": y_max - size["height"]},
            "top-edge": {"x": center_x - size["width"] // 2, "y": y_min},
        }

        type_candidates = {
            "sofa_1_seater": ["bottom-left", "left-center", "bottom-center", "top-left"],
            "sofa_2_seater": ["bottom-left", "left-center", "bottom-center", "top-left"],
            "sofa_3_seater": ["bottom-left", "left-center", "bottom-center", "top-left"],
            "sectional_sofa": ["bottom-left", "bottom-center", "left-center", "top-left"],
            "sofa_bed": ["bottom-left", "bottom-center", "left-center", "top-left"],
            "coffee_table": ["center", "center-bottom"],
            "tv": ["top-right", "right-center", "top-center"],
            "tv_stand": ["top-right", "right-center", "top-center"],
            "rug": ["center"],
            "potted_plant": ["bottom-right", "top-right", "bottom-left"],
            "floor_lamp": ["bottom-right", "top-right", "bottom-left"],
            "single_bed": ["right-center-bed", "left-center", "bottom-center", "top-center"],
            "semi_double_bed": ["right-center-bed", "left-center", "bottom-center", "top-center"],
            "double_bed": ["right-center-bed", "left-center", "bottom-center", "top-center"],
            "two_single_beds": ["right-center-bed", "left-center", "bottom-center", "top-center"],
            "pillow": ["center", "top-center", "bottom-center"],
            "blanket": ["center", "center-bottom"],
            "desk": ["bottom-left", "top-left", "bottom-right"],
            "shelf": ["bottom-left", "top-left", "bottom-right"],
            "kitchen_counter": ["left-edge", "bottom-edge", "top-edge"],
            "stove": ["left-edge", "bottom-edge", "top-edge"],
            "sink": ["left-edge", "bottom-edge", "top-edge"],
            "cabinet": ["left-edge", "top-edge", "bottom-edge"],
            "bathtub": ["left-center", "right-center", "center"],
            "shower": ["top-right", "bottom-right", "top-left"],
            "towel": ["top-right", "top-left", "right-center"],
        }
        order = type_candidates.get(furniture_type, ["center", "top-left", "top-right", "bottom-left", "bottom-right"])
        return [anchors[name] for name in order if name in anchors]

    def validate_candidate_bbox(
        self,
        candidate_bbox: LayoutBoundingBox,
        room_bbox: LayoutBoundingBox,
        placed_items: list[LayoutFurnitureObject],
        furniture_type: str,
        exclusion_zones: list[dict] | None = None,
    ) -> tuple[bool, list[str]]:
        notes: list[str] = []
        margin = self._room_margin(room_bbox)
        if (
            candidate_bbox.x_min < room_bbox.x_min + margin
            or candidate_bbox.y_min < room_bbox.y_min + margin
            or candidate_bbox.x_max > room_bbox.x_max - margin
            or candidate_bbox.y_max > room_bbox.y_max - margin
        ):
            return False, ["Candidate escaped room safe area."]

        for placed in placed_items:
            if placed.bbox is None:
                continue
            overlap = self.bbox_overlap_ratio(candidate_bbox, placed.bbox)
            allowed_overlap = self._allowed_placement_overlap(furniture_type, placed.type)
            if overlap > allowed_overlap:
                notes.append(f"Overlap {overlap:.2f} exceeded {allowed_overlap:.2f}.")
                return False, notes

        for zone in exclusion_zones or []:
            zone_bbox = zone.get("bbox")
            if zone_bbox is None:
                continue
            overlap = self.bbox_overlap_ratio(candidate_bbox, zone_bbox)
            allowed_overlap = self._allowed_exclusion_overlap(furniture_type, str(zone.get("category") or ""))
            if overlap > allowed_overlap:
                notes.append(f"Excluded zone overlap {overlap:.2f} exceeded {allowed_overlap:.2f} with {zone.get('label') or zone.get('category')}.")
                return False, notes
        return True, notes

    def build_exclusion_zones(
        self,
        layout: LayoutValidationArtifact,
        room,
        furniture_type: str,
        all_planned_items: list[LayoutFurnitureObject],
    ) -> list[dict]:
        target_bbox = getattr(room, "bbox", None)
        if room is None or target_bbox is None:
            return []

        zones: list[dict] = []
        normalized_room_type = self._normalize_room_type(getattr(room, "type", None))

        for other_room in layout.rooms:
            if getattr(other_room, "id", None) == getattr(room, "id", None) or other_room.bbox is None:
                continue
            other_type = self._normalize_room_type(other_room.type)
            if self.bbox_intersection_area(target_bbox, other_room.bbox) <= 0:
                continue
            if normalized_room_type == "living_room" and other_type in self.LIVING_ROOM_EXCLUDED_ROOM_TYPES:
                zones.append({"bbox": other_room.bbox, "category": "room", "label": other_type})

        for fixture in layout.fixtures:
            if fixture.bbox is None:
                continue
            fixture_type = self._normalize_room_type(getattr(fixture, "type", None))
            if self.bbox_intersection_area(target_bbox, fixture.bbox) <= 0:
                continue
            if furniture_type in self.KITCHEN_FURNITURE and fixture_type in {"kitchen", "kitchen_counter", "sink", "stove"}:
                continue
            if furniture_type in self.BATH_FURNITURE and fixture_type in {"bath_room", "bath"}:
                continue
            zones.append({"bbox": fixture.bbox, "category": "fixture", "label": fixture_type})

        for opening in list(layout.doors) + list(layout.windows):
            if opening.bbox is None:
                continue
            if self.bbox_intersection_area(target_bbox, opening.bbox) <= 0:
                continue
            zones.append({"bbox": opening.bbox, "category": getattr(opening, "type", "opening"), "label": getattr(opening, "type", "opening")})

        for item in all_planned_items:
            if item.bbox is None or item.room_id == getattr(room, "id", None):
                continue
            if self.bbox_intersection_area(target_bbox, item.bbox) <= 0:
                continue
            zones.append({"bbox": item.bbox, "category": "planned_furniture", "label": item.type})

        return zones

    def _find_supporting_bed(self, placed_items: list[LayoutFurnitureObject]) -> LayoutFurnitureObject | None:
        for item in placed_items:
            if item.type in {"bed", "two_single_beds"} and item.placement_status == "auto_placed" and item.bbox is not None:
                return item
        return None

    def _allowed_placement_overlap(self, type_a: str, type_b: str) -> float:
        pair = {type_a, type_b}
        if "rug" in pair:
            return 0.80
        if pair.intersection({"wall_art", "curtain"}):
            return 0.30
        if pair.intersection({"pillow", "blanket"}) and pair.intersection({"bed", "two_single_beds"}):
            return 0.95
        if pair.intersection({"sink", "stove", "cabinet"}) and "kitchen_counter" in pair:
            return 0.95
        return 0.10

    def _allowed_exclusion_overlap(self, furniture_type: str, category: str) -> float:
        if furniture_type in self.WALL_SIDE_FURNITURE and category == "window":
            return 0.30
        return 0.10

    def write_layout_furniture_planned(self, run_id: str, artifact: FurniturePlacementArtifact) -> None:
        path = self._artifacts_dir(run_id) / "layout_furniture_planned.json"
        try:
            with path.open("w", encoding="utf-8") as output_file:
                json.dump(artifact.model_dump(mode="json"), output_file, ensure_ascii=False, indent=2)
        except OSError as exc:
            raise HTTPException(status_code=500, detail="failed to write layout_furniture_planned artifact") from exc

    def build_metadata_updates(self, metadata: RunMetadata, artifact: FurniturePlacementArtifact) -> dict:
        return {
            "status": "furniture_placement_planned",
            "run_status": "furniture_placement_planned",
            "processing": metadata.processing.model_copy(
                update={
                    "layout_initial_creation": True,
                    "layout_validation": True,
                    "furniture_placement_planning": True,
                    "image_generation": False,
                    "watercolor_rendering": False,
                }
            ),
            "pipeline": {
                "current_phase": "phase_4c_furniture_placement_planning",
                "next_phase": "phase_4d_furniture_placement_validation",
            },
            "layout_furniture_planned_path": self._relative_artifact_path(metadata.run_id, "layout_furniture_planned.json"),
            "furniture_placement_summary": FurniturePlacementSummary(
                planning_status=artifact.planning_status,
                furniture_count=artifact.quality.furniture_count,
                furniture_placed_count=artifact.quality.furniture_placed_count,
                furniture_unplaced_count=artifact.quality.furniture_unplaced_count,
                placement_confidence_avg=artifact.quality.placement_confidence_avg,
                needs_human_review=artifact.quality.needs_human_review,
                warnings_count=len(artifact.warnings),
                errors_count=len(artifact.errors),
            ),
        }

    def _build_planned_artifact(self, layout: LayoutValidationArtifact) -> FurniturePlacementArtifact:
        furniture, placement, warnings, errors = self.plan_all_furniture(layout)
        placed_count = placement["placed_count"]
        unplaced_count = placement["unplaced_count"]
        invalid_count = placement["invalid_count"]
        furniture_count = len(furniture)
        avg_confidence = 0.0
        confidences = [float(getattr(item, "placement_confidence", 0.0) or 0.0) for item in furniture if getattr(item, "placement_status", None) == "auto_placed"]
        if confidences:
            avg_confidence = sum(confidences) / len(confidences)

        room_bbox_available = any(room.bbox is not None for room in layout.rooms)
        geometry_ready = bool(room_bbox_available and layout.rooms)
        quality = FurniturePlacementQualitySummary(
            needs_human_review=True,
            structure_locked=True,
            semantic_layout_only=True,
            pixel_perfect_geometry=False,
            furniture_placement_done=placed_count > 0,
            image_generation_done=False,
            watercolor_rendering_done=False,
            room_count=len(layout.rooms),
            fixture_count=len(layout.fixtures),
            label_count=len(layout.labels),
            furniture_count=furniture_count,
            furniture_placed_count=placed_count,
            furniture_unplaced_count=unplaced_count + invalid_count,
            placement_confidence_avg=round(avg_confidence, 4),
        )
        validation = {
            "layout_validated_loaded": True,
            "geometry_ready_for_furniture_planning": geometry_ready,
            "room_bbox_available": room_bbox_available,
            "furniture_objects_available": furniture_count > 0,
            "collision_check_passed": invalid_count == 0,
            "warnings_count": len(warnings),
            "errors_count": len(errors),
        }
        planning_status = "planned"
        if errors:
            planning_status = "failed"
        elif warnings or unplaced_count or invalid_count or placed_count == 0:
            planning_status = "planned_with_warnings"

        if furniture_count == 0:
            warnings.append("No furniture objects available for placement planning.")
        if not room_bbox_available:
            warnings.append("No rooms with valid bbox are available for furniture placement planning.")

        return FurniturePlacementArtifact(
            run_id=layout.run_id,
            generated_at=datetime.now(timezone.utc),
            planning_status=planning_status,
            source={
                "layout_validated_artifact": self._relative_artifact_path(layout.run_id, "layout_validated.json"),
                "layout_validated_preview_url": f"/{self._relative_artifact_path(layout.run_id, 'layout_validated.json')}",
                "normalized_floorplan_preview_url": layout.source.get("normalized_floorplan_preview_url"),
            },
            canvas=layout.canvas,
            layers=layout.layers,
            rooms=layout.rooms,
            fixtures=layout.fixtures,
            doors=layout.doors,
            windows=layout.windows,
            balcony=layout.balcony,
            labels=layout.labels,
            furniture=furniture,
            style=layout.style,
            constraints=layout.constraints,
            placement=placement,
            quality=quality,
            validation=validation,
            warnings=self._dedupe_keep_order(warnings),
            errors=self._dedupe_keep_order(errors),
        )

    def _resolve_target_room(self, furniture: LayoutFurnitureObject, room_lookup: dict[str, list]) -> tuple[object | None, list[str]]:
        notes: list[str] = []
        candidate_roles = self._candidate_room_roles_for_furniture(furniture.type)
        candidate_types = self._candidate_room_types_for_furniture(furniture.type)
        normalized_role = self._normalize_room_role(getattr(furniture, "room_functional_role", None))
        normalized_room_type = self._normalize_room_type(furniture.room_type)

        if furniture.room_id and furniture.room_id in room_lookup["by_id"]:
            room = room_lookup["by_id"][furniture.room_id]
            room_role = self._normalize_room_role(getattr(room, "functional_role", None))
            room_type = self._normalize_room_type(getattr(room, "type", None))
            if self._room_matches_furniture_targets(room_role, room_type, candidate_roles, candidate_types):
                return room, notes
            notes.append(
                f"Furniture {furniture.id}: original target room {furniture.room_id} conflicts with functional-role placement rules; reassigned."
            )

        if normalized_role != "unknown" and normalized_role in room_lookup["by_role"]:
            if normalized_role in candidate_roles or not candidate_roles:
                return room_lookup["by_role"][normalized_role][0], notes

        for fallback_role in candidate_roles:
            if fallback_role in room_lookup["by_role"]:
                notes.append(f"Furniture {furniture.id}: target room inferred from functional role.")
                return room_lookup["by_role"][fallback_role][0], notes

        if normalized_room_type != "unknown" and normalized_room_type in room_lookup["by_type"]:
            if normalized_room_type in candidate_types or not candidate_types:
                return room_lookup["by_type"][normalized_room_type][0], notes

        for fallback_type in candidate_types:
            if fallback_type in room_lookup["by_type"]:
                notes.append(f"Furniture {furniture.id}: target room inferred from furniture type.")
                return room_lookup["by_type"][fallback_type][0], notes

        notes.append(f"Furniture {furniture.id}: target room could not be resolved.")
        return None, notes

    @staticmethod
    def _room_matches_furniture_targets(
        room_role: str,
        room_type: str,
        candidate_roles: list[str],
        candidate_types: list[str],
    ) -> bool:
        role_match = bool(candidate_roles) and room_role in candidate_roles
        type_match = bool(candidate_types) and room_type in candidate_types
        if candidate_roles or candidate_types:
            return role_match or type_match
        return room_role != "unknown" or room_type != "unknown"

    def _candidate_room_types_for_furniture(self, furniture_type: str | None) -> list[str]:
        normalized = (furniture_type or "").strip().lower()
        if not normalized:
            return []
        if "bed" in normalized or normalized in {"pillow", "blanket"}:
            return ["bed_room"]
        if normalized in {"kitchen_counter", "sink", "stove", "cabinet"}:
            return ["kitchen"]
        if normalized in {"bathtub", "shower", "towel"}:
            return ["bath_room"]
        if normalized in {"dining_table", "chair"}:
            return ["dining", "living_room", "dining_kitchen", "kitchen"]
        if normalized in {"curtain", "wall_art", "potted_plant", "floor_lamp", "rug", "tv", "tv_stand", "coffee_table"}:
            return ["living_room", "dining_kitchen"]
        fallback_type = self.FURNITURE_ROOM_FALLBACKS.get(normalized)
        return [fallback_type] if fallback_type else []

    def _candidate_room_roles_for_furniture(self, furniture_type: str | None) -> list[str]:
        normalized = (furniture_type or "").strip().lower()
        if not normalized:
            return []
        if "bed" in normalized or normalized in {"pillow", "blanket"}:
            return ["main_bedroom", "guest_bedroom"]
        if normalized in {"kitchen_counter", "sink", "stove", "cabinet"}:
            return ["kitchen"]
        if normalized in {"bathtub", "shower", "towel"}:
            return ["bath_room"]
        if normalized in {"dining_table", "chair"}:
            return ["dining_zone", "living_dining"]
        if normalized in {"curtain", "wall_art", "potted_plant", "floor_lamp", "rug", "tv", "tv_stand", "coffee_table"} or normalized.startswith("sofa"):
            return ["media_lounge", "living_dining"]
        return []

    def _normalize_room_type(self, value: str | None) -> str:
        if not value:
            return "unknown"
        normalized = str(value).strip().lower().replace("_", " ")
        normalized = self.ROOM_TYPE_ALIASES.get(normalized, normalized)
        return normalized.replace(" ", "_")

    @staticmethod
    def _normalize_room_role(value: str | None) -> str:
        if not value:
            return "unknown"
        return str(value).strip().lower().replace(" ", "_")

    def _build_unknown_type_recovery_map(self, layout: LayoutValidationArtifact) -> dict[str, str]:
        present_by_room: dict[str, set[str]] = {}
        unknown_by_room: dict[str, list[LayoutFurnitureObject]] = {}
        for item in layout.furniture:
            room_type = self._normalize_room_type(item.room_type)
            if item.type and item.type != "unknown":
                present_by_room.setdefault(room_type, set()).add(str(item.type))
            elif item.source == "interior_analysis_validated":
                unknown_by_room.setdefault(room_type, []).append(item)

        recovered: dict[str, str] = {}
        for room_type, unknown_items in unknown_by_room.items():
            canonical = list(self.UNKNOWN_TYPE_RECOVERY_BY_ROOM.get(room_type, []))
            if not canonical:
                continue
            remaining = [item_type for item_type in canonical if item_type not in present_by_room.get(room_type, set())]
            for item, recovered_type in zip(unknown_items, remaining):
                recovered[item.id] = recovered_type
        return recovered

    @staticmethod
    def bbox_area(bbox: LayoutBoundingBox) -> int:
        return max(0, int((bbox.x_max - bbox.x_min) * (bbox.y_max - bbox.y_min)))

    @staticmethod
    def bbox_intersection_area(a: LayoutBoundingBox, b: LayoutBoundingBox) -> int:
        x_min = max(int(a.x_min), int(b.x_min))
        y_min = max(int(a.y_min), int(b.y_min))
        x_max = min(int(a.x_max), int(b.x_max))
        y_max = min(int(a.y_max), int(b.y_max))
        if x_min >= x_max or y_min >= y_max:
            return 0
        return (x_max - x_min) * (y_max - y_min)

    def bbox_overlap_ratio(self, a: LayoutBoundingBox, b: LayoutBoundingBox) -> float:
        intersection = self.bbox_intersection_area(a, b)
        denominator = min(self.bbox_area(a), self.bbox_area(b))
        if denominator <= 0:
            return 0.0
        return intersection / denominator

    @staticmethod
    def _room_margin(room_bbox: LayoutBoundingBox) -> int:
        room_width = int(room_bbox.x_max - room_bbox.x_min)
        room_height = int(room_bbox.y_max - room_bbox.y_min)
        return int(max(12, min(room_width, room_height) * 0.05))

    @staticmethod
    def _candidate_bbox_from_anchor(anchor: dict, size: dict) -> LayoutBoundingBox:
        x = int(anchor["x"])
        y = int(anchor["y"])
        width = int(size["width"])
        height = int(size["height"])
        return LayoutBoundingBox(
            x_min=max(0, min(1199, x)),
            y_min=max(0, min(1199, y)),
            x_max=max(0, min(1199, x + width)),
            y_max=max(0, min(1199, y + height)),
        )

    @staticmethod
    def _dedupe_keep_order(values: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for value in values:
            if value in seen:
                continue
            seen.add(value)
            result.append(value)
        return result

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

    def _relative_artifact_path(self, run_id: str, filename: str) -> str:
        return f"storage/runs/{run_id}/artifacts/{filename}"
