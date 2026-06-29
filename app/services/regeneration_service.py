from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException

from app.config import get_settings
from app.schemas.run import (
    ImageGenerationDraftRequest,
    ImageGenerationRequestPreviewArtifact,
    PromptPackageArtifact,
    QAFeedbackResponse,
    RegenerateWithFeedbackRequest,
    RegenerationAttemptResponse,
    RegenerationSummary,
    RenderPlanArtifact,
    RunMetadata,
)
from app.services.cloudinary_storage_service import CloudinaryStorageService
from app.services.image_generation_draft_service import ImageGenerationDraftService
from app.services.image_generation_request_preview_service import ImageGenerationRequestPreviewService


class RegenerationService:
    ATTEMPT_PATTERN = re.compile(r"^regeneration_attempt_(\d+)\.json$")

    def __init__(self, storage_dir: Path, storage_runs_dir: Path) -> None:
        self.storage_dir = storage_dir
        self.storage_runs_dir = storage_runs_dir
        self.settings = get_settings()
        self.draft_service = ImageGenerationDraftService(storage_dir, storage_runs_dir)
        self.preview_service = ImageGenerationRequestPreviewService(storage_dir, storage_runs_dir)
        self.cloudinary_service = CloudinaryStorageService()

    def regenerate_with_feedback(
        self,
        metadata: RunMetadata,
        request: RegenerateWithFeedbackRequest,
    ) -> RegenerationAttemptResponse:
        self._validate_request(request)

        qa_feedback = self._load_feedback(metadata.run_id, request)
        preview, source_prompt_path = self._load_or_build_preview(metadata)
        selected_reference_images = self._select_reference_images(preview, request)
        draft_request = ImageGenerationDraftRequest(
            confirm_generation=True,
            provider="openai",
            output_format=request.output_format,
            use_reference_images=request.use_reference_images,
            max_reference_images=request.max_reference_images,
        )
        _, guard_errors = self.draft_service.validate_generation_guards(metadata.run_id, draft_request, preview, metadata)
        guard_errors = [item for item in guard_errors if not item.startswith("unsupported_provider:")]
        if guard_errors:
            detail = guard_errors[0]
            raise HTTPException(status_code=400, detail=detail)

        selected_reference_files, structure_reference_path = self.draft_service.require_structure_reference_image(
            metadata.run_id,
            draft_request,
            preview,
            selected_reference_images,
        )
        openai_input_images, input_warnings = self.draft_service.validate_openai_input_images(selected_reference_files)
        attempt = self._next_attempt_number(metadata.run_id)

        prompt_text, correction_guidance_used, negative_guidance_used = self._build_regeneration_prompt(
            qa_feedback,
            preview,
            metadata.run_id,
        )
        payload = {
            "model": self.settings.openai_image_model,
            "prompt": prompt_text,
            "size": str(preview.request_payload_preview.get("size") or self.settings.openai_image_provider_size),
            "quality": self.settings.openai_image_quality,
            "n": 1,
            "output_format": request.output_format or self.settings.openai_image_output_format,
            "prompt_mode": "strict_layout_with_feedback",
            "strict_layout_test_enabled": True,
            "primary_structure_reference": "normalized_floorplan.png",
            "layout_preservation_priority": "maximum",
            "long_prompt_disabled": True,
        }

        try:
            provider_response, provider_warnings, openai_image_attempts = self.draft_service.call_openai_image_api(
                payload,
                selected_reference_images,
                openai_input_images,
                structure_reference_path,
            )
        except HTTPException as exc:
            if self._is_openai_image_timeout_error(exc):
                self._write_failed_regeneration_attempt_artifact(
                    metadata.run_id,
                    attempt,
                    preview,
                    prompt_text,
                    source_prompt_path,
                    qa_feedback,
                    correction_guidance_used,
                    negative_guidance_used,
                    openai_input_images,
                    exc,
                )
            raise

        output_info, output_warnings = self._decode_and_save_regenerated_image(
            metadata.run_id,
            attempt,
            provider_response,
            preview,
        )
        cloudinary_info, cloudinary_warnings = self._upload_regenerated_output_to_cloudinary(
            metadata.run_id,
            attempt,
            output_info,
        )
        public_output_url = (
            cloudinary_info.get("regenerated", {}).get("secure_url")
            if isinstance(cloudinary_info, dict)
            else None
        ) or output_info.get("output_preview_url")

        highest_severity = self._highest_severity(qa_feedback)
        warnings = self._dedupe_keep_order(
            list(preview.preview_warnings)
            + list(preview.upstream_warnings)
            + input_warnings
            + provider_warnings
            + output_warnings
            + cloudinary_warnings
        )
        artifact = RegenerationAttemptResponse(
            run_id=metadata.run_id,
            generated_at=datetime.now(timezone.utc),
            attempt=attempt,
            status="completed",
            provider="openai",
            model=self.settings.openai_image_model,
            prompt_mode="strict_layout_with_feedback",
            source_feedback={
                "qa_feedback_path": self._relative_artifact_path(metadata.run_id, "qa_feedback.json"),
                "issues_count": len(qa_feedback.issues),
                "highest_severity": highest_severity,
            },
            correction_guidance_used=correction_guidance_used,
            negative_guidance_used=negative_guidance_used,
            prompt={
                "text": prompt_text,
                "source_prompt_path": source_prompt_path,
                "prompt_length": len(prompt_text),
            },
            openai_image_attempts=openai_image_attempts,
            openai_input_images=[
                {
                    "path": str(item.get("path") or ""),
                    "filename": str(item.get("filename") or Path(str(item.get("path") or "")).name),
                    "mime_type": str(item.get("mime_type") or ""),
                    "role": str(item.get("role") or "reference_image"),
                }
                for item in openai_input_images
            ],
            outputs={
                **output_info,
                "public_output_url": public_output_url,
            },
            cloudinary=cloudinary_info,
            warnings=warnings,
            errors=[],
        )
        self.write_regeneration_attempt(metadata.run_id, attempt, artifact)
        return artifact

    def build_metadata_updates(self, metadata: RunMetadata, artifact: RegenerationAttemptResponse) -> dict:
        now = datetime.now(timezone.utc)
        cloudinary = artifact.cloudinary if isinstance(artifact.cloudinary, dict) else {}
        outputs = artifact.outputs if isinstance(artifact.outputs, dict) else {}
        source_feedback = artifact.source_feedback if isinstance(artifact.source_feedback, dict) else {}
        public_output_url = outputs.get("public_output_url") or outputs.get("output_preview_url")
        return {
            "status": "regenerated_with_feedback",
            "run_status": "regenerated_with_feedback",
            "updated_at": now,
            "processing": metadata.processing.model_copy(
                update={
                    "regeneration_with_feedback": True,
                    "image_generation": True,
                    "watercolor_rendering": True,
                }
            ),
            "pipeline": {
                "current_phase": "phase_8b_regenerate_with_feedback",
                "next_phase": "phase_8c_review_regeneration",
            },
            "latest_regeneration_path": self._relative_artifact_path(
                metadata.run_id,
                f"regeneration_attempt_{artifact.attempt}.json",
            ),
            "regeneration_summary": RegenerationSummary(
                latest_attempt=artifact.attempt,
                status=artifact.status,
                issues_count=int(source_feedback.get("issues_count") or 0),
                highest_severity=str(source_feedback.get("highest_severity") or "low"),
                output_preview_url=outputs.get("output_preview_url"),
                public_output_url=public_output_url,
                cloudinary_uploaded=bool(cloudinary.get("regenerated", {}).get("uploaded")),
                warnings_count=len(artifact.warnings),
                errors_count=len(artifact.errors),
            ),
            "public_output_url": public_output_url,
        }

    def load_latest_regeneration(self, run_id: str, metadata: RunMetadata | None = None) -> RegenerationAttemptResponse:
        metadata = metadata or self._load_metadata(run_id)
        relative_path = str(metadata.latest_regeneration_path or "").strip()
        if not relative_path:
            raise HTTPException(status_code=404, detail="latest_regeneration artifact not found")
        path = self._resolve_relative_storage_path(relative_path)
        if not path.exists():
            raise HTTPException(status_code=404, detail="latest_regeneration artifact not found")
        return self._load_regeneration_artifact(path)

    def write_regeneration_attempt(self, run_id: str, attempt: int, artifact: RegenerationAttemptResponse) -> None:
        path = self._artifacts_dir(run_id) / f"regeneration_attempt_{attempt}.json"
        try:
            with path.open("w", encoding="utf-8") as output_file:
                json.dump(artifact.model_dump(mode="json"), output_file, ensure_ascii=False, indent=2)
        except OSError as exc:
            raise HTTPException(status_code=500, detail="failed to write regeneration attempt artifact") from exc

    def _write_failed_regeneration_attempt_artifact(
        self,
        run_id: str,
        attempt: int,
        preview: ImageGenerationRequestPreviewArtifact,
        prompt_text: str,
        source_prompt_path: str,
        qa_feedback: QAFeedbackResponse,
        correction_guidance_used: list[str],
        negative_guidance_used: list[str],
        openai_input_images: list[dict],
        exc: HTTPException,
    ) -> None:
        path = self._artifacts_dir(run_id) / f"regeneration_attempt_{attempt}_failed.json"
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        attempts = detail.get("openai_image_attempts") if isinstance(detail, dict) else []
        highest_severity = self._highest_severity(qa_feedback)
        failed_artifact = RegenerationAttemptResponse(
            run_id=run_id,
            generated_at=datetime.now(timezone.utc),
            attempt=attempt,
            status="failed",
            provider="openai",
            model=self.settings.openai_image_model,
            prompt_mode="strict_layout_with_feedback",
            source_feedback={
                "qa_feedback_path": self._relative_artifact_path(run_id, "qa_feedback.json"),
                "issues_count": len(qa_feedback.issues),
                "highest_severity": highest_severity,
            },
            correction_guidance_used=correction_guidance_used,
            negative_guidance_used=negative_guidance_used,
            prompt={
                "text": prompt_text,
                "source_prompt_path": source_prompt_path,
                "prompt_length": len(prompt_text),
            },
            openai_image_attempts=attempts if isinstance(attempts, list) else [],
            openai_input_images=[
                {
                    "path": str(item.get("path") or ""),
                    "filename": str(item.get("filename") or Path(str(item.get("path") or "")).name),
                    "mime_type": str(item.get("mime_type") or ""),
                    "role": str(item.get("role") or "reference_image"),
                }
                for item in openai_input_images
            ],
            outputs={},
            cloudinary={
                "enabled": bool(self.settings.cloudinary_enabled),
                "regenerated": {
                    "enabled": bool(self.settings.cloudinary_enabled),
                    "uploaded": False,
                    "reason": "openai_image_generation_failed",
                },
                "warnings": [],
            },
            warnings=[
                "OpenAI image generation failed before a regenerated output image could be created.",
            ],
            errors=[
                {
                    "error": str(detail.get("error") or "openai_image_timeout"),
                    "message": str(detail.get("message") or exc.detail or "OpenAI image generation failed."),
                    "details": detail.get("details") if isinstance(detail, dict) else {},
                }
            ],
        )
        try:
            with path.open("w", encoding="utf-8") as output_file:
                json.dump(failed_artifact.model_dump(mode="json"), output_file, ensure_ascii=False, indent=2)
        except OSError:
            return

    def _validate_request(self, request: RegenerateWithFeedbackRequest) -> None:
        if request.provider != "openai":
            raise HTTPException(
                status_code=400,
                detail={"error": "unsupported_provider", "message": "Only provider=openai is supported."},
            )
        if not request.confirm_generation:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "confirmation_required",
                    "message": "confirm_generation must be true to regenerate with feedback.",
                },
            )
        if request.feedback_source not in {"latest", "specific"}:
            raise HTTPException(status_code=400, detail="feedback_source must be one of: latest, specific")
        if request.feedback_source == "specific" and not str(request.qa_feedback_path or "").strip():
            raise HTTPException(status_code=400, detail="qa_feedback_path is required when feedback_source=specific")
        if not self.settings.enable_openai_image_generation:
            raise HTTPException(status_code=400, detail="ENABLE_OPENAI_IMAGE_GENERATION=true is required")
        if self.settings.openai_image_dry_run:
            raise HTTPException(status_code=400, detail="OPENAI_IMAGE_DRY_RUN=false is required")
        if not self.settings.openai_api_key:
            raise HTTPException(status_code=400, detail="OPENAI_API_KEY is required for image generation")
        if self.settings.openai_image_require_structure_reference and not request.use_reference_images:
            raise HTTPException(
                status_code=400,
                detail="use_reference_images=true is required because structure reference input is mandatory for layout-preserving floorplans",
            )

    @staticmethod
    def _is_openai_image_timeout_error(exc: HTTPException) -> bool:
        detail = exc.detail
        if isinstance(detail, dict):
            error = str(detail.get("error") or "").lower()
            if error == "openai_image_timeout":
                return True
            message = str(detail.get("message") or "").lower()
            return "timed out" in message or "readtimeout" in message or "timeouterror" in message
        message = str(detail or "").lower()
        return "timed out" in message or "readtimeout" in message or "timeouterror" in message

    def _load_feedback(self, run_id: str, request: RegenerateWithFeedbackRequest) -> QAFeedbackResponse:
        if request.feedback_source == "latest":
            path = self._artifacts_dir(run_id) / "qa_feedback.json"
            if not path.exists():
                raise HTTPException(
                    status_code=400,
                    detail={"error": "qa_feedback_missing", "message": "qa_feedback.json is required before regeneration."},
                )
            return self._load_qa_feedback(path)

        relative_path = str(request.qa_feedback_path or "").replace("\\", "/").lstrip("/")
        path = self._resolve_relative_storage_path(relative_path)
        try:
            path.relative_to(self._safe_run_dir(run_id))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="qa_feedback_path must stay within the run workspace") from exc
        if not path.exists():
            raise HTTPException(
                status_code=400,
                detail={"error": "qa_feedback_missing", "message": "qa_feedback.json is required before regeneration."},
            )
        return self._load_qa_feedback(path)

    def _load_or_build_preview(self, metadata: RunMetadata) -> tuple[ImageGenerationRequestPreviewArtifact, str]:
        preview_path = self._artifacts_dir(metadata.run_id) / "image_generation_request_preview.json"
        if preview_path.exists():
            return self.preview_service.load_image_generation_request_preview(metadata.run_id), self._relative_artifact_path(
                metadata.run_id,
                "image_generation_request_preview.json",
            )

        prompt_package_path = self._artifacts_dir(metadata.run_id) / "prompt_package.json"
        if not prompt_package_path.exists():
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "source_prompt_missing",
                    "message": "prompt_package.json or image_generation_request_preview.json is required before regeneration.",
                },
            )

        prompt_package: PromptPackageArtifact = self.preview_service.load_prompt_package(metadata.run_id)
        render_plan: RenderPlanArtifact | None = self.preview_service.load_render_plan(metadata.run_id)
        interior_validated = self.preview_service.load_interior_analysis_validated(metadata.run_id)
        interior_source = self.preview_service.load_interior_analysis_source(metadata.run_id, interior_validated)
        preview = self.preview_service.build_request_preview(
            metadata.run_id,
            prompt_package,
            render_plan,
            metadata,
            interior_validated,
            interior_source,
        )
        return preview, self._relative_artifact_path(metadata.run_id, "prompt_package.json")

    def _select_reference_images(
        self,
        preview: ImageGenerationRequestPreviewArtifact,
        request: RegenerateWithFeedbackRequest,
    ) -> list[dict]:
        selected = [dict(item) for item in (preview.request_payload_preview.get("selected_reference_images") or preview.selected_reference_images or [])]
        if not selected:
            selected = [dict(item) for item in (preview.request_payload_preview.get("input_images") or [])]
        if not selected:
            raise HTTPException(
                status_code=400,
                detail="selected_reference_images must include role=structure_reference when use_reference_images=true",
            )

        structure = [item for item in selected if str(item.get("role") or "") == "structure_reference"]
        others = [item for item in selected if str(item.get("role") or "") != "structure_reference"]
        limited = structure + others[: max(0, request.max_reference_images - len(structure))]
        return limited[: max(1, request.max_reference_images)]

    def _build_regeneration_prompt(
        self,
        qa_feedback: QAFeedbackResponse,
        preview: ImageGenerationRequestPreviewArtifact,
        run_id: str,
    ) -> tuple[str, list[str], list[str]]:
        correction_plan = qa_feedback.correction_plan if isinstance(qa_feedback.correction_plan, dict) else {}
        prompt_guidance = self._normalize_list(correction_plan.get("prompt_guidance"))
        negative_guidance = self._normalize_list(correction_plan.get("negative_guidance"))
        room_function_guidance = self._load_room_function_guidance(run_id)
        selected_interior_filenames = preview.selected_interior_filenames or []
        issue_lines = [
            f"- {issue.issue_type}: {issue.description or issue.correction_instruction or issue.issue_type}"
            for issue in qa_feedback.issues
        ]
        lines = [
            "Preserve the exact layout from normalized_floorplan.png.",
            "normalized_floorplan.png is the primary structure reference.",
            "Correct the issues from manual QA feedback.",
            "Follow room_function_assignment functional roles.",
            "Use English labels only.",
            "Keep Japanese watercolor style with soft texture.",
            "Use interior references only as furniture and style cues.",
            "Do not copy mistakes from previous failed output.",
            "Customer style correction: use a brighter overall palette.",
            "Customer style correction: replace heavy black or dark wall fills with light neutral wall tones.",
            "Customer style correction: keep thin outlines if needed, but avoid dark filled wall masses.",
            "Furniture correction: correct furniture orientation without changing the floorplan.",
            "Furniture correction: TV faces sofa.",
            "Furniture correction: coffee table sits between sofa and TV when possible.",
            "Furniture correction: beds align naturally to walls.",
            "Furniture correction: dining furniture aligns neatly.",
            "Fixture correction: the washing machine must be in the Wash Room at the Wash / 洗 mark.",
            "Fixture correction: never place the washing machine outside the Wash Room.",
        ]
        lines.extend(prompt_guidance)
        lines.extend(room_function_guidance)
        lines.append("Manual QA issues to correct:")
        lines.extend(issue_lines or ["- Apply the latest QA correction plan."])
        if selected_interior_filenames:
            lines.append(f"Selected interior references: {', '.join(selected_interior_filenames)}.")
        if negative_guidance:
            lines.append("Negative guidance:")
            lines.extend(f"- {item}" for item in negative_guidance)
        return "\n".join(self._dedupe_keep_order(lines)), prompt_guidance, negative_guidance

    def _load_room_function_guidance(self, run_id: str) -> list[str]:
        path = self._artifacts_dir(run_id) / "render_plan.json"
        if not path.exists():
            return []
        try:
            render_plan = RenderPlanArtifact.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
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

    def _decode_and_save_regenerated_image(
        self,
        run_id: str,
        attempt: int,
        provider_response: dict,
        preview: ImageGenerationRequestPreviewArtifact,
    ) -> tuple[dict, list[str]]:
        raw_bytes = self.draft_service._extract_image_bytes(provider_response)
        raw_path = self._artifacts_dir(run_id) / f"generated_regeneration_{attempt}_raw.png"
        final_path = self._outputs_dir(run_id) / f"{run_id}_regenerated_{attempt}.png"
        try:
            raw_path.write_bytes(raw_bytes)
        except OSError as exc:
            raise HTTPException(status_code=500, detail="failed to save generated regeneration raw image") from exc

        postprocess = self.draft_service.postprocess_draft_image(run_id, raw_path, preview, final_path)
        quality = postprocess["quality"]
        return {
            "raw_image_path": self._relative_storage_path(raw_path),
            "output_image_path": self._relative_storage_path(final_path),
            "output_preview_url": f"/{self._relative_storage_path(final_path)}",
            "width": quality["width"],
            "height": quality["height"],
            "format": "png",
        }, []

    def _upload_regenerated_output_to_cloudinary(
        self,
        run_id: str,
        attempt: int,
        output_info: dict,
    ) -> tuple[dict, list[str]]:
        cloudinary_info: dict = {
            "enabled": bool(self.settings.cloudinary_enabled),
            "regenerated": {
                "enabled": bool(self.settings.cloudinary_enabled),
                "uploaded": False,
                "reason": "cloudinary_disabled" if not self.settings.cloudinary_enabled else "regenerated_upload_not_attempted",
            },
            "warnings": [],
        }
        warnings: list[str] = []
        if not self.settings.cloudinary_enabled:
            return cloudinary_info, warnings
        if not self.settings.cloudinary_upload_drafts:
            cloudinary_info["regenerated"] = {
                "enabled": True,
                "uploaded": False,
                "reason": "cloudinary_upload_drafts_disabled",
            }
            return cloudinary_info, warnings

        local_path = self._resolve_output_local_path(output_info.get("output_image_path"))
        if local_path is None or not local_path.exists():
            cloudinary_info["regenerated"] = {
                "enabled": True,
                "uploaded": False,
                "reason": "local_output_missing",
            }
            return cloudinary_info, warnings
        try:
            cloudinary_info["regenerated"] = self.cloudinary_service.upload_run_image(
                run_id=run_id,
                local_path=local_path,
                asset_kind="regenerated",
                public_id_suffix=str(attempt),
            )
        except HTTPException as exc:
            message = exc.detail if isinstance(exc.detail, str) else "Cloudinary upload failed"
            warning = f"Cloudinary regenerated upload failed: {message}"
            cloudinary_info["warnings"].append(warning)
            cloudinary_info["regenerated"] = {
                "enabled": True,
                "uploaded": False,
                "reason": "upload_failed",
                "error": message,
            }
            if self.settings.cloudinary_upload_required:
                raise HTTPException(status_code=502, detail=warning) from exc
            warnings.append(warning)
        return cloudinary_info, warnings

    def _next_attempt_number(self, run_id: str) -> int:
        artifacts_dir = self._artifacts_dir(run_id)
        max_attempt = 0
        for path in artifacts_dir.iterdir():
            if not path.is_file():
                continue
            match = self.ATTEMPT_PATTERN.fullmatch(path.name)
            if not match:
                continue
            max_attempt = max(max_attempt, int(match.group(1)))
        return max_attempt + 1

    def _highest_severity(self, qa_feedback: QAFeedbackResponse) -> str:
        rank = {"low": 1, "medium": 2, "high": 3}
        highest = "low"
        for issue in qa_feedback.issues:
            if rank.get(issue.severity, 0) > rank.get(highest, 0):
                highest = issue.severity
        return highest

    @staticmethod
    def _normalize_list(value) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()]

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

    def _load_qa_feedback(self, path: Path) -> QAFeedbackResponse:
        try:
            return QAFeedbackResponse.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=500, detail="failed to read qa_feedback artifact") from exc

    def _load_regeneration_artifact(self, path: Path) -> RegenerationAttemptResponse:
        try:
            return RegenerationAttemptResponse.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=500, detail="failed to read regeneration artifact") from exc

    def _load_metadata(self, run_id: str) -> RunMetadata:
        metadata_path = self._safe_run_dir(run_id) / "run_metadata.json"
        try:
            return RunMetadata.model_validate_json(metadata_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=500, detail="failed to read run metadata") from exc

    def _resolve_output_local_path(self, relative_storage_path: str | None) -> Path | None:
        if not str(relative_storage_path or "").strip():
            return None
        storage_relative = str(relative_storage_path).replace("\\", "/").strip().lstrip("/")
        base_root = self.storage_dir.parent.resolve()
        candidate = (base_root / storage_relative).resolve()
        try:
            candidate.relative_to(base_root)
        except ValueError:
            return None
        return candidate

    def _resolve_relative_storage_path(self, relative_storage_path: str) -> Path:
        normalized = relative_storage_path.replace("\\", "/").lstrip("/")
        candidate = (self.storage_dir.parent / normalized).resolve()
        try:
            candidate.relative_to(self.storage_dir.parent.resolve())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid relative path") from exc
        return candidate

    def _artifacts_dir(self, run_id: str) -> Path:
        return self._safe_run_dir(run_id) / "artifacts"

    def _outputs_dir(self, run_id: str) -> Path:
        return self._safe_run_dir(run_id) / "outputs"

    def _safe_run_dir(self, run_id: str) -> Path:
        run_dir = (self.storage_runs_dir / run_id).resolve()
        try:
            run_dir.relative_to(self.storage_runs_dir.resolve())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid run path") from exc
        if not run_dir.exists():
            raise HTTPException(status_code=404, detail="run not found")
        return run_dir

    @staticmethod
    def _relative_artifact_path(run_id: str, filename: str) -> str:
        return f"storage/runs/{run_id}/artifacts/{filename}"

    def _relative_storage_path(self, path: Path) -> str:
        return path.relative_to(self.storage_dir.parent).as_posix()
