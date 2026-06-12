from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException
from PIL import Image

from app.config import get_settings


class StructureExtractor:
    def extract_structure(
        self,
        normalized_floorplan_path: Path,
        output_mask_path: Path,
        output_layer_path: Path,
    ) -> dict:
        if not normalized_floorplan_path.exists():
            raise HTTPException(status_code=404, detail="normalized floorplan image not found")

        try:
            import cv2
            import numpy as np
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"opencv-python is required for structure extraction: {exc}") from exc

        settings = get_settings()
        threshold = int(settings.structure_line_dark_threshold)
        min_area = int(settings.structure_min_component_area)
        dilate_iterations = max(0, int(settings.structure_dilate_iterations))
        warnings: list[str] = []

        image = cv2.imread(str(normalized_floorplan_path), cv2.IMREAD_COLOR)
        if image is None:
            raise HTTPException(status_code=500, detail="failed to read normalized floorplan for structure extraction")

        height, width = image.shape[:2]
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        mask = (gray < threshold).astype("uint8") * 255

        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=1)
        if dilate_iterations:
            mask = cv2.dilate(mask, kernel, iterations=dilate_iterations)

        components_total, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
        kept = np.zeros_like(mask)
        components_kept = 0
        for component_id in range(1, components_total):
            area = int(stats[component_id, cv2.CC_STAT_AREA])
            if area < min_area:
                continue
            kept[labels == component_id] = 255
            components_kept += 1

        if components_kept == 0:
            warnings.append("No structural line components were kept; review extraction thresholds.")

        output_mask_path.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(output_mask_path), kept)

        rgba = np.zeros((height, width, 4), dtype=np.uint8)
        rgba[:, :, 0] = 42
        rgba[:, :, 1] = 36
        rgba[:, :, 2] = 30
        rgba[:, :, 3] = kept
        Image.fromarray(rgba, mode="RGBA").save(output_layer_path, format="PNG")

        coverage = float((kept > 0).sum()) / float(width * height)
        return {
            "method": "opencv_dark_line_extraction",
            "structure_type": "structure_full",
            "image_width": width,
            "image_height": height,
            "dark_threshold": threshold,
            "components_total": max(0, int(components_total) - 1),
            "components_kept": components_kept,
            "mask_coverage_ratio": round(coverage, 6),
            "includes_text_labels": True,
            "notes": "This mask is structure_full and may include original labels/text.",
            "warnings": warnings,
        }

    def extract_mask_only(self, image_path: Path, output_mask_path: Path) -> dict:
        temp_layer_path = output_mask_path.with_name(output_mask_path.stem + "_layer_tmp.png")
        metadata = self.extract_structure(image_path, output_mask_path, temp_layer_path)
        try:
            temp_layer_path.unlink(missing_ok=True)
        except OSError:
            pass
        return metadata
