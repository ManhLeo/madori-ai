from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException
from PIL import Image

from app.config import get_settings
from app.schemas.run import (
    FinalOutputResponse,
    FinalOutputSummary,
    FinalizeOutputRequest,
    ImageGenerationDraftArtifact,
    RunMetadata,
    VisualQAReportResponse,
)
from app.services.cloudinary_storage_service import CloudinaryStorageService


class FinalOutputService:
    EXPECTED_SIZE = (1200, 1200)

    def __init__(self, storage_dir: Path, storage_runs_dir: Path) -> None:
        self.storage_dir = storage_dir
        self.storage_runs_dir = storage_runs_dir
        self.settings = get_settings()
        self.cloudinary_service = CloudinaryStorageService()

    def finalize_output(self, metadata: RunMetadata, request: FinalizeOutputRequest) -> FinalOutputResponse:
        self._validate_request(request)
        warnings: list[str] = []
        errors: list[str] = []

        visual_qa_artifact = self._load_optional_visual_qa_report(metadata.run_id)
        self._enforce_visual_qa_gate(request, visual_qa_artifact)

        draft_artifact = self._load_optional_draft_artifact(metadata.run_id)
        source_type, source_path = self._resolve_source_image(metadata.run_id, request.source)
        if draft_artifact is None:
            warnings.append("No image_generation_draft artifact found.")

        outputs_dir = self._outputs_dir(metadata.run_id)
        outputs_dir.mkdir(parents=True, exist_ok=True)
        final_path = outputs_dir / f"{metadata.run_id}_final.png"
        try:
            shutil.copyfile(source_path, final_path)
        except OSError as exc:
            raise HTTPException(
                status_code=500,
                detail={"error": "final_output_copy_failed", "message": "Failed to create final output image."},
            ) from exc

        width, height, image_format = self._inspect_image(final_path)
        if (width, height) != self.EXPECTED_SIZE:
            warnings.append(
                f"Final image size is {width}x{height}; expected {self.EXPECTED_SIZE[0]}x{self.EXPECTED_SIZE[1]}."
            )

        local_final_preview_url = self._output_preview_url(metadata.run_id, final_path.name)
        cloudinary_info, cloudinary_warnings = self._upload_final_image_to_cloudinary(metadata.run_id, final_path)
        warnings.extend(cloudinary_warnings)
        final_public_output_url = (
            cloudinary_info.get("final", {}).get("secure_url")
            if isinstance(cloudinary_info, dict)
            else None
        ) or local_final_preview_url

        artifact = FinalOutputResponse(
            run_id=metadata.run_id,
            generated_at=datetime.now(timezone.utc),
            final_status="finalized",
            source=self._build_source_section(metadata.run_id, source_type, source_path, draft_artifact),
            final={
                "final_image_path": self._relative_output_path(metadata.run_id, final_path.name),
                "final_image_preview_url": local_final_preview_url,
                "width": width,
                "height": height,
                "format": (image_format or "PNG").lower(),
                "public_output_url": final_public_output_url,
            },
            qa={
                "qa_status": visual_qa_artifact.qa_status if visual_qa_artifact else None,
                "visual_qa_report_path": self._relative_artifact_path(metadata.run_id, "visual_qa_report.json")
                if visual_qa_artifact
                else None,
            },
            generation=self._build_generation_section(draft_artifact),
            cloudinary=cloudinary_info,
            warnings=warnings,
            errors=errors,
        )
        self.write_final_output(metadata.run_id, artifact)
        return artifact

    def build_metadata_updates(self, metadata: RunMetadata, artifact: FinalOutputResponse) -> dict:
        now = datetime.now(timezone.utc)
        final = artifact.final if isinstance(artifact.final, dict) else {}
        qa = artifact.qa if isinstance(artifact.qa, dict) else {}
        cloudinary = artifact.cloudinary if isinstance(artifact.cloudinary, dict) else {}
        public_output_url = final.get("public_output_url") or metadata.public_output_url
        return {
            "status": "finalized",
            "run_status": "finalized",
            "updated_at": now,
            "processing": metadata.processing.model_copy(update={"final_output": True}),
            "pipeline": {
                "current_phase": "phase_7b1_finalize_output_local",
                "next_phase": "phase_7b2_final_output_index_summary",
            },
            "final_output_path": self._relative_artifact_path(metadata.run_id, "final_output.json"),
            "final_output_summary": FinalOutputSummary(
                final_status=artifact.final_status,
                final_image_preview_url=final.get("final_image_preview_url"),
                public_output_url=public_output_url,
                width=int(final.get("width") or 0),
                height=int(final.get("height") or 0),
                qa_status=qa.get("qa_status"),
                cloudinary_enabled=bool(cloudinary.get("enabled")),
                cloudinary_uploaded=bool(cloudinary.get("final", {}).get("uploaded")),
                warnings_count=len(artifact.warnings),
                errors_count=len(artifact.errors),
            ),
            "public_output_url": public_output_url,
        }

    def write_final_output(self, run_id: str, artifact: FinalOutputResponse) -> None:
        path = self._artifacts_dir(run_id) / "final_output.json"
        try:
            with path.open("w", encoding="utf-8") as output_file:
                json.dump(artifact.model_dump(mode="json"), output_file, ensure_ascii=False, indent=2)
        except OSError as exc:
            raise HTTPException(status_code=500, detail="failed to write final_output artifact") from exc

    def load_final_output(self, run_id: str) -> FinalOutputResponse:
        path = self._artifacts_dir(run_id) / "final_output.json"
        if not path.exists():
            raise HTTPException(status_code=404, detail="final_output artifact not found")
        try:
            return FinalOutputResponse.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=500, detail="failed to read final_output artifact") from exc

    def _validate_request(self, request: FinalizeOutputRequest) -> None:
        if request.source not in {"auto", "draft", "raw"}:
            raise HTTPException(
                status_code=400,
                detail={"error": "invalid_finalize_source", "message": "source must be one of: auto, draft, raw."},
            )

    def _enforce_visual_qa_gate(
        self,
        request: FinalizeOutputRequest,
        visual_qa_artifact: VisualQAReportResponse | None,
    ) -> None:
        if request.force:
            return
        if visual_qa_artifact is None or visual_qa_artifact.qa_status != "passed":
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "visual_qa_required",
                    "message": "Run must pass visual QA before finalizing, or use force=true.",
                },
            )

    def _resolve_source_image(self, run_id: str, source: str) -> tuple[str, Path]:
        raw_path = self._artifacts_dir(run_id) / "generated_draft_raw.png"
        draft_path = self._outputs_dir(run_id) / f"{run_id}_draft.png"

        if source == "raw":
            if raw_path.exists():
                return "raw", raw_path
            raise HTTPException(
                status_code=400,
                detail={"error": "source_image_missing", "message": "No generated draft image found to finalize."},
            )

        if source == "draft":
            if draft_path.exists():
                return "draft", draft_path
            raise HTTPException(
                status_code=400,
                detail={"error": "source_image_missing", "message": "No generated draft image found to finalize."},
            )

        if raw_path.exists():
            return "raw", raw_path
        if draft_path.exists():
            return "draft", draft_path
        raise HTTPException(
            status_code=400,
            detail={"error": "source_image_missing", "message": "No generated draft image found to finalize."},
        )

    def _inspect_image(self, image_path: Path) -> tuple[int, int, str | None]:
        try:
            with Image.open(image_path) as image:
                return int(image.width), int(image.height), image.format
        except OSError as exc:
            raise HTTPException(
                status_code=500,
                detail={"error": "final_output_invalid_image", "message": "Final output image could not be opened."},
            ) from exc

    def _build_source_section(
        self,
        run_id: str,
        source_type: str,
        source_path: Path,
        draft_artifact: ImageGenerationDraftArtifact | None,
    ) -> dict:
        source_public_output_url = None
        if draft_artifact is not None:
            source_public_output_url = draft_artifact.public_output_url or draft_artifact.cloudinary_url
        return {
            "source_type": source_type,
            "source_image_path": self._relative_storage_path(source_path),
            "source_image_preview_url": self._preview_url_for_path(run_id, source_path),
            "source_public_output_url": source_public_output_url,
        }

    def _build_generation_section(self, draft_artifact: ImageGenerationDraftArtifact | None) -> dict:
        if draft_artifact is None:
            return {"provider": None, "model": None, "prompt_mode": None}
        provider = draft_artifact.provider if isinstance(draft_artifact.provider, dict) else {}
        request = draft_artifact.request if isinstance(draft_artifact.request, dict) else {}
        return {
            "provider": provider.get("provider_name") or provider.get("name") or "openai",
            "model": provider.get("model"),
            "prompt_mode": request.get("prompt_mode"),
        }

    def _upload_final_image_to_cloudinary(self, run_id: str, final_path: Path) -> tuple[dict, list[str]]:
        cloudinary_info: dict = {
            "enabled": bool(self.settings.cloudinary_enabled),
            "final": {
                "enabled": bool(self.settings.cloudinary_enabled),
                "uploaded": False,
                "reason": "cloudinary_disabled" if not self.settings.cloudinary_enabled else "final_upload_not_attempted",
            },
            "warnings": [],
        }
        warnings: list[str] = []

        if not self.settings.cloudinary_enabled:
            return cloudinary_info, warnings

        if not self.settings.cloudinary_upload_finals:
            cloudinary_info["final"] = {
                "enabled": True,
                "uploaded": False,
                "reason": "cloudinary_upload_finals_disabled",
            }
            return cloudinary_info, warnings

        try:
            cloudinary_info["final"] = self.cloudinary_service.upload_run_image(
                run_id=run_id,
                local_path=final_path,
                asset_kind="final",
            )
        except HTTPException as exc:
            if isinstance(exc.detail, dict):
                message = str(exc.detail.get("message") or exc.detail.get("error") or "Cloudinary upload failed")
            else:
                message = str(exc.detail or "Cloudinary upload failed")
            warning = f"Cloudinary final upload failed: {message}"
            cloudinary_info["warnings"].append(warning)
            cloudinary_info["final"] = {
                "enabled": True,
                "uploaded": False,
                "reason": "upload_failed",
                "error": message,
            }
            if self.settings.cloudinary_upload_required:
                raise HTTPException(status_code=502, detail=warning) from exc
            warnings.append(warning)

        return cloudinary_info, warnings

    def _load_optional_draft_artifact(self, run_id: str) -> ImageGenerationDraftArtifact | None:
        path = self._artifacts_dir(run_id) / "image_generation_draft.json"
        if not path.exists():
            return None
        try:
            return ImageGenerationDraftArtifact.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=500, detail="failed to read image_generation_draft artifact") from exc

    def _load_optional_visual_qa_report(self, run_id: str) -> VisualQAReportResponse | None:
        path = self._artifacts_dir(run_id) / "visual_qa_report.json"
        if not path.exists():
            return None
        try:
            return VisualQAReportResponse.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=500, detail="failed to read visual_qa_report artifact") from exc

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

    @staticmethod
    def _relative_output_path(run_id: str, filename: str) -> str:
        return f"storage/runs/{run_id}/outputs/{filename}"

    @staticmethod
    def _output_preview_url(run_id: str, filename: str) -> str:
        return f"/storage/runs/{run_id}/outputs/{filename}"

    def _preview_url_for_path(self, run_id: str, path: Path) -> str:
        if path.parent.name == "artifacts":
            return f"/storage/runs/{run_id}/artifacts/{path.name}"
        return self._output_preview_url(run_id, path.name)

    def _relative_storage_path(self, path: Path) -> str:
        return path.resolve().relative_to(self.storage_dir.parent.resolve()).as_posix()
