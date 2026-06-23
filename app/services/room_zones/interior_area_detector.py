from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException

from app.config import get_settings


class InteriorAreaDetector:
    def detect(
        self,
        normalized_floorplan_path: Path,
        structure_mask_path: Path,
        content_bbox: list[int] | None,
        output_path: Path,
    ) -> dict:
        try:
            import cv2
            import numpy as np
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"opencv-python is required for interior area detection: {exc}") from exc

        if not normalized_floorplan_path.exists():
            raise HTTPException(status_code=404, detail="normalized floorplan not found for interior area detection")
        if not structure_mask_path.exists():
            raise HTTPException(status_code=404, detail="structure mask not found for interior area detection")

        settings = get_settings()
        warnings: list[str] = []
        image = cv2.imread(str(normalized_floorplan_path), cv2.IMREAD_COLOR)
        structure = cv2.imread(str(structure_mask_path), cv2.IMREAD_GRAYSCALE)
        if image is None or structure is None:
            raise HTTPException(status_code=500, detail="failed to read room-zone input images")

        height, width = image.shape[:2]
        bbox = self._coerce_bbox(content_bbox, width, height)
        if not bbox:
            warnings.append("content_bbox unavailable; using full canvas for interior area detection.")
            bbox = [0, 0, width, height]

        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        light_space = ((gray > 168) & (structure < 1)).astype("uint8") * 255

        focus = np.zeros_like(light_space)
        x1, y1, x2, y2 = bbox
        focus[y1:y2, x1:x2] = light_space[y1:y2, x1:x2]

        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        focus = cv2.morphologyEx(
            focus,
            cv2.MORPH_CLOSE,
            kernel,
            iterations=max(1, int(settings.room_zone_morph_close_iterations)),
        )
        focus = cv2.morphologyEx(focus, cv2.MORPH_OPEN, kernel, iterations=1)

        components_total, labels, stats, _ = cv2.connectedComponentsWithStats(focus, connectivity=8)
        kept = np.zeros_like(focus)
        components_kept = 0
        min_area = max(1, int(settings.room_zone_min_area))
        max_area = int(width * height * float(settings.room_zone_max_area_ratio))
        for component_id in range(1, components_total):
            area = int(stats[component_id, cv2.CC_STAT_AREA])
            if area < min_area:
                continue
            if area > max_area:
                warnings.append(f"Skipped huge interior component area={area}.")
                continue
            kept[labels == component_id] = 255
            components_kept += 1

        output_path.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(output_path), kept)
        area_ratio = float((kept > 0).sum()) / float(width * height)
        if components_kept == 0:
            warnings.append("No interior area components were kept; room zones need manual review.")

        return {
            "method": "opencv_interior_area_detection",
            "image_width": width,
            "image_height": height,
            "content_bbox": bbox,
            "components_total": max(0, int(components_total) - 1),
            "components_kept": components_kept,
            "area_ratio": round(area_ratio, 6),
            "warnings": warnings,
        }

    @staticmethod
    def _coerce_bbox(value: list[int] | None, width: int, height: int) -> list[int] | None:
        if not isinstance(value, list) or len(value) != 4:
            return None
        try:
            x1, y1, x2, y2 = [int(round(float(item))) for item in value]
        except (TypeError, ValueError):
            return None
        x1 = max(0, min(width, x1))
        x2 = max(0, min(width, x2))
        y1 = max(0, min(height, y1))
        y2 = max(0, min(height, y2))
        if x2 <= x1 or y2 <= y1:
            return None
        return [x1, y1, x2, y2]
