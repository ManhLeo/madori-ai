from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException

from app.schemas.run import (
    FloorplanAnalysisValidatedArtifact,
    FloorplanPreprocessReport,
    InteriorAnalysisValidatedArtifact,
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
    RoomFunctionAssignmentSummary,
    RunMetadata,
)
from app.services.room_function_assignment_service import RoomFunctionAssignmentService


class LayoutCreationService:
    ROOM_TYPE_TO_LAYOUT_TYPE = {
        "bedroom": "bed_room",
        "bathroom": "bath_room",
        "washroom": "wash_area",
        "walk_in_closet": "closet",
        "living_room": "living_room",
        "dining": "dining",
        "kitchen": "kitchen",
        "closet": "closet",
        "toilet": "toilet",
        "entrance": "entrance",
        "balcony": "balcony",
        "hallway": "hallway",
        "storage": "storage",
        "unknown": "unknown",
    }
    APPROVED_LAYOUT_LABELS = {
        "living_room": "Living Room",
        "dining": "Dining",
        "kitchen": "Kitchen",
        "closet": "Closet",
        "toilet": "Toilet",
        "entrance": "Entrance",
        "bed_room": "Bed Room",
        "bath_room": "Bath Room",
        "wash_area": "Wash Room",
        "balcony": "Balcony",
        "hallway": "Hallway",
        "storage": "Storage",
        "unknown": "Unknown",
    }

    def __init__(self, storage_dir: Path, storage_runs_dir: Path) -> None:
        self.storage_dir = storage_dir
        self.storage_runs_dir = storage_runs_dir

    def create_initial_layout(self, metadata: RunMetadata) -> LayoutInitialArtifact:
        run_id = metadata.run_id
        floorplan = self.load_floorplan_validated(run_id)
        interior = self.load_interior_validated(run_id)
        preprocess = self.load_floorplan_preprocess(run_id)
        room_assignment = self.load_or_create_room_function_assignment(metadata)
        layout = self.build_layout_initial(run_id, floorplan, interior, preprocess, metadata, room_assignment)
        self.write_layout_initial(run_id, layout)
        return layout

    def load_layout_initial(self, run_id: str) -> LayoutInitialArtifact:
        path = self._artifacts_dir(run_id) / "layout_initial.json"
        if not path.exists():
            raise HTTPException(status_code=404, detail="layout_initial artifact not found")
        try:
            return LayoutInitialArtifact.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=500, detail="failed to read layout_initial artifact") from exc

    def load_floorplan_validated(self, run_id: str) -> FloorplanAnalysisValidatedArtifact:
        path = self._artifacts_dir(run_id) / "floorplan_analysis_validated.json"
        if not path.exists():
            raise HTTPException(status_code=400, detail="Run floorplan analysis validation before layout creation")
        try:
            return FloorplanAnalysisValidatedArtifact.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="invalid floorplan analysis validated JSON") from exc

    def load_interior_validated(self, run_id: str) -> InteriorAnalysisValidatedArtifact | None:
        path = self._artifacts_dir(run_id) / "interior_analysis_validated.json"
        if not path.exists():
            return None
        try:
            return InteriorAnalysisValidatedArtifact.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def load_floorplan_preprocess(self, run_id: str) -> FloorplanPreprocessReport | None:
        path = self._artifacts_dir(run_id) / "floorplan_preprocess.json"
        if not path.exists():
            return None
        try:
            return FloorplanPreprocessReport.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def load_or_create_room_function_assignment(self, metadata: RunMetadata):
        service = RoomFunctionAssignmentService(self.storage_dir, self.storage_runs_dir)
        return service.load_or_assign(metadata)

    def build_layout_initial(
        self,
        run_id: str,
        floorplan: FloorplanAnalysisValidatedArtifact,
        interior: InteriorAnalysisValidatedArtifact | None,
        preprocess: FloorplanPreprocessReport | None,
        metadata: RunMetadata,
        room_assignment,
    ) -> LayoutInitialArtifact:
        warnings = list(floorplan.warnings)
        errors = list(floorplan.errors)
        if interior is None:
            warnings.append("Validated interior analysis is missing; using safe defaults for furniture and style.")

        rooms = self.build_room_objects(floorplan, interior, room_assignment, warnings)
        fixtures = self.build_fixture_objects(floorplan)
        labels = self.build_label_objects(floorplan, rooms, warnings)
        furniture = self.build_furniture_suggestions(floorplan, interior, room_assignment, warnings)
        style = self.build_style_object(interior, warnings)
        doors = self.build_door_objects(floorplan)
        windows = self.build_window_objects(floorplan)
        balcony = self.build_balcony_objects(floorplan)
        constraints = self.build_constraints(floorplan)

        if not rooms:
            warnings.append("No rooms found in validated floorplan analysis.")
        if not labels:
            warnings.append("No labels found or derived for the initial layout.")
        if not furniture:
            warnings.append("No furniture suggestions created.")
        if style.floor_tone == "unknown":
            warnings.append("Floor tone is unknown.")
        warnings.append("Layout is semantic-only and not pixel-perfect.")

        quality = LayoutQualitySummary(
            needs_human_review=True,
            structure_locked=True,
            semantic_layout_only=True,
            pixel_perfect_geometry=False,
            furniture_placement_done=False,
            image_generation_done=False,
            watercolor_rendering_done=False,
            room_count=len(rooms),
            fixture_count=len(fixtures),
            label_count=len(labels),
            furniture_suggestion_count=len(furniture),
        )

        normalized_preview_url = None
        if preprocess is not None:
            normalized_preview_url = preprocess.artifacts.get("normalized_floorplan").preview_url
        elif floorplan.source_image is not None:
            normalized_preview_url = floorplan.source_image.preview_url

        source = {
            "floorplan_analysis_validated": self._relative_artifact_path(run_id, "floorplan_analysis_validated.json"),
            "interior_analysis_validated": self._relative_artifact_path(run_id, "interior_analysis_validated.json")
            if interior is not None
            else None,
            "room_function_assignment": self._relative_artifact_path(run_id, "room_function_assignment.json") if room_assignment is not None else None,
            "floorplan_preprocess": self._relative_artifact_path(run_id, "floorplan_preprocess.json") if preprocess is not None else None,
            "normalized_floorplan_preview_url": normalized_preview_url,
        }
        canvas_width = preprocess.output_size["width"] if preprocess is not None else 1200
        canvas_height = preprocess.output_size["height"] if preprocess is not None else 1200

        return LayoutInitialArtifact(
            run_id=run_id,
            generated_at=datetime.now(timezone.utc),
            layout_status="created",
            source=source,
            canvas={
                "width": canvas_width,
                "height": canvas_height,
                "coordinate_space": "normalized_floorplan_1200",
                "background_color": "white",
            },
            layers={
                "reference_floorplan": LayoutLayerConfig(
                    visible=True,
                    locked=True,
                    editable=False,
                    opacity=0.35,
                    preview_url=normalized_preview_url,
                ),
                "structure": LayoutLayerConfig(visible=True, locked=True, editable=False),
                "rooms": LayoutLayerConfig(visible=True, locked=True, editable=False),
                "fixtures": LayoutLayerConfig(visible=True, locked=True, editable=False),
                "furniture": LayoutLayerConfig(visible=True, locked=False, editable=True),
                "labels": LayoutLayerConfig(visible=True, locked=False, editable=True),
                "style": LayoutLayerConfig(visible=True, locked=False, editable=True),
            },
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
            warnings=self._dedupe_keep_order(warnings),
            errors=self._dedupe_keep_order(errors),
        )

    def build_room_objects(
        self,
        floorplan: FloorplanAnalysisValidatedArtifact,
        interior: InteriorAnalysisValidatedArtifact | None,
        room_assignment,
        warnings: list[str],
    ) -> list[LayoutRoomObject]:
        suggested_floor_tone = self._safe_string(
            ((interior.recommendations_for_next_phase if interior else {}) or {}).get("suggested_floor_tone"),
            default="unknown",
        )
        assignment_lookup = self._room_assignment_lookup(room_assignment)
        rooms: list[LayoutRoomObject] = []
        for index, room in enumerate(floorplan.rooms, start=1):
            layout_type = self._normalize_layout_room_type(room.type)
            bbox = self._bbox_from_any(room.bbox or room.bounding_box)
            approx_bbox = self._bbox_from_any(room.approx_bbox)
            notes: list[str] = []
            assignment = assignment_lookup.get(room.id, {})
            functional_role = assignment.get("functional_role")
            if bbox is None:
                notes.append("No approximate bounding box available.")
                warnings.append(f"Room {room.id or index} is missing bbox.")
            if functional_role:
                notes.append(f"functional_role={functional_role}")
                notes.extend([str(reason) for reason in assignment.get("reasons", []) if reason])
            rooms.append(
                LayoutRoomObject(
                    id=room.id or f"room_{index:03d}",
                    type=layout_type,
                    label=self._layout_label_for_room(layout_type, room.approved_label),
                    functional_role=functional_role,
                    source_label_original=room.source_label,
                    bbox=bbox,
                    approx_bbox=approx_bbox,
                    polygon=room.polygon,
                    position=room.position,
                    connected_to=[self._normalize_layout_room_type(value) for value in room.connected_to],
                    floor_tone=suggested_floor_tone,
                    locked=True,
                    editable=False,
                    confidence=room.confidence or (0.85 if bbox is not None else 0.65),
                    geometry_confidence=room.geometry_confidence,
                    geometry_notes=list(room.geometry_notes or []),
                    notes=notes,
                )
            )
        return rooms

    def build_fixture_objects(self, floorplan: FloorplanAnalysisValidatedArtifact) -> list[LayoutFixtureObject]:
        fixtures: list[LayoutFixtureObject] = []
        for fixture in floorplan.fixtures:
            fixtures.append(
                LayoutFixtureObject(
                    id=fixture.id,
                    type=self._normalize_layout_fixture_type(fixture.fixture_type or fixture.type),
                    bbox=self._bbox_from_any(fixture.bbox),
                    approx_bbox=self._bbox_from_any(fixture.approx_bbox),
                    polygon=fixture.polygon,
                    position=fixture.position,
                    locked=True,
                    editable=False,
                    confidence=fixture.confidence or 0.8,
                    geometry_confidence=fixture.geometry_confidence,
                    geometry_notes=list(fixture.geometry_notes or []),
                    notes=(
                        (["No approximate bounding box available."] if not (fixture.bbox or fixture.approx_bbox) else [])
                        + ([f"source_label={fixture.source_label}"] if getattr(fixture, "source_label", None) else [])
                        + (["required fixture anchor"] if getattr(fixture, "required", False) else [])
                    ),
                )
            )
        return fixtures

    def build_label_objects(
        self,
        floorplan: FloorplanAnalysisValidatedArtifact,
        rooms: list[LayoutRoomObject],
        warnings: list[str],
    ) -> list[LayoutLabelObject]:
        labels: list[LayoutLabelObject] = []
        seen_room_ids: set[str] = set()
        for label in floorplan.labels:
            labels.append(
                LayoutLabelObject(
                    id=label.id,
                    text=label.approved_text or self.APPROVED_LAYOUT_LABELS.get(self._normalize_layout_room_type(label.room_type or "unknown"), "Unknown"),
                    room_id=label.room_id,
                    bbox=None,
                    position=label.position or "center",
                    font_family="default",
                    font_size=24,
                    locked=False,
                    editable=True,
                    confidence=0.8,
                )
            )
            if label.room_id:
                seen_room_ids.add(label.room_id)
        for room in rooms:
            if room.id in seen_room_ids:
                continue
            labels.append(
                LayoutLabelObject(
                    id=f"label_{len(labels) + 1:03d}",
                    text=room.label,
                    room_id=room.id,
                    bbox=room.bbox,
                    position=room.position or "center",
                    font_family="default",
                    font_size=24,
                    locked=False,
                    editable=True,
                    confidence=0.7,
                )
            )
        if not labels:
            warnings.append("No label objects could be created from validated analysis.")
        return labels

    def build_furniture_suggestions(
        self,
        floorplan: FloorplanAnalysisValidatedArtifact,
        interior: InteriorAnalysisValidatedArtifact | None,
        room_assignment,
        warnings: list[str],
    ) -> list[LayoutFurnitureObject]:
        if interior is None:
            return []

        built_rooms = self.build_room_objects(floorplan, interior, room_assignment, [])
        room_lookup_by_id = {room.id: room for room in built_rooms}
        room_lookup_by_type: dict[str, list[LayoutRoomObject]] = {}
        room_lookup_by_role: dict[str, LayoutRoomObject] = {}
        for room in built_rooms:
            room_lookup_by_type.setdefault(self._normalize_layout_room_type(room.type), []).append(room)
            if room.functional_role:
                room_lookup_by_role[room.functional_role] = room
        recs = interior.recommendations_for_next_phase or {}
        room_observations = interior.room_observations or {}
        furniture_signals = interior.furniture_signals or {}
        suggestions: list[LayoutFurnitureObject] = []
        suggestion_keys: set[tuple[str, str]] = set()
        living_room_observation = room_observations.get("living_room", {}) if isinstance(room_observations.get("living_room", {}), dict) else {}
        dining_observation = room_observations.get("dining", {}) if isinstance(room_observations.get("dining", {}), dict) else {}
        bed_room_observation = room_observations.get("bed_room", {}) if isinstance(room_observations.get("bed_room", {}), dict) else {}
        kitchen_observation = room_observations.get("kitchen", {}) if isinstance(room_observations.get("kitchen", {}), dict) else {}
        bath_room_observation = room_observations.get("bath_room", {}) if isinstance(room_observations.get("bath_room", {}), dict) else {}
        sofa_observation = living_room_observation.get("sofa") if isinstance(living_room_observation.get("sofa"), dict) else {}
        bed_observation = bed_room_observation.get("bed") if isinstance(bed_room_observation.get("bed"), dict) else {}

        def add_suggestion(
            *,
            furniture_type: str,
            room: LayoutRoomObject | None,
            base_color: str | None = None,
            observed_color: str | None = None,
            accent_colors: list[str] | None = None,
            confidence: float = 0.65,
        ) -> None:
            if room is None or not furniture_type:
                return
            key = (room.id, furniture_type)
            if key in suggestion_keys:
                return
            suggestion_keys.add(key)
            suggestions.append(
                self._build_furniture_object(
                    furniture_id=f"furniture_{len(suggestions) + 1:03d}",
                    furniture_type=furniture_type,
                    room=room,
                    base_color=base_color,
                    observed_color=observed_color,
                    accent_colors=accent_colors or [],
                    confidence=confidence,
                    notes=["Placement is a suggestion only. Human review required."],
                )
            )

        living_room = room_lookup_by_role.get("media_lounge") or room_lookup_by_role.get("living_dining") or (room_lookup_by_type.get("living_room") or [None])[0]
        if living_room and self._safe_string(recs.get("suggested_sofa_type"), default="unknown") != "unknown":
            add_suggestion(
                furniture_type=self._safe_string(recs.get("suggested_sofa_type"), default="unknown"),
                room=living_room,
                base_color=self._safe_string(sofa_observation.get("base_color"), default="white"),
                observed_color=self._first_color(living_room_observation.get("dominant_colors")),
                accent_colors=self._string_list(sofa_observation.get("cushion_colors")),
                confidence=float(interior.quality.get("overall_confidence") or 0.7),
            )

        bed_room = room_lookup_by_role.get("main_bedroom") or room_lookup_by_role.get("guest_bedroom") or (room_lookup_by_type.get("bed_room") or [None])[0]
        if bed_room and self._safe_string(recs.get("suggested_bed_type"), default="unknown") != "unknown":
            add_suggestion(
                furniture_type=self._safe_string(recs.get("suggested_bed_type"), default="unknown"),
                room=bed_room,
                base_color=self._safe_string(bed_observation.get("base_color"), default="white"),
                observed_color=self._first_color(bed_room_observation.get("dominant_colors")),
                accent_colors=self._string_list(bed_observation.get("cushion_colors")),
                confidence=float(interior.quality.get("overall_confidence") or 0.7),
            )

        kitchen = room_lookup_by_role.get("kitchen") or (room_lookup_by_type.get("kitchen") or [None])[0]
        bath_room = room_lookup_by_role.get("bath_room") or (room_lookup_by_type.get("bath_room") or [None])[0] or (room_lookup_by_type.get("wash_area") or [None])[0]
        wash_room = room_lookup_by_role.get("wash_room") or (room_lookup_by_type.get("wash_area") or [None])[0]
        dining_room = room_lookup_by_role.get("living_dining") or room_lookup_by_role.get("dining_zone") or (room_lookup_by_type.get("dining") or [None])[0] or living_room or kitchen
        media_lounge_room = room_lookup_by_role.get("media_lounge") or living_room
        washing_machine_anchor = next((fixture for fixture in floorplan.fixtures if str(getattr(fixture, "fixture_type", fixture.type) or "") == "washing_machine_anchor"), None)

        signal_to_room_map = {
            "living_room": media_lounge_room,
            "dining": dining_room,
            "bed_room": bed_room,
            "kitchen": kitchen,
            "bath_room": bath_room,
        }
        signal_observation_map = {
            "living_room": living_room_observation,
            "dining": dining_observation if dining_observation else (living_room_observation if living_room else kitchen_observation),
            "bed_room": bed_room_observation,
            "kitchen": kitchen_observation,
            "bath_room": bath_room_observation,
        }
        for signal_room_key, signal_values in furniture_signals.items():
            target_room = signal_to_room_map.get(signal_room_key)
            observation = signal_observation_map.get(signal_room_key, {})
            for signal in self._string_list(signal_values):
                mapped = self._map_signal_to_furniture_type(signal)
                if not mapped:
                    continue
                add_suggestion(
                    furniture_type=mapped,
                    room=target_room,
                    base_color=self._default_base_color_for_furniture(mapped, observation),
                    observed_color=self._first_color(observation.get("dominant_colors") if isinstance(observation, dict) else None),
                    accent_colors=self._observation_accent_colors(observation),
                    confidence=0.68,
                )

        for detected in self._detected_object_types(living_room_observation):
            mapped = self._map_detected_object_to_furniture_type(detected)
            if not mapped:
                continue
            add_suggestion(
                furniture_type=mapped,
                room=living_room,
                base_color=self._default_base_color_for_furniture(mapped, living_room_observation),
                observed_color=self._first_color(living_room_observation.get("dominant_colors")),
                accent_colors=self._observation_accent_colors(living_room_observation),
                confidence=0.65,
            )

        for detected in self._detected_object_types(bed_room_observation):
            mapped = self._map_detected_object_to_furniture_type(detected)
            if not mapped:
                continue
            add_suggestion(
                furniture_type=mapped,
                room=bed_room,
                base_color=self._default_base_color_for_furniture(mapped, bed_room_observation),
                observed_color=self._first_color(bed_room_observation.get("dominant_colors")),
                accent_colors=self._observation_accent_colors(bed_room_observation),
                confidence=0.64,
            )

        for detected in self._detected_object_types(kitchen_observation):
            mapped = {
                "kitchen": "kitchen_counter",
                "stove": "stove",
                "sink": "sink",
                "cabinet": "cabinet",
                "dining_table": "dining_table",
                "chair": "chair",
            }.get(detected)
            if not mapped:
                mapped = self._map_detected_object_to_furniture_type(detected)
            add_suggestion(
                furniture_type=mapped,
                room=dining_room if mapped in {"dining_table", "chair"} else kitchen,
                base_color=self._default_base_color_for_furniture(mapped, kitchen_observation),
                observed_color=self._first_color(kitchen_observation.get("dominant_colors")),
                accent_colors=[],
                confidence=0.6,
            )

        for detected in self._detected_object_types(dining_observation):
            mapped = self._map_detected_object_to_furniture_type(detected)
            add_suggestion(
                furniture_type=mapped,
                room=dining_room,
                base_color=self._default_base_color_for_furniture(mapped, dining_observation),
                observed_color=self._first_color(dining_observation.get("dominant_colors")),
                accent_colors=[],
                confidence=0.62,
            )

        for detected in self._detected_object_types(bath_room_observation):
            mapped = {"bathtub": "bathtub", "shower": "shower", "towel": "towel"}.get(detected)
            if not mapped:
                mapped = self._map_detected_object_to_furniture_type(detected)
            add_suggestion(
                furniture_type=mapped,
                room=bath_room,
                base_color=self._default_base_color_for_furniture(mapped, bath_room_observation),
                observed_color=self._first_color(bath_room_observation.get("dominant_colors")),
                accent_colors=[],
                confidence=0.58,
            )

        if wash_room is not None or washing_machine_anchor is not None:
            target_room = wash_room or bath_room
            if target_room is not None:
                add_suggestion(
                    furniture_type="washing_machine",
                    room=target_room,
                    base_color="white",
                    observed_color="white",
                    accent_colors=[],
                    confidence=0.72 if washing_machine_anchor is not None else 0.6,
                )

        assignment_service = RoomFunctionAssignmentService(self.storage_dir, self.storage_runs_dir)
        cleaned_suggestions, cleanup_summary = assignment_service.apply_furniture_cleanup(suggestions, built_rooms, room_assignment)
        if room_assignment is not None:
            updated_room_assignment = room_assignment.model_copy(update={"furniture_cleanup_summary": cleanup_summary})
            assignment_service.write_room_function_assignment(floorplan.run_id, updated_room_assignment)

        if not suggestions:
            warnings.append("No furniture suggestions could be derived from validated interior analysis.")
        return cleaned_suggestions

    def write_layout_initial(self, run_id: str, layout: LayoutInitialArtifact) -> None:
        path = self._artifacts_dir(run_id) / "layout_initial.json"
        try:
            with path.open("w", encoding="utf-8") as output_file:
                json.dump(layout.model_dump(mode="json"), output_file, ensure_ascii=False, indent=2)
        except OSError as exc:
            raise HTTPException(status_code=500, detail="failed to write layout_initial artifact") from exc

    def build_metadata_updates(self, metadata: RunMetadata, layout: LayoutInitialArtifact) -> dict:
        room_assignment_summary = metadata.room_function_assignment_summary
        room_assignment_path = metadata.room_function_assignment_path
        assignment_artifact_path = self._artifacts_dir(metadata.run_id) / "room_function_assignment.json"
        if assignment_artifact_path.exists():
            if room_assignment_path is None:
                room_assignment_path = self._relative_artifact_path(metadata.run_id, "room_function_assignment.json")
            try:
                assignment_payload = json.loads(assignment_artifact_path.read_text(encoding="utf-8"))
                rooms = assignment_payload.get("rooms") or []
                cleanup = assignment_payload.get("furniture_cleanup_summary") or {}
                room_assignment_summary = RoomFunctionAssignmentSummary(
                    assignment_status=assignment_payload.get("assignment_status") or "assigned",
                    western_room_count=sum(1 for room in rooms if str(room.get("semantic_type") or "") in {"bedroom", "bed_room"}),
                    media_lounge_room_id=next((room.get("room_id") for room in rooms if room.get("functional_role") == "media_lounge"), None),
                    main_bedroom_room_id=next((room.get("room_id") for room in rooms if room.get("functional_role") == "main_bedroom"), None),
                    dining_zone_assigned=any(room.get("functional_role") in {"living_dining", "dining_zone"} for room in rooms),
                    allowed_furniture_count=int(cleanup.get("allowed_furniture_count") or 0),
                    suppressed_furniture_count=int(cleanup.get("suppressed_furniture_count") or 0),
                    role_conflict_count=int(cleanup.get("role_conflict_count") or 0),
                    needs_human_review=True,
                    warnings_count=len(assignment_payload.get("warnings") or []),
                    errors_count=len(assignment_payload.get("errors") or []),
                )
            except (OSError, ValueError):
                room_assignment_summary = metadata.room_function_assignment_summary
        completed_phases = ["phase_1_upload"]
        if metadata.processing.input_inspection:
            completed_phases.append("phase_2a_input_inspection")
        if metadata.processing.floorplan_preprocess:
            completed_phases.append("phase_2b_floorplan_preprocessing")
        if metadata.processing.semantic_analysis:
            completed_phases.append("phase_2c_floorplan_semantic_analysis")
        if metadata.processing.semantic_validation:
            completed_phases.append("phase_2d_floorplan_analysis_validation")
        if metadata.artifact_index_path:
            completed_phases.append("phase_2e_artifact_index")
        if metadata.processing.interior_style_analysis:
            completed_phases.append("phase_3a_interior_semantic_analysis")
        if metadata.processing.interior_analysis_validation:
            completed_phases.append("phase_3b_interior_analysis_validation")
        completed_phases.append("phase_3c_room_function_assignment")
        completed_phases.append("phase_4a_layout_object_creation")
        return {
            "status": "layout_initial_created",
            "run_status": "layout_initial_created",
            "processing": metadata.processing.model_copy(
                update={
                    "room_function_assignment": True,
                    "layout_initial_creation": True,
                    "image_generation": False,
                    "watercolor_rendering": False,
                }
            ),
            "pipeline": {
                "current_phase": "phase_4a_layout_object_creation",
                "next_phase": "phase_4b_layout_validation",
                "completed_phases": completed_phases,
            },
            "room_function_assignment_path": room_assignment_path,
            "room_function_assignment_summary": room_assignment_summary,
            "layout_initial_path": self._relative_artifact_path(metadata.run_id, "layout_initial.json"),
            "layout_summary": LayoutSummary(
                layout_status=layout.layout_status,
                room_count=layout.quality.room_count,
                fixture_count=layout.quality.fixture_count,
                label_count=layout.quality.label_count,
                furniture_suggestion_count=layout.quality.furniture_suggestion_count,
                structure_locked=layout.quality.structure_locked,
                needs_human_review=layout.quality.needs_human_review,
            ),
        }

    def build_style_object(self, interior: InteriorAnalysisValidatedArtifact | None, warnings: list[str]) -> LayoutStyleObject:
        if interior is None:
            return LayoutStyleObject()
        summary = interior.interior_summary or {}
        recommendations = interior.recommendations_for_next_phase or {}
        living_room = interior.room_observations.get("living_room", {}) if interior.room_observations else {}
        bed_room = interior.room_observations.get("bed_room", {}) if interior.room_observations else {}
        style_keywords = self._string_list(summary.get("style_keywords"))
        if not style_keywords:
            style_keywords = self._string_list(summary.get("overall_style"))
        return LayoutStyleObject(
            floor_tone=self._safe_string(recommendations.get("suggested_floor_tone"), default=self._safe_string(summary.get("floor_tone"), default="unknown")),
            bed_base_color=self._safe_string((bed_room.get("bed") or {}).get("base_color"), default="white"),
            sofa_base_color=self._safe_string((living_room.get("sofa") or {}).get("base_color"), default="white"),
            dominant_colors=self._dedupe_keep_order(self._string_list(summary.get("dominant_colors"))),
            accent_colors=self._dedupe_keep_order(
                self._string_list((living_room.get("sofa") or {}).get("cushion_colors"))
                + self._string_list((bed_room.get("bed") or {}).get("cushion_colors"))
            ),
            material_keywords=self._dedupe_keep_order(self._string_list(summary.get("material_keywords"))),
            style_keywords=self._dedupe_keep_order(style_keywords),
            avoid_keywords=self._dedupe_keep_order(self._collect_avoid_keywords(interior)),
        )

    def build_door_objects(self, floorplan: FloorplanAnalysisValidatedArtifact) -> list[LayoutConnectionObject]:
        return [
            LayoutConnectionObject(
                id=door.id,
                type="door",
                bbox=self._bbox_from_any(door.bbox),
                approx_bbox=self._bbox_from_any(door.approx_bbox),
                polygon=door.polygon,
                position=door.position,
                connects=[self._normalize_layout_room_type(value) for value in door.connects],
                locked=True,
                editable=False,
                confidence=door.confidence or 0.75,
                geometry_confidence=door.geometry_confidence,
                geometry_notes=list(door.geometry_notes or []),
                notes=["Contains unknown room connection."] if door.has_unknown_connection else [],
            )
            for door in floorplan.doors
        ]

    def build_window_objects(self, floorplan: FloorplanAnalysisValidatedArtifact) -> list[LayoutConnectionObject]:
        return [
            LayoutConnectionObject(
                id=window.id,
                type="window",
                bbox=self._bbox_from_any(window.bbox),
                approx_bbox=self._bbox_from_any(window.approx_bbox),
                polygon=window.polygon,
                position=window.position,
                room_id=window.room_id,
                room_type=self._normalize_layout_room_type(window.room_type or "unknown"),
                approved_label=window.approved_label,
                locked=True,
                editable=False,
                confidence=window.confidence or 0.8,
                geometry_confidence=window.geometry_confidence,
                geometry_notes=list(window.geometry_notes or []),
                notes=[],
            )
            for window in floorplan.windows
        ]

    def build_balcony_objects(self, floorplan: FloorplanAnalysisValidatedArtifact) -> list[LayoutConnectionObject]:
        balcony_data = floorplan.normalized_analysis.get("balcony") if floorplan.normalized_analysis else None
        if not isinstance(balcony_data, dict) or not balcony_data.get("exists"):
            return []
        return [
            LayoutConnectionObject(
                id="balcony_001",
                type="balcony",
                bbox=self._bbox_from_any(balcony_data.get("bounding_box") or balcony_data.get("bbox")),
                approx_bbox=self._bbox_from_any(balcony_data.get("approx_bbox") or balcony_data.get("approximate_bbox")),
                polygon=balcony_data.get("polygon"),
                position=self._safe_string(balcony_data.get("position"), default="unknown"),
                exists=True,
                locked=True,
                editable=False,
                confidence=float(balcony_data.get("confidence") or 0.75),
                geometry_confidence=float(balcony_data.get("geometry_confidence") or 0.0),
                geometry_notes=self._string_list(balcony_data.get("geometry_notes")),
                notes=[],
            )
        ]

    def build_constraints(self, floorplan: FloorplanAnalysisValidatedArtifact) -> list[str]:
        constraints = [
            "Do not change wall positions.",
            "Do not move doors or windows.",
            "Preserve original room layout.",
            "Furniture placement must stay inside the target room.",
            "Labels must use customer-approved English text.",
            "Human review is required before rendering.",
            "Use a bright, airy palette with light warm neutrals.",
            "Do not render walls, partitions, or wet-area blocks as heavy dark filled masses.",
            "Place the washing machine only in the Wash Room at the Wash / 洗 mark.",
            "Orient furniture naturally without changing the floorplan.",
        ]
        for constraint in floorplan.normalized_analysis.get("constraints") or []:
            if isinstance(constraint, str) and constraint not in constraints:
                constraints.append(constraint)
        return constraints

    def _build_furniture_object(
        self,
        furniture_id: str,
        furniture_type: str,
        room: LayoutRoomObject,
        base_color: str | None,
        observed_color: str | None,
        accent_colors: list[str],
        confidence: float,
        notes: list[str],
    ) -> LayoutFurnitureObject:
        return LayoutFurnitureObject(
            id=furniture_id,
            type=furniture_type,
            room_type=room.type,
            room_functional_role=room.functional_role,
            room_id=room.id,
            bbox=None,
            position_hint=f"inside {room.label.lower()}",
            rotation=0.0,
            base_color=base_color,
            observed_color=observed_color,
            accent_colors=self._dedupe_keep_order(accent_colors),
            source="interior_analysis_validated",
            placement_status="suggested_unplaced",
            orientation=self._default_orientation_for_furniture(furniture_type),
            facing_to=self._default_facing_to_for_furniture(furniture_type),
            orientation_rule=self._default_orientation_rule_for_furniture(furniture_type),
            aligned_to=self._default_aligned_to_for_furniture(furniture_type),
            headboard_against_wall=True if "bed" in furniture_type else None,
            required_by_fixture_anchor=(furniture_type == "washing_machine"),
            anchor_fixture_id="fixture_washing_machine_anchor_001" if furniture_type == "washing_machine" else None,
            locked=False,
            editable=True,
            confidence=max(0.0, min(1.0, confidence)),
            notes=notes,
        )

    def _bbox_from_any(self, value) -> LayoutBoundingBox | None:
        if value is None:
            return None
        if isinstance(value, LayoutBoundingBox):
            return value
        if isinstance(value, dict):
            keys = {"x_min", "y_min", "x_max", "y_max"}
            if keys.issubset(value.keys()):
                return LayoutBoundingBox(**{key: value.get(key) for key in keys})
        if isinstance(value, list) and len(value) == 4:
            return LayoutBoundingBox(x_min=value[0], y_min=value[1], x_max=value[2], y_max=value[3])
        return None

    def _normalize_layout_room_type(self, room_type: str | None) -> str:
        normalized = self._safe_string(room_type, default="unknown").lower().replace(" ", "_")
        return self.ROOM_TYPE_TO_LAYOUT_TYPE.get(normalized, normalized if normalized in self.APPROVED_LAYOUT_LABELS else "unknown")

    def _normalize_layout_fixture_type(self, fixture_type: str | None) -> str:
        normalized = self._safe_string(fixture_type, default="unknown").lower().replace(" ", "_")
        if normalized == "washing_machine_anchor":
            return "washing_machine_anchor"
        return self._normalize_layout_room_type(normalized)

    def _layout_label_for_room(self, layout_type: str, fallback: str | None) -> str:
        return self.APPROVED_LAYOUT_LABELS.get(layout_type, fallback or "Unknown")

    def _first_color(self, value) -> str | None:
        colors = self._string_list(value)
        return colors[0] if colors else None

    def _string_list(self, value) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return []

    def _safe_string(self, value, default: str | None = None) -> str | None:
        if value is None:
            return default
        text = str(value).strip()
        return text if text else default

    def _detected_object_types(self, room_observation: dict) -> list[str]:
        detected = []
        for item in room_observation.get("detected_objects", []) if isinstance(room_observation, dict) else []:
            if isinstance(item, dict) and item.get("object_type"):
                detected.append(str(item["object_type"]).strip().lower())
        return self._dedupe_keep_order(detected)

    def _map_signal_to_furniture_type(self, signal: str | None) -> str | None:
        mapping = {
            "sofa_1_seater": "sofa_1_seater",
            "sofa_2_seater": "sofa_2_seater",
            "sofa_3_seater": "sofa_3_seater",
            "sectional_sofa": "sectional_sofa",
            "sofa_bed": "sofa_bed",
            "single_bed": "single_bed",
            "semi_double_bed": "semi_double_bed",
            "double_bed": "double_bed",
            "two_single_beds": "two_single_beds",
            "tv": "tv",
            "tv_stand": "tv_stand",
            "dining_table": "dining_table",
            "coffee_table": "coffee_table",
            "chair": "chair",
            "potted_plant": "potted_plant",
            "curtain": "curtain",
            "wall_art": "wall_art",
            "desk": "desk",
            "shelf": "shelf",
            "floor_lamp": "floor_lamp",
            "bed": "bed",
            "pillow": "pillow",
            "blanket": "blanket",
            "rug": "rug",
            "kitchen_counter": "kitchen_counter",
            "sink": "sink",
            "stove": "stove",
            "cabinet": "cabinet",
            "bathtub": "bathtub",
            "shower": "shower",
            "towel": "towel",
            "washing_machine": "washing_machine",
        }
        if not signal:
            return None
        return mapping.get(str(signal).strip().lower())

    def _map_detected_object_to_furniture_type(self, detected: str | None) -> str | None:
        mapping = {
            "table": "coffee_table",
            "coffee_table": "coffee_table",
            "tv": "tv",
            "tv_stand": "tv_stand",
            "rug": "rug",
            "curtain": "curtain",
            "plant": "potted_plant",
            "potted_plant": "potted_plant",
            "wall_art": "wall_art",
            "dining_table": "dining_table",
            "chair": "chair",
            "desk": "desk",
            "shelf": "shelf",
            "floor_lamp": "floor_lamp",
            "bed": "bed",
            "pillow": "pillow",
            "blanket": "blanket",
            "kitchen_counter": "kitchen_counter",
            "sink": "sink",
            "stove": "stove",
            "cabinet": "cabinet",
            "bathtub": "bathtub",
            "shower": "shower",
            "towel": "towel",
            "washing_machine": "washing_machine",
        }
        if not detected:
            return None
        return mapping.get(str(detected).strip().lower())

    @staticmethod
    def _default_orientation_for_furniture(furniture_type: str) -> str:
        if furniture_type in {"tv", "tv_stand"}:
            return "north"
        if furniture_type.startswith("sofa"):
            return "south"
        if "bed" in furniture_type:
            return "east"
        if furniture_type == "washing_machine":
            return "unknown"
        return "unknown"

    @staticmethod
    def _default_facing_to_for_furniture(furniture_type: str) -> str:
        if furniture_type.startswith("sofa"):
            return "tv"
        if furniture_type in {"tv", "tv_stand"}:
            return "sofa"
        if "bed" in furniture_type:
            return "room_center"
        if furniture_type == "washing_machine":
            return "wall"
        return "unknown"

    @staticmethod
    def _default_orientation_rule_for_furniture(furniture_type: str) -> str | None:
        if furniture_type.startswith("sofa"):
            return "face_tv_when_possible"
        if furniture_type in {"tv", "tv_stand"}:
            return "face_sofa_when_possible"
        if furniture_type == "coffee_table":
            return "between_sofa_and_tv_when_possible"
        if "bed" in furniture_type:
            return "align_to_wall_with_headboard"
        if furniture_type in {"dining_table", "chair"}:
            return "align_neatly_and_keep_circulation_clear"
        if furniture_type == "washing_machine":
            return "align_to_wash_anchor_or_wall"
        return None

    @staticmethod
    def _default_aligned_to_for_furniture(furniture_type: str) -> str | None:
        if furniture_type in {"dining_table", "chair"}:
            return "room_axis"
        if furniture_type == "washing_machine":
            return "wash_anchor"
        if "bed" in furniture_type:
            return "wall"
        return None

    def _observation_accent_colors(self, observation: dict) -> list[str]:
        if not isinstance(observation, dict):
            return []
        sofa = observation.get("sofa") if isinstance(observation.get("sofa"), dict) else {}
        bed = observation.get("bed") if isinstance(observation.get("bed"), dict) else {}
        return self._dedupe_keep_order(
            self._string_list(sofa.get("cushion_colors")) + self._string_list(bed.get("cushion_colors"))
        )

    def _default_base_color_for_furniture(self, furniture_type: str | None, observation: dict) -> str | None:
        if not furniture_type:
            return None
        normalized = str(furniture_type).strip().lower()
        if normalized.startswith("sofa"):
            sofa = observation.get("sofa") if isinstance(observation, dict) and isinstance(observation.get("sofa"), dict) else {}
            return self._safe_string(sofa.get("base_color"), default="white")
        if "bed" in normalized:
            bed = observation.get("bed") if isinstance(observation, dict) and isinstance(observation.get("bed"), dict) else {}
            return self._safe_string(bed.get("base_color"), default="white")
        if normalized in {"curtain", "rug", "pillow", "blanket", "towel"}:
            return "white"
        return None

    def _collect_avoid_keywords(self, interior: InteriorAnalysisValidatedArtifact) -> list[str]:
        avoid_keywords: list[str] = []
        for group_name in ("ideal", "acceptable", "ng"):
            for item in interior.style_reference_analysis.get(group_name, []):
                if isinstance(item, dict):
                    avoid_keywords.extend(self._string_list(item.get("avoid_cues")))
        return avoid_keywords

    @staticmethod
    def _room_assignment_lookup(room_assignment) -> dict[str, dict]:
        records = getattr(room_assignment, "rooms", None) or []
        result: dict[str, dict] = {}
        for record in records:
            room_id = getattr(record, "room_id", None)
            if not room_id:
                continue
            result[str(room_id)] = {
                "functional_role": getattr(record, "functional_role", None),
                "reasons": list(getattr(record, "reasons", None) or []),
            }
        return result

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
