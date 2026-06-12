from __future__ import annotations

from pathlib import Path
import json

from fastapi import HTTPException
from PIL import Image, ImageDraw, ImageFont

from app.schemas import FloorplanAnalysis


LABEL_TRANSLATIONS = {
    "リビング": "Living Room",
    "K": "Kitchen",
    "洋室": "Bed Room",
    "玄関": "Entrance",
    "洗": "Wash",
    "浴室": "Bath Room",
    "トイレ": "Toilet",
    "収納": "Closet",
    "WIC": "Closet",
    "バルコニー": "Balcony",
}

ROOM_TYPE_LABELS = {
    "living_room": "Living Room",
    "bedroom": "Bed Room",
    "kitchen": "Kitchen",
    "dining_kitchen": "Dining Kitchen",
    "bathroom": "Bath Room",
    "toilet": "Toilet",
    "washroom": "Wash",
    "closet": "Closet",
    "walk_in_closet": "Closet",
    "entrance": "Entrance",
    "balcony": "Balcony",
    "hallway": "Hallway",
}

ALLOWED_LABEL_MODES = {"translate", "remove"}


def edit_output_labels(
    output_image_path: Path,
    analysis: FloorplanAnalysis,
    mode: str = "translate",
    language: str = "en",
) -> dict:
    label_mode = (mode or "translate").strip().lower()
    if label_mode not in ALLOWED_LABEL_MODES:
        raise HTTPException(status_code=500, detail=f"Unsupported OUTPUT_LABEL_MODE: {mode}. Expected translate or remove.")

    metadata = {
        "enabled": True,
        "mode": label_mode,
        "language": language,
        "status": "skipped",
        "edited_labels": [],
        "warnings": [],
    }

    if (language or "en").strip().lower() != "en":
        metadata["status"] = "needs_review"
        metadata["warnings"].append("Only English label editing is supported in Phase 1.")
        return metadata

    label_targets = _build_label_targets(analysis)
    if not label_targets:
        metadata["status"] = "skipped"
        metadata["warnings"].append("No room label bounding boxes were available; manual label review is required.")
        return metadata

    if not output_image_path.exists():
        raise HTTPException(status_code=404, detail="generated output image not found for label editing")

    try:
        with Image.open(output_image_path) as image:
            editable = image.convert("RGB")
            draw = ImageDraw.Draw(editable)
            font = ImageFont.load_default()
            width, height = editable.size

            for target in label_targets:
                box = _resolve_bbox(target["bbox"], width, height)
                if not box:
                    metadata["warnings"].append(f"Skipped label {target['source_label']} because bbox was invalid.")
                    continue

                x0, y0, x1, y1 = box
                draw.rounded_rectangle(box, radius=4, fill=(255, 253, 248), outline=(229, 223, 214), width=1)
                if label_mode == "translate":
                    text = target["english_label"]
                    text_box = draw.textbbox((0, 0), text, font=font)
                    text_width = text_box[2] - text_box[0]
                    text_height = text_box[3] - text_box[1]
                    text_x = x0 + max(2, ((x1 - x0) - text_width) / 2)
                    text_y = y0 + max(2, ((y1 - y0) - text_height) / 2)
                    draw.text((text_x, text_y), text, fill=(45, 38, 30), font=font)

                metadata["edited_labels"].append(
                    {
                        "source_label": target["source_label"],
                        "english_label": target["english_label"] if label_mode == "translate" else None,
                        "bbox": [x0, y0, x1, y1],
                    }
                )

            editable.save(output_image_path, format="PNG")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"failed to edit output labels: {exc}") from exc

    if metadata["edited_labels"]:
        metadata["status"] = "done"
    else:
        metadata["status"] = "needs_review"
        metadata["warnings"].append("No labels were edited; manual label review is required.")
    return metadata


def disabled_label_edit_metadata() -> dict:
    return {
        "enabled": False,
        "mode": None,
        "language": None,
        "status": "skipped",
        "edited_labels": [],
        "warnings": ["Output label editing is disabled."],
    }


