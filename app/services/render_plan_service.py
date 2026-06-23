from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException

from app.schemas.run import (
    FloorplanAnalysisValidatedArtifact,
    FurniturePlacementValidationArtifact,
    InteriorAnalysisValidatedArtifact,
    RenderPlanArtifact,
    RenderPlanSummary,
    RenderReadinessSummary,
    RunMetadata,
)


class RenderPlanService:
    ALLOWED_FLOOR_TONES = {"white", "light_brown", "dark_brown", "unknown"}
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
    ALWAYS_NEGATIVE_CONSTRAINTS = [
        "Do not change the floorplan structure.",
        "Do not move walls.",
        "Do not move doors.",
        "Do not move windows.",
        "Do not redraw rooms with different proportions.",
        "Do not use Japanese labels.",
        "Do not use oversaturated colors.",
        "Do not use flat brown fill.",
        "Do not create cartoon-like furniture.",
        "Do not create a generic interior unrelated to the provided layout.",
        "Do not draw unplaced furniture unless manually reviewed.",
    ]

    def __init__(self, storage_dir: Path, storage_runs_dir: Path) -> None:
        self.storage_dir = storage_dir
        self.storage_runs_dir = storage_runs_dir

    def create_render_plan(self, metadata: RunMetadata) -> RenderPlanArtifact:
        run_id = metadata.run_id
        layout = self.load_layout_furniture_validated(run_id)
        interior = self.load_interior_analysis_validated(run_id)
        floorplan = self.load_floorplan_analysis_validated(run_id)
        artifact = self.build_render_plan(run_id, layout, interior, floorplan, metadata)
        self.write_render_plan(run_id, artifact)
        return artifact

    def load_layout_furniture_validated(self, run_id: str) -> FurniturePlacementValidationArtifact:
        path = self._artifacts_dir(run_id) / "layout_furniture_validated.json"
        if not path.exists():
            raise HTTPException(status_code=400, detail="Run furniture placement validation before render plan creation")
        try:
            return FurniturePlacementValidationArtifact.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"invalid layout_furniture_validated.json: {exc}") from exc

    def load_interior_analysis_validated(self, run_id: str) -> InteriorAnalysisValidatedArtifact | None:
        path = self._artifacts_dir(run_id) / "interior_analysis_validated.json"
        if not path.exists():
            return None
        try:
            return InteriorAnalysisValidatedArtifact.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def load_floorplan_analysis_validated(self, run_id: str) -> FloorplanAnalysisValidatedArtifact | None:
        path = self._artifacts_dir(run_id) / "floorplan_analysis_validated.json"
        if not path.exists():
            return None
        try:
            return FloorplanAnalysisValidatedArtifact.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def load_render_plan(self, run_id: str) -> RenderPlanArtifact:
        path = self._artifacts_dir(run_id) / "render_plan.json"
        if not path.exists():
            raise HTTPException(status_code=404, detail="render_plan artifact not found")
        try:
            return RenderPlanArtifact.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=500, detail="failed to read render_plan artifact") from exc

    def build_render_plan(
        self,
        run_id: str,
        layout: FurniturePlacementValidationArtifact,
        interior: InteriorAnalysisValidatedArtifact | None,
        floorplan: FloorplanAnalysisValidatedArtifact | None,
        metadata: RunMetadata,
    ) -> RenderPlanArtifact:
        warnings = list(layout.warnings)
        errors = list(layout.errors)

        if interior is None:
            warnings.append("interior_analysis_validated.json is missing; using safe style defaults.")
        else:
            warnings.extend(interior.warnings)
            errors.extend(interior.errors)
        if floorplan is None:
            warnings.append("floorplan_analysis_validated.json is missing; using layout_furniture_validated structure only.")
        else:
            warnings.extend(floorplan.warnings)
            errors.extend(floorplan.errors)

        canvas = self.build_canvas_plan(layout)
        rooms = self.build_room_render_plan(layout, interior)
        furniture = self.build_furniture_render_plan(layout, interior)
        labels = self.build_label_render_plan(layout)
        style = self.build_style_render_plan(layout, interior)
        negative_constraints = self.build_negative_constraints(interior)
        prompt_sections = self.build_prompt_sections(rooms, furniture, labels, style, negative_constraints)

        auto_placed_count = sum(1 for item in furniture if item["render_action"] == "draw" and item.get("placement_status") == "auto_placed")
        unplaced_count = sum(1 for item in furniture if item.get("placement_status") == "suggested_unplaced")
        render_readiness = RenderReadinessSummary(
            ready_for_prompt_building=True,
            ready_for_image_generation=False,
            requires_human_review=True,
            has_validated_layout=True,
            has_validated_furniture=bool(layout.furniture),
            has_style_profile=bool(style),
            auto_placed_furniture_count=auto_placed_count,
            unplaced_furniture_count=unplaced_count,
            warnings_count=len(warnings),
            errors_count=len(errors),
        )

        quality = {
            "needs_human_review": True,
            "structure_locked": True,
            "semantic_layout_only": True,
            "pixel_perfect_geometry": False,
            "furniture_placement_validated": True,
            "image_generation_done": False,
            "watercolor_rendering_done": False,
        }

        render_plan_status = "created"
        if errors:
            render_plan_status = "failed"
        elif warnings or unplaced_count > 0 or interior is None:
            render_plan_status = "created_with_warnings"

        return RenderPlanArtifact(
            run_id=run_id,
            generated_at=datetime.now(timezone.utc),
            render_plan_status=render_plan_status,
            source={
                "layout_furniture_validated_artifact": self._relative_artifact_path(run_id, "layout_furniture_validated.json"),
                "interior_analysis_validated_artifact": self._relative_artifact_path(run_id, "interior_analysis_validated.json") if interior is not None else None,
                "floorplan_analysis_validated_artifact": self._relative_artifact_path(run_id, "floorplan_analysis_validated.json") if floorplan is not None else None,
                "normalized_floorplan_preview_url": self._normalized_floorplan_preview_url(run_id),
            },
            canvas=canvas,
            render_mode={
                "mode": "watercolor_floorplan_illustration",
                "image_generation_done": False,
                "watercolor_rendering_done": False,
                "render_engine": "not_selected",
                "render_phase": "planning_only",
            },
            structure_lock={
                "enabled": True,
                "do_not_modify_walls": True,
                "do_not_modify_room_boundaries": True,
                "do_not_modify_doors": True,
                "do_not_modify_windows": True,
                "do_not_modify_balcony": True,
                "preserve_original_layout": True,
                "structure_source": "layout_furniture_validated",
            },
            rooms=rooms,
            fixtures=[item.model_dump(mode="json") for item in layout.fixtures],
            doors=[item.model_dump(mode="json") for item in layout.doors],
            windows=[item.model_dump(mode="json") for item in layout.windows],
            balcony=[item.model_dump(mode="json") for item in layout.balcony],
            furniture=furniture,
            labels=labels,
            style=style,
            prompt_sections=prompt_sections,
            render_readiness=render_readiness,
            quality=quality,
            warnings=self._dedupe_keep_order(warnings),
            errors=self._dedupe_keep_order(errors),
        )

    def build_canvas_plan(self, layout: FurniturePlacementValidationArtifact) -> dict:
        return {
            "width": 1200,
            "height": 1200,
            "coordinate_space": "normalized_floorplan_1200",
            "output_format_candidates": ["png", "jpeg"],
            "background_color": layout.canvas.get("background_color", "white"),
        }

    def build_room_render_plan(
        self,
        layout: FurniturePlacementValidationArtifact,
        interior: InteriorAnalysisValidatedArtifact | None,
    ) -> list[dict]:
        fallback_floor_tone = self._style_floor_tone(layout, interior)
        records: list[dict] = []
        for room in layout.rooms:
            floor_tone = room.floor_tone if room.floor_tone in self.ALLOWED_FLOOR_TONES and room.floor_tone != "unknown" else fallback_floor_tone
            records.append(
                {
                    "id": room.id,
                    "type": room.type,
                    "label": room.label,
                    "functional_role": room.functional_role,
                    "bbox": room.bbox.model_dump(mode="json") if room.bbox else None,
                    "floor_tone": floor_tone,
                    "render_instruction": f"Render this room with {floor_tone.replace('_', ' ')} watercolor floor tone while preserving exact room boundaries.",
                    "locked": True,
                    "editable": False,
                }
            )
        return records

    def build_furniture_render_plan(
        self,
        layout: FurniturePlacementValidationArtifact,
        interior: InteriorAnalysisValidatedArtifact | None,
    ) -> list[dict]:
        records: list[dict] = []
        for item in layout.furniture:
            base_color = item.base_color
            if item.type.startswith("sofa") or "bed" in item.type:
                base_color = "white"
            action = str(item.render_action or "draw")
            manual_review_required = True
            if item.placement_status == "suppressed_by_functional_role" or item.compatibility_status == "suppressed":
                action = "do_not_draw"
            elif item.placement_status == "suggested_unplaced":
                action = "skip_until_manual_placement"
            elif item.placement_status == "invalid":
                action = "do_not_draw"
            instruction = self._furniture_instruction(item.type, base_color, action)
            records.append(
                {
                    "id": item.id,
                    "type": item.type,
                    "room_id": item.room_id,
                    "room_type": item.room_type,
                    "room_functional_role": item.room_functional_role,
                    "functional_role": item.functional_role,
                    "bbox": item.bbox.model_dump(mode="json") if item.bbox else None,
                    "placement_status": item.placement_status,
                    "base_color": base_color,
                    "observed_color": item.observed_color,
                    "accent_colors": list(item.accent_colors or []),
                    "editable": True,
                    "locked": False,
                    "placement_confidence": float(item.placement_confidence or 0.0),
                    "compatibility_status": item.compatibility_status,
                    "suppression_reason": item.suppression_reason,
                    "render_action": action,
                    "prompt_action": item.prompt_action,
                    "render_instruction": instruction,
                    "manual_review_required": manual_review_required,
                }
            )
        return records

    def build_label_render_plan(self, layout: FurniturePlacementValidationArtifact) -> list[dict]:
        records: list[dict] = []
        for item in layout.labels:
            render_text = item.text if item.text in self.ALLOWED_LABELS else "Unknown"
            records.append(
                {
                    "id": item.id,
                    "render_text": render_text,
                    "text_original": item.text if render_text == "Unknown" else item.text_original,
                    "room_id": item.room_id,
                    "bbox": item.bbox.model_dump(mode="json") if item.bbox else None,
                    "position": item.position or "center",
                    "render_instruction": "Render label in clear English text. Do not use Japanese text.",
                }
            )
        return records

    def build_style_render_plan(
        self,
        layout: FurniturePlacementValidationArtifact,
        interior: InteriorAnalysisValidatedArtifact | None,
    ) -> dict:
        interior_summary = interior.interior_summary if interior is not None else {}
        customer_rules = interior.customer_rules if interior is not None else {}
        recommendations = interior.recommendations_for_next_phase if interior is not None else {}
        style_reference_analysis = interior.style_reference_analysis if interior is not None else {}
        positive_cues = [cue for group in style_reference_analysis.get("ideal", []) for cue in group.get("positive_cues", [])]
        acceptable_cues = [cue for group in style_reference_analysis.get("acceptable", []) for cue in group.get("positive_cues", [])]
        avoid_cues = [cue for group in style_reference_analysis.get("ng", []) for cue in group.get("avoid_cues", [])]

        floor_tone = self._style_floor_tone(layout, interior)
        return {
            "style_name": "soft_watercolor_floorplan",
            "watercolor_strength": "soft_translucent",
            "linework_style": "clean_precise_thin_lines",
            "palette": {
                "floor_tone": floor_tone,
                "dominant_colors": list(interior_summary.get("dominant_colors") or layout.style.dominant_colors or []),
                "accent_colors": list(layout.style.accent_colors or []),
                "material_keywords": list(interior_summary.get("material_keywords") or layout.style.material_keywords or []),
            },
            "positive_cues": self._dedupe_keep_order([str(value) for value in positive_cues]),
            "acceptable_cues": self._dedupe_keep_order([str(value) for value in acceptable_cues]),
            "avoid_cues": self._dedupe_keep_order([str(value) for value in avoid_cues or layout.style.avoid_keywords]),
            "customer_rules": {
                "bed_base_color": str(customer_rules.get("bed_and_sofa_base_color") or "white"),
                "sofa_base_color": str(customer_rules.get("bed_and_sofa_base_color") or "white"),
                "labels_language": "english",
                "suggested_floor_tone": str(recommendations.get("suggested_floor_tone") or floor_tone),
            },
        }

    def build_negative_constraints(self, interior: InteriorAnalysisValidatedArtifact | None) -> list[str]:
        constraints = list(self.ALWAYS_NEGATIVE_CONSTRAINTS)
        if interior is not None:
            for group in interior.style_reference_analysis.get("ng", []):
                constraints.extend([str(value) for value in group.get("avoid_cues", [])])
        return self._dedupe_keep_order(constraints)

    def build_prompt_sections(
        self,
        rooms: list[dict],
        furniture: list[dict],
        labels: list[dict],
        style: dict,
        negative_constraints: list[str],
    ) -> dict:
        return {
            "system_intent": "Create a watercolor-style illustrated floorplan from the validated layout plan. Preserve the exact structure, room geometry, doors, windows, and labels. Use English labels only.",
            "layout_constraints": [
                "Preserve the exact floorplan structure.",
                "Do not change wall positions.",
                "Do not move room boundaries, doors, windows, or balcony.",
            ],
            "room_instructions": [room["render_instruction"] for room in rooms],
            "furniture_instructions": [item["render_instruction"] for item in furniture if item["render_action"] == "draw"],
            "label_instructions": [item["render_instruction"] for item in labels],
            "style_instructions": [
                f"Use {style.get('style_name', 'soft watercolor')} style.",
                f"Use {style.get('watercolor_strength', 'soft')} watercolor strength.",
                f"Use {style.get('linework_style', 'clean lines')} linework.",
            ],
            "negative_constraints": negative_constraints,
        }

    def write_render_plan(self, run_id: str, artifact: RenderPlanArtifact) -> None:
        path = self._artifacts_dir(run_id) / "render_plan.json"
        try:
            with path.open("w", encoding="utf-8") as output_file:
                json.dump(artifact.model_dump(mode="json"), output_file, ensure_ascii=False, indent=2)
        except OSError as exc:
            raise HTTPException(status_code=500, detail="failed to write render_plan artifact") from exc

    def build_metadata_updates(self, metadata: RunMetadata, artifact: RenderPlanArtifact) -> dict:
        readiness = artifact.render_readiness
        return {
            "status": "render_plan_created",
            "run_status": "render_plan_created",
            "processing": metadata.processing.model_copy(
                update={
                    "layout_initial_creation": True,
                    "layout_validation": True,
                    "furniture_placement_planning": True,
                    "furniture_placement_validation": True,
                    "render_plan_creation": True,
                    "image_generation": False,
                    "watercolor_rendering": False,
                }
            ),
            "pipeline": {
                "current_phase": "phase_5a_render_plan_creation",
                "next_phase": "phase_5b_prompt_package_creation",
            },
            "render_plan_path": self._relative_artifact_path(metadata.run_id, "render_plan.json"),
            "render_plan_summary": RenderPlanSummary(
                render_plan_status=artifact.render_plan_status,
                ready_for_prompt_building=readiness.ready_for_prompt_building,
                ready_for_image_generation=readiness.ready_for_image_generation,
                auto_placed_furniture_count=readiness.auto_placed_furniture_count,
                unplaced_furniture_count=readiness.unplaced_furniture_count,
                label_count=len(artifact.labels),
                room_count=len(artifact.rooms),
                needs_human_review=readiness.requires_human_review,
                warnings_count=len(artifact.warnings),
                errors_count=len(artifact.errors),
            ),
        }

    def _style_floor_tone(
        self,
        layout: FurniturePlacementValidationArtifact,
        interior: InteriorAnalysisValidatedArtifact | None,
    ) -> str:
        tone = layout.style.floor_tone
        if tone not in self.ALLOWED_FLOOR_TONES or tone == "unknown":
            if interior is not None:
                tone = str(interior.recommendations_for_next_phase.get("suggested_floor_tone") or interior.interior_summary.get("floor_tone") or "light_brown")
            else:
                tone = "light_brown"
        return tone if tone in self.ALLOWED_FLOOR_TONES else "light_brown"

    @staticmethod
    def _furniture_instruction(furniture_type: str, base_color: str | None, action: str) -> str:
        if action == "skip_until_manual_placement":
            return "Skip drawing this furniture until manual placement is reviewed."
        if action == "do_not_draw":
            return "Do not draw this invalid furniture record."
        instruction_map = {
            "two_single_beds": "Draw two single beds with white bedding and soft watercolor shading inside the given bbox.",
            "bed": "Draw a bed with white base bedding and soft watercolor shading inside the given bbox.",
            "pillow": "Draw pillows as subtle bedding details only if the associated bed is drawn.",
            "blanket": "Draw a blanket as subtle bedding detail only if the associated bed is drawn.",
            "kitchen_counter": "Draw a kitchen counter with clean thin linework and soft watercolor shading.",
            "sink": "Draw a compact kitchen sink integrated with the kitchen counter.",
            "stove": "Draw a compact stove integrated with the kitchen counter.",
            "cabinet": "Draw kitchen cabinet storage with soft watercolor shading.",
            "bathtub": "Draw a bathtub fixture with soft watercolor shading inside the bath room.",
            "shower": "Draw a shower/bath detail subtly inside the bath room.",
            "towel": "Draw a towel detail only if it does not clutter the bath room.",
        }
        if furniture_type in instruction_map:
            return instruction_map[furniture_type]
        color_phrase = f"{base_color.replace('_', ' ')}-base " if base_color else ""
        readable_type = furniture_type.replace("_", " ")
        return f"Draw a {color_phrase}{readable_type} with soft watercolor shading inside the given bbox."

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
