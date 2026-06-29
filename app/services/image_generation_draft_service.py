from __future__ import annotations

import base64
import json
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
import time

import requests
from fastapi import HTTPException
from PIL import Image

from app.config import get_settings
from app.schemas.run import (
    ImageGenerationDraftArtifact,
    ImageGenerationDraftRequest,
    ImageGenerationDraftSummary,
    ImageGenerationRequestPreviewArtifact,
    RunMetadata,
)
from app.services.cloudinary_storage_service import CloudinaryStorageService


class ImageGenerationDraftService:
    def __init__(self, storage_dir: Path, storage_runs_dir: Path) -> None:
        self.storage_dir = storage_dir
        self.storage_runs_dir = storage_runs_dir
        self.settings = get_settings()
        self.cloudinary_service = CloudinaryStorageService()

    def generate_image_draft(
        self,
        metadata: RunMetadata,
        request: ImageGenerationDraftRequest,
    ) -> ImageGenerationDraftArtifact:
        preview = self.load_image_generation_request_preview(metadata.run_id)
        _, errors = self.validate_generation_guards(metadata.run_id, request, preview, metadata)
        if errors:
            detail = errors[0]
            if detail.startswith("unsupported_provider:"):
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": "unsupported_provider",
                        "message": "Only provider=openai is supported.",
                    },
                )
            raise HTTPException(status_code=400, detail=detail)

        selected_reference_images = self.select_reference_images(
            preview,
            max_reference_images=request.max_reference_images or self.settings.openai_image_max_input_images,
            use_reference_images=request.use_reference_images,
        )
        selected_reference_files, structure_reference_path = self.require_structure_reference_image(
            metadata.run_id,
            request,
            preview,
            selected_reference_images,
        )
        openai_input_images, input_warnings = self.validate_openai_input_images(selected_reference_files)
        payload = self.build_openai_image_request(preview, request, metadata)
        try:
            provider_response, provider_warnings, openai_image_attempts = self.call_openai_image_api(
                payload,
                selected_reference_images,
                openai_input_images,
                structure_reference_path,
            )
        except HTTPException as exc:
            if self._is_retryable_openai_image_http_exception(exc):
                self._write_failed_image_generation_draft_artifact(
                    metadata.run_id,
                    preview,
                    payload,
                    selected_reference_images,
                    openai_input_images,
                    exc,
                )
            raise
        output_info, output_warnings = self.decode_and_save_generated_image(metadata.run_id, provider_response, preview)
        cloudinary_info, cloudinary_warnings = self.upload_output_images_to_cloudinary(metadata.run_id, output_info)

        warnings = self._dedupe_keep_order(
            list(preview.preview_warnings)
            + list(preview.upstream_warnings)
            + input_warnings
            + provider_warnings
            + output_warnings
            + cloudinary_warnings
            + ["Human visual QA is still required for this draft generation."]
            + ["Postprocess was required to reach final delivery size 1200x1200."]
        )
        usage_summary = self._extract_usage(provider_response)
        if not usage_summary.get("raw_usage_available"):
            warnings.append("Provider usage metrics were unavailable in the response.")
            warnings = self._dedupe_keep_order(warnings)
        draft_status = "generated_with_warnings" if warnings else "generated"
        artifact = self.build_image_generation_draft_artifact(
            metadata.run_id,
            preview,
            payload,
            provider_response,
            output_info,
            cloudinary_info,
            warnings,
            [],
            selected_reference_images,
            openai_input_images,
            openai_image_attempts,
            draft_status,
        )
        self.write_image_generation_draft(metadata.run_id, artifact)
        return artifact

    def load_image_generation_request_preview(self, run_id: str) -> ImageGenerationRequestPreviewArtifact:
        path = self._artifacts_dir(run_id) / "image_generation_request_preview.json"
        if not path.exists():
            raise HTTPException(status_code=400, detail="Run image generation request preview before draft generation")
        try:
            return ImageGenerationRequestPreviewArtifact.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"invalid image_generation_request_preview.json: {exc}") from exc

    def load_image_generation_draft(self, run_id: str) -> ImageGenerationDraftArtifact:
        path = self._artifacts_dir(run_id) / "image_generation_draft.json"
        if not path.exists():
            raise HTTPException(status_code=404, detail="image_generation_draft artifact not found")
        try:
            return ImageGenerationDraftArtifact.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=500, detail="failed to read image_generation_draft artifact") from exc

    def validate_generation_guards(
        self,
        run_id: str,
        request: ImageGenerationDraftRequest,
        preview: ImageGenerationRequestPreviewArtifact,
        metadata: RunMetadata,
    ) -> tuple[bool, list[str]]:
        errors: list[str] = []
        if request.provider != "openai":
            errors.append("unsupported_provider: Only provider=openai is supported.")
        if not request.confirm_generation:
            errors.append("confirm_generation=true is required to call image generation provider")
        if not self.settings.enable_openai_image_generation:
            errors.append("ENABLE_OPENAI_IMAGE_GENERATION=true is required")
        if self.settings.openai_image_dry_run:
            errors.append("OPENAI_IMAGE_DRY_RUN=false is required")
        if not self.settings.openai_api_key:
            errors.append("OPENAI_API_KEY is required for image generation")
        if not preview.request_payload_preview.get("provider_size_supported", False):
            errors.append("provider requested size is not supported")
        if not str(preview.request_payload_preview.get("prompt") or "").strip():
            errors.append("image generation preview prompt is empty")
        if not (self._safe_run_dir(run_id) / "run_metadata.json").exists():
            errors.append("run metadata not found")
        if self.settings.openai_image_require_structure_reference and not request.use_reference_images:
            errors.append("use_reference_images=true is required because structure reference input is mandatory for layout-preserving floorplans")
        if not self.settings.openai_image_require_structure_reference and not request.use_reference_images and not self.settings.openai_image_allow_prompt_only:
            errors.append("Prompt-only generation is disabled for layout-preserving floorplans")
        if self._get_prompt_mode(preview) == "strict_layout_test":
            selected_reference_images = list(preview.request_payload_preview.get("selected_reference_images") or [])
            if not selected_reference_images:
                errors.append("strict_layout_test requires selected_reference_images in the request preview")
            primary_reference = selected_reference_images[0] if selected_reference_images else {}
            if str(primary_reference.get("role") or "") != "structure_reference":
                errors.append("strict_layout_test requires normalized_floorplan.png as the primary structure reference")
            if str(preview.request_payload_preview.get("primary_structure_reference") or "") != "normalized_floorplan.png":
                errors.append("strict_layout_test requires primary_structure_reference=normalized_floorplan.png")
            if not bool(preview.request_payload_preview.get("strict_layout_test_enabled")):
                errors.append("strict_layout_test preview metadata is missing strict_layout_test_enabled=true")
        if self._get_prompt_mode(preview) == "strict_layout_with_interior_guidance":
            selected_reference_images = list(preview.request_payload_preview.get("selected_reference_images") or [])
            if not selected_reference_images:
                errors.append("strict_layout_with_interior_guidance requires selected_reference_images in the request preview")
            primary_reference = selected_reference_images[0] if selected_reference_images else {}
            if str(primary_reference.get("role") or "") != "structure_reference":
                errors.append("strict_layout_with_interior_guidance requires normalized_floorplan.png as the primary structure reference")
            if str(preview.request_payload_preview.get("primary_structure_reference") or "") != "normalized_floorplan.png":
                errors.append("strict_layout_with_interior_guidance requires primary_structure_reference=normalized_floorplan.png")
            if not bool(preview.request_payload_preview.get("strict_layout_test_enabled")):
                errors.append("strict_layout_with_interior_guidance preview metadata is missing strict_layout_test_enabled=true")
        return (not errors), errors

    def build_openai_image_request(
        self,
        preview: ImageGenerationRequestPreviewArtifact,
        request: ImageGenerationDraftRequest,
        metadata: RunMetadata,
    ) -> dict:
        prompt_mode = self._get_prompt_mode(preview)
        return {
            "model": self.settings.openai_image_model,
            "prompt": str(preview.request_payload_preview.get("prompt") or ""),
            "size": str(preview.request_payload_preview.get("size") or self.settings.openai_image_provider_size),
            "quality": self.settings.openai_image_quality,
            "n": 1,
            "output_format": request.output_format or self.settings.openai_image_output_format,
            "prompt_mode": prompt_mode,
            "strict_layout_test_enabled": prompt_mode in {"strict_layout_test", "strict_layout_with_interior_guidance"},
            "primary_structure_reference": str(preview.request_payload_preview.get("primary_structure_reference") or "normalized_floorplan.png"),
            "layout_preservation_priority": str(preview.request_payload_preview.get("layout_preservation_priority") or "high"),
            "long_prompt_disabled": bool(preview.request_payload_preview.get("long_prompt_disabled")),
        }

    def _build_prompt(self, preview: ImageGenerationRequestPreviewArtifact, prompt_mode: str) -> str:
        if prompt_mode == "strict_layout_test":
            return str(preview.request_payload_preview.get("prompt") or "")
        if prompt_mode == "strict_layout_with_interior_guidance":
            summary = (preview.request_payload_preview.get("interior_guidance_summary") or {})
            floor_tone = summary.get("floor_tone") or "unknown"
            room_summary = ", ".join([str(value).strip().lower() for value in (summary.get("room_summary") or []) if str(value).strip() and str(value).strip().lower() != "unknown"]) or "none"
            style_cues = ", ".join([str(value).strip().lower() for value in (summary.get("style_cues") or []) if str(value).strip() and str(value).strip().lower() != "unknown"]) or "none"
            color_cues = ", ".join([str(value).strip().lower() for value in (summary.get("color_cues") or []) if str(value).strip() and str(value).strip().lower() != "unknown"]) or "none"
            furniture_arrangement_rules_applied = bool(preview.request_payload_preview.get("furniture_arrangement_rules_applied"))
            living_room_arrangement_rule = str(preview.request_payload_preview.get("living_room_arrangement_rule") or "").strip()
            return "\n".join(
                [
                    "Preserve the exact layout from normalized_floorplan.png.",
                    "Preserve walls, doors, windows, balcony, room positions, and wet areas exactly.",
                    "Use English labels only.",
                    "Use selected interior references only for furniture type, color, and style.",
                    "Simplify or omit furniture if it conflicts with the layout.",
                    "Apply furniture arrangement only when it does not conflict with the floorplan.",
                    "Use a bright, airy Japanese watercolor floorplan style. Prefer light warm beige, soft greige, pale wood, and neutral tones. Avoid heavy dark color masses.",
                    "Do not render walls, room dividers, wet-area blocks, or partitions as large black or dark charcoal filled areas. Use light neutral wall tones with clean thin outlines where needed.",
                    "Living Room arrangement: if sofa and TV are both present and the floorplan allows, arrange them facing each other with a coffee table between them.",
                    "Keep sofa, TV, and coffee table inside the Living Room.",
                    "If the room is too small or the layout does not allow the arrangement, simplify or omit furniture rather than changing the floorplan.",
                    "Bedroom guidance: the Bed Room must contain either one bed or two single beds only. Do not draw more than two beds. Do not draw bunk beds unless the selected bedroom reference clearly shows bunk beds. If the selected bedroom reference shows two separate beds, draw two single beds. If the selected bedroom reference is unclear, draw one simple bed. Keep bed(s) inside the Bed Room only. Do not resize the Bed Room or move walls, doors, or windows to fit beds. If there is not enough space, simplify the bed drawing rather than changing the floorplan.",
                    "Orient furniture naturally according to room geometry. TV should face the sofa. Coffee table should be between sofa and TV when possible. Beds should align naturally to room walls with headboards against a wall. Dining table and chairs should align neatly and should not block circulation.",
                    "The washing machine must be placed in the Wash Room at the location marked Wash / 洗. Do not place the washing machine in Kitchen, Living Room, Bed Room, Bath Room, Toilet, or Entrance.",
                    f"Interior summary: {summary.get('summary') or 'none'}.",
                    f"Floor tone: {floor_tone}.",
                    f"Interior room guidance: {room_summary}.",
                    f"Style cues: {style_cues}.",
                    f"Color cues: {color_cues}.",
                    f"Furniture arrangement rules applied: {str(furniture_arrangement_rules_applied).lower()}.",
                    f"Living room arrangement rule: {living_room_arrangement_rule or 'none'}.",
                    f"Bedroom bed count rule applied: {str(bool(preview.request_payload_preview.get('bedroom_bed_count_rule_applied'))).lower()}.",
                    f"Bedroom bed count rule: {str(preview.request_payload_preview.get('bedroom_bed_count_rule') or 'none')}.",
                    f"Prompt source: {preview.request_payload_preview.get('prompt_package_status') or 'created_with_warnings'}.",
                ]
            )
        return str(preview.request_payload_preview.get("prompt") or "")

    def select_reference_images(
        self,
        preview: ImageGenerationRequestPreviewArtifact,
        max_reference_images: int,
        use_reference_images: bool,
    ) -> list[dict]:
        if not use_reference_images:
            return []

        prompt_mode = self._get_prompt_mode(preview)
        preview_selected = [dict(item) for item in (preview.request_payload_preview.get("selected_reference_images") or [])]
        if prompt_mode == "strict_layout_test" and preview_selected:
            return preview_selected[: min(max(0, max_reference_images), 3)]
        if prompt_mode == "strict_layout_with_interior_guidance" and preview_selected:
            return preview_selected[: min(max(0, max_reference_images), 4)]
        if prompt_mode == "strict_layout_with_interior_guidance" and preview_selected:
            return preview_selected[: min(max(0, max_reference_images), 4)]

        selected: list[dict] = []
        input_images = list(preview.request_payload_preview.get("input_images") or [])

        def add_first_match(predicate) -> None:
            for image in input_images:
                if predicate(image) and image not in selected:
                    selected.append(image)
                    return

        add_first_match(lambda image: image.get("role") == "structure_reference")
        add_first_match(lambda image: image.get("reference_type") == "ideal")
        add_first_match(lambda image: image.get("reference_type") == "acceptable")
        if prompt_mode != "strict_layout_test":
            add_first_match(lambda image: image.get("reference_type") == "ng")

        if prompt_mode != "strict_layout_test":
            interior_added = 0
            for image in input_images:
                if image.get("role") != "interior_photo":
                    continue
                if image in selected:
                    continue
                selected.append(image)
                interior_added += 1
                if interior_added >= 2:
                    break

        return selected[: max(0, max_reference_images)]

    def require_structure_reference_image(
        self,
        run_id: str,
        request: ImageGenerationDraftRequest,
        preview: ImageGenerationRequestPreviewArtifact,
        selected_reference_images: list[dict],
    ) -> tuple[list[dict], Path | None]:
        if not request.use_reference_images:
            if not self.settings.openai_image_require_structure_reference:
                return [], None
            raise HTTPException(
                status_code=400,
                detail="use_reference_images=true is required because structure reference input is mandatory for layout-preserving floorplans",
            )

        structure_reference = next(
            (image for image in selected_reference_images if str(image.get("role") or "") == "structure_reference"),
            None,
        )
        if structure_reference is None:
            raise HTTPException(
                status_code=400,
                detail="selected_reference_images must include role=structure_reference when use_reference_images=true",
            )
        prompt_mode = self._get_prompt_mode(preview)
        if prompt_mode in {"strict_layout_test", "strict_layout_with_interior_guidance"}:
            structure_name = str(structure_reference.get("relative_path") or "").replace("\\", "/")
            if not structure_name.endswith("artifacts/normalized_floorplan.png"):
                raise HTTPException(
                    status_code=400,
                    detail=f"{prompt_mode} requires normalized_floorplan.png as the selected structure reference",
                )

        resolved_images = self._resolve_selected_reference_images(run_id, selected_reference_images)
        structure_reference_entry = next(
            (item for item in resolved_images if str(item.get("role") or "") == "structure_reference"),
            None,
        )
        structure_path = Path(structure_reference_entry["path"]) if structure_reference_entry is not None else None
        if structure_path is None or not structure_path.exists():
            raise HTTPException(
                status_code=400,
                detail="Selected structure_reference file does not exist locally",
            )

        return resolved_images, structure_path

    def call_openai_image_api(
        self,
        payload: dict,
        selected_images: list[dict],
        selected_image_files: list[dict],
        structure_reference_path: Path | None,
    ) -> tuple[dict, list[str], list[dict]]:
        warnings: list[str] = []
        if structure_reference_path is not None:
            if not selected_image_files:
                raise HTTPException(
                    status_code=501,
                    detail="Image reference input is required; prompt-only generation is disabled for layout-preserving floorplans.",
                )
            provider_response, retry_warnings, attempts = self._run_openai_image_request_with_retry(
                request_kind="edit",
                request_fn=lambda attempt_timeout: self._call_openai_image_edit_api_once(
                    payload,
                    selected_image_files,
                    attempt_timeout,
                ),
                input_images=selected_image_files,
            )
            warnings.extend(retry_warnings)
            return provider_response, warnings, attempts
        if selected_images and not self.settings.openai_image_allow_prompt_only:
            raise HTTPException(
                status_code=501,
                detail="Image reference input is required; prompt-only generation is disabled for layout-preserving floorplans.",
            )

        provider_response, retry_warnings, attempts = self._run_openai_image_request_with_retry(
            request_kind="generation",
            request_fn=lambda attempt_timeout: self._call_openai_image_generation_api_once(
                payload,
                attempt_timeout,
            ),
            input_images=selected_images,
        )
        warnings.extend(retry_warnings)
        return provider_response, warnings, attempts

    def _call_openai_image_generation_api_once(self, payload: dict, timeout_seconds: int) -> dict:
        try:
            response = requests.post(
                "https://api.openai.com/v1/images/generations",
                headers={
                    "Authorization": f"Bearer {self.settings.openai_api_key}",
                },
                json=payload,
                timeout=timeout_seconds,
            )
        except requests.RequestException as exc:
            if self._is_retryable_openai_image_exception(exc):
                raise
            raise HTTPException(
                status_code=502,
                detail=f"OpenAI image generation request failed: {exc.__class__.__name__}",
            ) from exc

        if response.status_code >= 400:
            safe_detail = self._extract_safe_provider_error(response)
            raise HTTPException(status_code=502, detail=f"OpenAI image generation failed: {safe_detail}")

        try:
            return response.json()
        except ValueError as exc:
            raise HTTPException(status_code=502, detail="invalid OpenAI image generation response") from exc

    def _call_openai_image_edit_api_once(self, payload: dict, selected_image_files: list[dict], timeout_seconds: int) -> dict:
        form_data = {
            "model": payload["model"],
            "prompt": payload["prompt"],
            "size": payload["size"],
            "quality": payload.get("quality") or self.settings.openai_image_quality,
            "n": str(payload.get("n", 1)),
        }
        if payload.get("output_format"):
            form_data["output_format"] = payload["output_format"]

        try:
            with ExitStack() as stack:
                multipart_files = []
                for image_info in selected_image_files:
                    image_path = Path(image_info["path"])
                    file_handle = stack.enter_context(image_path.open("rb"))
                    multipart_files.append(
                        (
                            "image[]",
                            (
                                str(image_info.get("filename") or image_path.name),
                                file_handle,
                                str(image_info["mime_type"]),
                            ),
                        )
                    )

                response = requests.post(
                    "https://api.openai.com/v1/images/edits",
                    headers={
                        "Authorization": f"Bearer {self.settings.openai_api_key}",
                    },
                    data=form_data,
                    files=multipart_files,
                    timeout=timeout_seconds,
                )
        except requests.RequestException as exc:
            if self._is_retryable_openai_image_exception(exc):
                raise
            raise HTTPException(
                status_code=502,
                detail=f"OpenAI image edit request failed: {exc.__class__.__name__}",
            ) from exc
        except OSError as exc:
            raise HTTPException(status_code=400, detail=f"failed to open reference image for provider request: {exc}") from exc

        if response.status_code == 404:
            raise HTTPException(
                status_code=501,
                detail="Image reference input is required; prompt-only generation is disabled for layout-preserving floorplans.",
            )
        if response.status_code >= 400:
            safe_detail = self._extract_safe_provider_error(response)
            raise HTTPException(status_code=502, detail=f"OpenAI image generation failed: {safe_detail}")

        try:
            return response.json()
        except ValueError as exc:
            raise HTTPException(status_code=502, detail="invalid OpenAI image generation response") from exc

    def _run_openai_image_request_with_retry(
        self,
        *,
        request_kind: str,
        request_fn,
        input_images: list[dict],
    ) -> tuple[dict, list[str], list[dict]]:
        timeout_seconds = max(1, int(self.settings.openai_image_timeout_seconds))
        max_attempts = max(1, int(self.settings.openai_image_retry_attempts))
        delay_seconds = max(0, int(self.settings.openai_image_retry_delay_seconds))
        attempts: list[dict] = []
        warnings: list[str] = []

        for attempt in range(1, max_attempts + 1):
            try:
                provider_response = request_fn(timeout_seconds)
                attempts.append(
                    self._build_openai_image_attempt_record(
                        attempt=attempt,
                        status="completed",
                        timeout_seconds=timeout_seconds,
                        input_images=input_images,
                    )
                )
                return provider_response, warnings, attempts
            except HTTPException as exc:
                if not self._is_retryable_openai_image_http_exception(exc) or attempt >= max_attempts:
                    if self._is_retryable_openai_image_http_exception(exc):
                        attempts.append(
                            self._build_openai_image_attempt_record(
                                attempt=attempt,
                                status="failed",
                                timeout_seconds=timeout_seconds,
                                input_images=input_images,
                                error_type="openai_image_timeout",
                                message="OpenAI image generation timed out after retry attempts.",
                            )
                        )
                        raise HTTPException(
                            status_code=504,
                            detail={
                                "error": "openai_image_timeout",
                                "message": "OpenAI image generation timed out after retry attempts.",
                                "details": {
                                    "attempts": len(attempts),
                                    "timeout_seconds": timeout_seconds,
                                },
                                "openai_image_attempts": attempts,
                            },
                        ) from exc
                    raise

                attempts.append(
                    self._build_openai_image_attempt_record(
                        attempt=attempt,
                        status="failed",
                        timeout_seconds=timeout_seconds,
                        input_images=input_images,
                        error_type=exc.detail.get("error") if isinstance(exc.detail, dict) else exc.__class__.__name__,
                        message=self._shorten_error_detail(exc.detail),
                    )
                )
                warnings.append(
                    f"OpenAI {request_kind} attempt {attempt} failed with {exc.__class__.__name__}; retrying."
                )
                if delay_seconds:
                    time.sleep(delay_seconds)
                continue
            except (requests.ReadTimeout, requests.Timeout, requests.ConnectionError, TimeoutError) as exc:
                error_type = exc.__class__.__name__
                attempts.append(
                    self._build_openai_image_attempt_record(
                        attempt=attempt,
                        status="failed",
                        timeout_seconds=timeout_seconds,
                        input_images=input_images,
                        error_type=error_type,
                        message=str(exc)[:200],
                    )
                )
                if attempt >= max_attempts:
                    raise HTTPException(
                        status_code=504,
                        detail={
                            "error": "openai_image_timeout",
                            "message": "OpenAI image generation timed out after retry attempts.",
                            "details": {
                                "attempts": len(attempts),
                                "timeout_seconds": timeout_seconds,
                            },
                            "openai_image_attempts": attempts,
                        },
                    ) from exc
                warnings.append(
                    f"OpenAI {request_kind} attempt {attempt} failed with {error_type}; retrying."
                )
                if delay_seconds:
                    time.sleep(delay_seconds)

        raise HTTPException(
            status_code=504,
            detail={
                "error": "openai_image_timeout",
                "message": "OpenAI image generation timed out after retry attempts.",
                "details": {
                    "attempts": len(attempts),
                    "timeout_seconds": timeout_seconds,
                },
                "openai_image_attempts": attempts,
            },
        )

    @staticmethod
    def _is_retryable_openai_image_exception(exc: Exception) -> bool:
        return isinstance(
            exc,
            (
                requests.Timeout,
                requests.ReadTimeout,
                requests.ConnectionError,
                TimeoutError,
            ),
        )

    @staticmethod
    def _is_retryable_openai_image_http_exception(exc: HTTPException) -> bool:
        detail = exc.detail
        if isinstance(detail, dict):
            error = str(detail.get("error") or "").lower()
            message = str(detail.get("message") or "").lower()
            return error == "openai_image_timeout" or "timed out" in message or "readtimeout" in message
        message = str(detail or "").lower()
        return "timed out" in message or "readtimeout" in message or "timeouterror" in message

    @staticmethod
    def _shorten_error_detail(detail) -> str:
        if isinstance(detail, dict):
            message = detail.get("message") or detail.get("detail") or detail.get("error")
            return str(message or "HTTP error")[:200]
        return str(detail or "HTTP error")[:200]

    def _build_openai_image_attempt_record(
        self,
        *,
        attempt: int,
        status: str,
        timeout_seconds: int,
        input_images: list[dict],
        error_type: str | None = None,
        message: str | None = None,
    ) -> dict:
        return {
            "attempt": attempt,
            "status": status,
            "error_type": error_type,
            "message": message,
            "timeout_seconds": timeout_seconds,
            "input_image_count": len(input_images),
            "input_images": [
                {
                    "path": str(item.get("path") or ""),
                    "filename": str(item.get("filename") or Path(str(item.get("path") or "")).name),
                    "mime_type": str(item.get("mime_type") or ""),
                    "size_bytes": item.get("size_bytes"),
                }
                for item in input_images
            ],
        }

    def _resolve_selected_reference_images(self, run_id: str, selected_reference_images: list[dict]) -> list[dict]:
        resolved: list[dict] = []
        for image_payload in selected_reference_images:
            path = self._resolve_reference_image_path(run_id, image_payload)
            if path is None or not path.exists():
                if str(image_payload.get("role") or "") == "structure_reference":
                    raise HTTPException(
                        status_code=400,
                        detail="Selected structure_reference file does not exist locally",
                    )
                continue
            resolved.append(
                {
                    **image_payload,
                    "path": str(path),
                }
            )
        return resolved

    def validate_openai_input_images(self, selected_reference_files: list[dict]) -> tuple[list[dict], list[str]]:
        validated: list[dict] = []
        warnings: list[str] = []
        for image_info in selected_reference_files:
            validated_image = self._validate_openai_input_image(image_info)
            validated.append(validated_image)
        return validated, warnings

    def _validate_openai_input_image(self, image_info: dict) -> dict:
        image_path = Path(str(image_info.get("path") or ""))
        role = str(image_info.get("role") or "reference_image")
        if not image_path.exists():
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "invalid_openai_image_input",
                    "message": "Reference image file does not exist.",
                    "path": str(image_path),
                    "detected_mime_type": None,
                },
            )
        try:
            size_bytes = image_path.stat().st_size
        except OSError as exc:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "invalid_openai_image_input",
                    "message": "Reference image could not be inspected.",
                    "path": str(image_path),
                    "detected_mime_type": None,
                },
            ) from exc
        if size_bytes <= 0:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "invalid_openai_image_input",
                    "message": "Reference image file is empty.",
                    "path": str(image_path),
                    "detected_mime_type": None,
                },
            )

        try:
            mime_type = self.detect_image_mime_type(image_path)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "invalid_openai_image_input",
                    "message": "Reference image has unsupported MIME type or extension.",
                    "path": str(image_path),
                    "detected_mime_type": "application/octet-stream",
                },
            ) from exc

        filename = image_path.name
        if "." not in filename:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "invalid_openai_image_input",
                    "message": "Reference image filename must include a supported extension.",
                    "path": str(image_path),
                    "detected_mime_type": mime_type,
                },
            )

        return {
            **image_info,
            "path": str(image_path),
            "filename": filename,
            "mime_type": mime_type,
            "role": role,
            "size_bytes": size_bytes,
            "supported_by_openai": True,
        }

    def _resolve_reference_image_path(self, run_id: str, image_payload: dict) -> Path | None:
        relative_path = str(image_payload.get("relative_path") or "").strip()
        if not relative_path:
            return None

        run_dir = self._safe_run_dir(run_id)
        storage_root = self.storage_dir.parent.resolve()
        candidates = []
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
    def detect_image_mime_type(path: Path) -> str:
        suffix = path.suffix.lower()
        if suffix in {".jpg", ".jpeg"}:
            return "image/jpeg"
        if suffix == ".png":
            return "image/png"
        if suffix == ".webp":
            return "image/webp"
        raise ValueError(f"Unsupported image format for OpenAI image input: {path}")

    def decode_and_save_generated_image(
        self,
        run_id: str,
        openai_response: dict,
        preview: ImageGenerationRequestPreviewArtifact,
    ) -> tuple[dict, list[str]]:
        raw_bytes = self._extract_image_bytes(openai_response)
        artifacts_dir = self._artifacts_dir(run_id)
        outputs_dir = self._outputs_dir(run_id)
        raw_path = artifacts_dir / "generated_draft_raw.png"
        final_path = outputs_dir / f"{run_id}_draft.png"

        try:
            raw_path.write_bytes(raw_bytes)
        except OSError as exc:
            raise HTTPException(status_code=500, detail="failed to save generated_draft_raw.png") from exc

        postprocess = self.postprocess_draft_image(run_id, raw_path, preview, final_path)
        warnings: list[str] = []
        quality = postprocess["quality"]
        return {
            "raw_image_path": self._relative_storage_path(raw_path),
            "raw_image_preview_url": f"/{self._relative_storage_path(raw_path)}",
            "draft_image_path": self._relative_storage_path(final_path),
            "draft_image_preview_url": f"/{self._relative_storage_path(final_path)}",
            "output_url": f"/{self._relative_storage_path(final_path)}",
            "preview_url": f"/{self._relative_storage_path(final_path)}",
            "width": quality["width"],
            "height": quality["height"],
            "format": "png",
            "postprocess": postprocess,
        }, warnings

    def postprocess_draft_image(
        self,
        run_id: str,
        raw_image_path: Path,
        preview: ImageGenerationRequestPreviewArtifact,
        final_path: Path,
    ) -> dict:
        final_size = str(
            preview.target_output.get("final_delivery_size")
            or self.settings.openai_image_final_output_size
            or "1200x1200"
        )
        target_width, target_height = self._parse_size(final_size)
        try:
            with Image.open(raw_image_path) as image:
                converted = image.convert("RGB")
                resized = converted.resize((target_width, target_height), Image.Resampling.LANCZOS)
                final_path.parent.mkdir(parents=True, exist_ok=True)
                resized.save(final_path, format="PNG")
        except OSError as exc:
            raise HTTPException(status_code=500, detail="failed to postprocess generated draft image") from exc

        return {
            "required": True,
            "postprocess_done": True,
            "resize_or_pad_to": final_size,
            "source_provider_size": str(preview.request_payload_preview.get("size") or self.settings.openai_image_provider_size),
            "quality": {
                "width": target_width,
                "height": target_height,
            },
        }

    def build_image_generation_draft_artifact(
        self,
        run_id: str,
        preview: ImageGenerationRequestPreviewArtifact,
        payload: dict,
        openai_response: dict,
        output_info: dict,
        cloudinary_info: dict,
        warnings: list[str],
        errors: list[str],
        selected_reference_images: list[dict],
        openai_input_images: list[dict],
        openai_image_attempts: list[dict],
        draft_status: str,
    ) -> ImageGenerationDraftArtifact:
        usage = self._extract_usage(openai_response)
        postprocess = output_info.get("postprocess") or {}
        prompt_mode = self._get_prompt_mode(preview)
        return ImageGenerationDraftArtifact(
            run_id=run_id,
            generated_at=datetime.now(timezone.utc),
            draft_status=draft_status,
            provider={
                "provider_name": "openai",
                "model": payload["model"],
                "api_call_performed": True,
                "request_sent": True,
                "prompt_mode": prompt_mode,
                "strict_layout_test_enabled": prompt_mode == "strict_layout_test",
            },
            source={
                "image_generation_request_preview_artifact": self._relative_artifact_path(run_id, "image_generation_request_preview.json"),
                "prompt_package_artifact": self._relative_artifact_path(run_id, "prompt_package.json"),
                "primary_structure_reference": str(payload.get("primary_structure_reference") or "normalized_floorplan.png"),
            },
            request={
                "provider_size": payload["size"],
                "final_delivery_size": str(
                    preview.target_output.get("final_delivery_size") or self.settings.openai_image_final_output_size
                ),
                "quality": payload.get("quality"),
                "output_format": payload.get("output_format"),
                "prompt_mode": prompt_mode,
                "strict_layout_test_enabled": prompt_mode == "strict_layout_test",
                "primary_structure_reference": str(payload.get("primary_structure_reference") or "normalized_floorplan.png"),
                "layout_preservation_priority": str(payload.get("layout_preservation_priority") or "high"),
                "long_prompt_disabled": bool(payload.get("long_prompt_disabled")),
                "selected_reference_images": selected_reference_images,
                "prompt_char_count": len(payload.get("prompt") or ""),
            },
            outputs={
                "raw_image_path": output_info["raw_image_path"],
                "raw_image_preview_url": output_info.get("raw_image_preview_url"),
                "draft_image_path": output_info["draft_image_path"],
                "draft_image_preview_url": output_info["draft_image_preview_url"],
                "output_image_path": output_info["draft_image_path"],
                "output_url": output_info.get("output_url"),
                "preview_url": output_info.get("preview_url"),
                "cloudinary_url": cloudinary_info.get("draft", {}).get("secure_url"),
                "public_output_url": cloudinary_info.get("draft", {}).get("secure_url"),
                "width": output_info["width"],
                "height": output_info["height"],
                "format": output_info["format"],
            },
            postprocess={
                "required": bool(postprocess.get("required", True)),
                "postprocess_done": bool(postprocess.get("postprocess_done", False)),
                "resize_or_pad_to": postprocess.get("resize_or_pad_to"),
                "source_provider_size": postprocess.get("source_provider_size"),
            },
            usage=usage,
            quality={
                "needs_human_review": True,
                "layout_accuracy_not_verified": True,
                "structure_guard_prompt_used": True,
                "image_generation_done": True,
                "watercolor_rendering_done": True,
                "ready_for_visual_qa": True,
            },
            reference_selection_path=preview.reference_selection_path,
            selected_reference_images=selected_reference_images,
            interior_reference_count=preview.interior_reference_count,
            selected_interior_filenames=preview.selected_interior_filenames,
            interior_guidance_summary=preview.interior_guidance_summary,
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
            cloudinary=cloudinary_info,
            public_output_url=cloudinary_info.get("draft", {}).get("secure_url"),
            cloudinary_url=cloudinary_info.get("draft", {}).get("secure_url"),
            output_url=output_info.get("output_url"),
            preview_url=output_info.get("preview_url"),
            warnings=self._dedupe_keep_order(warnings),
            errors=self._dedupe_keep_order(errors),
        )

    @staticmethod
    def _get_prompt_mode(preview: ImageGenerationRequestPreviewArtifact) -> str:
        normalized = str(preview.request_payload_preview.get("prompt_mode") or "default").strip().lower()
        if normalized in {"default", "strict_layout_test", "strict_layout_with_interior_guidance"}:
            return normalized
        return "default"

    def write_image_generation_draft(self, run_id: str, artifact: ImageGenerationDraftArtifact) -> None:
        path = self._artifacts_dir(run_id) / "image_generation_draft.json"
        try:
            with path.open("w", encoding="utf-8") as output_file:
                json.dump(artifact.model_dump(mode="json"), output_file, ensure_ascii=False, indent=2)
        except OSError as exc:
            raise HTTPException(status_code=500, detail="failed to write image_generation_draft artifact") from exc

    def _write_failed_image_generation_draft_artifact(
        self,
        run_id: str,
        preview: ImageGenerationRequestPreviewArtifact,
        payload: dict,
        selected_reference_images: list[dict],
        openai_input_images: list[dict],
        exc: HTTPException,
    ) -> None:
        path = self._artifacts_dir(run_id) / "image_generation_draft_failed.json"
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        attempts = detail.get("openai_image_attempts") if isinstance(detail, dict) else []
        payload = {
            "schema_version": "image_generation_draft_failed.v1",
            "run_id": run_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "draft_status": "failed",
            "provider": {
                "provider_name": "openai",
                "model": payload.get("model"),
                "api_call_performed": True,
                "request_sent": True,
                "prompt_mode": self._get_prompt_mode(preview),
                "strict_layout_test_enabled": self._get_prompt_mode(preview)
                in {"strict_layout_test", "strict_layout_with_interior_guidance"},
            },
            "source": {
                "image_generation_request_preview_artifact": self._relative_artifact_path(run_id, "image_generation_request_preview.json"),
                "prompt_package_artifact": self._relative_artifact_path(run_id, "prompt_package.json"),
                "primary_structure_reference": str(payload.get("primary_structure_reference") or "normalized_floorplan.png"),
            },
            "request": {
                "provider_size": payload.get("size"),
                "final_delivery_size": str(
                    preview.target_output.get("final_delivery_size") or self.settings.openai_image_final_output_size
                ),
                "quality": payload.get("quality"),
                "output_format": payload.get("output_format"),
                "prompt_mode": self._get_prompt_mode(preview),
                "strict_layout_test_enabled": self._get_prompt_mode(preview)
                in {"strict_layout_test", "strict_layout_with_interior_guidance"},
                "primary_structure_reference": str(payload.get("primary_structure_reference") or "normalized_floorplan.png"),
                "layout_preservation_priority": str(payload.get("layout_preservation_priority") or "high"),
                "long_prompt_disabled": bool(payload.get("long_prompt_disabled")),
                "selected_reference_images": selected_reference_images,
                "prompt_char_count": len(payload.get("prompt") or ""),
            },
            "outputs": {},
            "postprocess": {},
            "usage": {},
            "quality": {
                "needs_human_review": True,
                "layout_accuracy_not_verified": True,
                "structure_guard_prompt_used": True,
                "image_generation_done": False,
                "watercolor_rendering_done": False,
                "ready_for_visual_qa": False,
            },
            "reference_selection_path": preview.reference_selection_path,
            "selected_reference_images": selected_reference_images,
            "interior_reference_count": preview.interior_reference_count,
            "selected_interior_filenames": preview.selected_interior_filenames,
            "interior_guidance_summary": preview.interior_guidance_summary,
            "openai_image_attempts": attempts if isinstance(attempts, list) else [],
            "openai_input_images": [
                {
                    "path": str(item.get("path") or ""),
                    "filename": str(item.get("filename") or Path(str(item.get("path") or "")).name),
                    "mime_type": str(item.get("mime_type") or ""),
                    "role": str(item.get("role") or "reference_image"),
                }
                for item in openai_input_images
            ],
            "cloudinary": {
                "enabled": bool(self.settings.cloudinary_enabled),
                "draft": {
                    "enabled": bool(self.settings.cloudinary_enabled),
                    "uploaded": False,
                    "reason": "openai_image_generation_failed",
                },
                "raw_draft": {
                    "enabled": bool(self.settings.cloudinary_enabled),
                    "uploaded": False,
                    "reason": "openai_image_generation_failed",
                },
                "warnings": [],
            },
            "public_output_url": None,
            "cloudinary_url": None,
            "output_url": None,
            "preview_url": None,
            "warnings": [
                "OpenAI image generation failed before output could be produced.",
            ],
            "errors": [
                {
                    "error": str(detail.get("error") or "openai_image_timeout"),
                    "message": str(detail.get("message") or exc.detail or "OpenAI image generation failed."),
                    "details": detail.get("details") if isinstance(detail, dict) else {},
                }
            ],
        }
        try:
            with path.open("w", encoding="utf-8") as output_file:
                json.dump(payload, output_file, ensure_ascii=False, indent=2)
        except OSError:
            return

    def build_metadata_updates(self, metadata: RunMetadata, artifact: ImageGenerationDraftArtifact) -> dict:
        now = datetime.now(timezone.utc)
        cloudinary = artifact.cloudinary if isinstance(artifact.cloudinary, dict) else {}
        cloudinary_warnings = list(cloudinary.get("warnings") or [])
        public_output_url = artifact.public_output_url or artifact.cloudinary_url
        local_output_preview_url = artifact.preview_url or artifact.output_url or artifact.outputs.get("draft_image_preview_url")
        return {
            "status": "image_generation_draft_created",
            "run_status": "image_generation_draft_created",
            "updated_at": now,
            "processing": metadata.processing.model_copy(
                update={
                    "image_generation_request_preview": True,
                    "image_generation_draft": True,
                    "image_generation": True,
                    "watercolor_rendering": True,
                }
            ),
            "pipeline": {
                "current_phase": "phase_5c1_image_generation_draft",
                "next_phase": "phase_5d_visual_qa",
            },
            "image_generation_draft_path": self._relative_artifact_path(metadata.run_id, "image_generation_draft.json"),
            "cloudinary_summary": {
                "enabled": bool(cloudinary.get("enabled")),
                "draft_uploaded": bool(cloudinary.get("draft", {}).get("uploaded")),
                "draft_secure_url": cloudinary.get("draft", {}).get("secure_url"),
                "raw_draft_uploaded": bool(cloudinary.get("raw_draft", {}).get("uploaded")),
                "warnings_count": len(cloudinary_warnings),
            },
            "public_output_url": public_output_url,
            "image_generation_draft_summary": ImageGenerationDraftSummary(
                draft_status=artifact.draft_status,
                provider_name=str(artifact.provider.get("provider_name") or "openai"),
                model=str(artifact.provider.get("model") or self.settings.openai_image_model),
                api_call_performed=bool(artifact.provider.get("api_call_performed")),
                provider_size=str(artifact.request.get("provider_size") or self.settings.openai_image_provider_size),
                final_delivery_size=str(artifact.request.get("final_delivery_size") or self.settings.openai_image_final_output_size),
                draft_image_preview_url=public_output_url or local_output_preview_url,
                needs_human_review=bool(artifact.quality.get("needs_human_review", True)),
                ready_for_visual_qa=bool(artifact.quality.get("ready_for_visual_qa", False)),
                warnings_count=len(artifact.warnings),
                errors_count=len(artifact.errors),
            ),
        }

    @staticmethod
    def _extract_safe_provider_error(response: requests.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            return f"HTTP {response.status_code}"
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict):
            message = str(error.get("message") or f"HTTP {response.status_code}")
            return message[:200]
        return f"HTTP {response.status_code}"

    @staticmethod
    def _extract_image_bytes(openai_response: dict) -> bytes:
        data = openai_response.get("data")
        if not isinstance(data, list) or not data:
            raise HTTPException(status_code=502, detail="invalid OpenAI image generation response")
        first_item = data[0]
        if not isinstance(first_item, dict):
            raise HTTPException(status_code=502, detail="invalid OpenAI image generation response")
        image_base64 = first_item.get("b64_json")
        if image_base64:
            return base64.b64decode(str(image_base64))
        image_url = first_item.get("url")
        if image_url:
            try:
                response = requests.get(str(image_url), timeout=120)
                response.raise_for_status()
            except requests.RequestException as exc:
                raise HTTPException(status_code=502, detail="failed to download generated image from provider response") from exc
            return response.content
        raise HTTPException(status_code=502, detail="image data missing from OpenAI response")

    @staticmethod
    def _extract_usage(openai_response: dict) -> dict:
        usage = openai_response.get("usage")
        if not isinstance(usage, dict):
            return {
                "input_tokens": None,
                "output_tokens": None,
                "total_tokens": None,
                "raw_usage_available": False,
            }
        return {
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "total_tokens": usage.get("total_tokens"),
            "raw_usage_available": True,
        }

    def upload_output_images_to_cloudinary(self, run_id: str, output_info: dict) -> tuple[dict, list[str]]:
        cloudinary_info: dict = {
            "enabled": bool(self.settings.cloudinary_enabled),
            "draft": {
                "enabled": bool(self.settings.cloudinary_enabled),
                "uploaded": False,
                "reason": "cloudinary_disabled" if not self.settings.cloudinary_enabled else "draft_upload_not_attempted",
            },
            "raw_draft": {
                "enabled": bool(self.settings.cloudinary_enabled),
                "uploaded": False,
                "reason": "cloudinary_disabled" if not self.settings.cloudinary_enabled else "raw_draft_upload_not_attempted",
            },
            "warnings": [],
        }
        warnings: list[str] = []

        if not self.settings.cloudinary_enabled:
            return cloudinary_info, warnings

        if not self.settings.cloudinary_upload_drafts:
            cloudinary_info["draft"] = {
                "enabled": True,
                "uploaded": False,
                "reason": "cloudinary_upload_drafts_disabled",
            }
            cloudinary_info["raw_draft"] = {
                "enabled": True,
                "uploaded": False,
                "reason": "cloudinary_upload_drafts_disabled",
            }
            return cloudinary_info, warnings

        upload_targets = (
            ("draft", output_info.get("draft_image_path")),
            ("raw_draft", output_info.get("raw_image_path")),
        )
        for asset_kind, relative_storage_path in upload_targets:
            local_path = self._resolve_output_local_path(relative_storage_path)
            if local_path is None or not local_path.exists():
                cloudinary_info[asset_kind] = {
                    "enabled": True,
                    "uploaded": False,
                    "reason": "local_output_missing",
                }
                continue
            try:
                cloudinary_info[asset_kind] = self.cloudinary_service.upload_run_image(
                    run_id=run_id,
                    local_path=local_path,
                    asset_kind=asset_kind,
                )
            except HTTPException as exc:
                message = exc.detail if isinstance(exc.detail, str) else "Cloudinary upload failed"
                warning = f"Cloudinary upload failed: {message}"
                cloudinary_info["warnings"].append(warning)
                cloudinary_info[asset_kind] = {
                    "enabled": True,
                    "uploaded": False,
                    "reason": "upload_failed",
                    "error": message,
                }
                if self.settings.cloudinary_upload_required:
                    raise HTTPException(status_code=502, detail=warning) from exc
                warnings.append(warning)

        return cloudinary_info, warnings

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

    @staticmethod
    def _parse_size(size: str) -> tuple[int, int]:
        try:
            width_text, height_text = size.lower().split("x", 1)
            return int(width_text), int(height_text)
        except (ValueError, AttributeError) as exc:
            raise HTTPException(status_code=500, detail=f"invalid output size: {size}") from exc

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

    def _relative_artifact_path(self, run_id: str, filename: str) -> str:
        return f"storage/runs/{run_id}/artifacts/{filename}"

    def _relative_storage_path(self, path: Path) -> str:
        return path.relative_to(self.storage_dir.parent).as_posix()

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
