from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException
from PIL import Image

from app.config import get_settings


class FloorplanNormalizer:
    def normalize_to_canvas(
        self,
        input_path: Path,
        output_path: Path,
        width: int = 1200,
        height: int = 1200,
        mode: str = "contain",
    ) -> dict:
        if mode != "contain":
            raise HTTPException(status_code=500, detail=f"Unsupported floorplan normalization mode: {mode}")
        if not input_path.exists():
            raise HTTPException(status_code=404, detail="floorplan image not found for normalization")

        try:
            with Image.open(input_path) as image:
                source = image.convert("RGB")
                original_width, original_height = source.size
                scale = min(width / original_width, height / original_height)
                resized_width = max(1, int(round(original_width * scale)))
                resized_height = max(1, int(round(original_height * scale)))
                resized = source.resize((resized_width, resized_height), Image.Resampling.LANCZOS)

                canvas = Image.new("RGB", (width, height), (255, 253, 248))
                offset_x = (width - resized_width) // 2
                offset_y = (height - resized_height) // 2
                canvas.paste(resized, (offset_x, offset_y))
                output_path.parent.mkdir(parents=True, exist_ok=True)
                canvas.save(output_path, format="PNG")
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"failed to normalize floorplan: {exc}") from exc

        content_bbox = self.detect_content_bbox(output_path)
        return {
            "original_width": original_width,
            "original_height": original_height,
            "canvas_width": width,
            "canvas_height": height,
            "scale": scale,
            "offset_x": offset_x,
            "offset_y": offset_y,
            "mode": mode,
            "content_bbox": content_bbox,
        }

    def detect_content_bbox(self, normalized_image_path: Path) -> dict:
        settings = get_settings()
        padding = max(0, int(settings.layout_content_bbox_padding))
        warnings: list[str] = []
        if not normalized_image_path.exists():
            raise HTTPException(status_code=404, detail="normalized image not found for content bbox detection")

        try:
            import numpy as np
            with Image.open(normalized_image_path) as image:
                gray = image.convert("L")
                width, height = gray.size
                pixels = np.array(gray)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"failed to detect floorplan content bbox: {exc}") from exc

        mask = pixels < 245
        ys, xs = np.where(mask)
        if len(xs) == 0 or len(ys) == 0:
            warnings.append("No non-white floorplan content was detected; using full canvas.")
            bbox = [0, 0, width, height]
        else:
            x1 = max(0, int(xs.min()) - padding)
            y1 = max(0, int(ys.min()) - padding)
            x2 = min(width, int(xs.max()) + 1 + padding)
            y2 = min(height, int(ys.max()) + 1 + padding)
            bbox = [x1, y1, x2, y2]

        return {
            "bbox": bbox,
            "padding": padding,
            "method": "non_white_content_detection",
            "image_width": width,
            "image_height": height,
            "warnings": warnings,
        }
