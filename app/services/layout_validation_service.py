from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException

from app.schemas.run import (
    LayoutBoundingBox,
    LayoutConnectionObject,
    LayoutFixtureObject,
    LayoutFurnitureObject,
    LayoutInitialArtifact,
    LayoutLabelObject,
    LayoutLayerConfig,
    LayoutRoomObject,
    LayoutStyleObject,
    LayoutSummary,
    LayoutValidationArtifact,
    LayoutValidationQualitySummary,
    LayoutValidationSummary,
    RunMetadata,
)


class LayoutValidationService:
    ALLOWED_LABELS = {
        "Living Room",
        "Kitchen",
        "Closet",
        "Toilet",
        "Entrance",
        "Bed Room",
        "Bath Room",
        "Wash Room",
        "Dining Kitchen",
        "Balcony",
        "Hallway",
        "Storage",
        "Unknown",
    }
    ALLOWED_FURNITURE_TYPES = {
        "sofa_1_seater",
        "sofa_2_seater",
        "sofa_3_seater",
        "sectional_sofa",
        "sofa_bed",
        "single_bed",
        "semi_double_bed",
        "double_bed",
        "two_single_beds",
        "coffee_table",
        "dining_table",
        "chair",
        "tv",
        "tv_stand",
        "rug",
        "curtain",
        "potted_plant",
        "floor_lamp",
        "wall_art",
        "shelf",
        "desk",
        "kitchen_counter",
        "stove",
        "sink",
        "unknown",
    }
    ALLOWED_PLACEMENT_STATUSES = {
        "suggested_unplaced",
        "auto_placed",
        "manually_placed",
        "suppressed_by_functional_role",
        "invalid",
    }
    ALLOWED_FLOOR_TONES = {"white", "light_brown", "dark_brown", "unknown"}
    REQUIRED_LAYER_RULES = {
        "reference_floorplan": {"locked": True, "editable": False, "visible": True},
        "structure": {"locked": True, "editable": False, "visible": True},
        "rooms": {"locked": True, "editable": False, "visible": True},
        "fixtures": {"locked": True, "editable": False, "visible": True},
        "doors": {"locked": True, "editable": False, "visible": True},
        "windows": {"locked": True, "editable": False, "visible": True},
        "balcony": {"locked": True, "editable": False, "visible": True},
        "furniture": {"locked": False, "editable": True, "visible": True},
        "labels": {"locked": False, "editable": True, "visible": True},
        "style": {"locked": False, "editable": True, "visible": True},
    }

    def __init__(self, storage_dir: Path, storage_runs_dir: Path) -> None:
        self.storage_dir = storage_dir
        self.storage_runs_dir = storage_runs_dir

    def validate_layout(self, metadata: RunMetadata) -> LayoutValidationArtifact:
        run_id = metadata.run_id
        try:
            layout = self.load_layout_initial(run_id)
            artifact = self.normalize_layout(layout, run_id, metadata)
        except HTTPException as exc:
            if exc.status_code == 400 and "invalid layout_initial.json" in str(exc.detail):
                artifact = self._build_failed_artifact(
                    run_id=run_id,
                    source={
                        "layout_initial_artifact": self._relative_artifact_path(run_id, "layout_initial.json"),
                        "layout_initial_preview_url": f"/{self._relative_artifact_path(run_id, 'layout_initial.json')}",
                        "normalized_floorplan_preview_url": self._normalized_floorplan_preview_url(run_id),
                    },
                    errors=[str(exc.detail)],
                )
            else:
                raise
        except Exception as exc:
            artifact = self._build_failed_artifact(
                run_id=run_id,
                source={
                    "layout_initial_artifact": self._relative_artifact_path(run_id, "layout_initial.json"),
                    "layout_initial_preview_url": f"/{self._relative_artifact_path(run_id, 'layout_initial.json')}",
                    "normalized_floorplan_preview_url": self._normalized_floorplan_preview_url(run_id),
                },
                errors=[f"failed to validate layout_initial.json: {exc}"],
            )
        self.write_layout_validated(run_id, artifact)
        return artifact

    def load_layout_initial(self, run_id: str) -> LayoutInitialArtifact:
        path = self._artifacts_dir(run_id) / "layout_initial.json"
        if not path.exists():
            raise HTTPException(status_code=400, detail="Run initial layout creation before layout validation")
        try:
            return LayoutInitialArtifact.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            try:
                raw_payload = json.loads(path.read_text(encoding="utf-8"))
                return LayoutInitialArtifact.model_validate(raw_payload)
            except Exception as nested_exc:
                raise HTTPException(status_code=400, detail=f"invalid layout_initial.json: {nested_exc}") from exc

    def load_layout_validated(self, run_id: str) -> LayoutValidationArtifact:
        path = self._artifacts_dir(run_id) / "layout_validated.json"
        if not path.exists():
            raise HTTPException(status_code=404, detail="layout_validated artifact not found")
        try:
            return LayoutValidationArtifact.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=500, detail="failed to read layout_validated artifact") from exc

    def normalize_layout(
        self,
        layout: LayoutInitialArtifact,
        run_id: str,
        metadata: RunMetadata,
    ) -> LayoutValidationArtifact:
        warnings = list(layout.warnings)
        errors = list(layout.errors)

        canvas, canvas_warnings, canvas_errors = self.validate_canvas(layout.canvas)
        warnings.extend(canvas_warnings)
        errors.extend(canvas_errors)

        layers, layer_warnings, layer_errors = self.validate_layers(layout.layers)
        warnings.extend(layer_warnings)
        errors.extend(layer_errors)

        rooms, fixtures, doors, windows, balcony, structure_warnings, structure_errors, structure_lock_valid = (
            self.validate_structure_objects(layout)
        )
        warnings.extend(structure_warnings)
        errors.extend(structure_errors)

        labels, label_warnings, label_errors, labels_editable = self.validate_label_objects(layout.labels)
        warnings.extend(label_warnings)
        errors.extend(label_errors)

        furniture, furniture_warnings, furniture_errors, furniture_editable, furniture_placement_done = (
            self.validate_furniture_objects(layout.furniture)
        )
        warnings.extend(furniture_warnings)
        errors.extend(furniture_errors)

        style, style_warnings, style_errors, style_valid = self.validate_style_object(layout.style)
        warnings.extend(style_warnings)
        errors.extend(style_errors)

        constraints = list(layout.constraints or [])
        if not rooms:
            warnings.append("No rooms found in validated layout.")
        if not labels:
            warnings.append("No labels found in validated layout.")
        if not furniture:
            warnings.append("No furniture suggestions found in validated layout.")
        if style.floor_tone == "unknown":
            warnings.append("Floor tone is unknown.")
        if not style.avoid_keywords:
            warnings.append("avoid_keywords is empty.")
        warnings.append("semantic_layout_only is true")

        required_fields_present = True
        if not rooms and not fixtures and not doors and not windows and not balcony:
            required_fields_present = False
            errors.append("Required structure objects are completely missing.")

        validation_status = self._compute_validation_status(errors, warnings, canvas)
        quality = LayoutValidationQualitySummary(
            needs_human_review=True,
            structure_locked=structure_lock_valid,
            semantic_layout_only=True,
            pixel_perfect_geometry=False,
            furniture_placement_done=furniture_placement_done,
            image_generation_done=False,
            watercolor_rendering_done=False,
            room_count=len(rooms),
            fixture_count=len(fixtures),
            label_count=len(labels),
            furniture_suggestion_count=len(furniture),
            canvas_valid=(canvas.get("width") == 1200 and canvas.get("height") == 1200 and canvas.get("coordinate_space") == "normalized_floorplan_1200"),
            structure_lock_valid=structure_lock_valid,
            editable_object_rules_valid=labels_editable and furniture_editable,
            style_valid=style_valid,
        )
        validation = {
            "required_fields_present": required_fields_present,
            "canvas_valid": quality.canvas_valid,
            "layer_rules_valid": True,
            "structure_objects_locked": structure_lock_valid,
            "labels_editable": labels_editable,
            "furniture_editable": furniture_editable,
            "style_normalized": style_valid,
            "warnings_count": len(warnings),
            "errors_count": len(errors),
        }

        artifact = LayoutValidationArtifact(
            run_id=run_id,
            generated_at=datetime.now(timezone.utc),
            validation_status=validation_status,
            source={
                "layout_initial_artifact": self._relative_artifact_path(run_id, "layout_initial.json"),
                "layout_initial_preview_url": f"/{self._relative_artifact_path(run_id, 'layout_initial.json')}",
                "normalized_floorplan_preview_url": self._normalized_floorplan_preview_url(run_id),
            },
            canvas=canvas,
            layers=layers,
            rooms=rooms,
            fixtures=fixtures,
            doors=doors,
            windows=windows,
            balcony=balcony,
            labels=labels,
            furniture=furniture,
            style=style,
            constraints=constraints,
            quality=quality,
            validation=validation,
            warnings=self._dedupe_keep_order(warnings),
            errors=self._dedupe_keep_order(errors),
        )
        if validation_status == "failed" and not artifact.errors:
            artifact.errors.append("Layout validation failed.")
        return artifact

    def validate_canvas(self, layout_canvas: dict) -> tuple[dict, list[str], list[str]]:
        warnings: list[str] = []
        errors: list[str] = []
        canvas = dict(layout_canvas or {})
        width = self._coerce_int(canvas.get("width"), default=1200)
        height = self._coerce_int(canvas.get("height"), default=1200)
        if canvas.get("width") is None or canvas.get("height") is None:
            warnings.append("Canvas width/height missing; normalized to 1200x1200.")
        if width != 1200 or height != 1200:
            warnings.append("Canvas size normalized to 1200x1200.")
            width = 1200
            height = 1200
        coordinate_space = canvas.get("coordinate_space") or "normalized_floorplan_1200"
        if coordinate_space != "normalized_floorplan_1200":
            warnings.append("Canvas coordinate_space normalized to normalized_floorplan_1200.")
            coordinate_space = "normalized_floorplan_1200"
        background_color = canvas.get("background_color") or "white"
        return {
            "width": width,
            "height": height,
            "coordinate_space": coordinate_space,
            "background_color": background_color,
        }, warnings, errors

    def validate_layers(self, layout_layers: dict) -> tuple[dict[str, LayoutLayerConfig], list[str], list[str]]:
        warnings: list[str] = []
        errors: list[str] = []
        layers_payload = layout_layers or {}
        normalized_layers: dict[str, LayoutLayerConfig] = {}
        for layer_name, rules in self.REQUIRED_LAYER_RULES.items():
            current = layers_payload.get(layer_name, {})
            if isinstance(current, LayoutLayerConfig):
                current = current.model_dump(mode="json")
            if not isinstance(current, dict):
                current = {}
            normalized = {
                "visible": bool(current.get("visible", rules["visible"])),
                "locked": rules["locked"],
                "editable": rules["editable"],
                "opacity": current.get("opacity"),
                "preview_url": current.get("preview_url"),
            }
            if current.get("locked") != rules["locked"] or current.get("editable") != rules["editable"] or layer_name not in layers_payload:
                warnings.append(f"Layer {layer_name} config normalized.")
            normalized_layers[layer_name] = LayoutLayerConfig.model_validate(normalized)
        return normalized_layers, warnings, errors

    def validate_structure_objects(
        self,
        layout: LayoutInitialArtifact,
    ) -> tuple[
        list[LayoutRoomObject],
        list[LayoutFixtureObject],
        list[LayoutConnectionObject],
        list[LayoutConnectionObject],
        list[LayoutConnectionObject],
        list[str],
        list[str],
        bool,
    ]:
        warnings: list[str] = []
        errors: list[str] = []
        structure_lock_valid = True

        rooms = self._normalize_structure_list(layout.rooms, "room", warnings)
        fixtures = self._normalize_structure_list(layout.fixtures, "fixture", warnings)
        doors = self._normalize_structure_list(layout.doors, "door", warnings)
        windows = self._normalize_structure_list(layout.windows, "window", warnings)
        balcony = self._normalize_structure_list(layout.balcony, "balcony", warnings)

        for collection in (rooms, fixtures, doors, windows, balcony):
            for item in collection:
                if item.locked is not True or item.editable is not False:
                    structure_lock_valid = False
        return rooms, fixtures, doors, windows, balcony, warnings, errors, structure_lock_valid

    def validate_label_objects(
        self,
        labels: list[LayoutLabelObject],
    ) -> tuple[list[LayoutLabelObject], list[str], list[str], bool]:
        warnings: list[str] = []
        errors: list[str] = []
        normalized: list[LayoutLabelObject] = []
        used_ids: set[str] = set()
        labels_editable = True
        for index, label in enumerate(labels or [], start=1):
            item = label if isinstance(label, LayoutLabelObject) else LayoutLabelObject.model_validate(label)
            item_id = self._ensure_unique_id(item.id, f"label_{index:03d}", used_ids, warnings, "label")
            text = item.text
            extra = {}
            if text not in self.ALLOWED_LABELS:
                warnings.append(f"Label {item_id} has unsupported text; normalized to Unknown.")
                extra["text_original"] = text
                text = "Unknown"
            bbox = self._normalize_bbox(item.bbox, warnings, f"label {item_id}")
            normalized_item = item.model_copy(
                update={
                    "id": item_id,
                    "text": text,
                    "bbox": bbox,
                    "locked": False,
                    "editable": True,
                    **extra,
                }
            )
            if normalized_item.room_id is None:
                warnings.append(f"Label {item_id} has no room_id.")
            normalized.append(normalized_item)
            if normalized_item.locked or not normalized_item.editable:
                labels_editable = False
        return normalized, warnings, errors, labels_editable

    def validate_furniture_objects(
        self,
        furniture: list[LayoutFurnitureObject],
    ) -> tuple[list[LayoutFurnitureObject], list[str], list[str], bool, bool]:
        warnings: list[str] = []
        errors: list[str] = []
        normalized: list[LayoutFurnitureObject] = []
        used_ids: set[str] = set()
        furniture_editable = True
        furniture_placement_done = True if furniture else False
        for index, item in enumerate(furniture or [], start=1):
            furniture_item = item if isinstance(item, LayoutFurnitureObject) else LayoutFurnitureObject.model_validate(item)
            item_id = self._ensure_unique_id(furniture_item.id, f"furniture_{index:03d}", used_ids, warnings, "furniture")
            furniture_type = furniture_item.type if furniture_item.type in self.ALLOWED_FURNITURE_TYPES else "unknown"
            if furniture_type == "unknown" and furniture_item.type != "unknown":
                warnings.append(f"Furniture {item_id} has unsupported type; normalized to unknown.")
            placement_status = furniture_item.placement_status if furniture_item.placement_status in self.ALLOWED_PLACEMENT_STATUSES else "invalid"
            if placement_status == "invalid":
                warnings.append(f"Furniture {item_id} has invalid placement_status.")
            bbox = self._normalize_bbox(furniture_item.bbox, warnings, f"furniture {item_id}")
            if bbox is None and placement_status in {"auto_placed", "manually_placed"}:
                placement_status = "suggested_unplaced"
                warnings.append(f"Furniture {item_id} had placement_status without bbox; normalized to suggested_unplaced.")
            if furniture_item.room_id is None and not furniture_item.room_type:
                warnings.append(f"Furniture {item_id} is missing room_id and room_type.")
            normalized_item = furniture_item.model_copy(
                update={
                    "id": item_id,
                    "type": furniture_type,
                    "bbox": bbox,
                    "placement_status": placement_status,
                    "locked": False,
                    "editable": True,
                }
            )
            normalized.append(normalized_item)
            if normalized_item.locked or not normalized_item.editable:
                furniture_editable = False
            if placement_status not in {"auto_placed", "manually_placed"} or bbox is None:
                furniture_placement_done = False
        return normalized, warnings, errors, furniture_editable, furniture_placement_done

    def validate_style_object(
        self,
        style: LayoutStyleObject,
    ) -> tuple[LayoutStyleObject, list[str], list[str], bool]:
        warnings: list[str] = []
        errors: list[str] = []
        style_payload = style if isinstance(style, LayoutStyleObject) else LayoutStyleObject.model_validate(style or {})
        floor_tone = style_payload.floor_tone if style_payload.floor_tone in self.ALLOWED_FLOOR_TONES else "unknown"
        if floor_tone != style_payload.floor_tone:
            warnings.append("Style floor_tone normalized to unknown.")
        if not style_payload.avoid_keywords:
            warnings.append("Style avoid_keywords is empty.")
        normalized = style_payload.model_copy(
            update={
                "floor_tone": floor_tone,
                "bed_base_color": "white",
                "sofa_base_color": "white",
                "dominant_colors": list(style_payload.dominant_colors or []),
                "accent_colors": list(style_payload.accent_colors or []),
                "material_keywords": list(style_payload.material_keywords or []),
                "style_keywords": list(style_payload.style_keywords or []),
                "avoid_keywords": list(style_payload.avoid_keywords or []),
            }
        )
        return normalized, warnings, errors, True

    def write_layout_validated(self, run_id: str, artifact: LayoutValidationArtifact) -> None:
        path = self._artifacts_dir(run_id) / "layout_validated.json"
        try:
            with path.open("w", encoding="utf-8") as output_file:
                json.dump(artifact.model_dump(mode="json"), output_file, ensure_ascii=False, indent=2)
        except OSError as exc:
            raise HTTPException(status_code=500, detail="failed to write layout_validated artifact") from exc

    def build_metadata_updates(self, metadata: RunMetadata, artifact: LayoutValidationArtifact) -> dict:
        return {
            "status": "layout_validated",
            "run_status": "layout_validated",
            "processing": metadata.processing.model_copy(
                update={
                    "layout_initial_creation": True,
                    "layout_validation": True,
                    "image_generation": False,
                    "watercolor_rendering": False,
                }
            ),
            "pipeline": {
                "current_phase": "phase_4b_layout_validation",
                "next_phase": "phase_4c_furniture_placement_planning",
            },
            "layout_validated_path": self._relative_artifact_path(metadata.run_id, "layout_validated.json"),
            "layout_validation_summary": LayoutValidationSummary(
                validation_status=artifact.validation_status,
                room_count=artifact.quality.room_count,
                fixture_count=artifact.quality.fixture_count,
                label_count=artifact.quality.label_count,
                furniture_count=artifact.quality.furniture_suggestion_count,
                structure_locked=artifact.quality.structure_locked,
                furniture_placement_done=artifact.quality.furniture_placement_done,
                needs_human_review=artifact.quality.needs_human_review,
                warnings_count=len(artifact.warnings),
                errors_count=len(artifact.errors),
            ),
        }

    def _normalize_structure_list(self, items, prefix: str, warnings: list[str]):
        normalized = []
        used_ids: set[str] = set()
        for index, item in enumerate(items or [], start=1):
            item_id_default = f"{prefix}_{index:03d}"
            if isinstance(item, LayoutRoomObject):
                bbox = self._normalize_bbox(item.bbox, warnings, f"{prefix} {item.id or index}")
                approx_bbox = self._normalize_bbox(item.approx_bbox, warnings, f"{prefix} {item.id or index} approx_bbox")
                normalized_item = item.model_copy(
                    update={
                        "id": self._ensure_unique_id(item.id, item_id_default, used_ids, warnings, prefix),
                        "bbox": bbox,
                        "approx_bbox": approx_bbox,
                        "polygon": self._normalize_polygon(item.polygon, warnings, f"{prefix} {item.id or index}"),
                        "locked": True,
                        "editable": False,
                        "geometry_confidence": self._clamp_confidence(item.geometry_confidence),
                        "geometry_notes": list(item.geometry_notes or []),
                    }
                )
                if bbox is None:
                    warnings.append(f"{prefix.capitalize()} {normalized_item.id} is missing bbox.")
                normalized.append(normalized_item)
            elif isinstance(item, LayoutFixtureObject):
                bbox = self._normalize_bbox(item.bbox, warnings, f"{prefix} {item.id or index}")
                approx_bbox = self._normalize_bbox(item.approx_bbox, warnings, f"{prefix} {item.id or index} approx_bbox")
                normalized.append(
                    item.model_copy(
                        update={
                            "id": self._ensure_unique_id(item.id, item_id_default, used_ids, warnings, prefix),
                            "bbox": bbox,
                            "approx_bbox": approx_bbox,
                            "polygon": self._normalize_polygon(item.polygon, warnings, f"{prefix} {item.id or index}"),
                            "locked": True,
                            "editable": False,
                            "geometry_confidence": self._clamp_confidence(item.geometry_confidence),
                            "geometry_notes": list(item.geometry_notes or []),
                        }
                    )
                )
            elif isinstance(item, LayoutConnectionObject):
                bbox = self._normalize_bbox(item.bbox, warnings, f"{prefix} {item.id or index}")
                approx_bbox = self._normalize_bbox(item.approx_bbox, warnings, f"{prefix} {item.id or index} approx_bbox")
                update = {
                    "id": self._ensure_unique_id(item.id, item_id_default, used_ids, warnings, prefix),
                    "bbox": bbox,
                    "approx_bbox": approx_bbox,
                    "polygon": self._normalize_polygon(item.polygon, warnings, f"{prefix} {item.id or index}"),
                    "locked": True,
                    "editable": False,
                    "geometry_confidence": self._clamp_confidence(item.geometry_confidence),
                    "geometry_notes": list(item.geometry_notes or []),
                }
                if hasattr(item, "placement_status"):
                    warnings.append(f"{prefix.capitalize()} {item.id or index} had placement_status and it was ignored.")
                normalized.append(item.model_copy(update=update))
        return normalized

    def _normalize_bbox(self, bbox, warnings: list[str], label: str) -> LayoutBoundingBox | None:
        if bbox is None:
            return None
        if isinstance(bbox, LayoutBoundingBox):
            raw = bbox.model_dump(mode="json")
        elif isinstance(bbox, dict):
            raw = dict(bbox)
        elif isinstance(bbox, list) and len(bbox) == 4:
            raw = {"x_min": bbox[0], "y_min": bbox[1], "x_max": bbox[2], "y_max": bbox[3]}
        else:
            warnings.append(f"{label} has invalid bbox; set to null.")
            return None
        try:
            x_min = self._clamp_bbox_value(raw.get("x_min"))
            y_min = self._clamp_bbox_value(raw.get("y_min"))
            x_max = self._clamp_bbox_value(raw.get("x_max"))
            y_max = self._clamp_bbox_value(raw.get("y_max"))
        except Exception:
            warnings.append(f"{label} has invalid bbox; set to null.")
            return None
        if x_min is None or y_min is None or x_max is None or y_max is None:
            warnings.append(f"{label} has invalid bbox; set to null.")
            return None
        if x_min >= x_max or y_min >= y_max:
            warnings.append(f"{label} bbox has non-positive area; set to null.")
            return None
        return LayoutBoundingBox(x_min=x_min, y_min=y_min, x_max=x_max, y_max=y_max)

    @staticmethod
    def _clamp_bbox_value(value) -> int | None:
        if value is None:
            return None
        number = int(float(value))
        return max(0, min(1199, number))

    @staticmethod
    def _clamp_confidence(value) -> float:
        try:
            return max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _normalize_polygon(value, warnings: list[str], label: str) -> list[list[float]] | None:
        if value is None:
            return None
        if not isinstance(value, list):
            warnings.append(f"{label} has invalid polygon; set to null.")
            return None
        normalized: list[list[float]] = []
        for point in value:
            if not isinstance(point, (list, tuple)) or len(point) < 2:
                continue
            try:
                normalized.append([
                    max(0.0, min(1199.0, float(point[0]))),
                    max(0.0, min(1199.0, float(point[1]))),
                ])
            except (TypeError, ValueError):
                continue
        if len(normalized) < 3:
            return None
        return normalized

    @staticmethod
    def _coerce_int(value, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _ensure_unique_id(
        self,
        value: str | None,
        default_id: str,
        used_ids: set[str],
        warnings: list[str],
        label: str,
    ) -> str:
        candidate = (value or "").strip() or default_id
        if candidate in used_ids:
            original = candidate
            counter = 1
            while candidate in used_ids:
                candidate = f"{default_id}_{counter}"
                counter += 1
            warnings.append(f"Duplicate {label} id {original} was reassigned to {candidate}.")
        elif not value:
            warnings.append(f"{label.capitalize()} id was missing and assigned to {candidate}.")
        used_ids.add(candidate)
        return candidate

    def _compute_validation_status(self, errors: list[str], warnings: list[str], canvas: dict) -> str:
        if errors:
            return "failed"
        if not canvas or canvas.get("width") is None or canvas.get("height") is None:
            return "failed"
        if warnings:
            return "passed_with_warnings"
        return "passed"

    def _build_failed_artifact(self, run_id: str, source: dict[str, str | None], errors: list[str]) -> LayoutValidationArtifact:
        return LayoutValidationArtifact(
            run_id=run_id,
            generated_at=datetime.now(timezone.utc),
            validation_status="failed",
            source=source,
            canvas={
                "width": 1200,
                "height": 1200,
                "coordinate_space": "normalized_floorplan_1200",
                "background_color": "white",
            },
            layers={name: LayoutLayerConfig(**rules) for name, rules in self.REQUIRED_LAYER_RULES.items()},
            rooms=[],
            fixtures=[],
            doors=[],
            windows=[],
            balcony=[],
            labels=[],
            furniture=[],
            style=LayoutStyleObject(),
            constraints=[],
            quality=LayoutValidationQualitySummary(
                needs_human_review=True,
                structure_locked=True,
                semantic_layout_only=True,
                pixel_perfect_geometry=False,
                furniture_placement_done=False,
                image_generation_done=False,
                watercolor_rendering_done=False,
                room_count=0,
                fixture_count=0,
                label_count=0,
                furniture_suggestion_count=0,
                canvas_valid=True,
                structure_lock_valid=False,
                editable_object_rules_valid=False,
                style_valid=False,
            ),
            validation={
                "required_fields_present": False,
                "canvas_valid": True,
                "layer_rules_valid": True,
                "structure_objects_locked": False,
                "labels_editable": True,
                "furniture_editable": True,
                "style_normalized": False,
                "warnings_count": 0,
                "errors_count": len(errors),
            },
            warnings=[],
            errors=errors,
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
