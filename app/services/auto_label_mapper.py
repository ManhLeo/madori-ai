from __future__ import annotations

import re
import unicodedata


ROOM_LABEL_MAP = {
    "リビング": "Living Room",
    "LDK": "Living Room",
    "DK": "Kitchen",
    "K": "Kitchen",
    "キッチン": "Kitchen",
    "玄関": "Entrance",
    "トイレ": "Toilet",
    "便所": "Toilet",
    "浴室": "Bath Room",
    "バス": "Bath Room",
    "洗": "Wash Room",
    "洗面": "Wash Room",
    "洗面所": "Wash Room",
    "洋室": "Bed Room",
    "寝室": "Bed Room",
    "収納": "Closet",
    "クローゼット": "Closet",
    "CL": "Closet",
    "WIC": "Closet",
    "バルコニー": "Balcony",
}


class AutoLabelMapper:
    def map_ocr_texts(self, ocr_result: dict) -> dict:
        labels = []
        unmapped_texts = []
        warnings = list(ocr_result.get("warnings", [])) if isinstance(ocr_result, dict) else []
        texts = self._merge_nearby_tokens(ocr_result.get("texts", []) if isinstance(ocr_result, dict) else [])

        for item in texts:
            original_text = str(item.get("text") or "").strip()
            normalized_text = self._normalize_text(original_text)
            if not normalized_text or self._is_dimension_text(normalized_text):
                continue

            mapped = ROOM_LABEL_MAP.get(normalized_text)
            if not mapped:
                unmapped_texts.append(item)
                continue

            confidence = float(item.get("confidence") or 0.0)
            labels.append(
                {
                    "id": f"auto_label_{len(labels) + 1}",
                    "original_text": original_text,
                    "text": mapped,
                    "original_bbox": item.get("bbox"),
                    "bbox": item.get("bbox"),
                    "confidence": round(confidence, 3),
                    "mapping_method": "dictionary",
                    "needs_review": False,
                }
            )

        return {
            "version": "1.0",
            "source": "ocr_dictionary_mapping",
            "labels": labels,
            "unmapped_texts": unmapped_texts,
            "warnings": warnings,
        }

    @staticmethod
    def _normalize_text(value: str) -> str:
        text = unicodedata.normalize("NFKC", value)
        return re.sub(r"\s+", "", text).upper() if text.isascii() else re.sub(r"\s+", "", text)

    @staticmethod
    def _is_dimension_text(value: str) -> bool:
        if re.fullmatch(r"[0-9.]+", value):
            return True
        return bool(re.search(r"([0-9.]+(M2|㎡|畳|帖)|^[0-9.]+J$)", value, re.IGNORECASE))

    def _merge_nearby_tokens(self, texts: list[dict]) -> list[dict]:
        # Google Vision usually returns complete Japanese room labels as words.
        # Keep this hook lightweight for now while preserving the API contract.
        return texts
