from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException
from PIL import Image, ImageDraw, ImageFont


class RoomZoneDebugRenderer:
    COLORS = [
        (221, 94, 86, 230),
        (70, 140, 215, 230),
        (65, 165, 110, 230),
        (198, 140, 40, 230),
        (150, 95, 190, 230),
    ]

    def render(
        self,
        normalized_floorplan_path: Path,
        interior_area_mask_path: Path,
        room_zones: dict,
        interior_debug_path: Path,
        room_zones_debug_path: Path,
    ) -> None:
        if not normalized_floorplan_path.exists():
            raise HTTPException(status_code=404, detail="normalized floorplan not found for room zone debug rendering")
        if not interior_area_mask_path.exists():
            raise HTTPException(status_code=404, detail="interior area mask not found for room zone debug rendering")

        try:
            base = Image.open(normalized_floorplan_path).convert("RGBA")
            mask = Image.open(interior_area_mask_path).convert("L")
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"failed to load room zone debug images: {exc}") from exc

        interior_overlay = Image.new("RGBA", base.size, (80, 160, 110, 0))
        interior_overlay.putalpha(mask.point(lambda value: 72 if value > 0 else 0))
        interior_debug = Image.alpha_composite(base, interior_overlay)
        interior_debug.save(interior_debug_path, format="PNG")

        debug = interior_debug.copy()
        draw = ImageDraw.Draw(debug, "RGBA")
        font = ImageFont.load_default()
        for index, zone in enumerate(room_zones.get("zones", []) if isinstance(room_zones, dict) else []):
            color = self.COLORS[index % len(self.COLORS)]
            bbox = zone.get("bbox")
            if isinstance(bbox, list) and len(bbox) == 4:
                draw.rectangle(tuple(bbox), outline=color, width=4)
            polygon = zone.get("polygon")
            if isinstance(polygon, list) and len(polygon) >= 3:
                points = [tuple(point) for point in polygon if isinstance(point, list) and len(point) == 2]
                if len(points) >= 3:
                    draw.line([*points, points[0]], fill=color, width=2)
            label = f"{zone.get('id', '?')} {zone.get('type', 'unknown')}"
            x = int(zone.get("center_x") or (bbox[0] if isinstance(bbox, list) else 12))
            y = int(zone.get("center_y") or (bbox[1] if isinstance(bbox, list) else 12))
            text_box = draw.textbbox((x, y), label, font=font)
            pad = 4
            draw.rounded_rectangle(
                (text_box[0] - pad, text_box[1] - pad, text_box[2] + pad, text_box[3] + pad),
                radius=4,
                fill=(255, 253, 248, 225),
                outline=color,
                width=1,
            )
            draw.text((x, y), label, fill=(35, 30, 24, 255), font=font)

        debug.save(room_zones_debug_path, format="PNG")