def apply_manual_labels_to_output(output_image_path: Path, manual_labels: dict) -> dict:
    labels = manual_labels.get("labels", []) if isinstance(manual_labels, dict) else []
    if not isinstance(labels, list):
        raise HTTPException(status_code=422, detail="manual_labels.labels must be a list")
    if not output_image_path.exists():
        raise HTTPException(status_code=404, detail="generated output image not found for manual label editing")

    metadata = {
        "enabled": True,
        "mode": "manual",
        "language": "en",
        "status": "skipped",
        "edited_labels": [],
        "warnings": [],
    }

    if not labels:
        metadata["warnings"].append("manual_labels.json contains no labels.")
        return metadata

    try:
        with Image.open(output_image_path) as image:
            editable = image.convert("RGB")
            draw = ImageDraw.Draw(editable)
            image_width, image_height = editable.size

            for label in labels:
                normalized = _normalize_manual_label(label)
                box = _manual_label_box(normalized, image_width, image_height)
                if not box:
                    metadata["warnings"].append(f"Skipped manual label {normalized.get('id') or normalized.get('text')} because box was invalid.")
                    continue

                x0, y0, x1, y1 = box
                text = normalized["text"]
                font = _load_font(normalized["font_size"])
                draw.rounded_rectangle(box, radius=5, fill=(255, 253, 248), outline=(229, 223, 214), width=1)
                text_box = draw.textbbox((0, 0), text, font=font)
                text_width = text_box[2] - text_box[0]
                text_height = text_box[3] - text_box[1]
                if normalized["align"] == "left":
                    text_x = x0 + 8
                elif normalized["align"] == "right":
                    text_x = x1 - text_width - 8
                else:
                    text_x = x0 + ((x1 - x0) - text_width) / 2
                text_y = y0 + ((y1 - y0) - text_height) / 2
                draw.text((max(x0 + 2, text_x), max(y0 + 2, text_y)), text, fill=(45, 38, 30), font=font)

                metadata["edited_labels"].append(
                    {
                        "id": normalized.get("id"),
                        "text": text,
                        "bbox": [x0, y0, x1, y1],
                    }
                )

            editable.save(output_image_path, format="PNG")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"failed to apply manual labels: {exc}") from exc

    metadata["status"] = "done" if metadata["edited_labels"] else "needs_review"
    if not metadata["edited_labels"]:
        metadata["warnings"].append("No manual labels were applied; manual label review is still required.")
    return metadata


def apply_manual_labels(
    image_path: Path,
    manual_labels_path: Path,
    max_padding: int = 4,
) -> dict:
    if not image_path.exists():
        raise HTTPException(status_code=404, detail="generated output image not found for manual labels")
    if not manual_labels_path.exists():
        raise HTTPException(status_code=404, detail="manual_labels.json not found")

    try:
        manual_labels = json.loads(manual_labels_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail=f"failed to read manual_labels.json: {exc}") from exc

    labels = manual_labels.get("labels", []) if isinstance(manual_labels, dict) else []
    if not isinstance(labels, list):
        raise HTTPException(status_code=422, detail="manual_labels.labels must be a list")

    padding = max(0, min(int(max_padding), 4))
    metadata = {
        "method": "manual_labels",
        "labels_processed": 0,
        "labels_skipped": 0,
        "warnings": [],
    }

    try:
        with Image.open(image_path) as image:
            editable = image.convert("RGB")
            draw = ImageDraw.Draw(editable)
            image_width, image_height = editable.size

            for label in labels:
                label_id = str(label.get("id") or "label") if isinstance(label, dict) else "label"
                text = str(label.get("text") or "").strip() if isinstance(label, dict) else ""
                if not text:
                    metadata["labels_skipped"] += 1
                    metadata["warnings"].append(f"Skipped {label_id}: text is empty.")
                    continue

                box = _manual_bbox(label.get("bbox"), image_width, image_height) if isinstance(label, dict) else None
                if not box:
                    metadata["labels_skipped"] += 1
                    metadata["warnings"].append(f"Skipped {label_id}: bbox is invalid.")
                    continue

                inner_box = _inset_box(box, padding)
                text_plan = _fit_text(draw, text, inner_box)
                if not text_plan:
                    metadata["labels_skipped"] += 1
                    metadata["warnings"].append(f"Skipped {label_id}: text does not fit in bbox.")
                    continue

                draw.rounded_rectangle(inner_box, radius=5, fill=(255, 253, 248), outline=(229, 223, 214), width=1)
                _draw_centered_lines(draw, text_plan["lines"], inner_box, text_plan["font"])
                metadata["labels_processed"] += 1

            editable.save(image_path, format="PNG")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"failed to apply manual labels: {exc}") from exc

    return metadata


def _build_label_targets(analysis: FloorplanAnalysis) -> list[dict]:
    targets = []
    for room in analysis.rooms:
        if not room.bounding_box:
            continue
        source_label = room.room_name or room.type
        targets.append(
            {
                "source_label": source_label,
                "english_label": _english_label(source_label, room.type),
                "bbox": room.bounding_box,
            }
        )
    return targets


def _normalize_manual_label(label: dict) -> dict:
    if not isinstance(label, dict):
        raise HTTPException(status_code=422, detail="each manual label must be an object")
    text = str(label.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=422, detail="each manual label requires non-empty text")
    return {
        "id": str(label.get("id") or ""),
        "text": text,
        "x": _coerce_number(label.get("x")),
        "y": _coerce_number(label.get("y")),
        "width": _coerce_number(label.get("width")),
        "height": _coerce_number(label.get("height")),
        "font_size": int(_coerce_number(label.get("font_size"), default=28)),
        "align": str(label.get("align") or "center").strip().lower(),
    }


