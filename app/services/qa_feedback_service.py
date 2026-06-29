from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException

from app.schemas.run import (
    FinalOutputResponse,
    ImageGenerationDraftArtifact,
    QAFeedbackIssue,
    QAFeedbackRequest,
    QAFeedbackResponse,
    QAFeedbackSummary,
    RunMetadata,
)


class QAFeedbackService:
    VALID_FEEDBACK_STATUSES = {"needs_regeneration"}
    VALID_TARGET_IMAGES = {"latest_draft", "final_output", "specific"}
    VALID_ISSUE_TYPES = {
        "layout_drift",
        "wrong_room_role",
        "dining_wrong_room",
        "sofa_tv_wrong_room",
        "too_many_beds",
        "wrong_bed_count",
        "labels_wrong",
        "missing_english_labels",
        "furniture_too_much",
        "furniture_missing",
        "style_not_watercolor",
        "output_too_flat",
        "room_function_wrong",
        "palette_too_dark",
        "walls_too_dark",
        "washing_machine_wrong_position",
        "furniture_orientation_wrong",
        "other",
    }
    VALID_SEVERITIES = {"low", "medium", "high"}
    SEVERITY_RANK = {"low": 1, "medium": 2, "high": 3}
    CORRECTION_INSTRUCTION_MAP = {
        "layout_drift": "Preserve the original floorplan structure exactly. Do not alter walls, doors, windows, room sizes, or room positions.",
        "wrong_room_role": "Follow room_function_assignment.json. Use each room according to its functional_role.",
        "dining_wrong_room": "Keep dining table and chairs in living_dining or dining_zone. Do not put dining furniture in bedroom or media_lounge.",
        "sofa_tv_wrong_room": "Put sofa, TV, TV stand, and coffee table in media_lounge when media_lounge exists. Sofa and TV should face each other if possible.",
        "too_many_beds": "Bedroom should contain only one bed or two single beds. Do not draw more than two beds.",
        "wrong_bed_count": "Bedroom should contain only one bed or two single beds. Do not draw more than two beds.",
        "labels_wrong": "Use English room labels only. Replace Japanese labels with English labels.",
        "missing_english_labels": "Use English room labels only. Replace Japanese labels with English labels.",
        "furniture_too_much": "Reduce furniture density. Keep only essential furniture for each assigned room role.",
        "furniture_missing": "Add missing key furniture according to functional role and interior references.",
        "style_not_watercolor": "Use soft Japanese watercolor style with light texture. Avoid flat solid fills.",
        "output_too_flat": "Use soft Japanese watercolor style with light texture. Avoid flat solid fills.",
        "room_function_wrong": "Respect functional room assignment. Do not infer room roles from Japanese labels alone.",
        "palette_too_dark": "Brighten the overall palette. Use light warm beige, soft greige, pale wood, and neutral watercolor tones. Avoid dull, heavy, or dark color masses.",
        "walls_too_dark": "Do not render walls or partition blocks as large black or dark charcoal filled areas. Use light neutral wall tones with thin dark outline only where needed.",
        "washing_machine_wrong_position": "Place the washing machine only in the Wash Room at the location marked Wash / 洗. Do not place the washing machine in any other room.",
        "furniture_orientation_wrong": "Correct furniture orientation. TV should face the sofa. Coffee table should sit between sofa and TV when possible. Beds should align naturally to room walls. Dining table and chairs should align neatly and not block circulation.",
        "other": "Apply the manual QA correction notes carefully while preserving layout and English labels.",
    }
    PROMPT_GUIDANCE_MAP = {
        "layout_drift": [
            "Preserve the original floorplan layout exactly.",
            "Do not change walls, doors, windows, room proportions, or room positions.",
        ],
        "wrong_room_role": [
            "Respect functional room assignment. Use each room according to room_function_assignment.json.",
        ],
        "dining_wrong_room": [
            "Keep dining table in the main living/dining area.",
        ],
        "sofa_tv_wrong_room": [
            "Place sofa and TV in the assigned media lounge / western-style room when that role exists.",
        ],
        "too_many_beds": [
            "Use the bedroom only for one bed or two single beds.",
            "Do not turn both western-style rooms into bedrooms.",
        ],
        "wrong_bed_count": [
            "Use the bedroom only for one bed or two single beds.",
            "Do not turn both western-style rooms into bedrooms.",
        ],
        "labels_wrong": [
            "Use English room labels only.",
        ],
        "missing_english_labels": [
            "Use English room labels only.",
        ],
        "furniture_too_much": [
            "Reduce furniture density and keep only essential furniture for each room role.",
        ],
        "furniture_missing": [
            "Add missing key furniture according to functional role and interior references.",
        ],
        "style_not_watercolor": [
            "Use soft Japanese watercolor style with light texture.",
        ],
        "output_too_flat": [
            "Use soft Japanese watercolor style with light texture.",
        ],
        "room_function_wrong": [
            "Respect functional room assignment. Do not infer room roles from Japanese labels alone.",
        ],
        "palette_too_dark": [
            "Use a brighter overall palette with light warm beige, soft greige, pale wood, and neutral watercolor tones.",
        ],
        "walls_too_dark": [
            "Use light neutral wall tones and avoid large black or dark charcoal wall fills.",
        ],
        "washing_machine_wrong_position": [
            "Place the washing machine only in the Wash Room at the location marked Wash / 洗.",
        ],
        "furniture_orientation_wrong": [
            "Orient furniture naturally according to room geometry.",
            "TV should face the sofa.",
            "Coffee table should be between sofa and TV when possible.",
            "Beds should align naturally to room walls with headboards against a wall.",
            "Dining table and chairs should align neatly and should not block circulation.",
        ],
    }
    NEGATIVE_GUIDANCE_MAP = {
        "dining_wrong_room": ["Do not move the dining table into a bedroom."],
        "too_many_beds": ["Do not place extra beds."],
        "wrong_bed_count": ["Do not place extra beds."],
        "labels_wrong": ["Do not replace English labels with Japanese labels."],
        "missing_english_labels": ["Do not replace English labels with Japanese labels."],
        "sofa_tv_wrong_room": ["Do not place sofa and TV in the wrong room when a media lounge is assigned."],
        "palette_too_dark": ["Do not use dull, heavy, or dark color masses."],
        "walls_too_dark": ["Do not render walls, partitions, or wet-area blocks as large black or dark charcoal fills."],
        "washing_machine_wrong_position": ["Do not place the washing machine outside the Wash Room."],
        "furniture_orientation_wrong": ["Do not rotate furniture unnaturally or block circulation with dining furniture."],
    }
    DEFAULT_PROMPT_GUIDANCE = [
        "Preserve the original floorplan layout exactly.",
        "Do not change walls, doors, windows, room proportions, or room positions.",
        "Keep dining table in the main living/dining area.",
        "Place sofa and TV in the assigned media lounge / western-style room when that role exists.",
        "Use the bedroom only for one bed or two single beds.",
        "Do not turn both western-style rooms into bedrooms.",
        "Use a bright, airy Japanese watercolor floorplan style with light warm beige, soft greige, pale wood, and neutral tones.",
        "Use light neutral wall tones with thin outlines, not heavy dark wall fills.",
        "Place the washing machine only in the Wash Room at the location marked Wash / 洗.",
        "Orient furniture naturally without changing the floorplan.",
    ]
    DEFAULT_NEGATIVE_GUIDANCE = [
        "Do not move the dining table into a bedroom.",
        "Do not place extra beds.",
        "Do not replace English labels with Japanese labels.",
        "Do not place the washing machine outside the Wash Room.",
        "Do not render walls or partitions as large dark filled masses.",
    ]

    def __init__(self, storage_dir: Path, storage_runs_dir: Path) -> None:
        self.storage_dir = storage_dir
        self.storage_runs_dir = storage_runs_dir

    def create_qa_feedback(self, metadata: RunMetadata, request: QAFeedbackRequest) -> QAFeedbackResponse:
        self._validate_request(request)
        warnings: list[str] = []
        errors: list[str] = []

        target_image = self._resolve_target_image(metadata.run_id, request, warnings)
        issues = self._build_feedback_issues(request.issues)
        highest_severity = self._highest_severity(issues)
        correction_plan = self._build_correction_plan(issues, highest_severity)

        artifact = QAFeedbackResponse(
            run_id=metadata.run_id,
            generated_at=datetime.now(timezone.utc),
            feedback_status=request.feedback_status,
            target_image=target_image,
            issues=issues,
            freeform_feedback=request.freeform_feedback,
            correction_plan=correction_plan,
            warnings=warnings,
            errors=errors,
        )
        self.write_qa_feedback(metadata.run_id, artifact)
        return artifact

    def build_metadata_updates(self, metadata: RunMetadata, artifact: QAFeedbackResponse) -> dict:
        now = datetime.now(timezone.utc)
        highest_severity = self._highest_severity(artifact.issues)
        correction_plan = artifact.correction_plan if isinstance(artifact.correction_plan, dict) else {}
        return {
            "status": "qa_feedback_created",
            "run_status": "qa_feedback_created",
            "updated_at": now,
            "processing": metadata.processing.model_copy(update={"qa_feedback": True}),
            "pipeline": {
                "current_phase": "phase_8a_qa_feedback",
                "next_phase": "phase_8b_regenerate_with_feedback",
            },
            "qa_feedback_path": self._relative_artifact_path(metadata.run_id, "qa_feedback.json"),
            "qa_feedback_summary": QAFeedbackSummary(
                feedback_status=artifact.feedback_status,
                issues_count=len(artifact.issues),
                highest_severity=highest_severity,
                correction_plan_status=str(correction_plan.get("status") or "created"),
            ),
        }

    def write_qa_feedback(self, run_id: str, artifact: QAFeedbackResponse) -> None:
        path = self._artifacts_dir(run_id) / "qa_feedback.json"
        try:
            with path.open("w", encoding="utf-8") as output_file:
                json.dump(artifact.model_dump(mode="json"), output_file, ensure_ascii=False, indent=2)
        except OSError as exc:
            raise HTTPException(status_code=500, detail="failed to write qa_feedback artifact") from exc

    def load_qa_feedback(self, run_id: str) -> QAFeedbackResponse:
        path = self._artifacts_dir(run_id) / "qa_feedback.json"
        if not path.exists():
            raise HTTPException(status_code=404, detail="qa_feedback artifact not found")
        try:
            return QAFeedbackResponse.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=500, detail="failed to read qa_feedback artifact") from exc

    def _validate_request(self, request: QAFeedbackRequest) -> None:
        if request.feedback_status not in self.VALID_FEEDBACK_STATUSES:
            raise HTTPException(status_code=400, detail="feedback_status must be one of: needs_regeneration")
        if request.target_image not in self.VALID_TARGET_IMAGES:
            raise HTTPException(status_code=400, detail="target_image must be one of: latest_draft, final_output, specific")
        if request.target_image == "specific" and not request.target_image_path:
            raise HTTPException(status_code=400, detail="target_image_path is required when target_image=specific")

        invalid_issue_types = [issue.issue_type for issue in request.issues if issue.issue_type not in self.VALID_ISSUE_TYPES]
        if invalid_issue_types:
            raise HTTPException(status_code=400, detail=f"invalid issue_type: {', '.join(sorted(set(invalid_issue_types)))}")
        invalid_severities = [issue.severity for issue in request.issues if issue.severity not in self.VALID_SEVERITIES]
        if invalid_severities:
            raise HTTPException(status_code=400, detail="issue severity must be one of: low, medium, high")

    def _resolve_target_image(self, run_id: str, request: QAFeedbackRequest, warnings: list[str]) -> dict:
        if request.target_image == "latest_draft":
            draft_artifact = self._load_optional_draft_artifact(run_id)
            if draft_artifact is None:
                warnings.append("No image_generation_draft artifact found for latest_draft target.")
                return {
                    "target_image_type": "latest_draft",
                    "target_image_path": self._relative_artifact_path(run_id, "image_generation_draft.json"),
                    "target_image_preview_url": None,
                    "target_public_url": None,
                }
            outputs = draft_artifact.outputs if isinstance(draft_artifact.outputs, dict) else {}
            return {
                "target_image_type": "latest_draft",
                "target_image_path": outputs.get("draft_image_path") or outputs.get("output_image_path"),
                "target_image_preview_url": outputs.get("draft_image_preview_url") or draft_artifact.preview_url,
                "target_public_url": draft_artifact.public_output_url or draft_artifact.cloudinary_url,
            }

        if request.target_image == "final_output":
            final_output_artifact = self._load_optional_final_output_artifact(run_id)
            if final_output_artifact is None:
                warnings.append("No final_output artifact found for final_output target.")
                return {
                    "target_image_type": "final_output",
                    "target_image_path": self._relative_artifact_path(run_id, "final_output.json"),
                    "target_image_preview_url": None,
                    "target_public_url": None,
                }
            final = final_output_artifact.final if isinstance(final_output_artifact.final, dict) else {}
            return {
                "target_image_type": "final_output",
                "target_image_path": final.get("final_image_path"),
                "target_image_preview_url": final.get("final_image_preview_url"),
                "target_public_url": final.get("public_output_url"),
            }

        path = self._resolve_specific_target_path(run_id, request.target_image_path or "")
        preview_url = self._preview_url_for_relative_path(path)
        return {
            "target_image_type": "specific",
            "target_image_path": path,
            "target_image_preview_url": preview_url,
            "target_public_url": preview_url,
        }

    def _build_feedback_issues(self, issues: list[QAFeedbackIssue]) -> list[QAFeedbackIssue]:
        normalized: list[QAFeedbackIssue] = []
        for issue in issues:
            normalized.append(
                QAFeedbackIssue(
                    issue_type=issue.issue_type,
                    severity=issue.severity,
                    description=issue.description,
                    correction_instruction=self.CORRECTION_INSTRUCTION_MAP.get(issue.issue_type, self.CORRECTION_INSTRUCTION_MAP["other"]),
                )
            )
        return normalized

    def _build_correction_plan(self, issues: list[QAFeedbackIssue], highest_severity: str) -> dict:
        prompt_guidance = self._collect_guidance(issues, self.PROMPT_GUIDANCE_MAP, self.DEFAULT_PROMPT_GUIDANCE)
        negative_guidance = self._collect_guidance(issues, self.NEGATIVE_GUIDANCE_MAP, self.DEFAULT_NEGATIVE_GUIDANCE)
        issue_labels = [issue.issue_type for issue in issues]
        if issue_labels:
            summary = f"Regenerate with QA corrections for: {', '.join(issue_labels)}."
        else:
            summary = "Regenerate while preserving layout, English labels, and assigned room functions."
        return {
            "status": "created",
            "priority": highest_severity,
            "summary": summary,
            "prompt_guidance": prompt_guidance,
            "negative_guidance": negative_guidance,
        }

    def _collect_guidance(
        self,
        issues: list[QAFeedbackIssue],
        mapping: dict[str, list[str]],
        defaults: list[str],
    ) -> list[str]:
        collected: list[str] = []
        for item in defaults:
            if item not in collected:
                collected.append(item)
        for issue in issues:
            for item in mapping.get(issue.issue_type, []):
                if item not in collected:
                    collected.append(item)
        return collected

    def _highest_severity(self, issues: list[QAFeedbackIssue]) -> str:
        if not issues:
            return "low"
        return max((issue.severity for issue in issues), key=lambda value: self.SEVERITY_RANK.get(value, 0))

    def _load_optional_draft_artifact(self, run_id: str) -> ImageGenerationDraftArtifact | None:
        path = self._artifacts_dir(run_id) / "image_generation_draft.json"
        if not path.exists():
            return None
        try:
            return ImageGenerationDraftArtifact.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=500, detail="failed to read image_generation_draft artifact") from exc

    def _load_optional_final_output_artifact(self, run_id: str) -> FinalOutputResponse | None:
        path = self._artifacts_dir(run_id) / "final_output.json"
        if not path.exists():
            return None
        try:
            return FinalOutputResponse.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=500, detail="failed to read final_output artifact") from exc

    def _resolve_specific_target_path(self, run_id: str, target_image_path: str) -> str:
        normalized = target_image_path.replace("\\", "/").lstrip("/")
        absolute_path = (self.storage_dir.parent / normalized).resolve()
        run_dir = self._safe_run_dir(run_id)
        try:
            absolute_path.relative_to(run_dir)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="target_image_path must stay within the run workspace") from exc
        if not absolute_path.exists():
            raise HTTPException(status_code=400, detail="target_image_path does not exist")
        return absolute_path.relative_to(self.storage_dir.parent).as_posix()

    @staticmethod
    def _preview_url_for_relative_path(relative_path: str) -> str | None:
        suffix = Path(relative_path).suffix.lower()
        if suffix in {".png", ".jpg", ".jpeg", ".webp", ".json"}:
            return f"/{relative_path}"
        return None

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

    @staticmethod
    def _relative_artifact_path(run_id: str, filename: str) -> str:
        return f"storage/runs/{run_id}/artifacts/{filename}"
