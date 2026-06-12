from __future__ import annotations

import json
from pathlib import Path

from fastapi import HTTPException
from PIL import Image


class LabelBoxDetector:
    def __init__(
        self,
        min_width: int = 35,
        max_width: int = 380,
        min_height: int = 15,
        max_height: int = 110,
        min_aspect_ratio: float = 1.1,
        max_aspect_ratio: float = 10.0,
    ) -> None:
        self.min_width = min_width
        self.max_width = max_width
        self.min_height = min_height
        self.max_height = max_height
        self.min_aspect_ratio = min_aspect_ratio
        self.max_aspect_ratio = max_aspect_ratio

    def detect(self, image_path: Path) -> dict:
        if not image_path.exists():
            raise HTTPException(status_code=404, detail="output image not found for label box detection")

        try:
            import cv2
            import numpy as np
        except ImportError as exc:
            raise HTTPException(status_code=500, detail="opencv-python is required for label box detection") from exc

        try:
            with Image.open(image_path) as image:
                image_width, image_height = image.size
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"failed to read output image for label detection: {exc}") from exc

        image = cv2.imread(str(image_path))
        if image is None:
            raise HTTPException(status_code=500, detail="OpenCV failed to load output image for label detection")

        candidates = []
        for strategy_name, mask in self._build_strategy_masks(image, cv2, np):
            candidates.extend(self._candidates_from_mask(strategy_name, mask, image, cv2))

        accepted_boxes = self._mark_duplicates(candidates)
        accepted_boxes.sort(key=lambda item: (item["center_y"], item["center_x"]))
        for index, box in enumerate(accepted_boxes, start=1):
            box["id"] = f"label_box_{index}"

        self._write_candidates(image_path, candidates)
        self._write_debug_image(image_path, image, candidates, cv2)

        return {
            "method": "opencv_label_rectangle_detection",
            "image_width": image_width,
            "image_height": image_height,
            "boxes": accepted_boxes,
            "warnings": [] if accepted_boxes else ["No rectangular label boxes were detected; manual review is required."],
        }

    def _build_strategy_masks(self, image, cv2, np) -> list[tuple[str, object]]:
        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

        bright_low_saturation = cv2.inRange(hsv, np.array([0, 0, 170]), np.array([179, 105, 255]))
        bright_low_saturation = cv2.morphologyEx(
            bright_low_saturation,
            cv2.MORPH_CLOSE,
            cv2.getStructuringElement(cv2.MORPH_RECT, (7, 5)),
            iterations=2,
        )

        adaptive = cv2.adaptiveThreshold(
            gray,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            31,
            4,
        )
        adaptive = cv2.morphologyEx(
            adaptive,
            cv2.MORPH_CLOSE,
            cv2.getStructuringElement(cv2.MORPH_RECT, (9, 3)),
            iterations=2,
        )

        edges = cv2.Canny(gray, 35, 130)
        edges = cv2.dilate(edges, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)), iterations=1)
        edges = cv2.morphologyEx(
            edges,
            cv2.MORPH_CLOSE,
            cv2.getStructuringElement(cv2.MORPH_RECT, (9, 5)),
            iterations=2,
        )

        return [
            ("bright_low_saturation", bright_low_saturation),
            ("rectangular_border", adaptive),
            ("edge_rectangles", edges),
        ]

    def _candidates_from_mask(self, strategy: str, mask, image, cv2) -> list[dict]:
        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        candidates = []
        image_height, image_width = image.shape[:2]
        for contour in contours:
            x, y, width, height = cv2.boundingRect(contour)
            candidate = self._candidate_from_rect(strategy, x, y, width, height, hsv, image_width, image_height)
            if candidate:
                candidates.append(candidate)
        return candidates

    def _candidate_from_rect(
        self,
        strategy: str,
        x: int,
        y: int,
        width: int,
        height: int,
        hsv,
        image_width: int,
        image_height: int,
    ) -> dict | None:
        if width <= 0 or height <= 0:
            return None

        roi = hsv[y : y + height, x : x + width]
        mean_saturation = float(roi[:, :, 1].mean()) if roi.size else 0.0
        mean_brightness = float(roi[:, :, 2].mean()) if roi.size else 0.0
        area = int(width * height)
        aspect_ratio = width / max(1, height)

        accepted = True
        rejection_reason = None
        if width < self.min_width or height < self.min_height or area < 525:
            accepted = False
            rejection_reason = "too_small"
        elif width > self.max_width or height > self.max_height:
            accepted = False
            rejection_reason = "too_large"
        elif aspect_ratio < self.min_aspect_ratio or aspect_ratio > self.max_aspect_ratio:
            accepted = False
            rejection_reason = "bad_aspect_ratio"
        elif x < 24 or y < 24 or x + width > image_width - 24 or y + height > image_height - 24:
            accepted = False
            rejection_reason = "too_close_to_border"

        confidence = self._confidence(width, height, mean_brightness, mean_saturation)
        if accepted and confidence < 0.42:
            accepted = False
            rejection_reason = "low_confidence"

        return {
            "id": "",
            "strategy": strategy,
            "bbox": [int(x), int(y), int(x + width), int(y + height)],
            "width": int(width),
            "height": int(height),
            "center_x": int(x + width / 2),
            "center_y": int(y + height / 2),
            "aspect_ratio": round(float(aspect_ratio), 3),
            "area": area,
            "mean_brightness": round(mean_brightness, 2),
            "mean_saturation": round(mean_saturation, 2),
            "confidence": round(confidence, 2),
            "accepted": accepted,
            "rejection_reason": rejection_reason,
        }

    @staticmethod
    def _confidence(width: int, height: int, mean_brightness: float, mean_saturation: float) -> float:
        size_score = min(0.35, (width * height) / 22000)
        brightness_score = max(0.0, min(0.35, (mean_brightness - 135) / 340))
        saturation_score = max(0.0, min(0.25, (125 - mean_saturation) / 500))
        return 0.18 + size_score + brightness_score + saturation_score

    def _mark_duplicates(self, candidates: list[dict]) -> list[dict]:
        accepted_candidates = [candidate for candidate in candidates if candidate["accepted"]]
        ordered = sorted(accepted_candidates, key=lambda item: item["confidence"], reverse=True)
        kept = []
        for candidate in ordered:
            duplicate = any(self._iou(candidate["bbox"], kept_candidate["bbox"]) >= 0.35 for kept_candidate in kept)
            if duplicate:
                candidate["accepted"] = False
                candidate["rejection_reason"] = "duplicate"
            else:
                kept.append(candidate)
        return kept

    @staticmethod
    def _iou(a: list[int], b: list[int]) -> float:
        ax0, ay0, ax1, ay1 = a
        bx0, by0, bx1, by1 = b
        ix0 = max(ax0, bx0)
        iy0 = max(ay0, by0)
        ix1 = min(ax1, bx1)
        iy1 = min(ay1, by1)
        intersection = max(0, ix1 - ix0) * max(0, iy1 - iy0)
        if intersection == 0:
            return 0.0
        area_a = max(0, ax1 - ax0) * max(0, ay1 - ay0)
        area_b = max(0, bx1 - bx0) * max(0, by1 - by0)
        return intersection / max(1, area_a + area_b - intersection)

    def _write_candidates(self, image_path: Path, candidates: list[dict]) -> None:
        payload = {"method": "opencv_label_rectangle_detection", "candidates": candidates}
        (image_path.parent / "detected_label_candidates.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _write_debug_image(self, image_path: Path, image, candidates: list[dict], cv2) -> None:
        debug_image = image.copy()
        for index, candidate in enumerate(candidates, start=1):
            x0, y0, x1, y1 = candidate["bbox"]
            color = (0, 180, 0) if candidate["accepted"] else (0, 0, 220)
            cv2.rectangle(debug_image, (x0, y0), (x1, y1), color, 2)
            cv2.putText(
                debug_image,
                str(index),
                (x0, max(12, y0 - 4)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.45,
                color,
                1,
                cv2.LINE_AA,
            )
        cv2.imwrite(str(image_path.parent / "detected_label_boxes_debug.png"), debug_image)
