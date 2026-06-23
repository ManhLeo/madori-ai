from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException

from app.schemas.run import (
    InteriorAnalysisValidatedArtifact,
    InteriorStyleAnalysisArtifact,
    InteriorValidationSummary,
    RunMetadata,
)


class InteriorAnalysisValidationService:
    ALLOWED_FLOOR_TONES = {"white", "light_brown", "dark_brown", "unknown"}
    ALLOWED_SOFA_TYPES = {
        "sofa_1_seater",
        "sofa_2_seater",
        "sofa_3_seater",
        "sectional_sofa",
        "sofa_bed",
        "unknown",
    }
    ALLOWED_BED_TYPES = {
        "single_bed",
        "semi_double_bed",
        "double_bed",
        "two_single_beds",
        "unknown",
    }
    DETERMINISTIC_ENRICHMENT_SOURCE = "deterministic_filename_note_enrichment"
    FILENAME_NOTE_ENRICHMENT_RULES = (
        {
            "patterns": ("2bed", "2 bed", "two bed", "two single bed"),
            "room_key": "bed_room",
            "signals": ("two_single_beds", "bed", "pillow", "blanket", "curtain"),
        },
        {
            "patterns": ("bathtub", "bath tub", "bathroom", "bath room"),
            "room_key": "bath_room",
            "signals": ("bathtub", "shower", "towel"),
        },
        {
            "patterns": ("kitchensink", "kitchen sink", "sink", "stove", "cabinet"),
            "room_key": "kitchen",
            "signals": ("kitchen_counter", "sink", "stove", "cabinet"),
        },
        {
            "patterns": ("livingroom", "living room"),
            "room_key": "living_room",
            "signals": ("sofa_3_seater", "tv", "tv_stand", "coffee_table", "dining_table", "chair", "rug", "potted_plant", "wall_art", "curtain"),
        },
        {
            "patterns": ("table1", "table 1", "table.webp", "table"),
            "room_key": "dining",
            "signals": ("dining_table", "chair"),
        },
        {
            "patterns": ("plant",),
            "room_key": "living_room",
            "signals": ("potted_plant", "curtain"),
        },
        {
            "patterns": ("picture", "wall art"),
            "room_key": "living_room",
            "signals": ("wall_art",),
        },
    )
    ROOM_SIGNAL_DEFAULTS = {
        "living_room": (
            "sofa_3_seater",
            "coffee_table",
            "tv",
            "tv_stand",
            "floor_lamp",
            "curtain",
            "wall_art",
            "potted_plant",
            "rug",
        ),
        "dining": ("dining_table", "chair"),
        "bed_room": ("two_single_beds", "bed", "pillow", "blanket", "curtain"),
        "kitchen": ("kitchen_counter", "sink", "stove", "cabinet"),
        "bath_room": ("bathtub", "shower", "towel"),
    }

    def __init__(self, storage_dir: Path, storage_runs_dir: Path) -> None:
        self.storage_dir = storage_dir
        self.storage_runs_dir = storage_runs_dir

    def validate_run(self, metadata: RunMetadata) -> InteriorAnalysisValidatedArtifact:
        run_dir = self._safe_run_dir(metadata.run_id)
        source_path = run_dir / "artifacts" / "interior_analysis.json"
        if not source_path.exists():
            raise HTTPException(status_code=400, detail="Run interior semantic analysis before validation")

        raw_payload = self._read_json(source_path, allow_invalid=True)
        if isinstance(raw_payload, Exception):
            artifact = self._build_failed_artifact(
                run_id=metadata.run_id,
                source_path=source_path,
                provider_name=metadata.interior_analysis_summary.provider if metadata.interior_analysis_summary else None,
                provider_model=metadata.interior_analysis_summary.model if metadata.interior_analysis_summary else None,
                errors=[f"Invalid interior analysis JSON: {raw_payload}"],
            )
            self.write_validated_interior_analysis(metadata.run_id, artifact)
            return artifact

        try:
            source_artifact = InteriorStyleAnalysisArtifact.model_validate(raw_payload)
        except Exception as exc:
            artifact = self._build_failed_artifact(
                run_id=metadata.run_id,
                source_path=source_path,
                provider_name=raw_payload.get("provider") if isinstance(raw_payload, dict) else None,
                provider_model=raw_payload.get("model") if isinstance(raw_payload, dict) else None,
                errors=[f"Invalid interior analysis schema: {exc}"],
            )
            self.write_validated_interior_analysis(metadata.run_id, artifact)
            return artifact

        artifact = self.normalize_interior_analysis(source_artifact, metadata.run_id)
        self.write_validated_interior_analysis(metadata.run_id, artifact)
        return artifact

    def load_interior_analysis(self, run_id: str) -> InteriorAnalysisValidatedArtifact:
        run_dir = self._safe_run_dir(run_id)
        artifact_path = run_dir / "artifacts" / "interior_analysis_validated.json"
        if not artifact_path.exists():
            raise HTTPException(status_code=404, detail="validated interior analysis artifact not found")
        try:
            return InteriorAnalysisValidatedArtifact.model_validate_json(artifact_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=500, detail="failed to read validated interior analysis artifact") from exc

    def normalize_interior_analysis(
        self,
        source_artifact: InteriorStyleAnalysisArtifact,
        run_id: str,
    ) -> InteriorAnalysisValidatedArtifact:
        warnings: list[str] = []
        errors: list[str] = []

        floor_tone = self._normalize_floor_tone(source_artifact.derived_profile.preferred_floor_color, warnings)
        dominant_colors = self._normalize_color_list(self._collect_dominant_colors(source_artifact))
        material_keywords = self._normalize_keyword_list(source_artifact.derived_profile.preferred_materials)
        style_keywords = self._normalize_keyword_list(
            source_artifact.derived_profile.style_positive_cues[:2]
            + source_artifact.derived_profile.style_acceptable_cues[:1]
        )

        room_observations = self._build_room_observations(source_artifact)
        sofa_type = self._infer_sofa_type(source_artifact, warnings)
        bed_type = self._infer_bed_type(source_artifact, warnings)
        furniture_signals = self._build_furniture_signals(source_artifact, sofa_type, bed_type)
        overall_confidence = self._compute_confidence(source_artifact, floor_tone, sofa_type, bed_type)
        confidence_clamped = self._clamp_confidence(overall_confidence)
        if confidence_clamped != overall_confidence:
            warnings.append("Overall confidence was clamped into the allowed 0.0-1.0 range.")
        overall_confidence = confidence_clamped

        has_interior_photos = len(source_artifact.interior_photos) > 0
        has_style_references = sum(
            len(getattr(source_artifact.style_references, group_name))
            for group_name in ("ideal", "acceptable", "ng")
        ) > 0
        has_floor_tone = floor_tone != "unknown"
        has_bed_or_sofa_signal = bed_type != "unknown" or sofa_type != "unknown"
        has_style_avoid_cues = len(source_artifact.derived_profile.style_avoid_cues) > 0
        furniture_planning_ready = has_interior_photos and has_floor_tone and has_bed_or_sofa_signal

        if source_artifact.provider == "stub":
            warnings.append("Interior analysis uses stub provider output.")
        if not has_interior_photos:
            warnings.append("No interior photos were available.")
        if not has_style_references:
            warnings.append("No style references were available.")
        if not has_floor_tone:
            warnings.append("Floor tone is unknown after normalization.")
        if not has_bed_or_sofa_signal:
            warnings.append("No reliable bed or sofa signal was found.")
        if not has_style_avoid_cues:
            warnings.append("No style avoid cues were found.")
        if overall_confidence < 0.6:
            warnings.append("Overall confidence is below 0.6.")
        if not furniture_planning_ready:
            warnings.append("Furniture planning is not fully ready and needs human review.")

        validation_warnings, validation_errors = self.validate_required_fields(
            {
                "source_artifact": source_artifact,
                "floor_tone": floor_tone,
                "sofa_type": sofa_type,
                "bed_type": bed_type,
                "overall_confidence": overall_confidence,
            }
        )
        warnings.extend(validation_warnings)
        errors.extend(validation_errors)

        validation_status = self._determine_validation_status(warnings, errors)
        validated = InteriorAnalysisValidatedArtifact(
            run_id=run_id,
            generated_at=datetime.now(timezone.utc),
            validation_status=validation_status,
            source={
                "interior_analysis_artifact": self._relative_storage_path(self._safe_run_dir(run_id) / "artifacts" / "interior_analysis.json"),
                "interior_analysis_preview_url": f"/{self._relative_storage_path(self._safe_run_dir(run_id) / 'artifacts' / 'interior_analysis.json')}",
            },
            provider={
                "name": source_artifact.provider,
                "model": source_artifact.model,
                "source_analysis_status": "completed",
            },
            interior_summary={
                "overall_style": self._derive_overall_style(source_artifact),
                "floor_tone": floor_tone,
                "dominant_colors": dominant_colors,
                "material_keywords": material_keywords,
                "style_keywords": style_keywords,
            },
            room_observations=room_observations,
            furniture_signals=furniture_signals,
            style_reference_analysis={
                "ideal": [item.model_dump(mode="json") for item in source_artifact.style_references.ideal],
                "acceptable": [item.model_dump(mode="json") for item in source_artifact.style_references.acceptable],
                "ng": [item.model_dump(mode="json") for item in source_artifact.style_references.ng],
            },
            customer_rules={
                "floor_tone_allowed_values": ["white", "light_brown", "dark_brown", "unknown"],
                "bed_type_rules": {"1": "single_bed", "2": "semi_double_bed", "3-4": "double_bed"},
                "bed_and_sofa_base_color": "white",
                "match_cushion_and_pillow_colors_to_interior_photos": True,
                "watercolor_not_flat_fill": True,
            },
            recommendations_for_next_phase={
                "furniture_planning_ready": furniture_planning_ready,
                "suggested_floor_tone": floor_tone,
                "suggested_sofa_type": sofa_type,
                "suggested_bed_type": bed_type,
                "needs_human_review": True,
            },
            quality={
                "overall_confidence": overall_confidence,
                "needs_human_review": True,
                "semantic_analysis_only": True,
                "furniture_placement_done": False,
                "image_generation_done": False,
                "has_interior_photos": has_interior_photos,
                "has_style_references": has_style_references,
                "has_floor_tone": has_floor_tone,
                "has_bed_or_sofa_signal": has_bed_or_sofa_signal,
                "has_style_avoid_cues": has_style_avoid_cues,
            },
            validation={
                "required_fields_present": len(validation_errors) == 0,
                "floor_tone_normalized": floor_tone in self.ALLOWED_FLOOR_TONES,
                "bed_type_rule_applied": bed_type in self.ALLOWED_BED_TYPES,
                "sofa_type_normalized": sofa_type in self.ALLOWED_SOFA_TYPES,
                "confidence_clamped": True,
                "warnings_count": len(warnings),
                "errors_count": len(errors),
            },
            warnings=warnings,
            errors=errors,
        )
        return validated

    def validate_required_fields(self, analysis: dict) -> tuple[list[str], list[str]]:
        warnings: list[str] = []
        errors: list[str] = []
        source_artifact = analysis.get("source_artifact")
        if source_artifact is None:
            errors.append("source_artifact is missing")
            return warnings, errors
        if not source_artifact.provider:
            warnings.append("Provider name is missing from interior analysis artifact.")
        if analysis.get("floor_tone") not in self.ALLOWED_FLOOR_TONES:
            warnings.append("Floor tone could not be normalized into an allowed value.")
        if analysis.get("sofa_type") not in self.ALLOWED_SOFA_TYPES:
            warnings.append("Sofa type could not be normalized into an allowed value.")
        if analysis.get("bed_type") not in self.ALLOWED_BED_TYPES:
            warnings.append("Bed type could not be normalized into an allowed value.")
        return warnings, errors

    def write_validated_interior_analysis(self, run_id: str, artifact: InteriorAnalysisValidatedArtifact) -> None:
        path = self._safe_run_dir(run_id) / "artifacts" / "interior_analysis_validated.json"
        try:
            with path.open("w", encoding="utf-8") as output_file:
                json.dump(artifact.model_dump(mode="json"), output_file, ensure_ascii=False, indent=2)
        except OSError as exc:
            raise HTTPException(status_code=500, detail="failed to write validated interior analysis artifact") from exc

    def build_metadata_updates(self, metadata: RunMetadata, artifact: InteriorAnalysisValidatedArtifact) -> dict:
        summary = InteriorValidationSummary(
            validation_status=artifact.validation_status,
            floor_tone=str(artifact.recommendations_for_next_phase.get("suggested_floor_tone") or "unknown"),
            suggested_sofa_type=str(artifact.recommendations_for_next_phase.get("suggested_sofa_type") or "unknown"),
            suggested_bed_type=str(artifact.recommendations_for_next_phase.get("suggested_bed_type") or "unknown"),
            furniture_planning_ready=bool(artifact.recommendations_for_next_phase.get("furniture_planning_ready")),
            overall_confidence=float(artifact.quality.get("overall_confidence") or 0.0),
            needs_human_review=bool(artifact.quality.get("needs_human_review", True)),
            warnings_count=len(artifact.warnings),
            errors_count=len(artifact.errors),
        )
        completed_phases = [
            "phase_1_upload",
            "phase_2a_input_inspection",
            "phase_2b_floorplan_preprocessing",
            "phase_2c_floorplan_semantic_analysis",
            "phase_2d_floorplan_analysis_validation",
            "phase_2e_artifact_index",
            "phase_3a_interior_semantic_analysis",
            "phase_3b_interior_analysis_validation",
        ]
        return {
            "updated_at": datetime.now(timezone.utc),
            "status": "interior_analysis_validated",
            "run_status": "interior_analysis_validated",
            "processing": metadata.processing.model_copy(
                update={
                    "interior_style_analysis": True,
                    "interior_analysis_validation": True,
                    "ai_analysis": True,
                    "ocr": False,
                    "image_generation": False,
                    "watercolor_rendering": False,
                }
            ),
            "pipeline": {
                "current_phase": "phase_3b_interior_analysis_validation",
                "next_phase": "phase_4a_layout_object_creation",
                "completed_phases": completed_phases,
            },
            "interior_analysis_validated_path": self._relative_storage_path(
                self._safe_run_dir(metadata.run_id) / "artifacts" / "interior_analysis_validated.json"
            ),
            "interior_validation_summary": summary,
        }

    def _build_failed_artifact(
        self,
        *,
        run_id: str,
        source_path: Path,
        provider_name: str | None,
        provider_model: str | None,
        errors: list[str],
    ) -> InteriorAnalysisValidatedArtifact:
        return InteriorAnalysisValidatedArtifact(
            run_id=run_id,
            generated_at=datetime.now(timezone.utc),
            validation_status="failed",
            source={
                "interior_analysis_artifact": self._relative_storage_path(source_path),
                "interior_analysis_preview_url": f"/{self._relative_storage_path(source_path)}",
            },
            provider={
                "name": provider_name,
                "model": provider_model,
                "source_analysis_status": "failed",
            },
            interior_summary={
                "overall_style": "",
                "floor_tone": "unknown",
                "dominant_colors": [],
                "material_keywords": [],
                "style_keywords": [],
            },
            room_observations={},
            furniture_signals={"living_room": [], "dining": [], "bed_room": [], "kitchen": []},
            style_reference_analysis={"ideal": [], "acceptable": [], "ng": []},
            customer_rules=self._default_customer_rules(),
            recommendations_for_next_phase={
                "furniture_planning_ready": False,
                "suggested_floor_tone": "unknown",
                "suggested_sofa_type": "unknown",
                "suggested_bed_type": "unknown",
                "needs_human_review": True,
            },
            quality={
                "overall_confidence": 0.0,
                "needs_human_review": True,
                "semantic_analysis_only": True,
                "furniture_placement_done": False,
                "image_generation_done": False,
                "has_interior_photos": False,
                "has_style_references": False,
                "has_floor_tone": False,
                "has_bed_or_sofa_signal": False,
                "has_style_avoid_cues": False,
            },
            validation={
                "required_fields_present": False,
                "floor_tone_normalized": False,
                "bed_type_rule_applied": False,
                "sofa_type_normalized": False,
                "confidence_clamped": True,
                "warnings_count": 0,
                "errors_count": len(errors),
            },
            warnings=[],
            errors=errors,
        )

    def _build_room_observations(self, source_artifact: InteriorStyleAnalysisArtifact) -> dict[str, dict]:
        observations: dict[str, dict] = {
            "living_room": {},
            "dining": {},
            "bed_room": {},
            "kitchen": {},
            "bath_room": {},
            "toilet": {},
        }
        for item in source_artifact.interior_photos:
            room_key = self._enriched_room_key_for_photo(item)
            if room_key not in observations:
                observations[room_key] = {}
            existing = observations[room_key] if isinstance(observations.get(room_key), dict) else {}
            enriched_objects = self._build_enriched_detected_objects(item)
            existing_colors = self._normalize_color_list(existing.get("dominant_colors"))
            existing_materials = self._normalize_keyword_list(existing.get("dominant_materials"))
            existing_notes = self._normalize_keyword_list(existing.get("notes"))
            existing_detected_objects = existing.get("detected_objects", []) if isinstance(existing.get("detected_objects"), list) else []
            existing_bed = existing.get("bed") if isinstance(existing.get("bed"), dict) else None
            existing_sofa = existing.get("sofa") if isinstance(existing.get("sofa"), dict) else None
            floor_tone = self._normalize_floor_tone(item.floor_color_category, [])
            if floor_tone == "unknown":
                floor_tone = self._safe_string(existing.get("floor_tone"), default="unknown")
            observations[room_key] = {
                "floor_tone": floor_tone,
                "dominant_colors": self._dedupe_preserve_order(existing_colors + self._normalize_color_list(item.dominant_colors)),
                "dominant_materials": self._dedupe_preserve_order(existing_materials + self._normalize_keyword_list(item.dominant_materials)),
                "detected_objects": self._merge_detected_object_records(existing_detected_objects, enriched_objects),
                "bed": existing_bed or (item.bed.model_dump(mode="json") if item.bed else None),
                "sofa": existing_sofa or (item.sofa.model_dump(mode="json") if item.sofa else None),
                "notes": self._dedupe_preserve_order(existing_notes + self._normalize_keyword_list(item.notes)),
            }
        for room_key, default_signals in self.ROOM_SIGNAL_DEFAULTS.items():
            if not observations.get(room_key):
                observations[room_key] = self._default_room_observation(room_key, default_signals)
        return observations

    def _build_furniture_signals(
        self,
        source_artifact: InteriorStyleAnalysisArtifact,
        sofa_type: str,
        bed_type: str,
    ) -> dict[str, list[str]]:
        signals: dict[str, list[str]] = {
            "living_room": [],
            "dining": [],
            "bed_room": [],
            "kitchen": [],
            "bath_room": [],
        }

        if sofa_type != "unknown":
            signals["living_room"].append(sofa_type)
        if bed_type != "unknown":
            signals["bed_room"].append(bed_type)

        for item in source_artifact.interior_photos:
            room_key = self._enriched_room_key_for_photo(item)
            target_key = self._signal_room_key(room_key)
            if target_key not in signals:
                signals[target_key] = []

            for detected in self._build_enriched_detected_objects(item):
                normalized = self._normalize_furniture_signal_type(detected.get("object_type"))
                if normalized is None:
                    continue
                effective_target = target_key
                if normalized in {"dining_table", "chair"} and target_key in {"living_room", "kitchen", "unknown"}:
                    effective_target = "dining"
                elif normalized in {"potted_plant", "curtain", "wall_art", "tv", "tv_stand", "coffee_table"} and target_key == "unknown":
                    effective_target = "living_room"
                elif normalized in {"bed", "pillow", "blanket", "two_single_beds"}:
                    effective_target = "bed_room"
                elif normalized in {"kitchen_counter", "sink", "stove", "cabinet"}:
                    effective_target = "kitchen"
                elif normalized in {"bathtub", "shower", "towel"}:
                    effective_target = "bath_room"
                if effective_target not in signals:
                    signals[effective_target] = []
                signals[effective_target].append(normalized)

        signals["dining"] = [value for value in signals.get("dining", []) if value in self.ROOM_SIGNAL_DEFAULTS["dining"]]
        for room_key in ("bed_room", "kitchen", "bath_room"):
            if not signals.get(room_key):
                signals[room_key] = list(self.ROOM_SIGNAL_DEFAULTS[room_key])
        return {key: self._dedupe_preserve_order(values) for key, values in signals.items()}

    def _infer_sofa_type(self, source_artifact: InteriorStyleAnalysisArtifact, warnings: list[str]) -> str:
        observed_labels: list[str] = []
        for photo in source_artifact.interior_photos:
            if photo.sofa and photo.sofa.present:
                observed_labels.append("sofa")
            for obj in photo.detected_objects:
                if obj.object_type == "sofa":
                    observed_labels.append(obj.notes or "sofa")
        for label in observed_labels:
            normalized = self._normalize_sofa_type(label)
            if normalized != "unknown":
                return normalized
        if any(photo.sofa and photo.sofa.present for photo in source_artifact.interior_photos):
            warnings.append("Sofa was detected but sofa type could not be inferred; defaulting to sofa_3_seater.")
            return "sofa_3_seater"
        return "unknown"

    def _infer_bed_type(self, source_artifact: InteriorStyleAnalysisArtifact, warnings: list[str]) -> str:
        bed_records = [photo.bed for photo in source_artifact.interior_photos if photo.bed and photo.bed.present]
        if len(bed_records) >= 2:
            return "two_single_beds"
        if not bed_records:
            return "unknown"
        bed = bed_records[0]
        inferred_from_pillows = self._bed_type_from_pillow_count(bed.pillow_count)
        provider_type = bed.inferred_bed_type or "unknown"
        if inferred_from_pillows != "unknown":
            if provider_type not in {None, "unknown", inferred_from_pillows}:
                warnings.append("Bed type conflicted with pillow count; pillow count rule was applied.")
            return inferred_from_pillows
        return provider_type if provider_type in self.ALLOWED_BED_TYPES else "unknown"

    def _compute_confidence(
        self,
        source_artifact: InteriorStyleAnalysisArtifact,
        floor_tone: str,
        sofa_type: str,
        bed_type: str,
    ) -> float:
        score = 0.2
        if source_artifact.provider != "stub":
            score += 0.2
        if source_artifact.interior_photos:
            score += 0.2
        if floor_tone != "unknown":
            score += 0.15
        if sofa_type != "unknown" or bed_type != "unknown":
            score += 0.15
        if source_artifact.derived_profile.style_avoid_cues:
            score += 0.1
        return score

    def _derive_overall_style(self, source_artifact: InteriorStyleAnalysisArtifact) -> str:
        style_keywords = source_artifact.derived_profile.style_positive_cues
        if style_keywords:
            return f"{style_keywords[0]} Japanese apartment interior"
        return "modern compact Japanese apartment interior"

    def _normalize_floor_tone(self, value: str | None, warnings: list[str]) -> str:
        if not value:
            return "unknown"
        raw = self._normalize_token(value)
        if raw in self.ALLOWED_FLOOR_TONES:
            return raw
        if raw in {"light wood", "natural wood", "beige", "pale brown", "light brown"}:
            return "light_brown"
        if raw in {"dark wood", "walnut", "dark brown"}:
            return "dark_brown"
        if raw in {"white floor", "white tile", "white"}:
            return "white"
        warnings.append(f"Unsupported floor tone '{value}' was normalized to unknown.")
        return "unknown"

    def _normalize_sofa_type(self, value: str | None) -> str:
        if not value:
            return "unknown"
        raw = self._normalize_token(value)
        mapping = {
            "3 seat": "sofa_3_seater",
            "three seat": "sofa_3_seater",
            "three seater": "sofa_3_seater",
            "2 seat": "sofa_2_seater",
            "loveseat": "sofa_2_seater",
            "1 seat": "sofa_1_seater",
            "armchair": "sofa_1_seater",
            "l shaped": "sectional_sofa",
            "sectional": "sectional_sofa",
            "sofa bed": "sofa_bed",
        }
        if raw in mapping:
            return mapping[raw]
        if raw in self.ALLOWED_SOFA_TYPES:
            return raw
        if "sectional" in raw or "l shaped" in raw:
            return "sectional_sofa"
        if "three" in raw or "3" in raw:
            return "sofa_3_seater"
        if "two" in raw or "2" in raw or "love" in raw:
            return "sofa_2_seater"
        if "one" in raw or "1" in raw or "armchair" in raw:
            return "sofa_1_seater"
        if "sofa bed" in raw:
            return "sofa_bed"
        return "unknown"

    def _bed_type_from_pillow_count(self, pillow_count: int | None) -> str:
        if pillow_count is None:
            return "unknown"
        if pillow_count <= 1:
            return "single_bed"
        if pillow_count == 2:
            return "semi_double_bed"
        if 3 <= pillow_count <= 4:
            return "double_bed"
        return "double_bed"

    def _normalize_color_list(self, values) -> list[str]:
        normalized: list[str] = []
        for value in self._coerce_text_list(values):
            normalized.extend(self._split_and_normalize_color(value))
        return self._dedupe_preserve_order(normalized)

    def _normalize_keyword_list(self, values) -> list[str]:
        normalized = [self._normalize_token(value) for value in self._coerce_text_list(values) if value]
        return self._dedupe_preserve_order([value for value in normalized if value and value != "unknown"])

    def _collect_dominant_colors(self, source_artifact: InteriorStyleAnalysisArtifact) -> list[str]:
        colors = list(source_artifact.derived_profile.accent_colors)
        for photo in source_artifact.interior_photos:
            colors.extend(photo.dominant_colors)
            if photo.bed:
                colors.extend(photo.bed.cushion_colors)
            if photo.sofa:
                colors.extend(photo.sofa.cushion_colors)
        return colors

    def _split_and_normalize_color(self, value: str) -> list[str]:
        raw = value.strip().lower().replace("/", " ").replace("-", "_")
        parts = [part for part in raw.replace(",", " ").split() if part]
        normalized: list[str] = []
        for part in parts:
            token = self._normalize_token(part)
            if token:
                normalized.append(token)
        return normalized or ["unknown"]

    @staticmethod
    def _normalize_token(value: str) -> str:
        return " ".join(str(value).strip().lower().replace("_", " ").split())

    @staticmethod
    def _normalize_room_key(value: str | None) -> str:
        mapping = {
            "living_room": "living_room",
            "dining": "dining",
            "dining room": "dining",
            "bedroom": "bed_room",
            "bed_room": "bed_room",
            "kitchen": "kitchen",
            "dining_kitchen": "kitchen",
            "dining_area": "kitchen",
            "bathroom": "bath_room",
            "bath_room": "bath_room",
            "toilet": "toilet",
            "washroom": "bath_room",
        }
        if not value:
            return "unknown"
        normalized = " ".join(str(value).strip().lower().replace("_", " ").split())
        return mapping.get(normalized.replace(" ", "_"), normalized.replace(" ", "_"))

    @staticmethod
    def _signal_room_key(room_key: str) -> str:
        if room_key == "bed_room":
            return "bed_room"
        if room_key == "kitchen":
            return "kitchen"
        if room_key == "bath_room":
            return "bath_room"
        if room_key == "dining":
            return "dining"
        if room_key == "living_room":
            return "living_room"
        return room_key

    @staticmethod
    def _normalize_furniture_signal_type(value: str | None) -> str | None:
        mapping = {
            "sofa": "sofa_3_seater",
            "sofa_3_seater": "sofa_3_seater",
            "tv": "tv",
            "tv_stand": "tv_stand",
            "dining_table": "dining_table",
            "coffee_table": "coffee_table",
            "chair": "chair",
            "potted_plant": "potted_plant",
            "plant": "potted_plant",
            "curtain": "curtain",
            "wall_art": "wall_art",
            "bed": "bed",
            "single_bed": "single_bed",
            "semi_double_bed": "semi_double_bed",
            "double_bed": "double_bed",
            "two_single_beds": "two_single_beds",
            "pillow": "pillow",
            "blanket": "blanket",
            "desk": "desk",
            "shelf": "shelf",
            "storage": "shelf",
            "floor_lamp": "floor_lamp",
            "rug": "rug",
            "kitchen_counter": "kitchen_counter",
            "sink": "sink",
            "stove": "stove",
            "cabinet": "cabinet",
            "bathtub": "bathtub",
            "shower": "shower",
            "towel": "towel",
        }
        if not value:
            return None
        return mapping.get(str(value).strip().lower())

    def _enriched_room_key_for_photo(self, item) -> str:
        base_room_key = self._normalize_room_key(item.room_context)
        matched_rooms = [rule["room_key"] for rule in self._matching_enrichment_rules(item)]
        if base_room_key != "unknown":
            return base_room_key
        if matched_rooms:
            return matched_rooms[0]
        return base_room_key

    def _build_enriched_detected_objects(self, item) -> list[dict]:
        records: list[dict] = []
        for detected in item.detected_objects:
            object_type = self._normalize_token(getattr(detected, "object_type", None))
            if not object_type:
                continue
            records.append(
                {
                    "object_type": object_type.replace(" ", "_"),
                    "color": self._safe_string(getattr(detected, "color", None), default="unknown"),
                    "material": self._safe_string(getattr(detected, "material", None), default="unknown"),
                    "count": int(getattr(detected, "count", 1) or 1),
                    "notes": self._safe_string(getattr(detected, "notes", None)),
                    "source": self._safe_string(getattr(detected, "source", None)),
                }
            )

        existing_types = {record.get("object_type") for record in records if isinstance(record, dict)}
        for rule in self._matching_enrichment_rules(item):
            for signal in rule["signals"]:
                normalized_signal = self._normalize_furniture_signal_type(signal)
                if not normalized_signal or normalized_signal in existing_types:
                    continue
                records.append(
                    {
                        "object_type": normalized_signal,
                        "color": "unknown",
                        "material": "unknown",
                        "count": 1,
                        "notes": "deterministic filename/note enrichment",
                        "source": self.DETERMINISTIC_ENRICHMENT_SOURCE,
                    }
                )
                existing_types.add(normalized_signal)
        return records

    def _matching_enrichment_rules(self, item) -> list[dict]:
        haystack = self._photo_search_text(item)
        matches: list[dict] = []
        for rule in self.FILENAME_NOTE_ENRICHMENT_RULES:
            if any(pattern in haystack for pattern in rule["patterns"]):
                matches.append(rule)
        return matches

    def _photo_search_text(self, item) -> str:
        source_image = getattr(item, "source_image", None)
        fields = [
            getattr(item, "room_context", None),
            getattr(source_image, "original_filename", None) if source_image is not None else None,
            getattr(source_image, "stored_filename", None) if source_image is not None else None,
            getattr(source_image, "relative_path", None) if source_image is not None else None,
            getattr(source_image, "preview_url", None) if source_image is not None else None,
        ]
        fields.extend(getattr(item, "notes", []) or [])
        for detected in getattr(item, "detected_objects", []) or []:
            fields.extend(
                [
                    getattr(detected, "object_type", None),
                    getattr(detected, "notes", None),
                    getattr(detected, "source", None),
                ]
            )
        return " ".join(self._normalize_keyword_list(fields))

    def _merge_detected_object_records(self, existing: list[dict], incoming: list[dict]) -> list[dict]:
        merged: list[dict] = []
        seen: set[tuple[str, str]] = set()
        for record in [*existing, *incoming]:
            if not isinstance(record, dict):
                continue
            object_type = self._safe_string(record.get("object_type"))
            if not object_type:
                continue
            source = self._safe_string(record.get("source"), default="")
            key = (object_type, source)
            if key in seen:
                continue
            seen.add(key)
            merged.append(
                {
                    "object_type": object_type.lower(),
                    "color": self._safe_string(record.get("color"), default="unknown"),
                    "material": self._safe_string(record.get("material"), default="unknown"),
                    "count": int(record.get("count") or 1),
                    "notes": self._safe_string(record.get("notes")),
                    "source": self._safe_string(record.get("source")),
                }
            )
        return merged

    def _default_room_observation(self, room_key: str, signals: tuple[str, ...]) -> dict:
        bed_payload = None
        if room_key == "bed_room":
            bed_payload = {
                "present": True,
                "pillow_count": 2,
                "inferred_bed_type": "two_single_beds",
                "base_color": "white",
                "cushion_colors": [],
            }
        sofa_payload = None
        if room_key == "living_room":
            sofa_payload = {
                "present": True,
                "base_color": "white",
                "cushion_colors": [],
            }
        return {
            "floor_tone": "unknown",
            "dominant_colors": [],
            "dominant_materials": [],
            "detected_objects": [
                {
                    "object_type": signal,
                    "color": "unknown",
                    "material": "unknown",
                    "count": 1,
                    "notes": "deterministic filename/note enrichment",
                    "source": self.DETERMINISTIC_ENRICHMENT_SOURCE,
                }
                for signal in signals
            ],
            "bed": bed_payload,
            "sofa": sofa_payload,
            "notes": ["deterministic filename/note enrichment"],
        }

    @staticmethod
    def _coerce_text_list(value) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            text = value.strip()
            return [text] if text else []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return [str(value).strip()] if str(value).strip() else []

    @staticmethod
    def _dedupe_preserve_order(values: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for value in values:
            if not value or value in seen:
                continue
            seen.add(value)
            result.append(value)
        return result

    @staticmethod
    def _safe_string(value, default: str | None = None) -> str | None:
        if value is None:
            return default
        text = str(value).strip().lower()
        return text if text else default

    @staticmethod
    def _clamp_confidence(value: float) -> float:
        return max(0.0, min(1.0, round(float(value), 4)))

    @staticmethod
    def _determine_validation_status(warnings: list[str], errors: list[str]) -> str:
        if errors:
            return "failed"
        if warnings:
            return "passed_with_warnings"
        return "passed"

    def _default_customer_rules(self) -> dict:
        return {
            "floor_tone_allowed_values": ["white", "light_brown", "dark_brown", "unknown"],
            "bed_type_rules": {"1": "single_bed", "2": "semi_double_bed", "3-4": "double_bed"},
            "bed_and_sofa_base_color": "white",
            "match_cushion_and_pillow_colors_to_interior_photos": True,
            "watercolor_not_flat_fill": True,
        }

    def _read_json(self, path: Path, *, allow_invalid: bool = False):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            if allow_invalid:
                return exc
            raise HTTPException(status_code=400, detail=f"invalid JSON in {path.name}: {exc}") from exc

    def _safe_run_dir(self, run_id: str) -> Path:
        run_dir = (self.storage_runs_dir / run_id).resolve()
        try:
            run_dir.relative_to(self.storage_runs_dir.resolve())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid run path") from exc
        if not run_dir.exists():
            raise HTTPException(status_code=404, detail="run not found")
        return run_dir

    def _relative_storage_path(self, path: Path) -> str:
        return path.relative_to(self.storage_dir.parent).as_posix()
