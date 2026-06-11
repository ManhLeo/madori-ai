from __future__ import annotations

from app.schemas import FloorplanAnalysis
from app.services.output_text_editor import ROOM_TYPE_LABELS


def build_manual_labels_from_analysis(
    analysis: FloorplanAnalysis,
    output_width: int,
    output_height: int,
    reference_width: int | None = None,
    reference_height: int | None = None,
) -> dict:
    labels = []
    for index, room in enumerate(analysis.rooms):
        if not room.bounding_box:
            continue

        box = _resolve_box(
            room.bounding_box,
            output_width=output_width,
            output_height=output_height,
            reference_width=reference_width,
            reference_height=reference_height,
        )
        if not box:
            continue

        x0, y0, x1, y1 = box
        label_width = min(240, max(120, int((x1 - x0) * 0.46)))
        label_height = min(64, max(40, int((y1 - y0) * 0.16)))
        labels.append(
            {
                "id": f"label_{_slug(room.type)}_{index + 1}",
                "text": ROOM_TYPE_LABELS.get(room.type, room.type.replace("_", " ").title()),
                "x": int((x0 + x1 - label_width) / 2),
                "y": int((y0 + y1 - label_height) / 2),
                "width": label_width,
                "height": label_height,
                "font_size": 28,
                "align": "center",
            }
        )

    return {"version": "1.0", "labels": labels}


def empty_manual_labels() -> dict:
    return {"version": "1.0", "labels": []}


def _resolve_box(
    bbox: list[float],
    output_width: int,
    output_height: int,
    reference_width: int | None,
    reference_height: int | None,
) -> tuple[int, int, int, int] | None:
    if len(bbox) != 4:
        return None
    try:
        x0, y0, x1, y1 = [float(value) for value in bbox]
    except (TypeError, ValueError):
        return None

    if max(abs(x0), abs(y0), abs(x1), abs(y1)) <= 1.5:
        x0, x1 = x0 * output_width, x1 * output_width
        y0, y1 = y0 * output_height, y1 * output_height
    elif reference_width and reference_height:
        x_scale = output_width / reference_width
        y_scale = output_height / reference_height
        x0, x1 = x0 * x_scale, x1 * x_scale
        y0, y1 = y0 * y_scale, y1 * y_scale

    left = max(0, min(output_width - 1, int(round(min(x0, x1)))))
    top = max(0, min(output_height - 1, int(round(min(y0, y1)))))
    right = max(0, min(output_width, int(round(max(x0, x1)))))
    bottom = max(0, min(output_height, int(round(max(y0, y1)))))
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def _slug(value: str) -> str:
    return "".join(char if char.isalnum() else "_" for char in value.lower()).strip("_") or "room"
