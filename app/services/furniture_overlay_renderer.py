from __future__ import annotations

import logging
import math
from pathlib import Path

from fastapi import HTTPException
from PIL import Image, ImageDraw, ImageFont

from app.schemas import FloorplanAnalysis, FurnitureItem, FurniturePlan, RoomFurniturePlan, RoomInfo


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class FurnitureOverlayRenderer:
    """Creates debug-only furniture overlays for inspecting furniture plans.

    These images are run artifacts only. They must not be sent to image providers
    or shown as the final generated illustration.
    """

    def render_overlay(
        self,
        floorplan_path: Path,
        furniture_plan: FurniturePlan,
        output_path: Path,
        analysis: FloorplanAnalysis | None = None,
    ) -> Path:
        if not floorplan_path.exists():
            raise HTTPException(status_code=404, detail="floorplan image not found")

        try:
            base_image = Image.open(floorplan_path).convert("RGBA")
        except OSError as exc:
            raise HTTPException(status_code=500, detail="failed to open floorplan image") from exc

        overlay = Image.new("RGBA", base_image.size, (0, 0, 0, 0))
        debug_overlay = Image.new("RGBA", base_image.size, (0, 0, 0, 0))
        width, height = base_image.size
        room_lookup = self._build_room_lookup(analysis)

        for room_plan in furniture_plan.room_plans:
            for index, item in enumerate(room_plan.items):
                placement = self._draw_item_symbol(
                    overlay,
                    debug_overlay,
                    item,
                    width,
                    height,
                    room_plan,
                    index,
                    room_lookup,
                    analysis,
                )
                if placement is not None:
                    self._draw_item_label(
                        debug_overlay,
                        item.item,
                        placement[0],
                        placement[1],
                    )

        composed = Image.alpha_composite(base_image, overlay)
        debug_composed = Image.alpha_composite(composed, self._build_debug_room_overlay(base_image.size, analysis))
        debug_composed = Image.alpha_composite(debug_composed, debug_overlay)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            composed.save(output_path)
            debug_output_path = output_path.with_name("overlay_floorplan_debug.png")
            if debug_output_path != output_path:
                debug_composed.save(debug_output_path)
        except OSError as exc:
            raise HTTPException(status_code=500, detail="failed to save overlay floorplan image") from exc

        return output_path

    def _draw_item_symbol(
        self,
        canvas: Image.Image,
        debug_canvas: Image.Image,
        item: FurnitureItem,
        image_width: int,
        image_height: int,
        room_plan: RoomFurniturePlan,
        item_index: int,
        room_lookup: dict[str, list[RoomInfo]],
        analysis: FloorplanAnalysis | None,
    ) -> tuple[float, float] | None:
        room_info = self._find_room_info(
            room_lookup,
            item.room,
            room_plan.room_type,
            room_plan.room_name,
        )
        if room_info is None or not room_info.bounding_box:
            logger.warning("SKIPPED furniture item because no room bbox was found")
            return None

        bbox_pixels = self._bbox_to_pixels(room_info.bounding_box, image_width, image_height)
        safe_bbox = self._safe_bbox_pixels(room_info.bounding_box, image_width, image_height)
        if bbox_pixels is None or safe_bbox is None:
            logger.warning("SKIPPED furniture item because no room bbox was found")
            return None

        center_x, center_y = self._resolve_item_center(
            item,
            image_width,
            image_height,
            room_plan,
            item_index,
            room_info,
            safe_bbox,
        )

        logger.warning(
            "overlay item=%s room=%s bbox=%s computed_pixel=(%.1f, %.1f)",
            item.item,
            item.room,
            room_info.bounding_box,
            center_x,
            center_y,
        )
        item_name = self._normalize_item_name(item.item)

        safe_width = safe_bbox[2] - safe_bbox[0]
        safe_height = safe_bbox[3] - safe_bbox[1]
        if safe_width < 8 or safe_height < 8:
            logger.warning("SKIPPED furniture item because symbol too small to render")
            return None

        symbol_width, symbol_height = self._symbol_size(item_name, image_width, image_height, safe_bbox)
        symbol_width = max(symbol_width, 8)
        symbol_height = max(symbol_height, 8)
        symbol = Image.new("RGBA", (symbol_width, symbol_height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(symbol)

        fill, outline = self._item_palette(item_name)
        try:
            self._draw_symbol(draw, item_name, symbol_width, symbol_height, fill, outline)
        except Exception:
            logger.exception("furniture symbol drawing failed; using placeholder item=%s", item_name)
            self._draw_simple_placeholder(draw, symbol_width, symbol_height, fill, outline)

        _, _, rotation = self.get_default_position(room_plan.room_type, item_name, item.position_hint)
        if rotation:
            symbol = symbol.rotate(rotation, expand=True, resample=Image.Resampling.BICUBIC)

        symbol = self._resize_symbol_to_fit_bbox(symbol, safe_bbox)
        if symbol is None:
            logger.warning("SKIPPED furniture item because it cannot fit inside room bbox")
            return None

        left, top = self._fit_symbol_inside_bbox(center_x, center_y, symbol.width, symbol.height, safe_bbox)
        self._paste_clamped(canvas, symbol, left, top, safe_bbox)
        return center_x, center_y

    def _resolve_item_center(
        self,
        item: FurnitureItem,
        image_width: int,
        image_height: int,
        room_plan: RoomFurniturePlan,
        item_index: int,
        room_info: RoomInfo,
        safe_bbox: tuple[float, float, float, float],
    ) -> tuple[float, float]:
        _ = image_width
        _ = image_height
        _ = item_index
        _ = room_info
        x1, y1, x2, y2 = safe_bbox
        box_width = max(1.0, x2 - x1)
        box_height = max(1.0, y2 - y1)
        local_x, local_y, _rotation = self.get_default_position(
            room_plan.room_type,
            self._normalize_item_name(item.item),
            item.position_hint,
        )

        center_x = x1 + box_width * local_x
        center_y = y1 + box_height * local_y
        center_x, center_y = self._clamp_point_to_bbox(center_x, center_y, safe_bbox)
        return center_x, center_y

    def get_default_position(self, room_type, item_name, position_hint) -> tuple[float, float, float]:
        normalized_room = self._normalize_room_lookup_value(room_type)[0] if room_type else "unknown"
        normalized_item = self._normalize_item_name(item_name)
        hint = self._normalize_text(position_hint)

        if "top" in hint or "window" in hint:
            hinted = (0.5, 0.18, 0.0)
        elif "bottom" in hint:
            hinted = (0.5, 0.82, 0.0)
        elif "left" in hint or "kitchen_counter" in hint or "counter" in hint:
            hinted = (0.18, 0.5, 90.0)
        elif "right" in hint or "closet" in hint:
            hinted = (0.82, 0.5, 90.0)
        elif "center" in hint or "centre" in hint:
            hinted = (0.5, 0.5, 0.0)
        elif "balcony" in hint:
            hinted = (0.75, 0.25, 0.0)
        else:
            hinted = None

        if normalized_room == "bedroom":
            if normalized_item == "bed":
                return 0.50, 0.55, 0.0
            if normalized_item == "wardrobe" or "storage" in normalized_item or "dresser" in normalized_item:
                return 0.50, 0.82, 0.0
            if normalized_item == "desk":
                return 0.50, 0.18, 0.0
            if "nightstand" in normalized_item or "bedside" in normalized_item:
                return 0.72, 0.64, 0.0
            return hinted or (0.5, 0.5, 0.0)

        if normalized_room == "living_room":
            if normalized_item == "sofa" or "seating" in normalized_item:
                return 0.45, 0.58, 0.0
            if normalized_item == "coffee_table":
                return 0.55, 0.58, 0.0
            if normalized_item == "tv_stand":
                return 0.50, 0.18, 0.0
            if normalized_item == "dining_table":
                return 0.78, 0.35, 0.0
            if normalized_item == "chair":
                return 0.78, 0.48, 0.0
            return hinted or (0.5, 0.54, 0.0)

        if normalized_room in {"kitchen", "dining_kitchen"}:
            if "refrigerator" in normalized_item or "fridge" in normalized_item:
                return 0.18, 0.72, 0.0
            if "counter" in normalized_item or "sink" in normalized_item or "stove" in normalized_item:
                return 0.32, 0.5, 0.0
            if "dining" in normalized_item:
                return 0.62, 0.52, 0.0
            return hinted or (0.35, 0.55, 0.0)

        if normalized_room == "entrance":
            if "shoe" in normalized_item or "cabinet" in normalized_item:
                return 0.78, 0.42, 90.0
            if "rug" in normalized_item:
                return 0.5, 0.62, 0.0
            return hinted or (0.5, 0.55, 0.0)

        if normalized_room == "washroom":
            if "washing" in normalized_item or "machine" in normalized_item:
                return 0.38, 0.62, 0.0
            if "storage" in normalized_item or "shelf" in normalized_item:
                return 0.78, 0.32, 90.0
            return hinted or (0.5, 0.5, 0.0)

        if normalized_room == "bathroom":
            if "storage" in normalized_item or "shelf" in normalized_item or "plant" in normalized_item:
                return 0.78, 0.22, 0.0
            return hinted or (0.5, 0.5, 0.0)

        if normalized_room == "toilet":
            if "shelf" in normalized_item or "storage" in normalized_item:
                return 0.72, 0.22, 0.0
            return hinted or (0.5, 0.5, 0.0)

        if normalized_room == "balcony":
            if "plant" in normalized_item:
                return 0.28, 0.28, 0.0
            if "chair" in normalized_item or "table" in normalized_item:
                return 0.62, 0.5, 0.0
            return hinted or (0.5, 0.5, 0.0)

        if normalized_room == "walk_in_closet":
            if "rail" in normalized_item:
                return 0.82, 0.5, 90.0
            return 0.5, 0.5, 0.0

        return hinted or (0.5, 0.5, 0.0)

    def _resolve_from_room_position(
        self,
        room_position: str | None,
        item_name: str,
        item_index: int,
        image_width: int,
        image_height: int,
    ) -> tuple[float, float]:
        base_x, base_y = {
            "top_left": (0.26, 0.26),
            "top_right": (0.74, 0.26),
            "bottom_left": (0.26, 0.74),
            "bottom_right": (0.74, 0.74),
            "left": (0.24, 0.5),
            "right": (0.76, 0.5),
            "top": (0.5, 0.24),
            "bottom": (0.5, 0.76),
            "center": (0.5, 0.5),
            "unknown": (0.5, 0.5),
            None: (0.5, 0.5),
        }.get(room_position, (0.5, 0.5))
        offset_x, offset_y = self._local_item_position(item_name, item_index)
        return (
            self._clamp_coordinate((base_x + offset_x * 0.22) * image_width, image_width),
            self._clamp_coordinate((base_y + offset_y * 0.22) * image_height, image_height),
        )

    def _local_item_position(self, item_name: str, item_index: int) -> tuple[float, float]:
        positions = {
            "bed": (0.45, 0.48),
            "single_bed": (0.45, 0.48),
            "semi_double_bed": (0.45, 0.48),
            "double_bed": (0.45, 0.48),
            "sofa": (0.35, 0.62),
            "dining_table": (0.5, 0.5),
            "chair": (0.72, 0.28),
            "outdoor_chair": (0.72, 0.28),
            "wardrobe": (0.8, 0.35),
            "storage_shelf": (0.82, 0.35),
            "desk": (0.65, 0.78),
            "study_desk": (0.65, 0.78),
            "compact_work_desk": (0.65, 0.78),
            "tv_stand": (0.78, 0.58),
            "tv_shelf": (0.78, 0.58),
            "shoe_cabinet": (0.75, 0.52),
            "small_rug": (0.5, 0.72),
            "small_plant": (0.2, 0.2),
            "small_storage": (0.8, 0.72),
            "storage_boxes": (0.5, 0.5),
        }
        if item_name in positions:
            return positions[item_name]

        fallback_positions = [
            (0.35, 0.45),
            (0.62, 0.45),
            (0.35, 0.7),
            (0.62, 0.7),
        ]
        return fallback_positions[item_index % len(fallback_positions)]

    def _build_room_lookup(self, analysis: FloorplanAnalysis | None) -> dict[str, list[RoomInfo]]:
        lookup: dict[str, list[RoomInfo]] = {}
        if analysis is None:
            return lookup

        for room in analysis.rooms:
            for key in self._room_lookup_keys(room):
                lookup.setdefault(key, []).append(room)
        return lookup

    def _find_room_info(
        self,
        room_lookup: dict[str, list[RoomInfo]],
        item_room: str | None,
        room_type: str,
        room_name: str | None,
    ) -> RoomInfo | None:
        for key in self._room_match_candidates(item_room, room_type, room_name):
            if key in room_lookup and room_lookup[key]:
                return room_lookup[key][0]
        return None

    def _room_lookup_keys(self, room: RoomInfo) -> list[str]:
        keys: list[str] = []
        for value in (room.type, room.room_name):
            keys.extend(self._room_match_candidates(value, None, None))
        return self._dedupe_preserve_order([key for key in keys if key])

    def _dedupe_preserve_order(self, values):
        seen = set()
        result = []
        for value in values:
            if value is None:
                continue
            if value in seen:
                continue
            seen.add(value)
            result.append(value)
        return result

    def _normalize_text(self, value):
        if value is None:
            return ""
        text = str(value).strip().lower()
        text = text.replace("-", "_").replace(" ", "_")
        while "__" in text:
            text = text.replace("__", "_")
        return text

    def _normalize_item_name(self, value) -> str:
        item_name = self._normalize_text(value)
        aliases = {
            "bed": "bed",
            "single_bed": "bed",
            "double_bed": "bed",
            "semi_double_bed": "bed",
            "compact_sofa": "sofa",
            "two_seater_sofa": "sofa",
            "sofa": "sofa",
            "coffee_table": "coffee_table",
            "small_coffee_table": "coffee_table",
            "dining_table": "dining_table",
            "small_dining_table": "dining_table",
            "dining_chair": "chair",
            "chair": "chair",
            "tv_stand": "tv_stand",
            "tv_shelf": "tv_stand",
            "closet": "wardrobe",
            "wardrobe": "wardrobe",
            "dresser_chest_of_drawers": "wardrobe",
            "chest_of_drawers": "wardrobe",
            "shelving_unit": "wardrobe",
            "small_desk": "desk",
            "compact_work_desk": "desk",
            "study_desk": "desk",
            "desk": "desk",
            "refrigerator": "refrigerator",
            "fridge": "refrigerator",
            "washing_machine": "washing_machine",
            "bathtub": "bathtub",
            "bath_tub": "bathtub",
            "toilet": "toilet",
            "toilet_bowl": "toilet",
            "shoe_cabinet": "shoe_cabinet",
            "rug": "rug",
            "entrance_rug": "rug",
            "small_rug": "rug",
            "plant": "plant",
            "plant_pot": "plant",
            "small_plant": "plant",
        }
        if item_name in aliases:
            return aliases[item_name]
        if "sofa" in item_name:
            return "sofa"
        if "coffee" in item_name and "table" in item_name:
            return "coffee_table"
        if "dining" in item_name and "chair" in item_name:
            return "chair"
        if "dining" in item_name and "table" in item_name:
            return "dining_table"
        if "tv" in item_name:
            return "tv_stand"
        if "bed" in item_name and "night" not in item_name:
            return "bed"
        if "night" in item_name or "bedside" in item_name:
            return "chair"
        if "wardrobe" in item_name or "dresser" in item_name:
            return "wardrobe"
        if "closet" in item_name or "shelving" in item_name or "rail" in item_name:
            return "wardrobe"
        if "desk" in item_name:
            return "desk"
        if "chair" in item_name:
            return "chair"
        if "shoe" in item_name:
            return "shoe_cabinet"
        if "rug" in item_name:
            return "rug"
        if "plant" in item_name:
            return "plant"
        if "bathtub" in item_name or "bath_tub" in item_name:
            return "bathtub"
        if "toilet" in item_name:
            return "toilet"
        if "storage" in item_name or "shelf" in item_name:
            return "wardrobe"
        if "washing" in item_name or "machine" in item_name:
            return "washing_machine"
        if "refrigerator" in item_name or "fridge" in item_name:
            return "refrigerator"
        return item_name

    def _room_match_candidates(
        self,
        item_room: str | None,
        room_type: str | None,
        room_name: str | None,
    ) -> list[str]:
        candidates: list[str] = []
        for value in (item_room, room_type, room_name):
            if not value:
                continue
            normalized = self._normalize_room_lookup_value(value)
            if normalized:
                candidates.extend(normalized)
        return self._dedupe_preserve_order(candidates)

    def _normalize_room_lookup_value(self, value: str) -> list[str]:
        normalized = self._normalize_text(value)
        aliases = {
            "living": "living_room",
            "living room": "living_room",
            "living_room": "living_room",
            "living dining kitchen": "living_room",
            "living dining": "living_room",
            "ldk": "living_room",
            "bed": "bedroom",
            "bedroom": "bedroom",
            "bed room": "bedroom",
            "k": "kitchen",
            "kitchen": "kitchen",
            "wash": "washroom",
            "washroom": "washroom",
            "洗": "washroom",
            "bath": "bathroom",
            "bathroom": "bathroom",
            "wc": "toilet",
            "toilet": "toilet",
            "entrance": "entrance",
            "genkan": "entrance",
            "玄関": "entrance",
            "balcony": "balcony",
            "バルコニー": "balcony",
            "wic": "walk_in_closet",
            "closet": "walk_in_closet",
            "walk in closet": "walk_in_closet",
            "walk in closet wic": "walk_in_closet",
            "walk in closet / wic": "walk_in_closet",
            "walkin closet": "walk_in_closet",
            "walk_in_closet": "walk_in_closet",
        }
        result = [normalized]
        if normalized in aliases:
            result.insert(0, aliases[normalized])
        if "living" in normalized:
            result.insert(0, "living_room")
        if "bed" in normalized:
            result.insert(0, "bedroom")
        if "kitchen" in normalized:
            result.insert(0, "kitchen")
        if "wash" in normalized:
            result.insert(0, "washroom")
        if "bath" in normalized:
            result.insert(0, "bathroom")
        if "toilet" in normalized or "wc" in normalized:
            result.insert(0, "toilet")
        if "genkan" in normalized or "entrance" in normalized:
            result.insert(0, "entrance")
        if "balcony" in normalized:
            result.insert(0, "balcony")
        if "wic" in normalized or "closet" in normalized:
            result.insert(0, "walk_in_closet")
        return self._dedupe_preserve_order(result)

    def _bbox_to_pixels(
        self,
        bounding_box: list[float] | tuple[float, float, float, float],
        image_width: int,
        image_height: int,
    ) -> tuple[float, float, float, float] | None:
        if len(bounding_box) < 4:
            return None

        try:
            x1, y1, x2, y2 = [float(value) for value in bounding_box[:4]]
        except (TypeError, ValueError):
            return None

        if self._bbox_looks_normalized(bounding_box):
            return (
                x1 * image_width,
                y1 * image_height,
                x2 * image_width,
                y2 * image_height,
            )

        return (
            max(0.0, min(float(image_width), x1)),
            max(0.0, min(float(image_height), y1)),
            max(0.0, min(float(image_width), x2)),
            max(0.0, min(float(image_height), y2)),
        )

    def _safe_bbox_pixels(
        self,
        bounding_box: list[float],
        image_width: int,
        image_height: int,
    ) -> tuple[float, float, float, float] | None:
        bbox_pixels = self._bbox_to_pixels(bounding_box, image_width, image_height)
        if bbox_pixels is None:
            return None

        x1, y1, x2, y2 = bbox_pixels
        width = max(1.0, x2 - x1)
        height = max(1.0, y2 - y1)
        inset_x = width * 0.10
        inset_y = height * 0.10
        safe_x1 = x1 + inset_x
        safe_y1 = y1 + inset_y
        safe_x2 = x2 - inset_x
        safe_y2 = y2 - inset_y
        if safe_x2 <= safe_x1 or safe_y2 <= safe_y1:
            return bbox_pixels
        return safe_x1, safe_y1, safe_x2, safe_y2

    def _bbox_looks_normalized(self, bounding_box: list[float]) -> bool:
        try:
            return all(0.0 <= float(value) <= 1.5 for value in bounding_box[:4])
        except (TypeError, ValueError):
            return False

    def _clamp_relative_coordinate(self, value: float | None) -> float:
        if value is None:
            return 0.5
        return max(0.08, min(0.92, float(value)))

    def _clamp_point_to_bbox(
        self,
        center_x: float,
        center_y: float,
        bbox_pixels: tuple[float, float, float, float],
    ) -> tuple[float, float]:
        x1, y1, x2, y2 = bbox_pixels
        clamped_x = max(x1, min(x2, center_x))
        clamped_y = max(y1, min(y2, center_y))
        return clamped_x, clamped_y

    def _fit_symbol_inside_bbox(
        self,
        center_x: float,
        center_y: float,
        symbol_width: int,
        symbol_height: int,
        bbox_pixels: tuple[float, float, float, float],
    ) -> tuple[int, int]:
        x1, y1, x2, y2 = bbox_pixels
        room_width = max(1.0, x2 - x1)
        room_height = max(1.0, y2 - y1)
        max_width = max(18, int(room_width * 0.55))
        max_height = max(18, int(room_height * 0.55))
        scale = min(1.0, max_width / max(1, symbol_width), max_height / max(1, symbol_height))
        scaled_width = max(12, int(symbol_width * scale))
        scaled_height = max(12, int(symbol_height * scale))
        left = int(max(x1, min(x2 - scaled_width, center_x - scaled_width / 2)))
        top = int(max(y1, min(y2 - scaled_height, center_y - scaled_height / 2)))
        return left, top

    def _resize_symbol_to_fit_bbox(
        self,
        symbol: Image.Image,
        bbox_pixels: tuple[float, float, float, float],
    ) -> Image.Image | None:
        x1, y1, x2, y2 = bbox_pixels
        room_width = max(1.0, x2 - x1)
        room_height = max(1.0, y2 - y1)
        scale = min(1.0, room_width / max(1, symbol.width), room_height / max(1, symbol.height))
        if scale <= 0:
            return None

        width = max(8, int(symbol.width * scale))
        height = max(8, int(symbol.height * scale))
        if width > room_width or height > room_height:
            return None
        if width == symbol.width and height == symbol.height:
            return symbol
        return symbol.resize((width, height), Image.Resampling.LANCZOS)

    def _clamp_coordinate(self, value: float, maximum: int) -> float:
        return max(0.0, min(float(maximum), float(value)))

    def _paste_clamped(self, canvas: Image.Image, symbol: Image.Image, left: int, top: int, bbox_pixels: tuple[float, float, float, float]) -> None:
        image_width, image_height = canvas.size
        x1, y1, x2, y2 = bbox_pixels
        symbol_left = max(int(x1), left)
        symbol_top = max(int(y1), top)
        symbol_right = min(int(x2), left + symbol.width)
        symbol_bottom = min(int(y2), top + symbol.height)

        if symbol_right <= symbol_left or symbol_bottom <= symbol_top:
            return

        crop_left = symbol_left - left
        crop_top = symbol_top - top
        crop_right = crop_left + (symbol_right - symbol_left)
        crop_bottom = crop_top + (symbol_bottom - symbol_top)
        cropped = symbol.crop((crop_left, crop_top, crop_right, crop_bottom))
        canvas.alpha_composite(cropped, (symbol_left, symbol_top))

    def _build_debug_room_overlay(
        self,
        image_size: tuple[int, int],
        analysis: FloorplanAnalysis | None,
    ) -> Image.Image:
        overlay = Image.new("RGBA", image_size, (0, 0, 0, 0))
        if analysis is None:
            return overlay

        draw = ImageDraw.Draw(overlay)
        for room in analysis.rooms:
            bbox_pixels = self._bbox_to_pixels(room.bounding_box or [], image_size[0], image_size[1]) if room.bounding_box else None
            if bbox_pixels is None:
                continue
            x1, y1, x2, y2 = [int(value) for value in bbox_pixels]
            draw.rectangle((x1, y1, x2, y2), outline=(220, 80, 80, 220), width=2)
            safe_bbox = self._safe_bbox_pixels(room.bounding_box, image_size[0], image_size[1]) if room.bounding_box else None
            if safe_bbox is not None:
                sx1, sy1, sx2, sy2 = [int(value) for value in safe_bbox]
                draw.rectangle((sx1, sy1, sx2, sy2), outline=(40, 190, 80, 220), width=2)
            label = room.room_name or room.type
            draw.text((x1 + 4, y1 + 4), label, fill=(180, 40, 40, 230), font=ImageFont.load_default())
        return overlay

    def _draw_item_label(self, canvas: Image.Image, item_name: str, center_x: float, center_y: float) -> None:
        draw = ImageDraw.Draw(canvas)
        draw.text((int(center_x) + 4, int(center_y) + 4), item_name, fill=(90, 60, 40, 220), font=ImageFont.load_default())

    def _draw_symbol(
        self,
        draw: ImageDraw.ImageDraw,
        item_name: str,
        width: int,
        height: int,
        fill: tuple[int, int, int, int],
        outline: tuple[int, int, int, int],
    ) -> None:
        if min(width, height) < 8:
            self._draw_simple_placeholder(draw, width, height, fill, outline)
            return

        padding = max(3, min(width, height) // 10)
        inner = (padding, padding, width - padding, height - padding)
        stroke = max(2, min(width, height) // 14)
        soft_fill = (239, 226, 204, 230)
        light_fill = (249, 241, 226, 235)
        wood_fill = (224, 201, 170, 230)

        if item_name == "bed":
            draw.rounded_rectangle(inner, radius=max(5, min(width, height) // 10), fill=light_fill, outline=outline, width=stroke)
            pillow_top = padding + stroke + 2
            pillow_bottom = min(height - padding - stroke, pillow_top + max(8, height // 5))
            gap = max(3, width // 24)
            pillow_left = padding + stroke + 3
            pillow_right = width - padding - stroke - 3
            pillow_mid = (pillow_left + pillow_right) // 2
            draw.rounded_rectangle(
                (pillow_left, pillow_top, pillow_mid - gap, pillow_bottom),
                radius=3,
                fill=(255, 250, 242, 240),
                outline=outline,
                width=max(1, stroke - 1),
            )
            draw.rounded_rectangle(
                (pillow_mid + gap, pillow_top, pillow_right, pillow_bottom),
                radius=3,
                fill=(255, 250, 242, 240),
                outline=outline,
                width=max(1, stroke - 1),
            )
            blanket_y = min(height - padding - stroke - 3, pillow_bottom + max(5, height // 8))
            draw.line((padding + stroke + 4, blanket_y, width - padding - stroke - 4, blanket_y), fill=outline, width=max(1, stroke - 1))
            return

        if item_name == "sofa":
            draw.rounded_rectangle(inner, radius=max(6, min(width, height) // 7), fill=soft_fill, outline=outline, width=stroke)
            back_h = max(6, height // 5)
            draw.rounded_rectangle(
                (padding + stroke, padding + stroke, width - padding - stroke, padding + stroke + back_h),
                radius=4,
                fill=(231, 211, 188, 235),
                outline=outline,
                width=max(1, stroke - 1),
            )
            arm_w = max(5, width // 8)
            draw.rectangle((padding + stroke, padding + back_h, padding + stroke + arm_w, height - padding - stroke), fill=(231, 211, 188, 235), outline=outline, width=max(1, stroke - 1))
            draw.rectangle((width - padding - stroke - arm_w, padding + back_h, width - padding - stroke, height - padding - stroke), fill=(231, 211, 188, 235), outline=outline, width=max(1, stroke - 1))
            seat_y1 = padding + back_h + stroke
            seat_y2 = height - padding - stroke
            draw.line((width // 2, seat_y1, width // 2, seat_y2), fill=outline, width=max(1, stroke - 1))
            return

        if item_name == "coffee_table":
            draw.rounded_rectangle(inner, radius=max(4, min(width, height) // 8), fill=wood_fill, outline=outline, width=stroke)
            inset = max(3, min(width, height) // 8)
            draw.rounded_rectangle((inner[0] + inset, inner[1] + inset, inner[2] - inset, inner[3] - inset), radius=3, outline=outline, width=max(1, stroke - 1))
            return

        if item_name == "dining_table":
            table_pad = max(padding + 3, min(width, height) // 5)
            table = (table_pad, table_pad, width - table_pad, height - table_pad)
            if abs(width - height) < max(8, width * 0.18):
                draw.ellipse(table, fill=wood_fill, outline=outline, width=stroke)
            else:
                draw.rounded_rectangle(table, radius=max(4, min(width, height) // 10), fill=wood_fill, outline=outline, width=stroke)
            chair = max(5, min(width, height) // 7)
            if width > chair * 4 and height > chair * 4:
                chairs = [
                    (width // 2 - chair // 2, padding),
                    (width // 2 - chair // 2, height - padding - chair),
                    (padding, height // 2 - chair // 2),
                    (width - padding - chair, height // 2 - chair // 2),
                ]
                for cx, cy in chairs:
                    draw.rounded_rectangle((cx, cy, cx + chair, cy + chair), radius=2, fill=soft_fill, outline=outline, width=max(1, stroke - 1))
            return

        if item_name == "chair":
            draw.rounded_rectangle(inner, radius=max(3, min(width, height) // 8), fill=soft_fill, outline=outline, width=stroke)
            draw.line((padding + stroke, padding + stroke + max(3, height // 5), width - padding - stroke, padding + stroke + max(3, height // 5)), fill=outline, width=max(1, stroke - 1))
            return

        if item_name == "tv_stand":
            stand_h = max(6, height // 3)
            stand = (padding, height - padding - stand_h, width - padding, height - padding)
            draw.rounded_rectangle(stand, radius=max(3, stand_h // 5), fill=wood_fill, outline=outline, width=stroke)
            tv_y = max(padding, stand[1] - max(4, height // 5))
            draw.line((padding + stroke, tv_y, width - padding - stroke, tv_y), fill=outline, width=stroke)
            return

        if item_name == "wardrobe":
            draw.rounded_rectangle(inner, radius=max(3, min(width, height) // 12), fill=wood_fill, outline=outline, width=stroke)
            mid_x = width // 2
            draw.line((mid_x, padding + stroke, mid_x, height - padding - stroke), fill=outline, width=max(1, stroke - 1))
            handle_r = max(1, min(width, height) // 18)
            draw.ellipse((mid_x - handle_r * 3, height // 2 - handle_r, mid_x - handle_r, height // 2 + handle_r), fill=outline)
            draw.ellipse((mid_x + handle_r, height // 2 - handle_r, mid_x + handle_r * 3, height // 2 + handle_r), fill=outline)
            return

        if item_name == "desk":
            desk_h = max(8, height // 3)
            desk = (padding, padding, width - padding, padding + desk_h)
            draw.rounded_rectangle(desk, radius=max(3, desk_h // 5), fill=wood_fill, outline=outline, width=stroke)
            chair_w = max(6, width // 4)
            chair_h = max(5, height // 4)
            chair = (width // 2 - chair_w // 2, min(height - padding - chair_h, padding + desk_h + max(3, height // 8)), width // 2 + chair_w // 2, min(height - padding, padding + desk_h + max(3, height // 8) + chair_h))
            draw.rounded_rectangle(chair, radius=3, fill=soft_fill, outline=outline, width=max(1, stroke - 1))
            return

        if item_name == "refrigerator":
            draw.rounded_rectangle(inner, radius=max(3, min(width, height) // 12), fill=light_fill, outline=outline, width=stroke)
            split_y = padding + int((height - padding * 2) * 0.38)
            draw.line((padding + stroke, split_y, width - padding - stroke, split_y), fill=outline, width=max(1, stroke - 1))
            handle_x = width - padding - stroke - max(2, width // 12)
            draw.line((handle_x, split_y + 3, handle_x, height - padding - stroke - 3), fill=outline, width=max(1, stroke - 1))
            return

        if item_name == "washing_machine":
            min_dim = min(width, height)
            if min_dim < 8:
                self._draw_simple_placeholder(draw, width, height, light_fill, outline)
                return
            self._safe_rounded_rectangle(draw, inner, radius=max(2, min_dim // 12), fill=light_fill, outline=outline, width=stroke)
            drum_pad = max(2, int(min_dim * 0.22))
            drum_box = (drum_pad, drum_pad, width - drum_pad, height - drum_pad)
            if not self._safe_ellipse(draw, drum_box, outline=outline, width=stroke, fill=(226, 236, 238, 210)):
                self._safe_rectangle(draw, (width // 3, height // 3, width * 2 // 3, height * 2 // 3), fill=(226, 236, 238, 210), outline=outline, width=max(1, stroke - 1))
            return

        if item_name == "bathtub":
            draw.rounded_rectangle(inner, radius=max(6, min(width, height) // 5), fill=(244, 241, 232, 235), outline=outline, width=stroke)
            inset = max(4, min(width, height) // 8)
            draw.rounded_rectangle((inner[0] + inset, inner[1] + inset, inner[2] - inset, inner[3] - inset), radius=max(4, min(width, height) // 6), outline=outline, width=max(1, stroke - 1))
            return

        if item_name == "toilet":
            tank_h = max(5, height // 4)
            tank = (padding + width // 5, padding, width - padding - width // 5, padding + tank_h)
            draw.rounded_rectangle(tank, radius=2, fill=light_fill, outline=outline, width=max(1, stroke - 1))
            bowl = (padding + width // 5, padding + tank_h + 2, width - padding - width // 5, height - padding)
            draw.ellipse(bowl, fill=(244, 241, 232, 235), outline=outline, width=stroke)
            return

        if item_name == "shoe_cabinet":
            draw.rounded_rectangle(inner, radius=max(3, min(width, height) // 14), fill=wood_fill, outline=outline, width=stroke)
            for y in (height // 3, height * 2 // 3):
                draw.line((padding + stroke, y, width - padding - stroke, y), fill=outline, width=max(1, stroke - 1))
            return

        if item_name == "rug":
            draw.rounded_rectangle(inner, radius=max(6, min(width, height) // 5), fill=(230, 210, 170, 170), outline=(150, 115, 75, 230), width=max(1, stroke - 1))
            dot_step = max(5, min(width, height) // 5)
            for x in range(padding + dot_step, max(padding + dot_step + 1, width - padding), dot_step):
                draw.point((x, padding + max(2, stroke)), fill=outline)
                draw.point((x, height - padding - max(2, stroke)), fill=outline)
            return

        if item_name == "plant":
            pot_h = max(5, height // 4)
            pot = (width // 2 - width // 5, height - padding - pot_h, width // 2 + width // 5, height - padding)
            draw.rounded_rectangle(pot, radius=2, fill=wood_fill, outline=outline, width=max(1, stroke - 1))
            leaf_fill = (99, 154, 91, 230)
            leaf_w = max(5, width // 4)
            leaf_h = max(5, height // 4)
            centers = [
                (width // 2, height // 2),
                (width // 2 - leaf_w // 2, height // 2 + leaf_h // 4),
                (width // 2 + leaf_w // 2, height // 2 + leaf_h // 4),
                (width // 2, height // 2 - leaf_h // 2),
            ]
            for cx, cy in centers:
                draw.ellipse((cx - leaf_w // 2, cy - leaf_h // 2, cx + leaf_w // 2, cy + leaf_h // 2), fill=leaf_fill, outline=(70, 120, 65, 230), width=max(1, stroke - 1))
            return

        draw.rounded_rectangle(inner, radius=max(4, min(width, height) // 10), fill=fill, outline=outline, width=3)

    def _draw_simple_placeholder(
        self,
        draw: ImageDraw.ImageDraw,
        width: int,
        height: int,
        fill: tuple[int, int, int, int],
        outline: tuple[int, int, int, int],
    ) -> None:
        padding = 1 if min(width, height) < 12 else 2
        self._safe_rounded_rectangle(
            draw,
            (padding, padding, width - padding, height - padding),
            radius=max(1, min(width, height) // 6),
            fill=fill,
            outline=outline,
            width=1,
        )

    def _safe_box(self, x0, y0, x1, y1):
        try:
            values = [float(x0), float(y0), float(x1), float(y1)]
        except (TypeError, ValueError):
            return None
        if not all(math.isfinite(value) for value in values):
            return None
        left, top, right, bottom = values
        if right < left or bottom < top:
            return None
        return int(round(left)), int(round(top)), int(round(right)), int(round(bottom))

    def _safe_ellipse(self, draw: ImageDraw.ImageDraw, box, **kwargs) -> bool:
        safe_box = self._safe_box(*box)
        if safe_box is None:
            return False
        x0, y0, x1, y1 = safe_box
        if x1 - x0 < 2 or y1 - y0 < 2:
            return False
        draw.ellipse(safe_box, **kwargs)
        return True

    def _safe_rectangle(self, draw: ImageDraw.ImageDraw, box, **kwargs) -> bool:
        safe_box = self._safe_box(*box)
        if safe_box is None:
            return False
        x0, y0, x1, y1 = safe_box
        if x1 - x0 < 1 or y1 - y0 < 1:
            return False
        draw.rectangle(safe_box, **kwargs)
        return True

    def _safe_rounded_rectangle(self, draw: ImageDraw.ImageDraw, box, radius=0, **kwargs) -> bool:
        safe_box = self._safe_box(*box)
        if safe_box is None:
            return False
        x0, y0, x1, y1 = safe_box
        if x1 - x0 < 1 or y1 - y0 < 1:
            return False
        max_radius = max(0, min(x1 - x0, y1 - y0) // 2)
        safe_radius = max(0, min(int(radius or 0), max_radius))
        draw.rounded_rectangle(safe_box, radius=safe_radius, **kwargs)
        return True

    def _safe_line(self, draw: ImageDraw.ImageDraw, points, **kwargs) -> bool:
        try:
            flattened = []
            for point in points:
                if isinstance(point, (tuple, list)):
                    flattened.extend(float(value) for value in point)
                else:
                    flattened.append(float(point))
        except (TypeError, ValueError):
            return False
        if not all(math.isfinite(value) for value in flattened):
            return False
        draw.line(points, **kwargs)
        return True

    def _symbol_size(
        self,
        item_name: str,
        image_width: int,
        image_height: int,
        room_bbox: list[float] | tuple[float, float, float, float] | None = None,
    ) -> tuple[int, int]:
        short_side = min(image_width, image_height)
        base = max(40, int(short_side * 0.06))
        if item_name == "bed":
            width = int(base * 1.6)
            height = int(base * 1.0)
        elif item_name == "sofa":
            width = int(base * 1.4)
            height = int(base * 0.8)
        elif item_name == "coffee_table":
            width = int(base * 0.9)
            height = int(base * 0.55)
        elif item_name == "dining_table":
            width = int(base * 1.0)
            height = int(base * 1.0)
        elif item_name in {"wardrobe", "shoe_cabinet", "refrigerator"}:
            width = int(base * 0.7)
            height = int(base * 1.4)
        elif item_name == "desk":
            width = int(base * 1.2)
            height = int(base * 0.7)
        elif item_name == "rug":
            width = int(base * 1.0)
            height = int(base * 0.6)
        elif item_name == "plant":
            width = int(base * 0.55)
            height = int(base * 0.55)
        elif item_name in {"washing_machine", "toilet"}:
            width = int(base * 0.8)
            height = int(base * 0.8)
        elif item_name == "bathtub":
            width = int(base * 1.0)
            height = int(base * 1.4)
        elif item_name == "chair":
            width = int(base * 0.55)
            height = int(base * 0.55)
        else:
            width = int(base * 0.8)
            height = int(base * 0.8)

        if room_bbox and len(room_bbox) >= 4:
            bbox_pixels = self._bbox_to_pixels(room_bbox, image_width, image_height)
            if bbox_pixels is not None:
                x1, y1, x2, y2 = bbox_pixels
                room_width = max(1.0, x2 - x1)
                room_height = max(1.0, y2 - y1)
                if item_name == "bed":
                    width = min(width, int(room_width * 0.55))
                    height = min(height, int(room_height * 0.45))
                elif item_name == "sofa":
                    width = min(width, int(room_width * 0.45))
                    height = min(height, int(room_height * 0.35))
                elif item_name == "dining_table":
                    width = min(width, int(room_width * 0.35))
                    height = min(height, int(room_height * 0.35))
                elif item_name in {
                    "wardrobe",
                    "shoe_cabinet",
                    "desk",
                    "rug",
                    "plant",
                    "chair",
                    "refrigerator",
                    "washing_machine",
                    "bathtub",
                    "toilet",
                    "tv_stand",
                    "coffee_table",
                }:
                    width = min(width, int(room_width * 0.20))
                    height = min(height, int(room_height * 0.20))
                width = min(width, int(room_width))
                height = min(height, int(room_height))

        return max(12, width), max(12, height)

    def _item_palette(self, item_name: str) -> tuple[tuple[int, int, int, int], tuple[int, int, int, int]]:
        outline = (104, 78, 54, 255)
        if item_name in {"plant"}:
            return (140, 190, 120, 220), outline
        if item_name in {"rug"}:
            return (232, 212, 170, 180), outline
        return (236, 220, 195, 230), outline
