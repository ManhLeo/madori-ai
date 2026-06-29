from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException

from app.schemas import FloorplanAnalysis, RoomInfo
from app.schemas.run import (
    AnalysisQualitySummary,
    FloorplanAnalysisValidatedArtifact,
    FloorplanSemanticAnalysisArtifact,
    GeometrySummary,
    LayoutBoundingBox,
    RunMetadata,
    ValidatedDimensionRecord,
    ValidatedDoorRecord,
    ValidatedFixtureRecord,
    ValidatedLabelRecord,
    ValidatedRoomRecord,
    ValidatedWindowRecord,
)
from app.services.vision_analyzer import VisionAnalyzer


class FloorplanAnalysisValidationService:
    APPROVED_LABEL_MAP = {
        "living_room": "Living Room",
        "bedroom": "Bed Room",
        "kitchen": "Kitchen",
        "dining_kitchen": "Dining Kitchen",
        "bathroom": "Bath Room",
        "toilet": "Toilet",
        "washroom": "Wash Room",
        "closet": "Closet",
        "walk_in_closet": "Closet",
        "entrance": "Entrance",
        "balcony": "Balcony",
        "hallway": "Hallway",
        "storage": "Storage",
        "unknown": "Unknown",
    }
    FIXTURE_ROOM_TYPES = {
        "kitchen",
        "dining_kitchen",
        "bathroom",
        "toilet",
        "washroom",
        "closet",
        "walk_in_closet",
        "entrance",
        "balcony",
        "storage",
    }
    DIMENSION_VALUE_PATTERN = re.compile(r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>m2|sqm|sq\.?m|j|jo|tatami)?", re.IGNORECASE)

    def __init__(self, storage_dir: Path, storage_runs_dir: Path) -> None:
        self.storage_dir = storage_dir
        self.storage_runs_dir = storage_runs_dir
        self.vision_analyzer = VisionAnalyzer()

    def validate_run(self, metadata: RunMetadata) -> FloorplanAnalysisValidatedArtifact:
        run_dir = self._safe_run_dir(metadata.run_id)
        source_artifact = self._load_analysis_artifact(run_dir)

        try:
            raw_analysis = FloorplanAnalysis.model_validate(source_artifact.analysis)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"failed to validate floorplan analysis schema: {exc}") from exc

        normalized_analysis = self.vision_analyzer.normalize_floorplan_analysis(raw_analysis)
        rooms = self._normalize_rooms(normalized_analysis.rooms)
        room_id_by_type = self._build_room_id_index(rooms)
        fixtures = self._derive_fixtures(rooms, normalized_analysis.model_dump(mode="json"))
        doors = self._normalize_doors(normalized_analysis, room_id_by_type)
        windows = self._normalize_windows(normalized_analysis, room_id_by_type)
        labels = self._normalize_labels(rooms)
        dimensions = self._normalize_dimensions(rooms)
        warnings = self._build_warnings(normalized_analysis, rooms, fixtures, doors, windows, dimensions)
        errors: list[str] = []
        checks = self._build_checks(rooms, labels, errors)
        quality_summary = self._build_quality_summary(rooms, fixtures, doors, windows, labels, dimensions)
        geometry_summary = self._build_geometry_summary(rooms, fixtures)

        artifact = FloorplanAnalysisValidatedArtifact(
            run_id=metadata.run_id,
            generated_at=datetime.now(timezone.utc),
            source_analysis_path="storage/runs/{run_id}/artifacts/floorplan_analysis.json".format(run_id=metadata.run_id),
            provider=source_artifact.provider,
            model=source_artifact.model,
            source_image=source_artifact.source_image,
            normalized_analysis=normalized_analysis.model_dump(mode="json"),
            approved_label_map=self.APPROVED_LABEL_MAP,
            rooms=rooms,
            fixtures=fixtures,
            doors=doors,
            windows=windows,
            labels=labels,
            dimensions=dimensions,
            checks=checks,
            quality_summary=quality_summary,
            geometry_summary=geometry_summary,
            warnings=warnings,
            errors=errors,
        )

        artifacts_dir = self._artifacts_dir(metadata, run_dir)
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        self._write_json(artifacts_dir / "floorplan_analysis_validated.json", artifact.model_dump(mode="json"))
        return artifact

    def load_validated_artifact(self, run_id: str) -> FloorplanAnalysisValidatedArtifact:
        run_dir = self._safe_run_dir(run_id)
        artifact_path = run_dir / "artifacts" / "floorplan_analysis_validated.json"
        if not artifact_path.exists():
            raise HTTPException(status_code=404, detail="validated floorplan analysis artifact not found")
        try:
            return FloorplanAnalysisValidatedArtifact.model_validate_json(artifact_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=500, detail="failed to read validated floorplan analysis artifact") from exc

    def _normalize_rooms(self, rooms: list[RoomInfo]) -> list[ValidatedRoomRecord]:
        normalized_rooms: list[ValidatedRoomRecord] = []
        for index, room in enumerate(rooms, start=1):
            room_type = room.type or "unknown"
            bbox, approx_bbox, geometry_warnings = self._normalize_geometry(
                bbox_value=room.bounding_box,
                approx_bbox_value=room.approx_bbox,
                label=f"room_{index:03d}",
            )
            normalized_rooms.append(
                ValidatedRoomRecord(
                    id=f"room_{index:03d}",
                    type=room_type,
                    approved_label=self.APPROVED_LABEL_MAP.get(room_type, "Unknown"),
                    source_label=room.room_name,
                    position=room.position or "unknown",
                    size=room.size,
                    bbox=bbox,
                    approx_bbox=approx_bbox,
                    bounding_box=bbox,
                    polygon=self._normalize_polygon(room.polygon),
                    confidence=self._clamp_confidence(room.confidence),
                    geometry_confidence=self._clamp_confidence(room.geometry_confidence),
                    geometry_notes=list(room.geometry_notes or []) + geometry_warnings,
                    connected_to=list(room.connected_to or []),
                )
            )
        return normalized_rooms

    @staticmethod
    def _build_room_id_index(rooms: list[ValidatedRoomRecord]) -> dict[str, list[str]]:
        index: dict[str, list[str]] = {}
        for room in rooms:
            index.setdefault(room.type, []).append(room.id)
        return index

    def _derive_fixtures(self, rooms: list[ValidatedRoomRecord], normalized_analysis: dict | None = None) -> list[ValidatedFixtureRecord]:
        fixtures: list[ValidatedFixtureRecord] = []
        for room in rooms:
            if room.type not in self.FIXTURE_ROOM_TYPES:
                continue
            fixtures.append(
                ValidatedFixtureRecord(
                    id=f"fixture_{len(fixtures) + 1:03d}",
                    type=room.type,
                    fixture_type=room.type,
                    approved_label=self.APPROVED_LABEL_MAP.get(room.type, "Unknown"),
                    source_room_id=room.id,
                    source_room_type=room.type,
                    room_type=room.type,
                    position=room.position,
                    bbox=room.bbox,
                    approx_bbox=room.approx_bbox,
                    polygon=room.polygon,
                    confidence=room.confidence,
                    geometry_confidence=room.geometry_confidence,
                    geometry_notes=list(room.geometry_notes or []),
                )
            )
        wash_anchor = self._build_washing_machine_anchor(rooms, normalized_analysis or {})
        if wash_anchor is not None:
            fixtures.append(wash_anchor)
        return fixtures

    def _build_washing_machine_anchor(
        self,
        rooms: list[ValidatedRoomRecord],
        normalized_analysis: dict,
    ) -> ValidatedFixtureRecord | None:
        wash_room = next((room for room in rooms if room.type == "washroom"), None)
        labels = normalized_analysis.get("labels") if isinstance(normalized_analysis, dict) else []
        has_wash_label = False
        for label in labels if isinstance(labels, list) else []:
            if not isinstance(label, dict):
                continue
            source_text = str(label.get("text") or label.get("source_text") or "").strip().lower()
            english_text = str(label.get("english") or "").strip().lower()
            if source_text in {"洗", "wash"} or english_text == "wash":
                has_wash_label = True
                break
        if not has_wash_label and wash_room is None:
            return None
        geometry_notes: list[str] = ["Semantic wash fixture anchor only; not CAD-accurate."]
        bbox = wash_room.bbox if wash_room is not None else None
        approx_bbox = wash_room.approx_bbox if wash_room is not None else None
        if bbox is None:
            geometry_notes.append("Wash anchor bbox is approximate or missing.")
        return ValidatedFixtureRecord(
            id="fixture_washing_machine_anchor_001",
            type="washing_machine_anchor",
            fixture_type="washing_machine_anchor",
            approved_label="Wash Room",
            source_room_id=wash_room.id if wash_room is not None else None,
            source_room_type=wash_room.type if wash_room is not None else "washroom",
            room_type="washroom",
            required=True,
            source_label="洗" if has_wash_label else "Wash",
            position=wash_room.position if wash_room is not None else None,
            bbox=bbox,
            approx_bbox=approx_bbox,
            polygon=wash_room.polygon if wash_room is not None else None,
            confidence=wash_room.confidence if wash_room is not None else 0.6,
            geometry_confidence=wash_room.geometry_confidence if wash_room is not None else 0.2,
            geometry_notes=geometry_notes,
        )

    def _normalize_doors(
        self,
        analysis: FloorplanAnalysis,
        room_id_by_type: dict[str, list[str]],
    ) -> list[ValidatedDoorRecord]:
        doors: list[ValidatedDoorRecord] = []
        for index, door in enumerate(analysis.doors, start=1):
            connects = list(door.connects or [])
            has_unknown = any(connection == "unknown" or connection not in room_id_by_type for connection in connects)
            bbox, approx_bbox, geometry_warnings = self._normalize_geometry(
                bbox_value=door.bounding_box,
                approx_bbox_value=door.approx_bbox,
                label=f"door_{index:03d}",
            )
            doors.append(
                ValidatedDoorRecord(
                    id=f"door_{index:03d}",
                    position=door.position or "unknown",
                    connects=connects,
                    has_unknown_connection=has_unknown,
                    bbox=bbox,
                    approx_bbox=approx_bbox,
                    polygon=self._normalize_polygon(door.polygon),
                    confidence=self._clamp_confidence(door.confidence),
                    geometry_confidence=self._clamp_confidence(door.geometry_confidence),
                    geometry_notes=list(door.geometry_notes or []) + geometry_warnings,
                )
            )
        return doors

    def _normalize_windows(
        self,
        analysis: FloorplanAnalysis,
        room_id_by_type: dict[str, list[str]],
    ) -> list[ValidatedWindowRecord]:
        windows: list[ValidatedWindowRecord] = []
        for index, window in enumerate(analysis.windows, start=1):
            room_type = window.room
            room_id = None
            if room_type and room_type in room_id_by_type and room_id_by_type[room_type]:
                room_id = room_id_by_type[room_type][0]
            bbox, approx_bbox, geometry_warnings = self._normalize_geometry(
                bbox_value=window.bounding_box,
                approx_bbox_value=window.approx_bbox,
                label=f"window_{index:03d}",
            )
            windows.append(
                ValidatedWindowRecord(
                    id=f"window_{index:03d}",
                    position=window.position or "unknown",
                    room_id=room_id,
                    room_type=room_type,
                    approved_label=self.APPROVED_LABEL_MAP.get(room_type, "Unknown") if room_type else None,
                    bbox=bbox,
                    approx_bbox=approx_bbox,
                    polygon=self._normalize_polygon(window.polygon),
                    confidence=self._clamp_confidence(window.confidence),
                    geometry_confidence=self._clamp_confidence(window.geometry_confidence),
                    geometry_notes=list(window.geometry_notes or []) + geometry_warnings,
                )
            )
        return windows

    def _normalize_labels(self, rooms: list[ValidatedRoomRecord]) -> list[ValidatedLabelRecord]:
        labels: list[ValidatedLabelRecord] = []
        for room in rooms:
            labels.append(
                ValidatedLabelRecord(
                    id=f"label_{len(labels) + 1:03d}",
                    source_text=room.source_label,
                    approved_text=room.approved_label,
                    room_id=room.id,
                    room_type=room.type,
                    position=room.position,
                )
            )
        return labels

    def _normalize_dimensions(self, rooms: list[ValidatedRoomRecord]) -> list[ValidatedDimensionRecord]:
        dimensions: list[ValidatedDimensionRecord] = []
        for room in rooms:
            raw_value = room.size
            parsed_value = None
            unit = None
            status = "missing"
            if raw_value:
                match = self.DIMENSION_VALUE_PATTERN.search(raw_value)
                if match:
                    parsed_value = float(match.group("value"))
                    unit = match.group("unit")
                    status = "parsed"
                else:
                    status = "unparsed"
            dimensions.append(
                ValidatedDimensionRecord(
                    id=f"dimension_{len(dimensions) + 1:03d}",
                    room_id=room.id,
                    raw_value=raw_value,
                    parsed_value=parsed_value,
                    unit=unit,
                    status=status,
                )
            )
        return dimensions

    def _build_warnings(
        self,
        analysis: FloorplanAnalysis,
        rooms: list[ValidatedRoomRecord],
        fixtures: list[ValidatedFixtureRecord],
        doors: list[ValidatedDoorRecord],
        windows: list[ValidatedWindowRecord],
        dimensions: list[ValidatedDimensionRecord],
    ) -> list[str]:
        warnings: list[str] = []
        if any(room.type == "unknown" for room in rooms):
            warnings.append("Some room types remain unknown after deterministic normalization.")
        if any(door.has_unknown_connection for door in doors):
            warnings.append("Some door connections reference unknown or unmatched room types.")
        if any(window.room_id is None for window in windows):
            warnings.append("Some windows could not be matched to a normalized room record.")
        if any(dimension.status != "parsed" for dimension in dimensions):
            warnings.append("Some room dimensions are missing or could not be parsed deterministically.")
        if any(room.bbox is None for room in rooms):
            warnings.append("Some rooms are missing approximate geometry and will require manual review before safe furniture placement.")
        if any(fixture.type == "washing_machine_anchor" and fixture.bbox is None for fixture in fixtures):
            warnings.append("Wash / 洗 was detected but the washing_machine_anchor bbox is approximate or missing.")
        if analysis.balcony and analysis.balcony.exists and analysis.balcony.position == "unknown":
            warnings.append("Balcony presence was detected but its position is still unknown.")
        return warnings

    @staticmethod
    def _build_checks(
        rooms: list[ValidatedRoomRecord],
        labels: list[ValidatedLabelRecord],
        errors: list[str],
    ) -> dict[str, bool]:
        return {
            "source_analysis_present": True,
            "schema_valid": True,
            "rooms_present": len(rooms) > 0,
            "approved_labels_applied": len(labels) == len(rooms) and all(bool(label.approved_text) for label in labels),
            "has_errors": bool(errors),
        }

    @staticmethod
    def _build_quality_summary(
        rooms: list[ValidatedRoomRecord],
        fixtures: list[ValidatedFixtureRecord],
        doors: list[ValidatedDoorRecord],
        windows: list[ValidatedWindowRecord],
        labels: list[ValidatedLabelRecord],
        dimensions: list[ValidatedDimensionRecord],
    ) -> AnalysisQualitySummary:
        unknown_room_count = sum(1 for room in rooms if room.type == "unknown")
        door_unknown_connection_count = sum(1 for door in doors if door.has_unknown_connection)
        window_unassigned_count = sum(1 for window in windows if window.room_id is None)
        dimension_missing_count = sum(1 for dimension in dimensions if dimension.status != "parsed")
        approved_labels_complete = len(labels) == len(rooms) and all(bool(label.approved_text) for label in labels)
        needs_manual_review = any(
            [
                unknown_room_count > 0,
                door_unknown_connection_count > 0,
                window_unassigned_count > 0,
                dimension_missing_count > 0,
            ]
        )
        return AnalysisQualitySummary(
            room_count=len(rooms),
            fixture_count=len(fixtures),
            door_count=len(doors),
            window_count=len(windows),
            label_count=len(labels),
            dimension_count=len(dimensions),
            approved_labels_complete=approved_labels_complete,
            unknown_room_count=unknown_room_count,
            door_unknown_connection_count=door_unknown_connection_count,
            window_unassigned_count=window_unassigned_count,
            dimension_missing_count=dimension_missing_count,
            needs_manual_review=needs_manual_review,
            status="needs_review" if needs_manual_review else "ok",
        )

    def _build_geometry_summary(
        self,
        rooms: list[ValidatedRoomRecord],
        fixtures: list[ValidatedFixtureRecord],
    ) -> GeometrySummary:
        rooms_with_bbox = sum(1 for room in rooms if room.bbox is not None)
        fixtures_with_bbox = sum(1 for fixture in fixtures if fixture.bbox is not None)
        furniture_target_room_types = {"living_room", "bedroom", "kitchen"}
        geometry_ready = any(room.type in furniture_target_room_types and room.bbox is not None for room in rooms)
        return GeometrySummary(
            room_count=len(rooms),
            rooms_with_bbox=rooms_with_bbox,
            rooms_missing_bbox=max(0, len(rooms) - rooms_with_bbox),
            fixture_count=len(fixtures),
            fixtures_with_bbox=fixtures_with_bbox,
            geometry_ready_for_furniture_planning=geometry_ready,
        )

    def _normalize_geometry(
        self,
        *,
        bbox_value,
        approx_bbox_value,
        label: str,
    ) -> tuple[LayoutBoundingBox | None, LayoutBoundingBox | None, list[str]]:
        warnings: list[str] = []
        bbox = self._normalize_bbox_object(bbox_value)
        approx_bbox = self._normalize_bbox_object(approx_bbox_value or bbox_value)
        if bbox is None and bbox_value is not None:
            warnings.append(f"{label} bbox was invalid and has been cleared.")
        if approx_bbox is None and approx_bbox_value is not None:
            warnings.append(f"{label} approx_bbox was invalid and has been cleared.")
        if bbox is None and approx_bbox is not None:
            bbox = approx_bbox
        if bbox is None:
            approx_bbox = None
        return bbox, approx_bbox, warnings

    def _normalize_bbox_object(self, value) -> LayoutBoundingBox | None:
        if value is None:
            return None
        if isinstance(value, LayoutBoundingBox):
            raw = value.model_dump(mode="json")
        elif isinstance(value, dict):
            raw = dict(value)
        elif isinstance(value, (list, tuple)) and len(value) == 4:
            raw = {"x_min": value[0], "y_min": value[1], "x_max": value[2], "y_max": value[3]}
        else:
            return None

        try:
            x_min = self._clamp_bbox_value(raw.get("x_min", raw.get("left")))
            y_min = self._clamp_bbox_value(raw.get("y_min", raw.get("top")))
            x_max = self._clamp_bbox_value(raw.get("x_max", raw.get("right")))
            y_max = self._clamp_bbox_value(raw.get("y_max", raw.get("bottom")))
        except Exception:
            return None

        if None in {x_min, y_min, x_max, y_max}:
            return None
        if x_min >= x_max or y_min >= y_max:
            return None
        if (x_max - x_min) < 8 or (y_max - y_min) < 8:
            return None
        return LayoutBoundingBox(x_min=x_min, y_min=y_min, x_max=x_max, y_max=y_max)

    @staticmethod
    def _normalize_polygon(value) -> list[list[float]] | None:
        if not isinstance(value, list):
            return None
        polygon: list[list[float]] = []
        for point in value:
            if not isinstance(point, (list, tuple)) or len(point) < 2:
                continue
            try:
                polygon.append([float(point[0]), float(point[1])])
            except (TypeError, ValueError):
                continue
        return polygon or None

    @staticmethod
    def _clamp_confidence(value) -> float:
        try:
            return max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _clamp_bbox_value(value) -> int | None:
        if value is None:
            return None
        try:
            number = int(float(value))
        except (TypeError, ValueError):
            return None
        return max(0, min(1199, number))

    def _load_analysis_artifact(self, run_dir: Path) -> FloorplanSemanticAnalysisArtifact:
        artifact_path = run_dir / "artifacts" / "floorplan_analysis.json"
        if not artifact_path.exists():
            raise HTTPException(status_code=400, detail="floorplan analysis artifact not found; run semantic analysis first")
        try:
            return FloorplanSemanticAnalysisArtifact.model_validate_json(artifact_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=500, detail="failed to read floorplan analysis artifact") from exc

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

    def _artifacts_dir(self, metadata: RunMetadata, run_dir: Path) -> Path:
        if metadata.workspace and metadata.workspace.artifacts_dir:
            return self._resolve_relative_path(metadata.workspace.artifacts_dir)
        return run_dir / "artifacts"

    @staticmethod
    def _write_json(path: Path, payload: dict) -> None:
        try:
            with path.open("w", encoding="utf-8") as output_file:
                json.dump(payload, output_file, ensure_ascii=False, indent=2)
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"failed to write {path.name}") from exc
