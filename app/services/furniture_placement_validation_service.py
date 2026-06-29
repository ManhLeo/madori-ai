from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException

from app.schemas.run import (
    FurniturePlacementArtifact,
    FurniturePlacementValidationArtifact,
    FurniturePlacementValidationQualitySummary,
    FurniturePlacementValidationSummary,
    LayoutBoundingBox,
    LayoutFurnitureObject,
    RunMetadata,
)


class FurniturePlacementValidationService:
    ALLOWED_PLACEMENT_STATUSES = {
        "suggested_unplaced",
        "auto_placed",
        "manually_placed",
        "suppressed_by_functional_role",
        "invalid",
    }
    SUPPORTED_FURNITURE_TYPES = {
        "two_single_beds",
        "bed",
        "pillow",
        "blanket",
        "side_table",
        "kitchen_counter",
        "sink",
        "stove",
        "cabinet",
        "washing_machine",
        "bathtub",
        "shower",
        "towel",
        "sofa_3_seater",
        "coffee_table",
        "tv",
        "tv_stand",
        "floor_lamp",
        "curtain",
        "wall_art",
        "potted_plant",
        "rug",
        "dining_table",
        "chair",
        "sofa_1_seater",
        "sofa_2_seater",
        "sectional_sofa",
        "sofa_bed",
        "unknown",
    }
    UNKNOWN_TYPE_RECOVERY_BY_ROOM = {
        "living_room": ["sofa_3_seater", "coffee_table", "tv", "tv_stand", "floor_lamp", "curtain", "wall_art", "potted_plant", "rug"],
        "dining": ["dining_table", "chair"],
        "bed_room": ["two_single_beds", "bed", "pillow", "blanket", "side_table", "curtain"],
        "kitchen": ["kitchen_counter", "sink", "stove", "cabinet"],
        "bath_room": ["bathtub", "shower", "towel"],
    }
    BEDDING_DETAIL_TYPES = {"pillow", "blanket"}
    BED_BASE_TYPES = {"bed", "two_single_beds"}
    KITCHEN_FURNITURE_TYPES = {"kitchen_counter", "sink", "stove", "cabinet"}
    BATH_FURNITURE_TYPES = {"bathtub", "shower", "towel"}
    CRITICAL_FIXTURE_TYPES = {"toilet", "bath_room", "bath", "door"}
    FIXTURE_TYPES = {"toilet", "bath_room", "bath", "kitchen", "sink", "stove", "kitchen_counter", "door", "window", "closet"}

    def __init__(self, storage_dir: Path, storage_runs_dir: Path) -> None:
        self.storage_dir = storage_dir
        self.storage_runs_dir = storage_runs_dir

    def validate_furniture_placement(self, metadata: RunMetadata) -> FurniturePlacementValidationArtifact:
        layout = self.load_layout_furniture_planned(metadata.run_id)
        artifact = self.validate_planned_layout(layout, metadata.run_id, metadata)
        self.write_layout_furniture_validated(metadata.run_id, artifact)
        return artifact

    def load_layout_furniture_planned(self, run_id: str) -> FurniturePlacementArtifact:
        path = self._artifacts_dir(run_id) / "layout_furniture_planned.json"
        if not path.exists():
            raise HTTPException(status_code=400, detail="Run furniture placement planning before furniture placement validation")
        try:
            return FurniturePlacementArtifact.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"invalid layout_furniture_planned.json: {exc}") from exc

    def load_layout_furniture_validated(self, run_id: str) -> FurniturePlacementValidationArtifact:
        path = self._artifacts_dir(run_id) / "layout_furniture_validated.json"
        if not path.exists():
            raise HTTPException(status_code=404, detail="layout_furniture_validated artifact not found")
        try:
            return FurniturePlacementValidationArtifact.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=500, detail="failed to read layout_furniture_validated artifact") from exc

    def validate_planned_layout(
        self,
        layout: FurniturePlacementArtifact,
        run_id: str,
        metadata: RunMetadata,
    ) -> FurniturePlacementValidationArtifact:
        warnings = list(layout.warnings)
        errors = list(layout.errors)

        rooms = [room.model_copy(update={"locked": True, "editable": False}) for room in layout.rooms]
        fixtures = [fixture.model_copy(update={"locked": True, "editable": False}) for fixture in layout.fixtures]
        doors = [door.model_copy(update={"locked": True, "editable": False}) for door in layout.doors]
        windows = [window.model_copy(update={"locked": True, "editable": False}) for window in layout.windows]
        balcony = [item.model_copy(update={"locked": True, "editable": False}) for item in layout.balcony]
        labels = [label.model_copy(update={"locked": False, "editable": True}) for label in layout.labels]

        furniture, placement_validation, item_warnings, item_errors = self.validate_furniture_objects(layout)
        warnings.extend(item_warnings)
        errors.extend(item_errors)
        warnings = self._filter_recovered_unknown_warnings(warnings, furniture)

        structure_warnings = self._validate_structure_locks(rooms, fixtures, doors, windows, balcony)
        warnings.extend(structure_warnings)
        label_warnings = self._validate_labels_editable(labels)
        warnings.extend(label_warnings)

        if not furniture:
            warnings.append("No furniture objects available for furniture placement validation.")

        validation_status = self._compute_validation_status(errors, warnings)
        auto_placed_count = placement_validation["auto_placed_count"]
        manually_placed_count = placement_validation["manually_placed_count"]
        placed_count = auto_placed_count + manually_placed_count
        furniture_count = len(furniture)
        avg_confidence = 0.0
        confidences = [
            float(getattr(item, "placement_confidence", 0.0) or 0.0)
            for item in furniture
            if item.placement_status in {"auto_placed", "manually_placed"}
        ]
        if confidences:
            avg_confidence = sum(confidences) / len(confidences)

        quality = FurniturePlacementValidationQualitySummary(
            needs_human_review=True,
            structure_locked=True,
            semantic_layout_only=True,
            pixel_perfect_geometry=False,
            furniture_placement_done=placed_count > 0 and placement_validation["outside_room_count"] == 0 and placement_validation["invalid_count"] == 0 and placement_validation["bbox_consistency_error_count"] == 0,
            furniture_placement_validated=True,
            image_generation_done=False,
            watercolor_rendering_done=False,
            room_count=len(rooms),
            fixture_count=len(fixtures),
            label_count=len(labels),
            furniture_count=furniture_count,
            furniture_placed_count=placed_count,
            furniture_unplaced_count=placement_validation["suggested_unplaced_count"] + placement_validation["invalid_count"],
            placement_confidence_avg=round(avg_confidence, 4),
        )
        validation = {
            "layout_furniture_planned_loaded": True,
            "furniture_objects_available": furniture_count > 0,
            "auto_placed_furniture_has_bbox": placement_validation["auto_placed_missing_bbox_count"] == 0,
            "auto_placed_furniture_inside_room": placement_validation["outside_room_count"] == 0,
            "bbox_consistency_valid": placement_validation["bbox_consistency_error_count"] == 0,
            "furniture_editable": all(item.locked is False and item.editable is True for item in furniture),
            "structure_objects_locked": all(item.locked is True and item.editable is False for item in rooms + fixtures + doors + windows + balcony),
            "overlap_rules_valid": placement_validation["severe_overlap_error_count"] == 0,
            "fixture_overlap_rules_valid": placement_validation["critical_fixture_overlap_error_count"] == 0,
            "warnings_count": len(warnings),
            "errors_count": len(errors),
        }

        return FurniturePlacementValidationArtifact(
            run_id=run_id,
            generated_at=datetime.now(timezone.utc),
            validation_status=validation_status,
            source={
                "layout_furniture_planned_artifact": self._relative_artifact_path(run_id, "layout_furniture_planned.json"),
                "layout_furniture_planned_preview_url": f"/{self._relative_artifact_path(run_id, 'layout_furniture_planned.json')}",
                "normalized_floorplan_preview_url": self._normalized_floorplan_preview_url(run_id),
            },
            canvas=layout.canvas,
            layers=layout.layers,
            rooms=rooms,
            fixtures=fixtures,
            doors=doors,
            windows=windows,
            balcony=balcony,
            labels=labels,
            furniture=furniture,
            style=layout.style,
            constraints=list(layout.constraints or []),
            placement_validation=placement_validation,
            quality=quality,
            validation=validation,
            warnings=self._dedupe_keep_order(warnings),
            errors=self._dedupe_keep_order(errors),
        )

    def validate_furniture_objects(
        self,
        layout: FurniturePlacementArtifact,
    ) -> tuple[list[LayoutFurnitureObject], dict, list[str], list[str]]:
        room_lookup = self.build_room_lookup(layout)
        fixtures = self._collect_fixture_like_objects(layout)
        recovered_types = self._build_unknown_type_recovery_map(layout)
        warnings: list[str] = []
        errors: list[str] = []
        validated: list[LayoutFurnitureObject] = []
        placed_items: list[LayoutFurnitureObject] = []

        counters = {
            "validated_count": 0,
            "auto_placed_count": 0,
            "manually_placed_count": 0,
            "suggested_unplaced_count": 0,
            "invalid_count": 0,
            "inside_room_count": 0,
            "outside_room_count": 0,
            "bbox_consistency_error_count": 0,
            "overlap_warning_count": 0,
            "fixture_overlap_warning_count": 0,
            "auto_placed_missing_bbox_count": 0,
            "severe_overlap_error_count": 0,
            "critical_fixture_overlap_error_count": 0,
        }

        for furniture in layout.furniture:
            updated, item_warnings, item_errors, item_counts = self.validate_single_furniture(
                furniture,
                room_lookup,
                placed_items,
                fixtures,
                recovered_types.get(furniture.id),
            )
            validated.append(updated)
            if updated.placement_status in {"auto_placed", "manually_placed"} and updated.bbox is not None:
                placed_items.append(updated)
            warnings.extend(item_warnings)
            errors.extend(item_errors)
            counters["validated_count"] += 1
            for key, value in item_counts.items():
                counters[key] += value

        placement_validation = {
            "algorithm": "deterministic_furniture_validation_v1",
            **counters,
        }
        return validated, placement_validation, self._dedupe_keep_order(warnings), self._dedupe_keep_order(errors)

    def validate_single_furniture(
        self,
        furniture: LayoutFurnitureObject,
        room_lookup: dict[str, list],
        placed_items: list[LayoutFurnitureObject],
        fixtures: list[dict],
        recovered_type: str | None = None,
    ) -> tuple[LayoutFurnitureObject, list[str], list[str], dict[str, int]]:
        warnings: list[str] = []
        errors: list[str] = []
        counts = {
            "auto_placed_count": 0,
            "manually_placed_count": 0,
            "suggested_unplaced_count": 0,
            "invalid_count": 0,
            "inside_room_count": 0,
            "outside_room_count": 0,
            "bbox_consistency_error_count": 0,
            "overlap_warning_count": 0,
            "fixture_overlap_warning_count": 0,
            "auto_placed_missing_bbox_count": 0,
            "severe_overlap_error_count": 0,
            "critical_fixture_overlap_error_count": 0,
        }

        effective_type = str(recovered_type or furniture.type or "unknown")
        if effective_type not in self.SUPPORTED_FURNITURE_TYPES:
            effective_type = "unknown"

        if effective_type != furniture.type:
            furniture = furniture.model_copy(update={"type": effective_type})

        room, room_warning = self._resolve_target_room(furniture, room_lookup)
        if room_warning:
            warnings.append(f"Furniture {furniture.id}: {room_warning}")

        placement_status = furniture.placement_status
        if placement_status not in self.ALLOWED_PLACEMENT_STATUSES:
            warnings.append(f"Furniture {furniture.id}: Unknown placement_status normalized to invalid.")
            placement_status = "invalid"

        bbox = self._normalize_bbox(furniture.bbox)
        target_room_bbox = self._normalize_bbox(furniture.target_room_bbox) or self._normalize_bbox(getattr(room, "bbox", None))
        update: dict = {
            "locked": False,
            "editable": True,
            "placement_status": placement_status,
            "bbox": bbox,
            "target_room_bbox": target_room_bbox,
        }

        if furniture.type in {"dining_table", "chair"} and getattr(room, "type", None) == "living_room":
            warnings.append("Dining furniture mapped to living_room because no dining_kitchen room was available.")
        if furniture.type == "washing_machine" and getattr(room, "type", None) not in {"wash_area", "wash_room", "washroom"}:
            errors.append(f"Furniture {furniture.id}: washing_machine must stay in Wash Room.")
            placement_status = "invalid"
            update["placement_status"] = "invalid"

        if placement_status == "suppressed_by_functional_role":
            note = "Suppressed by functional role cleanup; skipped during placement validation."
            update.update(
                {
                    "placement_status": "suppressed_by_functional_role",
                    "placement_notes": [note],
                    "bbox": None,
                    "target_room_bbox": None,
                    "x": None,
                    "y": None,
                    "width": None,
                    "height": None,
                }
            )
            counts["suggested_unplaced_count"] += 1
            return furniture.model_copy(update=update), warnings, errors, counts

        if placement_status in {"auto_placed", "manually_placed"}:
            if bbox is None:
                errors.append(f"Furniture {furniture.id}: placed furniture is missing bbox.")
                placement_status = "invalid"
                update["placement_status"] = "invalid"
                counts["auto_placed_missing_bbox_count"] += 1
                counts["invalid_count"] += 1
            else:
                bbox_ok, bbox_warnings = self.validate_bbox_consistency(furniture, bbox)
                warnings.extend([f"Furniture {furniture.id}: {message}" for message in bbox_warnings])
                if not bbox_ok:
                    counts["bbox_consistency_error_count"] += 1

                if target_room_bbox is None:
                    errors.append(f"Furniture {furniture.id}: target_room_bbox missing for placed furniture.")
                    placement_status = "invalid"
                    update["placement_status"] = "invalid"
                    counts["outside_room_count"] += 1
                    counts["invalid_count"] += 1
                else:
                    inside_room, room_messages = self.validate_bbox_inside_room(bbox, target_room_bbox)
                    warnings.extend([f"Furniture {furniture.id}: {message}" for message in room_messages[:-1]])
                    if not inside_room:
                        errors.append(f"Furniture {furniture.id}: {room_messages[-1]}")
                        placement_status = "invalid"
                        update["placement_status"] = "invalid"
                        counts["outside_room_count"] += 1
                        counts["invalid_count"] += 1
                    else:
                        counts["inside_room_count"] += 1

                overlap_ok, overlap_messages, severe_overlap = self.validate_overlap(furniture.type, bbox, placed_items)
                if overlap_messages:
                    counts["overlap_warning_count"] += len(overlap_messages)
                    warnings.extend([f"Furniture {furniture.id}: {message}" for message in overlap_messages])
                if severe_overlap:
                    counts["severe_overlap_error_count"] += 1

                fixture_ok, fixture_messages, critical_fixture_error = self.validate_fixture_overlap(
                    furniture.type,
                    bbox,
                    fixtures,
                    furniture.room_type,
                )
                if fixture_messages:
                    counts["fixture_overlap_warning_count"] += len(fixture_messages)
                    warnings.extend([f"Furniture {furniture.id}: {message}" for message in fixture_messages])
                if critical_fixture_error:
                    counts["critical_fixture_overlap_error_count"] += 1
                    errors.append(f"Furniture {furniture.id}: critical fixture overlap exceeded allowed ratio.")

                bbox_payload = bbox.model_dump(mode="json")
                update["x"] = int(bbox_payload["x_min"])
                update["y"] = int(bbox_payload["y_min"])
                update["width"] = int(bbox_payload["x_max"] - bbox_payload["x_min"])
                update["height"] = int(bbox_payload["y_max"] - bbox_payload["y_min"])
                update["placement_status"] = placement_status

            if placement_status == "auto_placed":
                counts["auto_placed_count"] += 1
            elif placement_status == "manually_placed":
                counts["manually_placed_count"] += 1
        elif placement_status == "suggested_unplaced":
            counts["suggested_unplaced_count"] += 1
            update["bbox"] = None if bbox is None else bbox
        else:
            counts["invalid_count"] += 1

        updated = furniture.model_copy(update=update)
        return updated, warnings, errors, counts

    def build_room_lookup(self, layout: FurniturePlacementArtifact) -> dict[str, list]:
        room_ids: dict[str, object] = {}
        room_types: dict[str, list] = {}
        for room in layout.rooms:
            room_ids[room.id] = room
            room_types.setdefault(room.type, []).append(room)
        return {"by_id": room_ids, "by_type": room_types}

    def validate_bbox_inside_room(self, bbox: LayoutBoundingBox, room_bbox: LayoutBoundingBox) -> tuple[bool, list[str]]:
        messages: list[str] = []
        inside = (
            bbox.x_min >= room_bbox.x_min
            and bbox.y_min >= room_bbox.y_min
            and bbox.x_max <= room_bbox.x_max
            and bbox.y_max <= room_bbox.y_max
        )
        if not inside:
            messages.append("bbox is outside target_room_bbox.")
        return inside, messages

    def validate_bbox_consistency(self, furniture: LayoutFurnitureObject, bbox: LayoutBoundingBox) -> tuple[bool, list[str]]:
        messages: list[str] = []
        expected_x = int(bbox.x_min)
        expected_y = int(bbox.y_min)
        expected_width = int(bbox.x_max - bbox.x_min)
        expected_height = int(bbox.y_max - bbox.y_min)
        if furniture.x != expected_x or furniture.y != expected_y or furniture.width != expected_width or furniture.height != expected_height:
            messages.append("x/y/width/height were inconsistent and normalized from bbox.")
        return True, messages

    def validate_overlap(
        self,
        furniture_type: str,
        bbox: LayoutBoundingBox,
        placed_items: list[LayoutFurnitureObject],
    ) -> tuple[bool, list[str], bool]:
        messages: list[str] = []
        severe_overlap = False
        for placed in placed_items:
            if placed.bbox is None:
                continue
            overlap = self._bbox_overlap_ratio(bbox, placed.bbox)
            allowed_overlap = self._allowed_overlap_ratio(furniture_type, placed.type)
            if overlap > allowed_overlap:
                messages.append(f"overlap ratio {overlap:.2f} exceeded allowed {allowed_overlap:.2f} with {placed.id}.")
            if (
                furniture_type != "rug"
                and placed.type != "rug"
                and not self._is_semantic_furniture_overlap(furniture_type, placed.type)
                and overlap > 0.80
            ):
                severe_overlap = True
        return not severe_overlap, messages, severe_overlap

    def validate_fixture_overlap(
        self,
        furniture_type: str,
        bbox: LayoutBoundingBox,
        fixtures: list[dict],
        room_type: str | None = None,
    ) -> tuple[bool, list[str], bool]:
        messages: list[str] = []
        critical_fixture_error = False
        for fixture in fixtures:
            fixture_bbox = fixture.get("bbox")
            fixture_type = str(fixture.get("type") or "unknown")
            if fixture_bbox is None:
                continue
            overlap = self._bbox_overlap_ratio(bbox, fixture_bbox)
            if overlap <= 0.10:
                continue
            messages.append(f"fixture overlap ratio {overlap:.2f} exceeded allowed 0.10 with {fixture_type}.")
            if self._is_semantic_fixture_overlap(furniture_type, room_type, fixture_type):
                continue
            if fixture_type in self.CRITICAL_FIXTURE_TYPES and overlap > 0.50:
                critical_fixture_error = True
        return not critical_fixture_error, messages, critical_fixture_error

    def write_layout_furniture_validated(self, run_id: str, artifact: FurniturePlacementValidationArtifact) -> None:
        path = self._artifacts_dir(run_id) / "layout_furniture_validated.json"
        try:
            with path.open("w", encoding="utf-8") as output_file:
                json.dump(artifact.model_dump(mode="json"), output_file, ensure_ascii=False, indent=2)
        except OSError as exc:
            raise HTTPException(status_code=500, detail="failed to write layout_furniture_validated artifact") from exc

    def build_metadata_updates(self, metadata: RunMetadata, artifact: FurniturePlacementValidationArtifact) -> dict:
        pv = artifact.placement_validation
        return {
            "status": "furniture_placement_validated",
            "run_status": "furniture_placement_validated",
            "processing": metadata.processing.model_copy(
                update={
                    "layout_initial_creation": True,
                    "layout_validation": True,
                    "furniture_placement_planning": True,
                    "furniture_placement_validation": True,
                    "image_generation": False,
                    "watercolor_rendering": False,
                }
            ),
            "pipeline": {
                "current_phase": "phase_4d_furniture_placement_validation",
                "next_phase": "phase_5a_render_plan_creation",
            },
            "layout_furniture_validated_path": self._relative_artifact_path(metadata.run_id, "layout_furniture_validated.json"),
            "furniture_placement_validation_summary": FurniturePlacementValidationSummary(
                validation_status=artifact.validation_status,
                furniture_count=artifact.quality.furniture_count,
                auto_placed_count=int(pv.get("auto_placed_count", 0)),
                suggested_unplaced_count=int(pv.get("suggested_unplaced_count", 0)),
                invalid_count=int(pv.get("invalid_count", 0)),
                inside_room_count=int(pv.get("inside_room_count", 0)),
                outside_room_count=int(pv.get("outside_room_count", 0)),
                overlap_warning_count=int(pv.get("overlap_warning_count", 0)),
                fixture_overlap_warning_count=int(pv.get("fixture_overlap_warning_count", 0)),
                needs_human_review=artifact.quality.needs_human_review,
                warnings_count=len(artifact.warnings),
                errors_count=len(artifact.errors),
            ),
        }

    def _resolve_target_room(self, furniture: LayoutFurnitureObject, room_lookup: dict[str, list]) -> tuple[object | None, str | None]:
        if furniture.room_id and furniture.room_id in room_lookup["by_id"]:
            return room_lookup["by_id"][furniture.room_id], None
        if furniture.room_type and furniture.room_type in room_lookup["by_type"]:
            return room_lookup["by_type"][furniture.room_type][0], "room matched by room_type because room_id was missing or invalid."
        return None, "target room could not be resolved."

    def _collect_fixture_like_objects(self, layout: FurniturePlacementArtifact) -> list[dict]:
        fixture_entries: list[dict] = []
        for fixture in layout.fixtures:
            if fixture.type in self.FIXTURE_TYPES:
                fixture_entries.append({"type": fixture.type, "bbox": self._normalize_bbox(fixture.bbox)})
        for door in layout.doors:
            fixture_entries.append({"type": "door", "bbox": self._normalize_bbox(door.bbox)})
        for window in layout.windows:
            fixture_entries.append({"type": "window", "bbox": self._normalize_bbox(window.bbox)})
        for balcony in layout.balcony:
            if balcony.type == "closet":
                fixture_entries.append({"type": "closet", "bbox": self._normalize_bbox(balcony.bbox)})
        return fixture_entries

    @staticmethod
    def _validate_structure_locks(rooms, fixtures, doors, windows, balcony) -> list[str]:
        warnings: list[str] = []
        for collection_name, collection in (
            ("room", rooms),
            ("fixture", fixtures),
            ("door", doors),
            ("window", windows),
            ("balcony", balcony),
        ):
            for item in collection:
                if item.locked is not True or item.editable is not False:
                    warnings.append(f"{collection_name} {item.id} structure lock was normalized.")
        return warnings

    @staticmethod
    def _validate_labels_editable(labels) -> list[str]:
        warnings: list[str] = []
        for item in labels:
            if item.locked is not False or item.editable is not True:
                warnings.append(f"label {item.id} editability was normalized.")
        return warnings

    @staticmethod
    def _normalize_bbox(bbox) -> LayoutBoundingBox | None:
        if bbox is None:
            return None
        if isinstance(bbox, LayoutBoundingBox):
            payload = bbox.model_dump(mode="json")
        elif isinstance(bbox, dict):
            payload = dict(bbox)
        else:
            return None
        try:
            x_min = max(0, min(1199, int(float(payload.get("x_min")))))
            y_min = max(0, min(1199, int(float(payload.get("y_min")))))
            x_max = max(0, min(1199, int(float(payload.get("x_max")))))
            y_max = max(0, min(1199, int(float(payload.get("y_max")))))
        except (TypeError, ValueError):
            return None
        if x_min >= x_max or y_min >= y_max:
            return None
        return LayoutBoundingBox(x_min=x_min, y_min=y_min, x_max=x_max, y_max=y_max)

    @staticmethod
    def _allowed_overlap_ratio(type_a: str, type_b: str) -> float:
        pair = {type_a, type_b}
        if "rug" in pair and pair.intersection({"sofa_1_seater", "sofa_2_seater", "sofa_3_seater", "sectional_sofa", "sofa_bed", "coffee_table"}):
            return 0.80
        if pair.intersection({"pillow", "blanket"}) and pair.intersection({"bed", "two_single_beds"}):
            return 0.95
        if type_a in {"curtain", "wall_art"} or type_b in {"curtain", "wall_art"}:
            return 0.30
        return 0.10

    def _is_semantic_fixture_overlap(self, furniture_type: str, room_type: str | None, fixture_type: str) -> bool:
        normalized_room_type = str(room_type or "")
        if furniture_type in self.BATH_FURNITURE_TYPES and normalized_room_type == "bath_room" and fixture_type in {"bath_room", "bath"}:
            return True
        if furniture_type in self.KITCHEN_FURNITURE_TYPES and normalized_room_type == "kitchen" and fixture_type in {"kitchen", "kitchen_counter", "sink", "stove"}:
            return True
        if furniture_type in self.BATH_FURNITURE_TYPES.union(self.KITCHEN_FURNITURE_TYPES) and fixture_type == "door":
            return True
        return False

    @staticmethod
    def _is_semantic_furniture_overlap(type_a: str, type_b: str) -> bool:
        pair = {type_a, type_b}
        return bool(pair.intersection({"pillow", "blanket"}) and pair.intersection({"bed", "two_single_beds"}))

    def _build_unknown_type_recovery_map(self, layout: FurniturePlacementArtifact) -> dict[str, str]:
        present_by_room: dict[str, set[str]] = {}
        unknown_by_room: dict[str, list[LayoutFurnitureObject]] = {}
        for item in layout.furniture:
            room_type = str(item.room_type or "")
            if item.type and item.type != "unknown":
                present_by_room.setdefault(room_type, set()).add(str(item.type))
            elif item.source == "interior_analysis_validated":
                unknown_by_room.setdefault(room_type, []).append(item)

        recovered: dict[str, str] = {}
        for room_type, unknown_items in unknown_by_room.items():
            canonical = list(self.UNKNOWN_TYPE_RECOVERY_BY_ROOM.get(room_type, []))
            if not canonical:
                continue
            used = set(present_by_room.get(room_type, set()))
            remaining = [item_type for item_type in canonical if item_type not in used]
            for item, recovered_type in zip(unknown_items, remaining):
                recovered[item.id] = recovered_type
        return recovered

    @staticmethod
    def _filter_recovered_unknown_warnings(warnings: list[str], furniture: list[LayoutFurnitureObject]) -> list[str]:
        recovered_ids = {
            item.id
            for item in furniture
            if item.type != "unknown"
        }
        filtered: list[str] = []
        for warning in warnings:
            if "has unsupported type; normalized to unknown." in warning:
                matched_id = next((item_id for item_id in recovered_ids if item_id in warning), None)
                if matched_id is not None:
                    continue
            filtered.append(warning)
        return filtered

    @staticmethod
    def _bbox_area(bbox: LayoutBoundingBox) -> int:
        return max(0, int((bbox.x_max - bbox.x_min) * (bbox.y_max - bbox.y_min)))

    def _bbox_overlap_ratio(self, a: LayoutBoundingBox, b: LayoutBoundingBox) -> float:
        x_min = max(int(a.x_min), int(b.x_min))
        y_min = max(int(a.y_min), int(b.y_min))
        x_max = min(int(a.x_max), int(b.x_max))
        y_max = min(int(a.y_max), int(b.y_max))
        if x_min >= x_max or y_min >= y_max:
            return 0.0
        intersection = (x_max - x_min) * (y_max - y_min)
        denominator = min(self._bbox_area(a), self._bbox_area(b))
        if denominator <= 0:
            return 0.0
        return intersection / denominator

    @staticmethod
    def _compute_validation_status(errors: list[str], warnings: list[str]) -> str:
        if errors:
            return "failed"
        if warnings:
            return "passed_with_warnings"
        return "passed"

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

    def _normalized_floorplan_preview_url(self, run_id: str) -> str | None:
        path = self._artifacts_dir(run_id) / "normalized_floorplan.png"
        if not path.exists():
            return None
        return f"/{self._relative_artifact_path(run_id, 'normalized_floorplan.png')}"

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
