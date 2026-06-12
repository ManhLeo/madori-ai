from __future__ import annotations

import json
from pathlib import Path

from fastapi import HTTPException
from PIL import Image

from app.config import get_settings
from app.services.layout_lock.structure_extractor import StructureExtractor


class LayoutGuard:
    def compare_structure(
        self,
        reference_mask_path: Path,
        final_output_path: Path,
        output_diff_path: Path,
        content_bbox_path: Path | None = None,
    ) -> dict:
        if not reference_mask_path.exists():
            raise HTTPException(status_code=404, detail="reference structure mask not found")
        if not final_output_path.exists():
            raise HTTPException(status_code=404, detail="final output image not found for layout guard")

        output_mask_path = output_diff_path.with_name("output_structure_mask.png")
        warnings: list[str] = []
        extraction_metadata = StructureExtractor().extract_mask_only(final_output_path, output_mask_path)

        try:
            import numpy as np
            reference = np.array(Image.open(reference_mask_path).convert("L")) > 0
            output = np.array(Image.open(output_mask_path).convert("L")) > 0
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"failed to compare structure masks: {exc}") from exc

        if reference.shape != output.shape:
            raise HTTPException(status_code=500, detail="layout guard masks have different dimensions")

        settings = get_settings()
        compare_region = settings.layout_guard_compare_region.strip().lower()
        content_bbox = None
        reference_for_score = reference
        output_for_score = output
        if compare_region == "content_bbox":
            content_bbox = self._load_content_bbox(content_bbox_path)
            if content_bbox:
                x1, y1, x2, y2 = content_bbox
                reference_for_score = reference[y1:y2, x1:x2]
                output_for_score = output[y1:y2, x1:x2]
                self._save_crop(reference_for_score, output_diff_path.with_name("layout_guard_reference_crop.png"))
                self._save_crop(output_for_score, output_diff_path.with_name("layout_guard_output_crop.png"))
            else:
                warnings.append("Content bbox is unavailable; falling back to full image comparison.")
                compare_region = "full_image"
        elif compare_region != "full_image":
            warnings.append(f"Unsupported LAYOUT_GUARD_COMPARE_REGION={settings.layout_guard_compare_region}; using full image.")
            compare_region = "full_image"

        reference_pixels = int(reference_for_score.sum())
        output_pixels = int(output_for_score.sum())
        intersection = int((reference_for_score & output_for_score).sum())
        union = int((reference_for_score | output_for_score).sum())
        # The production requirement is that original structure remains present.
        # Watercolor/underlay can add extra dark pixels, so the primary score is
        # reference coverage instead of strict IoU.
        score = float(intersection / reference_pixels) if reference_pixels else 0.0
        iou_score = float(intersection / union) if union else 0.0
        threshold = 0.85
        status = "passed" if score >= threshold else "needs_review"
        if status != "passed":
            warnings.append("Structure overlap score is below threshold; manual review required.")

        self._save_diff(reference_for_score, output_for_score, output_diff_path)
        return {
            "method": "structure_mask_iou",
            "compare_region": compare_region,
            "content_bbox": content_bbox,
            "ignore_canvas_border": bool(settings.layout_guard_ignore_canvas_border),
            "score": round(score, 4),
            "iou_score": round(iou_score, 4),
            "threshold": threshold,
            "status": status,
            "reference_pixels": reference_pixels,
            "output_pixels": output_pixels,
            "intersection_pixels": intersection,
            "union_pixels": union,
            "output_structure_extraction": extraction_metadata,
            "warnings": warnings,
        }

    @staticmethod
    def _load_content_bbox(content_bbox_path: Path | None) -> list[int] | None:
        if not content_bbox_path or not content_bbox_path.exists():
            return None
        try:
            payload = json.loads(content_bbox_path.read_text(encoding="utf-8"))
            bbox = payload.get("bbox")
            if not isinstance(bbox, list) or len(bbox) != 4:
                return None
            x1, y1, x2, y2 = [int(round(float(value))) for value in bbox]
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None
        if x2 <= x1 or y2 <= y1:
            return None
        return [x1, y1, x2, y2]

    @staticmethod
    def _save_crop(mask, output_path: Path) -> None:
        try:
            import numpy as np
            image = np.zeros((*mask.shape, 3), dtype=np.uint8)
            image[:, :, :] = 248
            image[mask] = (42, 36, 30)
            Image.fromarray(image, mode="RGB").save(output_path, format="PNG")
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"failed to save layout guard crop: {exc}") from exc

    @staticmethod
    def _save_diff(reference, output, output_diff_path: Path) -> None:
        try:
            import numpy as np
            height, width = reference.shape
            diff = np.zeros((height, width, 3), dtype=np.uint8)
            diff[:, :, :] = 248
            diff[reference & output] = (60, 150, 90)
            diff[reference & ~output] = (220, 70, 60)
            diff[~reference & output] = (70, 110, 220)
            Image.fromarray(diff, mode="RGB").save(output_diff_path, format="PNG")
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"failed to save layout diff image: {exc}") from exc
