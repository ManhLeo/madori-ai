from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException

from app.config import get_settings
from app.schemas.run import (
    ImageGenerationRequestPreviewArtifact,
    ImageGenerationRequestPreviewQualitySummary,
    ImageGenerationRequestPreviewSummary,
    InteriorAnalysisValidatedArtifact,
    PromptPackageArtifact,
    PromptPackageSummary,
    RenderPlanArtifact,
    RunMetadata,
)


class ImageGenerationRequestPreviewService:
    def __init__(self, storage_dir: Path, storage_runs_dir: Path) -> None:
        self.storage_dir = storage_dir
        self.storage_runs_dir = storage_runs_dir
        self.settings = get_settings()

    def create_image_generation_request_preview(self, metadata: RunMetadata) -> ImageGenerationRequestPreviewArtifact:
        prompt_package = self.load_prompt_package(metadata.run_id)
        render_plan = self.load_render_plan(metadata.run_id)
        interior_analysis_validated = self.load_interior_analysis_validated(metadata.run_id)
        interior_analysis_source = self.load_interior_analysis_source(metadata.run_id, interior_analysis_validated)
        artifact = self.build_request_preview(metadata.run_id, prompt_package, render_plan, metadata, interior_analysis_validated, interior_analysis_source)
        self.write_openai_reference_selection(metadata.run_id, artifact)
        self.write_image_generation_request_preview(metadata.run_id, artifact)
        return artifact

    def load_prompt_package(self, run_id: str) -> PromptPackageArtifact:
        path = self._artifacts_dir(run_id) / "prompt_package.json"
        if not path.exists():
            raise HTTPException(status_code=400, detail="Run prompt package creation before image generation request preview")
        try:
            return PromptPackageArtifact.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"invalid prompt_package.json: {exc}") from exc

    def load_render_plan(self, run_id: str) -> RenderPlanArtifact | None:
        path = self._artifacts_dir(run_id) / "render_plan.json"
        if not path.exists():
            return None
        try:
            return RenderPlanArtifact.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def load_image_generation_request_preview(self, run_id: str) -> ImageGenerationRequestPreviewArtifact:
        path = self._artifacts_dir(run_id) / "image_generation_request_preview.json"
        if not path.exists():
            raise HTTPException(status_code=404, detail="image_generation_request_preview artifact not found")
        try:
            return ImageGenerationRequestPreviewArtifact.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=500, detail="failed to read image_generation_request_preview artifact") from exc

    def load_interior_analysis_validated(self, run_id: str) -> InteriorAnalysisValidatedArtifact | None:
        path = self._artifacts_dir(run_id) / "interior_analysis_validated.json"
        if not path.exists():
            return None
        try:
            return InteriorAnalysisValidatedArtifact.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def load_interior_analysis_source(self, run_id: str, interior_analysis_validated: InteriorAnalysisValidatedArtifact | None) -> dict | None:
        candidate_paths: list[Path] = [self._artifacts_dir(run_id) / "interior_analysis.json"]
        if interior_analysis_validated is not None:
            source_info = getattr(interior_analysis_validated, "source", None)
            if isinstance(source_info, dict):
                source_path = source_info.get("interior_analysis_artifact")
                if source_path:
                    source_path_str = str(source_path)
                    if source_path_str.startswith("storage/"):
                        candidate_paths.insert(0, (self.storage_dir.parent.resolve() / source_path_str))
                    else:
                        candidate_paths.insert(0, self._artifacts_dir(run_id) / source_path_str)
        for path in candidate_paths:
            if not path.exists():
                continue
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
        return None

    def build_reference_inputs(self, run_id: str, metadata: RunMetadata, prompt_package: PromptPackageArtifact, render_plan: RenderPlanArtifact | None) -> dict:
        normalized_floorplan = {
            "role": "structure_reference",
            "relative_path": "artifacts/normalized_floorplan.png",
            "preview_url": prompt_package.reference_manifest.get("normalized_floorplan", {}).get("preview_url") if prompt_package.reference_manifest else None,
            "required": True,
        }
        style_references = {"ideal": [], "acceptable": [], "ng": []}
        interior_photos: list[dict] = []
        if prompt_package.reference_manifest:
            style_references = prompt_package.reference_manifest.get("style_references") or style_references
            interior_photos = prompt_package.reference_manifest.get("interior_photos") or []

        if not any(style_references.values()):
            style_references = self._build_style_references_from_metadata(metadata)
        if not interior_photos:
            interior_photos = self._build_interior_photos_from_metadata(metadata)

        if render_plan is not None and not normalized_floorplan.get("preview_url"):
            normalized_floorplan["preview_url"] = render_plan.source.get("normalized_floorplan_preview_url")
        if not normalized_floorplan.get("preview_url"):
            normalized_floorplan["preview_url"] = f"/{self._relative_artifact_path(run_id, 'normalized_floorplan.png')}"

        return {
            "normalized_floorplan": normalized_floorplan,
            "style_references": style_references,
            "interior_photos": interior_photos,
        }

    def build_request_preview(
        self,
        run_id: str,
        prompt_package: PromptPackageArtifact,
        render_plan: RenderPlanArtifact | None,
        metadata: RunMetadata,
        interior_analysis_validated: InteriorAnalysisValidatedArtifact | None,
        interior_analysis_source: dict | None,
    ) -> ImageGenerationRequestPreviewArtifact:
        upstream_warnings = list(prompt_package.warnings)
        preview_warnings: list[str] = []
        errors = list(prompt_package.errors)
        prompt_mode = self._get_prompt_mode()
        strict_layout_test_enabled = prompt_mode in {"strict_layout_test", "strict_layout_with_interior_guidance"}

        reference_inputs = self.build_reference_inputs(run_id, metadata, prompt_package, render_plan)
        selection = self.select_openai_reference_images(run_id, reference_inputs, interior_analysis_validated, interior_analysis_source, prompt_mode)
        if render_plan is not None:
            selection["room_function_guidance"] = self._build_room_function_guidance(render_plan)
        selected_reference_images = [dict(item) for item in selection["selected_images"]]
        if strict_layout_test_enabled:
            self._ensure_strict_structure_reference_available(run_id, reference_inputs)
        prompt = self._build_preview_prompt(prompt_package, prompt_mode, selection)
        input_images = [dict(item) for item in selected_reference_images]
        provider_requested_size = self.settings.openai_image_provider_size or self.settings.openai_image_output_size
        final_delivery_size = self.settings.openai_image_final_output_size or self.settings.openai_image_output_size
        provider_size_supported = self._is_supported_provider_size(provider_requested_size)
        request_payload_preview = {
            "model": self.settings.openai_image_model,
            "prompt": prompt,
            "size": provider_requested_size,
            "provider_size_supported": provider_size_supported,
            "quality": "auto",
            "input_images": input_images,
            "selected_reference_images": selected_reference_images,
            "prompt_mode": prompt_mode,
            "strict_layout_test_enabled": strict_layout_test_enabled,
            "primary_structure_reference": "normalized_floorplan.png",
            "layout_preservation_priority": "maximum" if strict_layout_test_enabled else "high",
            "long_prompt_disabled": strict_layout_test_enabled,
        }
        quality = ImageGenerationRequestPreviewQualitySummary(
            has_prompt=bool(prompt.strip()),
            has_normalized_floorplan=bool(reference_inputs["normalized_floorplan"].get("preview_url")),
            has_style_reference=any(reference_inputs["style_references"].values()),
            has_interior_photos=bool(
                any(image.get("role") == "interior_photo" for image in selected_reference_images)
            ),
            prompt_char_count=len(prompt),
            input_image_count=len(selected_reference_images),
            ready_for_generation_after_manual_approval=False,
        )

        provider = {
            "provider_name": "openai",
            "model": self.settings.openai_image_model,
            "request_will_be_sent": False,
            "api_call_performed": False,
        }
        source = {
            "prompt_package_artifact": self._relative_artifact_path(run_id, "prompt_package.json"),
            "render_plan_artifact": self._relative_artifact_path(run_id, "render_plan.json") if render_plan is not None else None,
            "normalized_floorplan_preview_url": reference_inputs["normalized_floorplan"].get("preview_url"),
            "prompt_mode": prompt_mode,
            "strict_layout_test_enabled": strict_layout_test_enabled,
            "primary_structure_reference": "normalized_floorplan.png",
            "layout_preservation_priority": "maximum" if strict_layout_test_enabled else "high",
            "long_prompt_disabled": strict_layout_test_enabled,
            "interior_photos_used": any(image.get("role") == "interior_photo" for image in selected_reference_images),
        }
        target_output = {
            "width": self.settings.output_width,
            "height": self.settings.output_height,
            "format": "png",
            "style": "watercolor_floorplan_illustration",
            "final_delivery_size": final_delivery_size,
        }
        postprocess_plan = {
            "required": True,
            "mode": "resize_or_pad_after_provider_generation",
            "provider_requested_size": provider_requested_size,
            "final_delivery_size": final_delivery_size,
            "resize_mode": "contain",
            "background": "white",
        }
        provider_size_policy = {
            "provider_requested_size": provider_requested_size,
            "final_delivery_size": final_delivery_size,
            "provider_size_supported": provider_size_supported,
            "sizes_are_separated": provider_requested_size != final_delivery_size,
            "output_size_backward_compatibility": self.settings.openai_image_output_size,
        }
        safety_and_cost_controls = {
            "requires_manual_approval": True,
            "dry_run_only": self.settings.openai_image_dry_run,
            "max_images": self.settings.openai_image_max_images,
            "allow_provider_call": False,
            "estimated_external_cost": "not_calculated",
        }

        if not metadata.prompt_package_summary or not metadata.prompt_package_summary.ready_for_openai_image_api:
            preview_warnings.append("prompt_package is not ready for openai image api; preview only.")
        if not reference_inputs["normalized_floorplan"].get("preview_url"):
            preview_warnings.append("normalized_floorplan preview is missing.")
        if not any(reference_inputs["style_references"].values()):
            preview_warnings.append("No style reference images were available for the preview.")
        if not reference_inputs["interior_photos"]:
            preview_warnings.append("No interior photos were available for the preview.")
        if strict_layout_test_enabled:
            if prompt_mode == "strict_layout_test":
                preview_warnings.append("strict_layout_test mode disables the long combined prompt and prioritizes structure preservation.")
            else:
                preview_warnings.append("strict_layout_with_interior_guidance uses compact layout-lock prompt with selected interior references.")
        if prompt_mode == "strict_layout_with_interior_guidance":
            if not selection["selected_interior_filenames"]:
                preview_warnings.append("No interior photos were selected for interior guidance mode.")
        if self.settings.openai_api_key is None:
            preview_warnings.append("OPENAI_API_KEY is not required for preview phase.")
        if len(selected_reference_images) > self.settings.openai_image_max_input_images:
            preview_warnings.append(
                f"input_image_count ({len(selected_reference_images)}) exceeds OPENAI_IMAGE_MAX_INPUT_IMAGES ({self.settings.openai_image_max_input_images}); keeping all inputs for QA preview."
            )
        if not provider_size_supported:
            preview_warnings.append(
                f"provider requested size {provider_requested_size} may not be supported by the provider; manual review required."
            )

        warnings = self._dedupe_keep_order(preview_warnings + upstream_warnings)

        preview_status = "created"
        if errors:
            preview_status = "failed"
        elif warnings or not metadata.prompt_package_summary or not metadata.prompt_package_summary.ready_for_openai_image_api:
            preview_status = "created_with_warnings"

        return ImageGenerationRequestPreviewArtifact(
            run_id=run_id,
            generated_at=datetime.now(timezone.utc),
            preview_status=preview_status,
            provider=provider,
            source=source,
            target_output=target_output,
            request_payload_preview=request_payload_preview,
            postprocess_plan=postprocess_plan,
            provider_size_policy=provider_size_policy,
            reference_inputs=reference_inputs,
            safety_and_cost_controls=safety_and_cost_controls,
            request_quality=quality,
            reference_selection_path=selection["reference_selection_path"],
            selected_reference_images=selected_reference_images,
            excluded_reference_images=selection["excluded_images"],
            reference_scoring_details=selection["scoring_details"],
            interior_reference_count=selection["interior_reference_count"],
            selected_interior_filenames=selection["selected_interior_filenames"],
            interior_guidance_summary=selection["interior_guidance_summary"],
            preview_warnings=self._dedupe_keep_order(preview_warnings),
            upstream_warnings=self._dedupe_keep_order(upstream_warnings),
            warnings=warnings,
            errors=self._dedupe_keep_order(errors),
        )

    def write_openai_reference_selection(self, run_id: str, artifact: ImageGenerationRequestPreviewArtifact) -> None:
        path = self._artifacts_dir(run_id) / "openai_reference_selection.json"
        selection_artifact = {
            "schema_version": "openai_reference_selection.v1",
            "run_id": run_id,
            "generated_at": artifact.generated_at,
            "selection_mode": artifact.request_payload_preview.get("prompt_mode") or "default",
            "selected_images": artifact.selected_reference_images,
            "excluded_images": artifact.excluded_reference_images,
            "scoring_details": artifact.reference_scoring_details,
            "interior_guidance_summary": artifact.interior_guidance_summary,
            "furniture_arrangement_rules_applied": (artifact.request_payload_preview.get("prompt_mode") == "strict_layout_with_interior_guidance"),
            "living_room_arrangement_rule": (
                "sofa_tv_opposite_with_coffee_table_between_when_possible"
                if artifact.request_payload_preview.get("prompt_mode") == "strict_layout_with_interior_guidance"
                else None
            ),
            "bedroom_bed_count_rule_applied": (artifact.request_payload_preview.get("prompt_mode") == "strict_layout_with_interior_guidance"),
            "bedroom_bed_count_rule": (
                "one_or_two_beds_only" if artifact.request_payload_preview.get("prompt_mode") == "strict_layout_with_interior_guidance" else None
            ),
            "warnings": artifact.preview_warnings,
            "errors": artifact.errors,
        }
        try:
            with path.open("w", encoding="utf-8") as output_file:
                json.dump(selection_artifact, output_file, ensure_ascii=False, indent=2, default=str)
        except OSError as exc:
            raise HTTPException(status_code=500, detail="failed to write openai_reference_selection artifact") from exc

    def write_image_generation_request_preview(self, run_id: str, artifact: ImageGenerationRequestPreviewArtifact) -> None:
        path = self._artifacts_dir(run_id) / "image_generation_request_preview.json"
        try:
            with path.open("w", encoding="utf-8") as output_file:
                json.dump(artifact.model_dump(mode="json"), output_file, ensure_ascii=False, indent=2)
        except OSError as exc:
            raise HTTPException(status_code=500, detail="failed to write image_generation_request_preview artifact") from exc

    def build_metadata_updates(self, metadata: RunMetadata, artifact: ImageGenerationRequestPreviewArtifact) -> dict:
        now = datetime.now(timezone.utc)
        return {
            "status": "image_generation_request_preview_created",
            "run_status": "image_generation_request_preview_created",
            "updated_at": now,
            "processing": metadata.processing.model_copy(
                update={
                    "prompt_package_creation": True,
                    "image_generation_request_preview": True,
                    "image_generation": False,
                    "watercolor_rendering": False,
                }
            ),
            "pipeline": {
                "current_phase": "phase_5c0_image_generation_request_preview",
                "next_phase": "phase_5c1_image_generation_draft",
            },
            "image_generation_request_preview_path": self._relative_artifact_path(metadata.run_id, "image_generation_request_preview.json"),
            "image_generation_request_preview_summary": self._build_preview_summary(artifact),
        }

    def select_openai_reference_images(
        self,
        run_id: str,
        reference_inputs: dict,
        interior_analysis_validated: InteriorAnalysisValidatedArtifact | None,
        interior_analysis_source: dict | None,
        prompt_mode: str,
    ) -> dict:
        selection_mode = prompt_mode
        selected_images: list[dict] = []
        warnings: list[str] = []
        errors: list[str] = []

        normalized_floorplan = reference_inputs.get("normalized_floorplan") or {}
        if normalized_floorplan.get("preview_url"):
            selected_images.append(
                {
                    "role": "structure_reference",
                    "relative_path": normalized_floorplan.get("relative_path"),
                    "preview_url": normalized_floorplan.get("preview_url"),
                    "required": True,
                }
            )

        style_reference = self._pick_style_reference(reference_inputs.get("style_references") or {})
        if style_reference is not None:
            selected_images.append(style_reference)
        else:
            warnings.append("No positive style reference was available for selection.")

        interior_candidates = self._score_interior_reference_candidates(run_id, reference_inputs, interior_analysis_validated, interior_analysis_source)
        selected_interior = self._select_top_interior_photos(interior_candidates)
        selected_images.extend(selected_interior)
        excluded_images = [item for item in interior_candidates if item not in selected_interior]
        scoring_details = {
            "candidate_scores": [
                {
                    "filename": item.get("filename"),
                    "stored_filename": item.get("stored_filename"),
                    "original_filename": item.get("original_filename"),
                    "room_hint": item.get("room_hint"),
                    "detected_objects": item.get("detected_objects") or [],
                    "style_cues": item.get("style_cues") or [],
                    "score": item.get("score"),
                    "scoring_reasons": item.get("scoring_reasons") or [],
                    "selection_source": item.get("selection_source"),
                    "selected": bool(item.get("selected")),
                    "exclusion_reason": item.get("exclusion_reason"),
                }
                for item in interior_candidates
            ],
            "selection_rules": [
                "normalized_floorplan first",
                "one style reference if available",
                "max two interior photos",
                "prefer living_room, then bed_room, then kitchen",
            ],
        }

        if not selected_interior:
            warnings.append("No interior photos were selected for OpenAI interior guidance mode.")

        return {
            "selection_mode": selection_mode,
            "selected_images": selected_images,
            "excluded_images": excluded_images,
            "scoring_details": scoring_details,
            "warnings": self._dedupe_keep_order(warnings),
            "errors": self._dedupe_keep_order(errors),
            "reference_selection_path": self._relative_artifact_path(run_id, "openai_reference_selection.json"),
            "interior_reference_count": len(selected_interior),
            "selected_interior_filenames": [str(item.get("filename") or Path(str(item.get("relative_path") or "")).name) for item in selected_interior],
            "interior_guidance_summary": self._build_interior_guidance_summary(interior_analysis_validated, selected_interior),
            "furniture_arrangement_rules_applied": prompt_mode == "strict_layout_with_interior_guidance",
            "living_room_arrangement_rule": "sofa_tv_opposite_with_coffee_table_between_when_possible" if prompt_mode == "strict_layout_with_interior_guidance" else None,
            "bedroom_bed_count_rule_applied": prompt_mode == "strict_layout_with_interior_guidance",
            "bedroom_bed_count_rule": "one_or_two_beds_only" if prompt_mode == "strict_layout_with_interior_guidance" else None,
        }

    def _build_preview_prompt(
        self,
        prompt_package: PromptPackageArtifact,
        prompt_mode: str,
        selection: dict,
    ) -> str:
        if prompt_mode == "strict_layout_test":
            return self._build_strict_layout_test_prompt()
        if prompt_mode == "strict_layout_with_interior_guidance":
            return self._build_interior_guidance_prompt(prompt_package, selection)
        combined_prompt = prompt_package.prompts.get("combined_prompt", "")
        negative_prompt = prompt_package.prompts.get("negative_prompt", "")
        deduped_negative_prompt = self._dedupe_prompt_lines(negative_prompt)
        if not combined_prompt:
            return deduped_negative_prompt
        if negative_prompt:
            return combined_prompt.replace(negative_prompt, deduped_negative_prompt, 1)
        return combined_prompt

    def _build_interior_guidance_prompt(self, prompt_package: PromptPackageArtifact, selection: dict) -> str:
        summary = selection.get("interior_guidance_summary") or {}
        summary_text = str(summary.get("summary") or "").strip()
        furniture_arrangement_rules_applied = bool(selection.get("furniture_arrangement_rules_applied"))
        living_room_arrangement_rule = str(selection.get("living_room_arrangement_rule") or "").strip()
        bedroom_bed_count_rule_applied = bool(selection.get("bedroom_bed_count_rule_applied"))
        bedroom_bed_count_rule = str(selection.get("bedroom_bed_count_rule") or "").strip()
        floor_tone = summary.get("floor_tone") or "unknown"
        room_lines = self._build_compact_room_guidance_lines(selection.get("selected_images") or [])
        room_function_guidance = self._normalize_list(selection.get("room_function_guidance"))
        style_cues = ", ".join(self._filter_unknown(summary.get("style_cues") or [])) or "none"
        color_cues = ", ".join(self._filter_unknown(summary.get("color_cues") or [])) or "none"
        lines = [
            "Preserve the exact layout from normalized_floorplan.png.",
            "Use English labels only.",
            "Use selected interior references only for furniture type, color, and style.",
            "Simplify or omit furniture if it conflicts with the layout.",
            "Apply furniture arrangement only when it does not conflict with the floorplan.",
            "Living Room arrangement: if sofa and TV are both present and the floorplan allows, arrange them facing each other with a coffee table between them.",
            "Keep sofa, TV, and coffee table inside the Living Room.",
            "If the room is too small or the layout does not allow the arrangement, simplify or omit furniture rather than changing the floorplan.",
            "Bedroom guidance: the Bed Room must contain either one bed or two single beds only. Do not draw more than two beds. Do not draw bunk beds unless the selected bedroom reference clearly shows bunk beds. If the selected bedroom reference shows two separate beds, draw two single beds. If the selected bedroom reference is unclear, draw one simple bed. Keep bed(s) inside the Bed Room only. Do not resize the Bed Room or move walls, doors, or windows to fit beds. If there is not enough space, simplify the bed drawing rather than changing the floorplan.",
        ]
        lines.extend(room_function_guidance)
        lines.extend(
            [
                f"Interior summary: {summary_text or 'none'}.",
                f"Floor tone: {floor_tone}.",
                f"Interior room guidance: {', '.join(room_lines) or 'none'}.",
                f"Style cues: {style_cues}.",
                f"Color cues: {color_cues}.",
                f"Furniture arrangement rules applied: {str(furniture_arrangement_rules_applied).lower()}.",
                f"Living room arrangement rule: {living_room_arrangement_rule or 'none'}.",
                f"Bedroom bed count rule applied: {str(bedroom_bed_count_rule_applied).lower()}.",
                f"Bedroom bed count rule: {bedroom_bed_count_rule or 'none'}.",
                f"Short prompt source: {prompt_package.prompt_package_status}.",
            ]
        )
        return "\n".join(lines)

    def _build_room_function_guidance(self, render_plan: RenderPlanArtifact) -> list[str]:
        media_lounge = next((room for room in render_plan.rooms if str(room.get("functional_role") or "") == "media_lounge"), None)
        main_bedroom = next((room for room in render_plan.rooms if str(room.get("functional_role") or "") == "main_bedroom"), None)
        living_dining = next((room for room in render_plan.rooms if str(room.get("functional_role") or "") in {"living_dining", "dining_zone"}), None)
        lines: list[str] = []
        if media_lounge and main_bedroom:
            lines.append("Do not turn both western-style rooms into bedrooms.")
        if living_dining:
            lines.append(f"Dining table should stay in the main living/dining area: {living_dining.get('label')}.")
        if media_lounge:
            lines.append(f"Sofa, TV, TV stand, and coffee table should stay in the assigned western-style lounge room: {media_lounge.get('label')}.")
        if main_bedroom:
            lines.append(f"The other assigned western-style room should be the bedroom: {main_bedroom.get('label')}.")
        return lines

    def _build_preview_summary(self, artifact: ImageGenerationRequestPreviewArtifact) -> ImageGenerationRequestPreviewSummary:
        provider_requested_size = str(
            artifact.request_payload_preview.get("size")
            or artifact.provider_size_policy.get("provider_requested_size")
            or self.settings.openai_image_provider_size
            or self.settings.openai_image_output_size
        )
        final_delivery_size = str(
            artifact.target_output.get("final_delivery_size")
            or artifact.postprocess_plan.get("final_delivery_size")
            or self.settings.openai_image_final_output_size
            or self.settings.openai_image_output_size
        )
        return ImageGenerationRequestPreviewSummary(
            preview_status=artifact.preview_status,
            provider_name=str(artifact.provider.get("provider_name") or "openai"),
            model=str(artifact.provider.get("model") or self.settings.openai_image_model),
            request_will_be_sent=bool(artifact.provider.get("request_will_be_sent")),
            api_call_performed=bool(artifact.provider.get("api_call_performed")),
            requires_manual_approval=bool(artifact.safety_and_cost_controls.get("requires_manual_approval", True)),
            input_image_count=artifact.request_quality.input_image_count,
            prompt_char_count=artifact.request_quality.prompt_char_count,
            provider_requested_size=provider_requested_size,
            final_delivery_size=final_delivery_size,
            provider_size_supported=bool(artifact.request_payload_preview.get("provider_size_supported", True)),
            postprocess_required=bool(artifact.postprocess_plan.get("required", True)),
            preview_warnings_count=len(artifact.preview_warnings),
            upstream_warnings_count=len(artifact.upstream_warnings),
            warnings_count=len(artifact.warnings),
            errors_count=len(artifact.errors),
        )

    def _build_style_references_from_metadata(self, metadata: RunMetadata) -> dict:
        inputs = metadata.inputs
        style_groups = inputs.style_references if inputs else metadata.style_references
        return {
            "ideal": [item.model_dump(mode="json") for item in style_groups.ideal],
            "acceptable": [item.model_dump(mode="json") for item in style_groups.acceptable],
            "ng": [item.model_dump(mode="json") for item in style_groups.ng],
        }

    def _pick_style_reference(self, style_references: dict) -> dict | None:
        for group_name in ("acceptable", "ideal"):
            items = style_references.get(group_name) or []
            if items:
                item = dict(items[0])
                item["role"] = "style_reference"
                item["reference_type"] = group_name
                return item
        return None

    def _score_interior_reference_candidates(
        self,
        run_id: str,
        reference_inputs: dict,
        interior_analysis_validated: InteriorAnalysisValidatedArtifact | None,
        interior_analysis_source: dict | None,
    ) -> list[dict]:
        candidates: list[dict] = []
        interiors = list(reference_inputs.get("interior_photos") or [])
        analysis_index = self._build_interior_analysis_index(interior_analysis_validated, interior_analysis_source)
        for item in interiors:
            filename = str(item.get("filename") or Path(str(item.get("relative_path") or "")).name)
            stored_filename = str(item.get("stored_filename") or filename)
            original_filename = str(item.get("original_filename") or item.get("source_image", {}).get("original_filename") or "")
            matching_analysis = self._match_interior_analysis_record(item, analysis_index)
            room_hint, selection_source, room_analysis = self._resolve_room_hint_and_analysis(
                item,
                matching_analysis,
                filename,
                stored_filename,
                original_filename,
            )
            detected_objects = self._extract_detected_objects(room_analysis)
            style_cues = self._extract_style_cues(room_analysis)
            color_cues = self._extract_color_cues(room_analysis)
            scoring_reasons: list[str] = []
            score = 0
            room_priority = {
                "living_room": 5,
                "bed_room": 4,
                "kitchen": 3,
                "bath_room": 2,
                "dining": 2,
                "toilet": 1,
                "wash_room": 1,
            }.get(room_hint, 0)
            if room_priority:
                score += room_priority
                scoring_reasons.append(f"room_hint:{room_hint}+{room_priority}")
            else:
                score += 1
                scoring_reasons.append("room_hint:unknown+1")
            if detected_objects:
                score += len(detected_objects)
                scoring_reasons.append(f"detected_objects:+{len(detected_objects)}")
            if self._has_major_furniture(detected_objects):
                score += 2
                scoring_reasons.append("major_furniture:+2")
            if style_cues or color_cues:
                score += 2
                scoring_reasons.append("style_or_color_cues:+2")
            if self._is_decoration_only(room_hint, detected_objects, original_filename, stored_filename):
                score -= 2
                scoring_reasons.append("decoration_only:-2")

            candidates.append(
                {
                    "role": "interior_photo",
                    "relative_path": item.get("relative_path"),
                    "preview_url": item.get("preview_url"),
                    "filename": filename,
                    "original_filename": original_filename or None,
                    "stored_filename": stored_filename or None,
                    "notes": item.get("notes"),
                    "detected_objects": detected_objects,
                    "style_cues": style_cues,
                    "color_cues": color_cues,
                    "source_image": item.get("source_image") or item,
                    "room_hint": room_hint,
                    "score": score,
                    "scoring_reasons": scoring_reasons,
                    "selection_source": selection_source,
                    "selected": False,
                    "exclusion_reason": None,
                }
            )

        candidates.sort(key=lambda item: (item.get("score", 0), self._room_priority(str(item.get("room_hint") or ""))), reverse=True)
        seen_rooms: set[str] = set()
        for item in candidates:
            room_hint = str(item.get("room_hint") or "")
            if room_hint in seen_rooms and room_hint in {"living_room", "bed_room"}:
                item["score"] = max(-10, item.get("score", 0) - 3)
                item["duplicate_room_penalty"] = True
                item.setdefault("scoring_reasons", []).append("duplicate_room_penalty:-3")
            if room_hint:
                seen_rooms.add(room_hint)
        candidates.sort(key=lambda item: (item.get("score", 0), self._room_priority(str(item.get("room_hint") or ""))), reverse=True)
        return candidates

    def _select_top_interior_photos(self, candidates: list[dict]) -> list[dict]:
        selected: list[dict] = []
        chosen_rooms: set[str] = set()

        def pick(preferred_rooms: list[str]) -> bool:
            for room_name in preferred_rooms:
                for index, item in enumerate(candidates):
                    if item in selected:
                        continue
                    if str(item.get("room_hint") or "") == room_name:
                        selected.append(item)
                        chosen_rooms.add(room_name)
                        item["selected"] = True
                        item["exclusion_reason"] = None
                        return True
            return False

        pick(["living_room"])
        pick(["bed_room"])
        if len(selected) < 2:
            pick(["kitchen"])
        if len(selected) < 2:
            pick(["living_room", "dining", "bath_room", "toilet", "wash_room"])
        if len(selected) < 2:
            for item in candidates:
                if len(selected) >= 2:
                    break
                if item in selected:
                    continue
                if self._is_decoration_only(
                    str(item.get("room_hint") or ""),
                    item.get("detected_objects") or [],
                    str(item.get("original_filename") or ""),
                    str(item.get("stored_filename") or ""),
                ):
                    item["exclusion_reason"] = "decoration_only"
                    continue
                selected.append(item)
                item["selected"] = True
                if str(item.get("room_hint") or ""):
                    chosen_rooms.add(str(item.get("room_hint") or ""))

        for item in candidates:
            if item not in selected and not item.get("exclusion_reason"):
                item["exclusion_reason"] = "not_selected"

        return selected[:2]

    def _build_interior_analysis_index(self, interior_analysis_validated: InteriorAnalysisValidatedArtifact | None, interior_analysis_source: dict | None) -> dict[str, dict]:
        index = {"records": [], "by_room": {}, "by_filename": {}}
        if isinstance(interior_analysis_source, dict):
            for key in ("interior_photos", "photos", "images", "analyzed_images", "validated_images", "per_image_analysis"):
                for item in interior_analysis_source.get(key) or []:
                    if not isinstance(item, dict):
                        continue
                    normalized = self._normalize_interior_analysis_record(item)
                    index["records"].append(normalized)
                    for candidate_name in self._analysis_filenames(normalized):
                        index["by_filename"][candidate_name] = normalized
        if interior_analysis_validated is not None:
            room_observations = interior_analysis_validated.room_observations or {}
            for room_name, room_payload in room_observations.items():
                if not isinstance(room_payload, dict):
                    continue
                room_key = self._normalize_room_name(str(room_name))
                index["by_room"][room_key] = {
                    "room_context": room_key,
                    "room_name": room_key,
                    "notes": room_payload.get("notes") or [],
                    "detected_objects": room_payload.get("detected_objects") or [],
                    "dominant_colors": room_payload.get("dominant_colors") or [],
                    "floor_tone": room_payload.get("floor_tone"),
                    "style_cues": room_payload.get("style_keywords") or room_payload.get("style_cues") or [],
                    "color_cues": room_payload.get("dominant_colors") or room_payload.get("color_cues") or [],
                }
        return index

    def _normalize_interior_analysis_record(self, item: dict) -> dict:
        source_image = item.get("source_image") if isinstance(item.get("source_image"), dict) else {}
        return {
            **item,
            "source_image": source_image,
            "room_context": item.get("room_context") or item.get("room_hint") or item.get("target_room") or item.get("inferred_room"),
            "detected_objects": item.get("detected_objects") or item.get("objects") or item.get("furniture") or item.get("furniture_signals") or item.get("normalized_objects") or [],
            "style_cues": item.get("style_cues") or item.get("style_keywords") or [],
            "color_cues": item.get("color_cues") or item.get("dominant_colors") or [],
            "notes": item.get("notes") or item.get("summary") or [],
        }

    def _analysis_filenames(self, record: dict) -> list[str]:
        result: list[str] = []
        for key in ("stored_filename", "original_filename", "filename"):
            value = record.get(key)
            if value:
                result.append(Path(str(value)).name.lower())
        source_image = record.get("source_image") if isinstance(record.get("source_image"), dict) else {}
        for key in ("stored_filename", "original_filename", "filename", "relative_path", "preview_url"):
            value = source_image.get(key)
            if value:
                result.append(Path(str(value)).name.lower())
        return self._dedupe_keep_order(result)

    def _match_interior_analysis_record(self, item: dict, analysis_index: dict) -> dict:
        candidates: list[str] = []
        for key in ("stored_filename", "original_filename", "filename"):
            value = item.get(key)
            if value:
                candidates.append(Path(str(value)).name.lower())
        source_image = item.get("source_image") if isinstance(item.get("source_image"), dict) else {}
        for key in ("stored_filename", "original_filename", "filename", "relative_path", "preview_url"):
            value = source_image.get(key)
            if value:
                candidates.append(Path(str(value)).name.lower())
        for candidate_name in candidates:
            matched = analysis_index.get("by_filename", {}).get(candidate_name)
            if matched:
                return matched
        room_context = self._normalize_room_name(str(source_image.get("room_context") or item.get("room_context") or item.get("room_hint") or item.get("target_room") or item.get("inferred_room") or ""))
        return analysis_index.get("by_room", {}).get(room_context, {})

    def _resolve_room_hint_and_analysis(self, item: dict, analysis: dict, base_filename: str, stored_filename: str, original_filename: str) -> tuple[str, str, dict]:
        combined = " ".join([base_filename, stored_filename, original_filename]).lower()
        analysis_room = self._normalize_room_name(str(analysis.get("room_context") or analysis.get("room_hint") or analysis.get("room_name") or analysis.get("target_room") or analysis.get("inferred_room") or ""))
        if analysis_room != "unknown":
            return analysis_room, "validated_analysis", analysis
        filename_room = self._infer_room_hint(combined)
        if filename_room != "unknown":
            if self._infer_room_hint(original_filename.lower()) == filename_room:
                return filename_room, "original_filename_heuristic", analysis
            return filename_room, "stored_filename_heuristic", analysis
        if analysis:
            inferred = self._infer_room_hint_from_analysis(analysis)
            if inferred != "unknown":
                return inferred, "validated_analysis", analysis
        return "unknown", "upload_order_fallback", analysis

    def _infer_room_hint(self, filename: str) -> str:
        filename = filename.lower()
        if any(token in filename for token in ("livingroom", "living_room", "living", "sofa", "television", "tv")):
            return "living_room"
        if any(token in filename for token in ("bedroom", "bed_room", "2bed", "2-bed", "futon", "bed")):
            return "bed_room"
        if any(token in filename for token in ("kitchen", "kitchensink", "kitchen_sink", "sink", "stove")):
            return "kitchen"
        if any(token in filename for token in ("bathtub", "bathroom", "bath_room", "bath", "shower")):
            return "bath_room"
        if any(token in filename for token in ("toilet", "wc")):
            return "toilet"
        if any(token in filename for token in ("wash", "washbasin", "basin", "vanity")):
            return "wash_room"
        if any(token in filename for token in ("table", "dining")):
            return "dining"
        if any(token in filename for token in ("plant", "picture", "wallart", "wall_art")):
            return "decoration"
        return "unknown"

    def _infer_room_hint_from_analysis(self, analysis: dict) -> str:
        text = " ".join(
            [
                str(analysis.get("room_context") or ""),
                str(analysis.get("notes") or ""),
                " ".join(self._extract_detected_objects(analysis)),
            ]
        ).lower()
        return self._infer_room_hint(text)

    def _extract_detected_objects(self, analysis: dict) -> list[str]:
        objects = analysis.get("detected_objects") or analysis.get("objects") or analysis.get("furniture") or analysis.get("furniture_signals") or analysis.get("normalized_objects") or []
        result: list[str] = []
        for item in objects if isinstance(objects, list) else []:
            if isinstance(item, dict):
                for key in ("object_type", "notes", "name", "label", "object"):
                    value = item.get(key)
                    if value:
                        result.append(str(value).lower())
                        break
            elif item is not None:
                result.append(str(item).lower())
        return self._dedupe_keep_order(result)

    def _extract_style_cues(self, analysis: dict) -> list[str]:
        cues = []
        cues.extend(self._normalize_list(analysis.get("style_cues")))
        cues.extend(self._normalize_list(analysis.get("style_keywords")))
        cues.extend(self._normalize_list(analysis.get("summary")))
        cues.extend(self._normalize_list(analysis.get("notes")))
        return self._dedupe_keep_order(cues)

    def _extract_color_cues(self, analysis: dict) -> list[str]:
        cues = []
        cues.extend(self._normalize_list(analysis.get("color_cues")))
        cues.extend(self._normalize_list(analysis.get("dominant_colors")))
        return self._dedupe_keep_order(cues)

    def _has_major_furniture(self, detected_objects: list[str]) -> bool:
        majors = {"sofa", "bed", "kitchen_counter", "bathtub", "stove", "cabinet", "tv", "tv_stand", "table", "dining_table", "coffee_table"}
        return any(any(major in detected for major in majors) for detected in detected_objects)

    def _is_decoration_only(self, room_hint: str, detected_objects: list[str], original_filename: str, stored_filename: str) -> bool:
        if room_hint in {"living_room", "bed_room", "kitchen", "bath_room", "dining"}:
            return False
        filename = f"{original_filename} {stored_filename}".lower()
        if not detected_objects:
            return any(token in filename for token in ("plant", "picture", "wall_art", "wallart"))
        non_decor = {"sofa", "bed", "kitchen_counter", "bathtub", "stove", "cabinet", "tv", "tv_stand", "dining_table", "coffee_table", "chair", "sink", "shower", "towel", "wardrobe"}
        has_non_decor = any(any(token in obj for token in non_decor) for obj in detected_objects)
        return not has_non_decor and any(token in filename for token in ("plant", "picture", "wall_art", "wallart"))

    def _build_interior_guidance_summary(self, interior_analysis_validated: InteriorAnalysisValidatedArtifact | None, selected_interior_photos: list[dict]) -> dict:
        if interior_analysis_validated is None:
            return {
                "floor_tone": "unknown",
                "summary": "Interior guidance: The living room should include a sofa, TV/TV stand, and coffee table arranged neatly, ideally with the sofa and TV facing each other. The bedroom should only have one or two single beds, bedding colors inspired by the selected bedroom reference. Use soft, neutral colors. Furniture is secondary to maintaining the exact layout.",
                "room_summary": [],
                "style_cues": [],
                "color_cues": [],
                "selected_interior_count": len(selected_interior_photos),
            }
        selected_rooms = self._selected_room_hints(selected_interior_photos)
        style_cues: list[str] = []
        color_cues: list[str] = []
        summary = getattr(interior_analysis_validated, "interior_summary", None)
        floor_tone = str(
            getattr(interior_analysis_validated, "floor_tone", None)
            or (summary.get("floor_tone") if isinstance(summary, dict) else None)
            or "unknown"
        ).lower()
        if "living_room" in selected_rooms:
            style_cues.append("living_room_reference")
        if "bed_room" in selected_rooms:
            style_cues.append("bed_room_reference")
        if "kitchen" in selected_rooms:
            style_cues.append("kitchen_reference")
        if any(room in selected_rooms for room in {"bath_room", "toilet", "wash_room"}):
            style_cues.append("wet_area_reference")
        summary_text = "Interior guidance: Living Room should include a sofa, TV/TV stand, and coffee table arranged compactly, with sofa and TV facing each other when possible. Bed Room should contain only one bed or two single beds with light bedding. Keep Kitchen/Bath/Toilet/Wash compact and simple. Use soft neutral watercolor colors. Furniture is secondary to exact layout preservation."
        return {
            "floor_tone": floor_tone,
            "summary": summary_text,
            "room_summary": self._dedupe_keep_order(selected_rooms),
            "style_cues": self._dedupe_keep_order(self._filter_unknown(style_cues)),
            "color_cues": self._dedupe_keep_order(self._filter_unknown(color_cues)),
            "selected_interior_count": len(selected_interior_photos),
        }

    def _build_compact_room_guidance_lines(self, selected_interior_photos: list[dict]) -> list[str]:
        rooms = self._selected_room_hints(selected_interior_photos)
        lines: list[str] = []
        if "living_room" in rooms:
            lines.append("Living Room: sofa, TV/TV stand, coffee table; rug optional; curtain or plant optional.")
        if "bed_room" in rooms:
            lines.append("Bed Room: one bed or two single beds only; light bedding; curtain or wardrobe optional.")
        if "kitchen" in rooms:
            lines.append("Kitchen: keep compact with sink, stove, and counter cues only.")
        if any(room in rooms for room in {"bath_room", "toilet", "wash_room"}):
            lines.append("Bath/Toilet/Wash: keep simple and avoid over-detail unless selected references clearly show them.")
        return lines

    def _selected_room_hints(self, selected_interior_photos: list[dict]) -> list[str]:
        rooms: list[str] = []
        for item in selected_interior_photos or []:
            if not isinstance(item, dict):
                continue
            room = self._normalize_room_name(str(item.get("room_hint") or item.get("room_context") or item.get("target_room") or item.get("inferred_room") or ""))
            if room and room != "unknown":
                rooms.append(room)
        return self._dedupe_keep_order(rooms)

    @staticmethod
    def _room_priority(room_name: str) -> int:
        priorities = {
            "living_room": 5,
            "bed_room": 4,
            "kitchen": 3,
            "bath_room": 2,
            "toilet": 1,
            "wash_room": 1,
            "unknown": 0,
        }
        return priorities.get(room_name, 0)

    @staticmethod
    def _normalize_room_name(value: str) -> str:
        normalized = value.strip().lower().replace(" ", "_")
        mapping = {
            "livingroom": "living_room",
            "living_room": "living_room",
            "bedroom": "bed_room",
            "bed_room": "bed_room",
            "kitchen": "kitchen",
            "bathroom": "bath_room",
            "bath_room": "bath_room",
            "washroom": "wash_room",
            "wash_room": "wash_room",
            "toilet": "toilet",
        }
        return mapping.get(normalized, normalized)

    @staticmethod
    def _normalize_list(value) -> list[str]:
        if not isinstance(value, list):
            return []
        result: list[str] = []
        for item in value:
            if item is None:
                continue
            text = str(item).strip().lower()
            if text and text != "unknown":
                result.append(text)
        return result

    @staticmethod
    def _filter_unknown(values: list[str]) -> list[str]:
        return [str(value).strip().lower() for value in values if str(value).strip() and str(value).strip().lower() != "unknown"]

    @staticmethod
    def _safe_filename(image: dict) -> str | None:
        if not isinstance(image, dict):
            return None
        for key in ("original_filename", "stored_filename", "filename", "relative_path"):
            value = image.get(key)
            if value:
                return Path(str(value)).name
        return None

    def _build_input_images(self, reference_inputs: dict, prompt_mode: str) -> list[dict]:
        input_images: list[dict] = []
        normalized_floorplan = reference_inputs.get("normalized_floorplan") or {}
        if normalized_floorplan.get("preview_url"):
            input_images.append(
                {
                    "role": "structure_reference",
                    "relative_path": normalized_floorplan.get("relative_path"),
                    "preview_url": normalized_floorplan.get("preview_url"),
                    "required": True,
                }
            )
        return input_images

    def _build_interior_photos_from_metadata(self, metadata: RunMetadata) -> list[dict]:
        inputs = metadata.inputs
        interior_photos = inputs.interior_photos if inputs else metadata.interior_photos
        return [item.model_dump(mode="json") for item in interior_photos]

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

    @staticmethod
    def _dedupe_prompt_lines(prompt: str) -> str:
        if not prompt.strip():
            return prompt
        lines = prompt.splitlines()
        result: list[str] = []
        seen: set[str] = set()
        for line in lines:
            normalized = line.strip()
            if normalized and normalized in seen:
                continue
            if normalized:
                seen.add(normalized)
            result.append(line)
        return "\n".join(result)

    @staticmethod
    def _is_supported_provider_size(size: str) -> bool:
        return size in {"1024x1024", "1024x1536", "1536x1024", "auto"}

    def _get_prompt_mode(self) -> str:
        normalized = str(self.settings.openai_image_prompt_mode or "default").strip().lower()
        if normalized in {"default", "strict_layout_test", "strict_layout_with_interior_guidance"}:
            return normalized
        return "default"

    def _ensure_strict_structure_reference_available(self, run_id: str, reference_inputs: dict) -> None:
        normalized_floorplan = reference_inputs.get("normalized_floorplan") or {}
        relative_path = str(normalized_floorplan.get("relative_path") or "").strip()
        if not relative_path:
            raise HTTPException(status_code=400, detail="strict_layout_test requires normalized_floorplan.png as structure_reference")
        structure_path = self._resolve_reference_image_path(run_id, relative_path)
        if structure_path is None or not structure_path.exists():
            raise HTTPException(status_code=400, detail="strict_layout_test requires normalized_floorplan.png as structure_reference")

    @staticmethod
    def _build_strict_layout_test_prompt() -> str:
        return (
            "You are editing a 2D real-estate floorplan image.\n\n"
            "The provided reference image is the exact structural blueprint.\n"
            "Follow the reference image layout with maximum precision.\n\n"
            "CRITICAL LAYOUT LOCK:\n"
            "Preserve the exact floorplan geometry from the reference image.\n"
            "Do not move, resize, rotate, redraw, reinterpret, simplify, or beautify the architectural structure.\n"
            "The outer boundary, walls, room divisions, doors, windows, entrance, kitchen, bath room, wash room, toilet, closet, balcony, and living/bedroom positions must remain in the same locations and proportions as the reference image.\n\n"
            "The output must remain a flat top-down 2D floorplan.\n"
            "Do not create a perspective view.\n"
            "Do not create a 3D room view.\n"
            "Do not create a new apartment layout.\n"
            "Do not add rooms.\n"
            "Do not remove rooms.\n"
            "Do not duplicate toilet, bath, kitchen, entrance, closet, or wash room.\n"
            "Do not rearrange the kitchen/bath/toilet/wash/closet cluster.\n"
            "Do not change the shape or size of any room.\n\n"
            "TASK:\n"
            "Convert the reference floorplan into a clean Japanese watercolor-style illustrated floorplan while keeping the exact layout.\n"
            "Replace Japanese room labels with English labels only.\n"
            "Use these English labels where applicable:\n"
            "Living Room, Bed Room, Kitchen, Closet, Toilet, Entrance, Bath Room, Wash Room, Balcony.\n\n"
            "STYLE:\n"
            "Light watercolor paper texture.\n"
            "Soft warm off-white background.\n"
            "Very light floor tones.\n"
            "Thin clean architectural lines.\n"
            "Minimal soft top-down furniture only if it does not interfere with the original layout.\n"
            "Furniture is optional and secondary.\n"
            "Layout accuracy is mandatory.\n\n"
            "TEXT:\n"
            "Do not use Japanese text.\n"
            "Do not transliterate Japanese labels.\n"
            "Use English labels only.\n"
            "English labels must be readable and placed inside the corresponding rooms.\n\n"
            "PRIORITY ORDER:\n"
            "1. Exact layout preservation from the reference image.\n"
            "2. Correct English room labels.\n"
            "3. Clean watercolor floorplan style.\n"
            "4. Simple furniture.\n\n"
            "If any instruction conflicts with exact layout preservation, ignore that instruction and preserve the layout."
        )

    def _resolve_reference_image_path(self, run_id: str, relative_path: str) -> Path | None:
        run_dir = self._safe_run_dir(run_id)
        storage_root = self.storage_dir.parent.resolve()
        candidates: list[Path] = []
        if relative_path.startswith("storage/"):
            candidates.append((storage_root / relative_path).resolve())
        else:
            candidates.append((run_dir / relative_path).resolve())
            candidates.append((storage_root / relative_path).resolve())
        for candidate in candidates:
            try:
                candidate.relative_to(storage_root)
            except ValueError:
                continue
            return candidate
        return None

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

    @staticmethod
    def _write_metadata(path: Path, metadata: RunMetadata) -> None:
        try:
            payload = json.dumps(metadata.model_dump(mode="json"), ensure_ascii=False, indent=2)
            temp_path = path.with_suffix(f"{path.suffix}.tmp")
            temp_path.write_text(payload, encoding="utf-8")
            temp_path.replace(path)
        except OSError as exc:
            raise HTTPException(status_code=500, detail="failed to write run metadata") from exc
