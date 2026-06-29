from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException

from app.schemas.run import (
    FloorplanAnalysisValidatedArtifact,
    FurnitureCleanupSummary,
    InteriorAnalysisValidatedArtifact,
    LayoutFurnitureObject,
    LayoutRoomObject,
    RoomFunctionAssignmentArtifact,
    RoomFunctionAssignmentRoomRecord,
    RoomFunctionAssignmentSummary,
    RunMetadata,
)


class RoomFunctionAssignmentService:
    LIVING_ROOM_TYPES = {"living_room", "dining_kitchen"}
    WESTERN_ROOM_LABEL_TOKENS = {"洋室", "western", "bed room", "bedroom"}
    BEDROOM_ALLOWED_FURNITURE = {"bed", "two_single_beds", "pillow", "blanket", "wardrobe", "curtain", "wall_art", "floor_lamp", "potted_plant"}
    MEDIA_LOUNGE_ALLOWED_FURNITURE = {"sofa", "sofa_1_seater", "sofa_2_seater", "sofa_3_seater", "tv", "tv_stand", "coffee_table", "rug", "curtain", "wall_art", "potted_plant", "floor_lamp", "shelf"}
    LIVING_DINING_ALLOWED_FURNITURE = {"dining_table", "chair", "wall_art", "curtain", "potted_plant", "floor_lamp", "shelf"}
    LIVING_DINING_CONDITIONAL_FURNITURE = {"sofa", "sofa_1_seater", "sofa_2_seater", "sofa_3_seater", "tv", "tv_stand", "coffee_table"}
    KITCHEN_ALLOWED_FURNITURE = {"kitchen_counter", "sink", "stove", "cabinet", "refrigerator"}
    BATH_ROOM_ALLOWED_FURNITURE = {"bathtub", "shower", "towel"}
    TOILET_ALLOWED_FURNITURE = {"toilet", "towel"}
    WASH_ROOM_ALLOWED_FURNITURE = {"washbasin", "sink", "towel", "washing_machine"}
    CLOSET_ALLOWED_FURNITURE = {"wardrobe", "shelf"}
    CIRCULATION_ALLOWED_FURNITURE = {"potted_plant"}
    DIRECT_ROLE_MAP = {
        "kitchen": "kitchen",
        "bath_room": "bath_room",
        "bathroom": "bath_room",
        "toilet": "toilet",
        "wash_area": "wash_room",
        "washroom": "wash_room",
        "walk_in_closet": "closet",
        "closet": "closet",
        "hallway": "circulation_only",
        "entrance": "circulation_only",
        "balcony": "circulation_only",
        "storage": "circulation_only",
        "unknown": "circulation_only",
    }

    def __init__(self, storage_dir: Path, storage_runs_dir: Path) -> None:
        self.storage_dir = storage_dir
        self.storage_runs_dir = storage_runs_dir

    def assign_room_functions(self, metadata: RunMetadata) -> RoomFunctionAssignmentArtifact:
        floorplan = self.load_floorplan_analysis_validated(metadata.run_id)
        interior = self.load_interior_analysis_validated(metadata.run_id)
        artifact = self.build_room_function_assignment(metadata.run_id, floorplan, interior)
        self.write_room_function_assignment(metadata.run_id, artifact)
        return artifact

    def load_room_function_assignment(self, run_id: str) -> RoomFunctionAssignmentArtifact:
        path = self._artifacts_dir(run_id) / "room_function_assignment.json"
        if not path.exists():
            raise HTTPException(status_code=404, detail="room_function_assignment artifact not found")
        try:
            return RoomFunctionAssignmentArtifact.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=500, detail="failed to read room_function_assignment artifact") from exc

    def load_or_assign(self, metadata: RunMetadata) -> RoomFunctionAssignmentArtifact:
        path = self._artifacts_dir(metadata.run_id) / "room_function_assignment.json"
        if path.exists():
            try:
                return RoomFunctionAssignmentArtifact.model_validate_json(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
        return self.assign_room_functions(metadata)

    def load_floorplan_analysis_validated(self, run_id: str) -> FloorplanAnalysisValidatedArtifact:
        path = self._artifacts_dir(run_id) / "floorplan_analysis_validated.json"
        if not path.exists():
            raise HTTPException(status_code=400, detail="Run floorplan analysis validation before room function assignment")
        try:
            return FloorplanAnalysisValidatedArtifact.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"invalid floorplan_analysis_validated.json: {exc}") from exc

    def load_interior_analysis_validated(self, run_id: str) -> InteriorAnalysisValidatedArtifact | None:
        path = self._artifacts_dir(run_id) / "interior_analysis_validated.json"
        if not path.exists():
            return None
        try:
            return InteriorAnalysisValidatedArtifact.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def build_room_function_assignment(
        self,
        run_id: str,
        floorplan: FloorplanAnalysisValidatedArtifact,
        interior: InteriorAnalysisValidatedArtifact | None,
    ) -> RoomFunctionAssignmentArtifact:
        warnings = list(floorplan.warnings)
        errors = list(floorplan.errors)
        assignment_rules_applied: list[str] = []
        room_records: list[RoomFunctionAssignmentRoomRecord] = []

        rooms = list(floorplan.rooms or [])
        living_candidate = self._pick_main_living_area(rooms)
        western_rooms = [room for room in rooms if self._is_western_room(room)]
        western_room_count = len(western_rooms)

        furniture_signals = interior.furniture_signals if interior is not None else {}
        dining_cues = self._has_any_signal(furniture_signals, "dining", {"dining_table", "chair"})
        media_cues = self._has_any_signal(furniture_signals, "living_room", {"sofa_3_seater", "sofa_2_seater", "sofa_1_seater", "tv", "tv_stand", "coffee_table"})
        bed_cues = self._has_any_signal(furniture_signals, "bed_room", {"bed", "two_single_beds", "single_bed", "double_bed", "semi_double_bed"})

        room_role_map: dict[str, tuple[str, float, list[str]]] = {}

        if living_candidate is not None:
            living_role = "living_dining" if dining_cues else "living_dining"
            living_reasons = ["Primary living/LDK semantic room selected as main living area."]
            if dining_cues:
                living_reasons.append("Dining table/chair reference cues indicate dining should stay in the main living area.")
                assignment_rules_applied.append("dining_table_belongs_to_main_living_area")
            room_role_map[living_candidate.id] = (living_role, 0.92 if dining_cues else 0.82, living_reasons)

        media_room = None
        bedroom_room = None
        if western_room_count == 2 and dining_cues and media_cues and bed_cues:
            media_room = self._pick_media_lounge_room(western_rooms, living_candidate)
            if media_room is not None:
                assignment_rules_applied.append("two_western_rooms_media_lounge_enabled")
                room_role_map[media_room.id] = (
                    "media_lounge",
                    0.9,
                    [
                        "Exactly two western-style rooms were found.",
                        "Sofa/TV/coffee-table cues indicate a western room should become a media lounge.",
                        "Selected the western room with stronger adjacency/proximity to the main living area.",
                    ],
                )
            bedroom_room = next((room for room in western_rooms if media_room is None or room.id != media_room.id), None)
            if bedroom_room is not None:
                assignment_rules_applied.append("remaining_western_room_main_bedroom")
                room_role_map[bedroom_room.id] = (
                    "main_bedroom",
                    0.9,
                    [
                        "Bed or two_single_beds cues indicate one western room should remain the main bedroom.",
                        "Remaining western room selected after reserving one western room for media lounge.",
                        "Bedroom rule: one bed or two single beds only.",
                    ],
                )
            assignment_rules_applied.append("do_not_assign_both_western_rooms_as_bedrooms")
        elif western_room_count >= 1 and bed_cues:
            bedroom_room = western_rooms[-1]
            room_role_map[bedroom_room.id] = (
                "main_bedroom",
                0.8,
                [
                    "Bed cues detected from interior references.",
                    "Western-style room used as the main bedroom.",
                    "Bedroom rule: one bed or two single beds only.",
                ],
            )
            assignment_rules_applied.append("bedroom_rule_one_or_two_beds_only")
        elif western_room_count >= 1 and media_cues and living_candidate is None:
            media_room = western_rooms[0]
            room_role_map[media_room.id] = (
                "media_lounge",
                0.68,
                [
                    "Media cues were detected and no explicit main living room semantic label was available.",
                    "One western-style room was used as a media/lounge fallback.",
                ],
            )

        if western_room_count > 2:
            assignment_rules_applied.append("extra_western_rooms_fallback_to_guest_bedroom")

        for room in western_rooms:
            if room.id in room_role_map:
                continue
            role = "guest_bedroom" if bed_cues else "circulation_only"
            reasons = ["Fallback assignment for additional western-style room."]
            if role == "guest_bedroom":
                reasons.append("Kept as guest bedroom to avoid placing all furniture into the main living area.")
            room_role_map[room.id] = (role, 0.55, reasons)

        for room in rooms:
            if room.id in room_role_map:
                continue
            semantic_type = self._normalize_semantic_type(room.type)
            direct_role = self.DIRECT_ROLE_MAP.get(semantic_type)
            if direct_role is not None:
                room_role_map[room.id] = (
                    direct_role,
                    0.9,
                    [f"Direct deterministic mapping from semantic room type {semantic_type}."],
                )
                continue
            if semantic_type == "dining":
                room_role_map[room.id] = (
                    "dining_zone",
                    0.8,
                    ["Semantic dining room mapped to dining_zone."],
                )
                continue
            if semantic_type == "living_room":
                room_role_map[room.id] = (
                    "living_dining",
                    0.8,
                    ["Semantic living room mapped to living_dining."],
                )
                continue
            room_role_map[room.id] = (
                "circulation_only",
                0.4,
                ["No stronger deterministic role assignment rule matched this room."],
            )

        if western_room_count == 2 and media_cues and bed_cues and not dining_cues:
            warnings.append("Two western-style rooms were detected with media and bed cues, but dining cues were missing; assignment used conservative fallback.")
        if western_room_count == 0:
            warnings.append("No western-style rooms were detected for role reassignment.")
        if western_room_count == 2 and media_room is None and bedroom_room is None and media_cues and bed_cues:
            warnings.append("Two western-style rooms were detected, but deterministic media/bedroom split could not be applied cleanly.")

        for room in rooms:
            semantic_type = self._normalize_semantic_type(room.type)
            label = str(room.approved_label or room.source_label or room.id)
            functional_role, confidence, reasons = room_role_map[room.id]
            room_records.append(
                RoomFunctionAssignmentRoomRecord(
                    room_id=room.id,
                    semantic_type=semantic_type,
                    label=label,
                    functional_role=functional_role,
                    confidence=max(0.0, min(1.0, float(confidence))),
                    reasons=self._dedupe_keep_order(reasons),
                )
            )

        assignment_status = "assigned"
        if errors:
            assignment_status = "failed"
        elif warnings:
            assignment_status = "assigned_with_warnings"

        if media_room is not None:
            assignment_rules_applied.append("media_lounge_rule_sofa_tv_opposite_with_coffee_table_between_when_possible")
        if bedroom_room is not None:
            assignment_rules_applied.append("bedroom_rule_one_or_two_beds_only")

        return RoomFunctionAssignmentArtifact(
            run_id=run_id,
            assignment_status=assignment_status,
            generated_at=datetime.now(timezone.utc),
            rooms=room_records,
            assignment_rules_applied=self._dedupe_keep_order(assignment_rules_applied),
            warnings=self._dedupe_keep_order(warnings),
            errors=self._dedupe_keep_order(errors),
        )

    def build_metadata_updates(self, metadata: RunMetadata, artifact: RoomFunctionAssignmentArtifact) -> dict:
        media_room_id = next((room.room_id for room in artifact.rooms if room.functional_role == "media_lounge"), None)
        main_bedroom_room_id = next((room.room_id for room in artifact.rooms if room.functional_role == "main_bedroom"), None)
        dining_zone_assigned = any(room.functional_role in {"living_dining", "dining_zone"} for room in artifact.rooms)
        cleanup = artifact.furniture_cleanup_summary
        return {
            "status": "room_functions_assigned",
            "run_status": "room_functions_assigned",
            "processing": metadata.processing.model_copy(
                update={
                    "interior_analysis_validation": True,
                    "room_function_assignment": True,
                    "image_generation": False,
                    "watercolor_rendering": False,
                }
            ),
            "pipeline": {
                "current_phase": "phase_3c_room_function_assignment",
                "next_phase": "phase_4a_layout_object_creation",
            },
            "room_function_assignment_path": self._relative_artifact_path(metadata.run_id, "room_function_assignment.json"),
            "room_function_assignment_summary": RoomFunctionAssignmentSummary(
                assignment_status=artifact.assignment_status,
                western_room_count=sum(1 for room in artifact.rooms if self._is_western_semantic_type(room.semantic_type)),
                media_lounge_room_id=media_room_id,
                main_bedroom_room_id=main_bedroom_room_id,
                dining_zone_assigned=dining_zone_assigned,
                allowed_furniture_count=int(getattr(cleanup, "allowed_furniture_count", 0) or 0),
                suppressed_furniture_count=int(getattr(cleanup, "suppressed_furniture_count", 0) or 0),
                role_conflict_count=int(getattr(cleanup, "role_conflict_count", 0) or 0),
                needs_human_review=True,
                warnings_count=len(artifact.warnings),
                errors_count=len(artifact.errors),
            ),
        }

    def apply_furniture_cleanup(
        self,
        furniture_items: list[LayoutFurnitureObject],
        rooms: list[LayoutRoomObject],
        room_assignment: RoomFunctionAssignmentArtifact | None,
    ) -> tuple[list[LayoutFurnitureObject], FurnitureCleanupSummary]:
        room_role_by_id = {
            room.id: self._normalize_functional_role(getattr(room, "functional_role", None))
            for room in rooms
        }
        global_context = {
            "media_lounge_assigned": any(role == "media_lounge" for role in room_role_by_id.values()),
            "room_role_by_id": room_role_by_id,
            "room_assignment": room_assignment.model_dump(mode="json") if room_assignment is not None else None,
        }

        cleaned_items: list[LayoutFurnitureObject] = []
        by_role: dict[str, dict[str, int]] = {}
        suppression_reasons: list[str] = []
        allowed_count = 0
        suppressed_count = 0
        conditional_count = 0
        role_conflict_count = 0

        for item in furniture_items:
            functional_role = self._resolve_functional_role_for_furniture(item, room_role_by_id)
            compatibility = self.classify_furniture_compatibility(item.type, functional_role, global_context)
            status = str(compatibility.get("compatibility_status") or "allowed")
            suppression_reason = self._clean_string(compatibility.get("suppression_reason"))
            prompt_action = self._clean_string(compatibility.get("prompt_action")) or "mention"
            render_action = self._clean_string(compatibility.get("render_action"))

            role_bucket = by_role.setdefault(functional_role, {"allowed": 0, "suppressed": 0, "conditional_allowed": 0})
            role_bucket[status] = int(role_bucket.get(status, 0)) + 1

            update = {
                "functional_role": functional_role,
                "room_functional_role": functional_role,
                "compatibility_status": status,
                "suppression_reason": suppression_reason,
                "prompt_action": prompt_action,
            }

            if status == "suppressed":
                suppressed_count += 1
                role_conflict_count += 1
                if suppression_reason:
                    suppression_reasons.append(suppression_reason)
                update.update(
                    {
                        "placement_status": "suppressed_by_functional_role",
                        "bbox": None,
                        "target_room_bbox": None,
                        "x": None,
                        "y": None,
                        "width": None,
                        "height": None,
                        "placement_method": None,
                        "placement_confidence": 0.0,
                        "placement_notes": [suppression_reason] if suppression_reason else [],
                        "render_action": "do_not_draw",
                        "prompt_action": "do_not_mention",
                    }
                )
            else:
                allowed_count += 1
                if status == "conditional_allowed":
                    conditional_count += 1
                if render_action:
                    update["render_action"] = render_action

            cleaned_items.append(item.model_copy(update=update))

        cleanup_summary = FurnitureCleanupSummary(
            allowed_furniture_count=allowed_count,
            suppressed_furniture_count=suppressed_count,
            conditional_allowed_furniture_count=conditional_count,
            role_conflict_count=role_conflict_count,
            by_functional_role=by_role,
            suppression_reasons=self._dedupe_keep_order(suppression_reasons),
            warnings_count=0,
            errors_count=0,
        )
        return cleaned_items, cleanup_summary

    def classify_furniture_compatibility(self, furniture_type: str | None, functional_role: str | None, global_context: dict | None) -> dict[str, str]:
        normalized_furniture = self._normalize_furniture_type(furniture_type)
        normalized_role = self._normalize_functional_role(functional_role)
        media_lounge_assigned = bool((global_context or {}).get("media_lounge_assigned"))

        if normalized_role in {"main_bedroom", "guest_bedroom"}:
            if normalized_furniture in self.BEDROOM_ALLOWED_FURNITURE:
                return self._compatibility_result("allowed", prompt_action="mention", render_action="draw")
            return self._compatibility_result(
                "suppressed",
                suppression_reason=f"{normalized_furniture} conflicts with {normalized_role}; bedrooms only allow bed, bedding, wardrobe, curtain, wall art, floor lamp, or potted plant.",
            )

        if normalized_role == "media_lounge":
            if normalized_furniture in self.MEDIA_LOUNGE_ALLOWED_FURNITURE:
                return self._compatibility_result("allowed", prompt_action="mention", render_action="draw")
            return self._compatibility_result(
                "suppressed",
                suppression_reason=f"{normalized_furniture} conflicts with media_lounge; keep the room focused on sofa, TV, coffee table, rug, curtain, wall art, plant, lamp, or shelf cues.",
            )

        if normalized_role in {"living_dining", "dining_zone"}:
            if normalized_furniture in self.LIVING_DINING_ALLOWED_FURNITURE:
                return self._compatibility_result("allowed", prompt_action="mention", render_action="draw")
            if normalized_furniture in self.LIVING_DINING_CONDITIONAL_FURNITURE:
                if media_lounge_assigned:
                    return self._compatibility_result(
                        "suppressed",
                        suppression_reason=f"{normalized_furniture} belongs in media_lounge, and a media_lounge is already assigned.",
                    )
                return self._compatibility_result("conditional_allowed", prompt_action="mention", render_action="draw")
            if normalized_furniture in {"bed", "two_single_beds", "pillow", "blanket", "bathtub", "shower", "toilet", "washbasin"}:
                return self._compatibility_result(
                    "suppressed",
                    suppression_reason=f"{normalized_furniture} conflicts with living_dining; keep bedrooms and wet areas separate.",
                )
            return self._compatibility_result("suppressed", suppression_reason=f"{normalized_furniture} is not part of the living_dining role.")

        if normalized_role == "kitchen":
            if normalized_furniture in self.KITCHEN_ALLOWED_FURNITURE:
                return self._compatibility_result("allowed", prompt_action="mention", render_action="draw")
            if normalized_furniture in {"dining_table", "chair"}:
                return self._compatibility_result(
                    "suppressed",
                    suppression_reason=f"{normalized_furniture} belongs in living_dining or dining_zone, not kitchen.",
                )
            return self._compatibility_result("suppressed", suppression_reason=f"{normalized_furniture} conflicts with kitchen role.")

        if normalized_role == "bath_room":
            if normalized_furniture in self.BATH_ROOM_ALLOWED_FURNITURE:
                return self._compatibility_result("allowed", prompt_action="mention", render_action="draw")
            return self._compatibility_result("suppressed", suppression_reason=f"{normalized_furniture} conflicts with bath_room role.")

        if normalized_role == "toilet":
            if normalized_furniture in self.TOILET_ALLOWED_FURNITURE:
                return self._compatibility_result("allowed", prompt_action="mention", render_action="draw")
            return self._compatibility_result("suppressed", suppression_reason=f"{normalized_furniture} conflicts with toilet role.")

        if normalized_role == "wash_room":
            if normalized_furniture in self.WASH_ROOM_ALLOWED_FURNITURE:
                return self._compatibility_result("allowed", prompt_action="mention", render_action="draw")
            return self._compatibility_result("suppressed", suppression_reason=f"{normalized_furniture} conflicts with wash_room role.")

        if normalized_role == "closet":
            if normalized_furniture in self.CLOSET_ALLOWED_FURNITURE:
                return self._compatibility_result("allowed", prompt_action="mention", render_action="draw")
            return self._compatibility_result("suppressed", suppression_reason=f"{normalized_furniture} conflicts with closet role.")

        if normalized_role == "circulation_only":
            if normalized_furniture in self.CIRCULATION_ALLOWED_FURNITURE:
                return self._compatibility_result("allowed", prompt_action="mention", render_action="draw")
            return self._compatibility_result("suppressed", suppression_reason=f"{normalized_furniture} conflicts with circulation_only role.")

        if normalized_furniture in (
            self.BEDROOM_ALLOWED_FURNITURE
            | self.MEDIA_LOUNGE_ALLOWED_FURNITURE
            | self.LIVING_DINING_ALLOWED_FURNITURE
            | self.KITCHEN_ALLOWED_FURNITURE
            | self.BATH_ROOM_ALLOWED_FURNITURE
            | self.TOILET_ALLOWED_FURNITURE
            | self.WASH_ROOM_ALLOWED_FURNITURE
            | self.CLOSET_ALLOWED_FURNITURE
            | self.CIRCULATION_ALLOWED_FURNITURE
        ):
            return self._compatibility_result("allowed", prompt_action="mention", render_action="draw")
        return self._compatibility_result("conditional_allowed", prompt_action="mention", render_action="draw")

    def is_furniture_allowed_for_functional_role(self, furniture_type: str | None, functional_role: str | None, global_context: dict | None) -> bool:
        return self.classify_furniture_compatibility(furniture_type, functional_role, global_context)["compatibility_status"] != "suppressed"

    def _resolve_functional_role_for_furniture(self, furniture: LayoutFurnitureObject, room_role_by_id: dict[str, str]) -> str:
        for candidate in (
            getattr(furniture, "functional_role", None),
            getattr(furniture, "room_functional_role", None),
            room_role_by_id.get(str(getattr(furniture, "room_id", "") or "")),
            self._normalize_functional_role(getattr(furniture, "room_type", None)),
        ):
            normalized = self._normalize_functional_role(candidate)
            if normalized != "unknown":
                return normalized
        return "unknown"

    @staticmethod
    def _normalize_functional_role(value: str | None) -> str:
        if not value:
            return "unknown"
        return str(value).strip().lower().replace(" ", "_")

    @staticmethod
    def _normalize_furniture_type(value: str | None) -> str:
        if not value:
            return "unknown"
        return str(value).strip().lower().replace(" ", "_")

    @staticmethod
    def _clean_string(value: str | None) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text if text else None

    @staticmethod
    def _compatibility_result(
        status: str,
        *,
        suppression_reason: str | None = None,
        prompt_action: str | None = None,
        render_action: str | None = None,
    ) -> dict[str, str]:
        return {
            "compatibility_status": status,
            "suppression_reason": suppression_reason or "",
            "prompt_action": prompt_action or "",
            "render_action": render_action or "",
        }

    def write_room_function_assignment(self, run_id: str, artifact: RoomFunctionAssignmentArtifact) -> None:
        path = self._artifacts_dir(run_id) / "room_function_assignment.json"
        try:
            payload = json.dumps(artifact.model_dump(mode="json"), ensure_ascii=False, indent=2)
            temp_path = path.with_suffix(f"{path.suffix}.tmp")
            temp_path.write_text(payload, encoding="utf-8")
            temp_path.replace(path)
        except OSError as exc:
            raise HTTPException(status_code=500, detail="failed to write room_function_assignment artifact") from exc

    def _pick_main_living_area(self, rooms: list) -> object | None:
        candidates = [room for room in rooms if self._normalize_semantic_type(room.type) in self.LIVING_ROOM_TYPES]
        if not candidates:
            candidates = [room for room in rooms if self._normalize_semantic_type(room.type) == "living_room"]
        if not candidates:
            return None
        return max(candidates, key=lambda room: self._bbox_area(getattr(room, "bbox", None)))

    def _pick_media_lounge_room(self, western_rooms: list, living_candidate) -> object | None:
        if not western_rooms:
            return None
        if living_candidate is None:
            return western_rooms[0]
        return max(
            western_rooms,
            key=lambda room: (
                self._shared_boundary_score(getattr(room, "bbox", None), getattr(living_candidate, "bbox", None)),
                -self._center_distance(getattr(room, "bbox", None), getattr(living_candidate, "bbox", None)),
                -self._bbox_area(getattr(room, "bbox", None)),
            ),
        )

    def _has_any_signal(self, furniture_signals: dict, room_key: str, expected: set[str]) -> bool:
        values = furniture_signals.get(room_key) if isinstance(furniture_signals, dict) else None
        normalized = {str(value).strip().lower() for value in (values or [])}
        return bool(normalized.intersection({item.lower() for item in expected}))

    def _is_western_room(self, room) -> bool:
        semantic_type = self._normalize_semantic_type(getattr(room, "type", None))
        source_label = str(getattr(room, "source_label", "") or "").lower()
        approved_label = str(getattr(room, "approved_label", "") or "").lower()
        if semantic_type in {"bedroom", "bed_room"}:
            return True
        return any(token in source_label or token in approved_label for token in self.WESTERN_ROOM_LABEL_TOKENS)

    @staticmethod
    def _is_western_semantic_type(value: str) -> bool:
        return value in {"bedroom", "bed_room"}

    @staticmethod
    def _normalize_semantic_type(value: str | None) -> str:
        normalized = str(value or "unknown").strip().lower().replace(" ", "_")
        mapping = {
            "bathroom": "bath_room",
            "washroom": "wash_area",
            "walk_in_closet": "closet",
        }
        return mapping.get(normalized, normalized)

    @staticmethod
    def _bbox_area(bbox) -> float:
        if bbox is None:
            return 0.0
        x_min = float(getattr(bbox, "x_min", 0) or 0)
        y_min = float(getattr(bbox, "y_min", 0) or 0)
        x_max = float(getattr(bbox, "x_max", 0) or 0)
        y_max = float(getattr(bbox, "y_max", 0) or 0)
        return max(0.0, x_max - x_min) * max(0.0, y_max - y_min)

    @staticmethod
    def _shared_boundary_score(a, b) -> float:
        if a is None or b is None:
            return 0.0
        ax1, ay1, ax2, ay2 = float(a.x_min or 0), float(a.y_min or 0), float(a.x_max or 0), float(a.y_max or 0)
        bx1, by1, bx2, by2 = float(b.x_min or 0), float(b.y_min or 0), float(b.x_max or 0), float(b.y_max or 0)
        vertical_touch = min(ay2, by2) - max(ay1, by1)
        horizontal_touch = min(ax2, bx2) - max(ax1, bx1)
        shared = 0.0
        if abs(ax2 - bx1) <= 16 or abs(bx2 - ax1) <= 16:
            shared = max(shared, max(0.0, vertical_touch))
        if abs(ay2 - by1) <= 16 or abs(by2 - ay1) <= 16:
            shared = max(shared, max(0.0, horizontal_touch))
        return shared

    @staticmethod
    def _center_distance(a, b) -> float:
        if a is None or b is None:
            return 10_000.0
        ax = (float(a.x_min or 0) + float(a.x_max or 0)) / 2.0
        ay = (float(a.y_min or 0) + float(a.y_max or 0)) / 2.0
        bx = (float(b.x_min or 0) + float(b.x_max or 0)) / 2.0
        by = (float(b.y_min or 0) + float(b.y_max or 0)) / 2.0
        return ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5

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
