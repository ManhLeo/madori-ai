from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont


AUTO_LABEL_DEBUG_FILENAME = "auto_label_debug.png"


class AutoLabelPlacer:
    def place_labels(
        self,
        image_path: Path,
        mapped_labels: dict,
        ocr_result: dict | None = None,
        confidence_threshold: float = 0.85,
    ) -> dict:
        image_width, image_height = self._image_size(image_path)
        labels = []
        warnings = list(mapped_labels.get("warnings", [])) if isinstance(mapped_labels, dict) else []
        unmapped_texts = mapped_labels.get("unmapped_texts", []) if isinstance(mapped_labels, dict) else []

        for label in mapped_labels.get("labels", []) if isinstance(mapped_labels, dict) else []:
            original_bbox = self._coerce_bbox(label.get("original_bbox") or label.get("bbox"), image_width, image_height)
            if not original_bbox:
                warnings.append(f"Skipped {label.get('id') or label.get('text')}: invalid OCR bbox.")
                continue

            candidate_bbox = self._choose_label_box(image_path, original_bbox)
            confidence = self._coerce_float(label.get("confidence"), default=0.0)
            text = str(label.get("text") or "").strip()
            text_fits = self._text_fits(text, candidate_bbox)
            needs_review = bool(label.get("needs_review")) or confidence < confidence_threshold or not text_fits

            labels.append(
                {
                    "id": str(label.get("id") or f"auto_label_{len(labels) + 1}"),
                    "original_text": str(label.get("original_text") or ""),
                    "text": text,
                    "original_bbox": list(original_bbox),
                    "bbox": list(candidate_bbox),
                    "confidence": round(confidence, 3),
                    "mapping_method": str(label.get("mapping_method") or "dictionary"),
                    "needs_review": needs_review,
                    "needs_text": not bool(text),
                }
            )

        result = {
            "version": "1.0",
            "source": "ocr_auto_label_placement",
            "image_width": image_width,
            "image_height": image_height,
            "labels": labels,
            "unmapped_texts": unmapped_texts,
            "warnings": warnings,
            "needs_manual_review": self._needs_manual_review(labels, unmapped_texts, warnings),
        }
        self.save_debug_image(image_path, result, ocr_result)
        return result

    def save_debug_image(self, image_path: Path, auto_suggestions: dict, ocr_result: dict | None = None) -> Path:
        debug_path = image_path.parent / AUTO_LABEL_DEBUG_FILENAME
        try:
            with Image.open(image_path) as image:
                debug = image.convert("RGB")
        except OSError:
            return debug_path

        draw = ImageDraw.Draw(debug)
        font = ImageFont.load_default()

        for item in (ocr_result or {}).get("texts", []) if isinstance(ocr_result, dict) else []:
            bbox = self._coerce_bbox(item.get("bbox"), debug.width, debug.height)
            if bbox:
                draw.rectangle(bbox, outline=(50, 115, 220), width=2)

        for item in auto_suggestions.get("unmapped_texts", []) if isinstance(auto_suggestions, dict) else []:
            bbox = self._coerce_bbox(item.get("bbox"), debug.width, debug.height)
            if bbox:
                draw.rectangle(bbox, outline=(220, 60, 50), width=2)
                draw.text((bbox[0], max(0, bbox[1] - 12)), str(item.get("text") or "unmapped"), fill=(220, 60, 50), font=font)

        for label in auto_suggestions.get("labels", []) if isinstance(auto_suggestions, dict) else []:
            bbox = self._coerce_bbox(label.get("bbox"), debug.width, debug.height)
            if not bbox:
                continue
            color = (225, 176, 30) if label.get("needs_review") else (40, 150, 80)
            draw.rectangle(bbox, outline=color, width=3)
            draw.text((bbox[0], max(0, bbox[1] - 12)), str(label.get("text") or "label"), fill=color, font=font)

        debug.save(debug_path, format="PNG")
        return debug_path

    def _choose_label_box(self, image_path: Path, bbox: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
        try:
            with Image.open(image_path) as image:
                width, height = image.size
                candidates = [
                    self._expand_bbox(bbox, width, height, 28, 10),
                    self._expand_bbox(bbox, width, height, 55, 18),
                    self._expand_bbox(bbox, width, height, 80, 25),
                ]
                return min(candidates, key=lambda candidate: self._edge_density(image, candidate))
        except OSError:
            return bbox

    @staticmethod
    def _expand_bbox(
        bbox: tuple[int, int, int, int],
        image_width: int,
        image_height: int,
        expand_x: int,
        expand_y: int,
    ) -> tuple[int, int, int, int]:
        x0, y0, x1, y1 = bbox
        return (
            max(0, x0 - expand_x),
            max(0, y0 - expand_y),
            min(image_width, x1 + expand_x),
            min(image_height, y1 + expand_y),
        )

    @staticmethod
    def _edge_density(image: Image.Image, bbox: tuple[int, int, int, int]) -> float:
        x0, y0, x1, y1 = bbox
        if x1 <= x0 or y1 <= y0:
            return 1.0
        crop = image.crop(bbox).convert("L").filter(ImageFilter.FIND_EDGES)
        histogram = crop.histogram()
        total = max(1, crop.width * crop.height)
        edge_pixels = sum(count for value, count in enumerate(histogram) if value > 48)
        return edge_pixels / total

    @staticmethod
    def _text_fits(text: str, bbox: tuple[int, int, int, int]) -> bool:
        if not text:
            return False
        x0, y0, x1, y1 = bbox
        width = max(1, x1 - x0 - 8)
        height = max(1, y1 - y0 - 6)
        image = Image.new("RGB", (1, 1))
        draw = ImageDraw.Draw(image)
        for font_size in range(min(34, height), 7, -1):
            font = _load_font(font_size)
            text_bbox = draw.textbbox((0, 0), text, font=font)
            if text_bbox[2] - text_bbox[0] <= width and text_bbox[3] - text_bbox[1] <= height:
                return True
        return False

    @staticmethod
    def _needs_manual_review(labels: list[dict], unmapped_texts: list, warnings: list[str]) -> bool:
        if warnings or unmapped_texts:
            return True
        if not labels:
            return True
        return any(label.get("needs_review") or not str(label.get("text") or "").strip() for label in labels)

    @staticmethod
    def _image_size(image_path: Path) -> tuple[int | None, int | None]:
        try:
            with Image.open(image_path) as image:
                return image.size
        except OSError:
            return None, None

    @staticmethod
    def _coerce_bbox(value, image_width: int | None, image_height: int | None) -> tuple[int, int, int, int] | None:
        if not isinstance(value, list) or len(value) != 4:
            return None
        try:
            x0, y0, x1, y1 = [float(item) for item in value]
        except (TypeError, ValueError):
            return None
        if image_width is not None:
            x0 = max(0, min(image_width, x0))
            x1 = max(0, min(image_width, x1))
        if image_height is not None:
            y0 = max(0, min(image_height, y0))
            y1 = max(0, min(image_height, y1))
        left, right = sorted((int(round(x0)), int(round(x1))))
        top, bottom = sorted((int(round(y0)), int(round(y1))))
        if right <= left or bottom <= top:
            return None
        return left, top, right, bottom

    @staticmethod
    def _coerce_float(value, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default


def create_manual_labels_from_auto_suggestions(auto_suggestions: dict, confidence_threshold: float = 0.85) -> dict:
    labels = []
    for item in auto_suggestions.get("labels", []) if isinstance(auto_suggestions, dict) else []:
        bbox = item.get("bbox")
        text = str(item.get("text") or "").strip()
        if not isinstance(bbox, list) or len(bbox) != 4:
            continue
        labels.append(
            {
                "id": str(item.get("id") or f"label_{len(labels) + 1}"),
                "text": text,
                "bbox": [int(round(float(value))) for value in bbox],
                "locked": False,
                "needs_text": not bool(text),
                "confidence": item.get("confidence"),
                "original_text": item.get("original_text"),
                "needs_review": bool(item.get("needs_review"))
                or _coerce_confidence(item.get("confidence")) < confidence_threshold,
            }
        )
    return {
        "version": "1.0",
        "source": "auto_ocr",
        "needs_manual_review": True,
        "labels": labels,
    }


def can_auto_apply_labels(auto_suggestions: dict, confidence_threshold: float = 0.85) -> bool:
    labels = auto_suggestions.get("labels", []) if isinstance(auto_suggestions, dict) else []
    if not labels:
        return False
    for label in labels:
        if not str(label.get("text") or "").strip():
            return False
        if label.get("needs_review"):
            return False
        if _coerce_confidence(label.get("confidence")) < confidence_threshold:
            return False
    return not auto_suggestions.get("unmapped_texts")


def _coerce_confidence(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _load_font(font_size: int):
    for font_name in ("arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(font_name, font_size)
        except OSError:
            continue
    return ImageFont.load_default()