def _manual_bbox(value, image_width: int, image_height: int) -> tuple[int, int, int, int] | None:
    if not isinstance(value, list) or len(value) != 4:
        return None
    try:
        x0, y0, x1, y1 = [float(item) for item in value]
    except (TypeError, ValueError):
        return None
    left = max(0, min(image_width - 1, int(round(min(x0, x1)))))
    top = max(0, min(image_height - 1, int(round(min(y0, y1)))))
    right = max(0, min(image_width, int(round(max(x0, x1)))))
    bottom = max(0, min(image_height, int(round(max(y0, y1)))))
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def _inset_box(box: tuple[int, int, int, int], padding: int) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = box
    if x1 - x0 <= padding * 2 or y1 - y0 <= padding * 2:
        return box
    return x0 + padding, y0 + padding, x1 - padding, y1 - padding


def _fit_text(draw: ImageDraw.ImageDraw, text: str, box: tuple[int, int, int, int]) -> dict | None:
    x0, y0, x1, y1 = box
    max_width = max(1, x1 - x0 - 6)
    max_height = max(1, y1 - y0 - 4)
    for font_size in range(min(42, max_height), 7, -1):
        font = _load_font(font_size)
        for lines in ([text], _wrap_two_lines(text)):
            if not lines:
                continue
            width, height = _lines_size(draw, lines, font)
            if width <= max_width and height <= max_height:
                return {"lines": lines, "font": font}
    return None


def _wrap_two_lines(text: str) -> list[str] | None:
    words = text.split()
    if len(words) < 2:
        return None
    midpoint = len(words) // 2
    return [" ".join(words[:midpoint]), " ".join(words[midpoint:])]


def _lines_size(draw: ImageDraw.ImageDraw, lines: list[str], font) -> tuple[int, int]:
    widths = []
    heights = []
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        widths.append(bbox[2] - bbox[0])
        heights.append(bbox[3] - bbox[1])
    return max(widths or [0]), sum(heights) + max(0, len(lines) - 1) * 4


def _draw_centered_lines(draw: ImageDraw.ImageDraw, lines: list[str], box: tuple[int, int, int, int], font) -> None:
    x0, y0, x1, y1 = box
    _, total_height = _lines_size(draw, lines, font)
    current_y = y0 + ((y1 - y0) - total_height) / 2
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        line_width = bbox[2] - bbox[0]
        line_height = bbox[3] - bbox[1]
        draw.text((x0 + ((x1 - x0) - line_width) / 2, current_y), line, fill=(45, 38, 30), font=font)
        current_y += line_height + 4


def _manual_label_box(label: dict, image_width: int, image_height: int) -> tuple[int, int, int, int] | None:
    x = label["x"]
    y = label["y"]
    width = label["width"]
    height = label["height"]
    if width <= 0 or height <= 0:
        return None

    left = max(0, min(image_width - 1, int(round(x))))
    top = max(0, min(image_height - 1, int(round(y))))
    right = max(0, min(image_width, int(round(x + width))))
    bottom = max(0, min(image_height, int(round(y + height))))
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def _coerce_number(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _load_font(font_size: int):
    safe_size = max(8, min(96, int(font_size or 28)))
    for font_name in ("arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(font_name, safe_size)
        except OSError:
            continue
    return ImageFont.load_default()


def _english_label(source_label: str | None, room_type: str | None) -> str:
    if source_label and source_label in LABEL_TRANSLATIONS:
        return LABEL_TRANSLATIONS[source_label]
    if room_type and room_type in ROOM_TYPE_LABELS:
        return ROOM_TYPE_LABELS[room_type]
    if source_label:
        return LABEL_TRANSLATIONS.get(source_label.upper(), str(source_label).replace("_", " ").title())
    return "Room"


def _resolve_bbox(bbox: list[float], width: int, height: int) -> tuple[int, int, int, int] | None:
    if len(bbox) != 4:
        return None
    try:
        x0, y0, x1, y1 = [float(value) for value in bbox]
    except (TypeError, ValueError):
        return None

    if max(abs(x0), abs(y0), abs(x1), abs(y1)) <= 1.5:
        x0, x1 = x0 * width, x1 * width
        y0, y1 = y0 * height, y1 * height

    left = max(0, min(width - 1, int(round(min(x0, x1)))))
    top = max(0, min(height - 1, int(round(min(y0, y1)))))
    right = max(0, min(width, int(round(max(x0, x1)))))
    bottom = max(0, min(height, int(round(max(y0, y1)))))

    if right - left < 10 or bottom - top < 8:
        cx = (left + right) // 2
        cy = (top + bottom) // 2
        left = max(0, cx - 45)
        right = min(width, cx + 45)
        top = max(0, cy - 12)
        bottom = min(height, cy + 12)

    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom
