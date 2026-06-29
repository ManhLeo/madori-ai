from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException

from app.schemas.run import PromptPackageArtifact, PromptPackageSummary, PromptQualitySummary, RenderPlanArtifact, RunMetadata


class PromptPackageService:
    ALLOWED_LABELS = [
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
    ]
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
        "Do not draw skipped or unplaced furniture.",
        "Do not create a generic interior unrelated to the validated layout.",
        "Furniture arrangement guidance: In the Living Room, if sofa, TV, and coffee table are present, arrange the sofa and TV facing each other with the coffee table between them. Keep this arrangement compact and inside the Living Room. This is a soft furniture-layout rule only. Do not change the apartment layout, wall geometry, door/window positions, or room proportions to satisfy it. If there is not enough space, simplify the furniture instead.",
        "Do not turn both western-style rooms into bedrooms.",
        "Keep the dining table in the main living/dining area.",
        "Keep sofa and TV in the assigned western-style media/lounge room when that role is present.",
        "Keep the other assigned western-style room as the bedroom.",
        "Do not render walls, partitions, wet-area blocks, or dividers as large black or dark charcoal filled masses.",
        "Do not place the washing machine outside the Wash Room at the Wash / 洗 mark.",
    ]

    def __init__(self, storage_dir: Path, storage_runs_dir: Path) -> None:
        self.storage_dir = storage_dir
        self.storage_runs_dir = storage_runs_dir

    def create_prompt_package(self, metadata: RunMetadata) -> PromptPackageArtifact:
        render_plan = self.load_render_plan(metadata.run_id)
        artifact = self.build_prompt_package(metadata.run_id, render_plan, metadata)
        self.write_prompt_package(metadata.run_id, artifact)
        return artifact

    def load_render_plan(self, run_id: str) -> RenderPlanArtifact:
        path = self._artifacts_dir(run_id) / "render_plan.json"
        if not path.exists():
            raise HTTPException(status_code=400, detail="Run render plan creation before prompt package creation")
        try:
            return RenderPlanArtifact.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"invalid render_plan.json: {exc}") from exc

    def load_prompt_package(self, run_id: str) -> PromptPackageArtifact:
        path = self._artifacts_dir(run_id) / "prompt_package.json"
        if not path.exists():
            raise HTTPException(status_code=404, detail="prompt_package artifact not found")
        try:
            return PromptPackageArtifact.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=500, detail="failed to read prompt_package artifact") from exc

    def build_prompt_package(self, run_id: str, render_plan: RenderPlanArtifact, metadata: RunMetadata) -> PromptPackageArtifact:
        warnings = list(render_plan.warnings)
        errors = list(render_plan.errors)

        system_prompt = self.build_system_prompt(render_plan)
        primary_generation_prompt = self.build_primary_generation_prompt(render_plan)
        structure_lock_prompt = self.build_structure_lock_prompt(render_plan)
        room_prompt = self.build_room_prompt(render_plan)
        furniture_prompt = self.build_furniture_prompt(render_plan)
        label_prompt = self.build_label_prompt(render_plan)
        style_prompt = self.build_style_prompt(render_plan)
        negative_prompt = self.build_negative_prompt(render_plan)
        combined_prompt = self.build_combined_prompt(
            system_prompt=system_prompt,
            primary_generation_prompt=primary_generation_prompt,
            structure_lock_prompt=structure_lock_prompt,
            room_prompt=room_prompt,
            furniture_prompt=furniture_prompt,
            label_prompt=label_prompt,
            style_prompt=style_prompt,
            negative_prompt=negative_prompt,
        )

        prompts = {
            "system_prompt": system_prompt,
            "primary_generation_prompt": primary_generation_prompt,
            "structure_lock_prompt": structure_lock_prompt,
            "room_prompt": room_prompt,
            "furniture_prompt": furniture_prompt,
            "label_prompt": label_prompt,
            "style_prompt": style_prompt,
            "negative_prompt": negative_prompt,
            "combined_prompt": combined_prompt,
        }

        if not system_prompt.strip():
            warnings.append("system_prompt is empty.")
        if not structure_lock_prompt.strip():
            warnings.append("structure_lock_prompt is empty.")
        if not room_prompt.strip():
            warnings.append("room_prompt is empty.")
        if not furniture_prompt.strip():
            warnings.append("furniture_prompt is empty.")
        if not label_prompt.strip():
            warnings.append("label_prompt is empty.")
        if not style_prompt.strip():
            warnings.append("style_prompt is empty.")
        if not negative_prompt.strip():
            warnings.append("negative_prompt is empty.")
        if not combined_prompt.strip():
            errors.append("combined_prompt is empty.")

        reference_manifest = self.build_reference_manifest(run_id, render_plan, metadata, warnings)
        prompt_quality = self.build_prompt_quality(render_plan, prompts)
        provider_readiness = {
            "ready_for_openai_image_api": False,
            "ready_for_manual_review": True,
            "image_generation_done": False,
            "watercolor_rendering_done": False,
            "requires_human_review_before_generation": True,
        }
        prompt_constraints = {
            "must_preserve_structure": True,
            "must_use_english_labels": True,
            "must_not_render_unplaced_furniture": True,
            "must_use_watercolor_style": True,
            "must_keep_canvas_1200": True,
            "must_not_turn_both_western_rooms_into_bedrooms": True,
        }

        prompt_package_status = "created"
        if errors:
            prompt_package_status = "failed"
        elif warnings or prompt_quality.skipped_furniture_count > 0 or provider_readiness["requires_human_review_before_generation"]:
            prompt_package_status = "created_with_warnings"

        return PromptPackageArtifact(
            run_id=run_id,
            generated_at=datetime.now(timezone.utc),
            prompt_package_status=prompt_package_status,
            source={
                "render_plan_artifact": self._relative_artifact_path(run_id, "render_plan.json"),
                "render_plan_preview_url": f"/{self._relative_artifact_path(run_id, 'render_plan.json')}",
                "normalized_floorplan_preview_url": render_plan.source.get("normalized_floorplan_preview_url"),
            },
            target_output={
                "width": int(render_plan.canvas.get("width") or 1200),
                "height": int(render_plan.canvas.get("height") or 1200),
                "format_candidates": list(render_plan.canvas.get("output_format_candidates") or ["png", "jpeg"]),
                "style": "watercolor_floorplan_illustration",
                "language": "english",
            },
            provider_readiness=provider_readiness,
            prompts=prompts,
            reference_manifest=reference_manifest,
            prompt_constraints=prompt_constraints,
            prompt_quality=prompt_quality,
            warnings=self._dedupe_keep_order(warnings),
            errors=self._dedupe_keep_order(errors),
        )

    def build_system_prompt(self, render_plan: RenderPlanArtifact) -> str:
        return (
            "Create a watercolor-style illustrated floorplan. "
            "Preserve exact room geometry, wall positions, doors, windows, and balcony. "
            "Use English labels only. Follow the validated render plan. "
            "Do not invent or rearrange structure. "
            "Use a bright, airy Japanese watercolor floorplan style with light warm beige, soft greige, pale wood, and neutral tones. "
            "Use light neutral wall tones with thin outlines, not heavy dark wall fills."
        )

    def build_primary_generation_prompt(self, render_plan: RenderPlanArtifact) -> str:
        room_summary = ", ".join(
            f"{room.get('label') or room.get('type') or 'Room'} ({room.get('type') or 'room'}) at {self._bbox_to_text(room.get('bbox'))}"
            for room in render_plan.rooms
        )
        drawable_furniture = [item for item in render_plan.furniture if item.get("render_action") == "draw"]
        furniture_summary = ", ".join(
            f"{item.get('type') or 'furniture'} in {item.get('room_type') or item.get('functional_role') or 'room'} at {self._bbox_to_text(item.get('bbox'))}"
            for item in drawable_furniture
        ) or "No drawable furniture."
        label_summary = ", ".join(label.get("render_text") or "English label" for label in render_plan.labels) or "No labels."
        style_summary = (
            f"Use {render_plan.style.get('style_name') or 'watercolor'} with "
            f"{render_plan.style.get('watercolor_strength') or 'soft'} washes and "
            f"{render_plan.style.get('linework_style') or 'clean linework'}."
        )
        return (
            f"Create a 1200x1200 watercolor floorplan illustration using the normalized floorplan as a strict structure reference. "
            f"Preserve the exact structure and room geometry. "
            f"Room rendering summary: {room_summary}. "
            f"Drawable furniture summary: {furniture_summary}. "
            f"English label summary: {label_summary}. "
            f"Style summary: {style_summary} "
            "Treat pillow and blanket as bedding details rather than large standalone furniture. "
            "Treat sink, stove, and cabinet as compact kitchen details that may integrate with the kitchen counter. "
            "Treat shower and towel as compact bath details rather than large standalone furniture. "
            "Avoid over-cluttering small rooms. "
            "Orient furniture naturally according to room geometry. "
            "TV should face the sofa. Coffee table should sit between sofa and TV when possible. "
            "Beds should align naturally to room walls with headboards against a wall. "
            "Dining table and chairs should align neatly and should not block circulation. "
            "The washing machine must be placed in the Wash Room at the location marked Wash / 洗 and nowhere else."
        )

    def build_structure_lock_prompt(self, render_plan: RenderPlanArtifact) -> str:
        return "\n".join(
            [
                "Do not change the floorplan structure.",
                "Do not move walls.",
                "Do not move doors.",
                "Do not move windows.",
                "Do not change room proportions.",
                "Do not add or remove rooms.",
                "Preserve source layout exactly.",
                "Keep wet areas and fixture locations in the same rooms.",
                "Keep the washing machine in the Wash Room at the Wash / 洗 location.",
            ]
        )

    def build_room_prompt(self, render_plan: RenderPlanArtifact) -> str:
        lines = []
        for room in render_plan.rooms:
            lines.append(
                f"- {room.get('id') or 'room'}: type={room.get('type') or 'room'}, "
                f"label={room.get('label') or 'Room'}, functional_role={room.get('functional_role') or 'unspecified'}, bbox={self._bbox_to_text(room.get('bbox'))}, "
                f"floor_tone={room.get('floor_tone') or 'unspecified'}"
            )
        return "\n".join(lines)

    def build_furniture_prompt(self, render_plan: RenderPlanArtifact) -> str:
        lines = []
        for item in render_plan.furniture:
            if item.get("render_action") != "draw":
                continue
            base_color = item.get("base_color")
            if str(item.get("type", "")).startswith("sofa") or "bed" in str(item.get("type", "")):
                base_color = "white"
            accent_values = [str(value) for value in (item.get("accent_colors") or [])]
            real_accent_values = [value for value in accent_values if value != "unknown"]
            accent_colors = ", ".join(real_accent_values or accent_values) or "none"
            semantic_note = self._semantic_furniture_note(str(item.get("type", "unknown")))
            details = [
                f"- {item.get('id', 'furniture')}: type={item.get('type') or 'furniture'}",
                f"room={item.get('room_type') or item.get('functional_role') or 'room'}",
                f"functional_role={item.get('room_functional_role') or item.get('functional_role') or 'unspecified'}",
                f"bbox={self._bbox_to_text(item.get('bbox'))}",
            ]
            if base_color and str(base_color).strip().lower() != "unknown":
                details.append(f"base_color={base_color}")
            observed_color = item.get("observed_color")
            if observed_color and str(observed_color).strip().lower() != "unknown":
                details.append(f"observed_color={observed_color}")
            if accent_colors and accent_colors != "none":
                details.append(f"accent_colors={accent_colors}")
            details.append(f"semantic_note={semantic_note}")
            details.append(f"instruction={item.get('render_instruction', '')}")
            lines.append(
                ", ".join(details)
            )
        if lines:
            lines.append("- guidance: avoid over-cluttering small rooms; keep semantic detail objects subtle and integrated.")
            lines.append("- role guidance: dining table belongs in the main living/dining area, not in the bedroom.")
            lines.append("- role guidance: when a media_lounge room exists, place sofa, TV, TV stand, and coffee table there instead of treating both western-style rooms as bedrooms.")
            lines.append("- orientation guidance: orient furniture naturally; TV faces sofa, coffee table between them when possible, beds align to walls, dining furniture aligns neatly.")
            lines.append("- wash guidance: washing machine belongs only in Wash Room at the Wash / 洗 mark.")
        return "\n".join(lines)

    def build_label_prompt(self, render_plan: RenderPlanArtifact) -> str:
        allowed = ", ".join(self.ALLOWED_LABELS)
        lines = [f"Use English labels only. Allowed labels: {allowed}."]
        for item in render_plan.labels:
            lines.append(
                f"- {item.get('id', 'unknown')}: text={item.get('render_text', 'Unknown')}, room_id={item.get('room_id') or 'unknown'}"
            )
        return "\n".join(lines)

    def build_style_prompt(self, render_plan: RenderPlanArtifact) -> str:
        style = render_plan.style or {}
        palette = style.get("palette") or {}
        positive = "; ".join(style.get("positive_cues") or [])
        acceptable = "; ".join(style.get("acceptable_cues") or [])
        avoid = "; ".join(style.get("avoid_cues") or [])
        return (
            "Use a bright, airy Japanese watercolor floorplan style with translucent washes and clean thin linework. "
            "Prefer light warm beige, soft greige, pale wood, and neutral tones. "
            "Avoid heavy dark color masses. "
            "Do not render walls, room dividers, wet-area blocks, or partitions as large black or dark charcoal filled areas. Use light neutral wall tones with thin outlines where needed. "
            f"Floor tone: {palette.get('floor_tone') or 'unspecified'}. "
            "Avoid flat digital fills, oversaturated colors, and cartoon-like furniture. "
            f"Positive cues: {positive or 'none'}. "
            f"Acceptable cues: {acceptable or 'none'}. "
            f"Avoid cues: {avoid or 'none'}."
        )

    def build_negative_prompt(self, render_plan: RenderPlanArtifact) -> str:
        negative_constraints = [str(item) for item in (render_plan.prompt_sections.get("negative_constraints") or [])]
        normalized_constraints = [
            "Do not create a generic interior unrelated to the validated layout."
            if value == "Do not create a generic interior unrelated to the provided layout."
            else value
            for value in negative_constraints
        ]
        return "\n".join(self._dedupe_keep_order(self.ALWAYS_NEGATIVE_CONSTRAINTS + normalized_constraints))

    @staticmethod
    def _semantic_furniture_note(furniture_type: str) -> str:
        if furniture_type in {"pillow", "blanket"}:
            return "bedding detail, not independent large furniture"
        if furniture_type in {"sink", "stove", "cabinet"}:
            return "compact kitchen detail that may integrate with the kitchen counter"
        if furniture_type in {"shower", "towel"}:
            return "compact bath detail, not standalone large furniture"
        return "standard furniture object"

    def build_combined_prompt(self, **sections: str) -> str:
        ordered_keys = [
            ("System Prompt", sections["system_prompt"]),
            ("Primary Generation Prompt", sections["primary_generation_prompt"]),
            ("Structure Lock Prompt", sections["structure_lock_prompt"]),
            ("Room Prompt", sections["room_prompt"]),
            ("Furniture Prompt", sections["furniture_prompt"]),
            ("Label Prompt", sections["label_prompt"]),
            ("Style Prompt", sections["style_prompt"]),
            ("Negative Prompt", sections["negative_prompt"]),
        ]
        return "\n\n".join(f"## {title}\n{content}".strip() for title, content in ordered_keys if content.strip())

    def build_reference_manifest(
        self,
        run_id: str,
        render_plan: RenderPlanArtifact,
        metadata: RunMetadata,
        warnings: list[str],
    ) -> dict:
        inputs = metadata.inputs
        if inputs is None:
            warnings.append("Run metadata inputs are missing; reference_manifest may be incomplete.")
        style_references = {"ideal": [], "acceptable": [], "ng": []}
        interior_photos = []
        if inputs is not None:
            for group_name in ("ideal", "acceptable", "ng"):
                group = getattr(inputs.style_references, group_name)
                style_references[group_name] = [item.model_dump(mode="json") for item in group]
            interior_photos = [item.model_dump(mode="json") for item in inputs.interior_photos]
        if not any(style_references.values()):
            warnings.append("No style references were available for prompt reference manifest.")
        if not interior_photos:
            warnings.append("No interior photos were available for prompt reference manifest.")
        return {
            "normalized_floorplan": {
                "preview_url": render_plan.source.get("normalized_floorplan_preview_url"),
                "relative_path": self._relative_artifact_path(run_id, "normalized_floorplan.png"),
            },
            "style_references": style_references,
            "interior_photos": interior_photos,
        }

    def build_prompt_quality(self, render_plan: RenderPlanArtifact, prompts: dict[str, str]) -> PromptQualitySummary:
        drawable_furniture_count = sum(1 for item in render_plan.furniture if item.get("render_action") == "draw")
        skipped_furniture_count = sum(1 for item in render_plan.furniture if item.get("render_action") in {"skip_until_manual_placement", "do_not_draw"})
        return PromptQualitySummary(
            has_system_prompt=bool(prompts["system_prompt"].strip()),
            has_structure_lock_prompt=bool(prompts["structure_lock_prompt"].strip()),
            has_room_prompt=bool(prompts["room_prompt"].strip()),
            has_furniture_prompt=bool(prompts["furniture_prompt"].strip()),
            has_label_prompt=bool(prompts["label_prompt"].strip()),
            has_style_prompt=bool(prompts["style_prompt"].strip()),
            has_negative_prompt=bool(prompts["negative_prompt"].strip()),
            combined_prompt_char_count=len(prompts["combined_prompt"]),
            negative_prompt_char_count=len(prompts["negative_prompt"]),
            drawable_furniture_count=drawable_furniture_count,
            skipped_furniture_count=skipped_furniture_count,
            label_count=len(render_plan.labels),
            room_count=len(render_plan.rooms),
        )

    def write_prompt_package(self, run_id: str, artifact: PromptPackageArtifact) -> None:
        path = self._artifacts_dir(run_id) / "prompt_package.json"
        try:
            with path.open("w", encoding="utf-8") as output_file:
                json.dump(artifact.model_dump(mode="json"), output_file, ensure_ascii=False, indent=2)
        except OSError as exc:
            raise HTTPException(status_code=500, detail="failed to write prompt_package artifact") from exc

    def build_metadata_updates(self, metadata: RunMetadata, artifact: PromptPackageArtifact) -> dict:
        quality = artifact.prompt_quality
        provider_readiness = artifact.provider_readiness
        return {
            "status": "prompt_package_created",
            "run_status": "prompt_package_created",
            "processing": metadata.processing.model_copy(
                update={
                    "render_plan_creation": True,
                    "prompt_package_creation": True,
                    "image_generation": False,
                    "watercolor_rendering": False,
                }
            ),
            "pipeline": {
                "current_phase": "phase_5b_prompt_package_creation",
                "next_phase": "phase_5c_image_generation_draft",
            },
            "prompt_package_path": self._relative_artifact_path(metadata.run_id, "prompt_package.json"),
            "prompt_package_summary": PromptPackageSummary(
                prompt_package_status=artifact.prompt_package_status,
                ready_for_openai_image_api=bool(provider_readiness.get("ready_for_openai_image_api")),
                ready_for_manual_review=bool(provider_readiness.get("ready_for_manual_review")),
                combined_prompt_char_count=quality.combined_prompt_char_count,
                negative_prompt_char_count=quality.negative_prompt_char_count,
                drawable_furniture_count=quality.drawable_furniture_count,
                skipped_furniture_count=quality.skipped_furniture_count,
                room_count=quality.room_count,
                label_count=quality.label_count,
                needs_human_review=bool(provider_readiness.get("requires_human_review_before_generation", True)),
                warnings_count=len(artifact.warnings),
                errors_count=len(artifact.errors),
            ),
        }

    @staticmethod
    def _bbox_to_text(bbox: dict | None) -> str:
        if not bbox:
            return "unplaced"
        return f"({bbox.get('x_min')}, {bbox.get('y_min')})-({bbox.get('x_max')}, {bbox.get('y_max')})"

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
