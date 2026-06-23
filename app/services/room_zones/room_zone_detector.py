from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException

from app.config import get_settings


class RoomZoneDetector:
    def detect(
        self,
        normalized_floorplan_path: Path,
        structure_mask_path: Path,
        interior_area_mask_path: Path,
        content_bbox: list[int] | None,
        analysis: dict | None = None,
    ) -> dict:
        try:
            import cv2
            import numpy as np
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"opencv-python is required for room zone detection: {exc}") from exc

        if not interior_area_mask_path.exists():
            raise HTTPException(status_code=404, detail="interior area mask not found for room zone detection")

        mask = cv2.imread(str(interior_area_mask_path), cv2.IMREAD_GRAYSCALE)
        if mask is None:
            raise HTTPException(status_code=500, detail="failed to read interior area mask")

        height, width = mask.shape[:2]
        settings = get_settings()
        min_area = max(1, int(settings.room_zone_min_area))
        max_area = int(width * height * float(settings.room_zone_max_area_ratio))
        bbox = self._coerce_bbox(content_bbox, width, height) or [0, 0, width, height]
        warnings: list[str] = []

        working = (mask > 0).astype("uint8") * 255
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        working = cv2.morphologyEx(
            working,
            cv2.MORPH_CLOSE,
            kernel,
            iterations=max(0, int(settings.room_zone_morph_close_iterations)),
        )

        contours, _ = cv2.findContours(working, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        zones = []
        for contour in contours:
            area = float(cv2.contourArea(contour))
            if area < min_area:
                continue
            if area > max_area:
                warnings.append(f"Huge room-zone candidate area={int(area)}; using heuristic room-zone fallback.")
                zones.extend(self._fallback_zones_from_analysis_or_grid(bbox, width, height, analysis, len(zones)))
                continue

            x, y, w, h = cv2.boundingRect(contour)
            center_x = int(round(x + w / 2))
            center_y = int(round(y + h / 2))
            epsilon = 0.018 * cv2.arcLength(contour, True)
            polygon_points = cv2.approxPolyDP(contour, epsilon, True).reshape(-1, 2)
            polygon = [[int(px), int(py)] for px, py in polygon_points[:16]]
            zone_type = self._classify_zone([x, y, x + w, y + h], center_x, center_y, bbox, area, analysis)
            zones.append(
                {
                    "id": f"zone_{len(zones) + 1}",
                    "type": zone_type,
                    "label": None,
                    "bbox": [int(x), int(y), int(x + w), int(y + h)],
                    "center_x": center_x,
                    "center_y": center_y,
                    "area": int(round(area)),
                    "polygon": polygon,
                    "confidence": 0.6 if zone_type != "unknown" else 0.45,
                    "needs_manual_review": True,
                }
            )

        zones.sort(key=lambda item: (item["bbox"][1], item["bbox"][0]))
        for index, zone in enumerate(zones, start=1):
            zone["id"] = f"zone_{index}"

        if not zones:
            warnings.append("No room zones were detected; manual room zone creation is required.")

        return {
            "version": "1.0",
            "source": "opencv_room_zone_detection",
            "image_width": width,
            "image_height": height,
            "content_bbox": bbox,
            "zones": zones,
            "warnings": warnings,
        }

    def _fallback_zones_from_analysis_or_grid(
        self,
        content_bbox: list[int],
        image_width: int,
        image_height: int,
        analysis: dict | None,
        start_index: int = 0,
    ) -> list[dict]:
        rooms = []
        if isinstance(analysis, dict) and isinstance(analysis.get("rooms"), list):
            rooms = [room for room in analysis["rooms"] if isinstance(room, dict)]

        zone_specs = []
        for room in rooms:
            room_type = str(room.get("type") or "unknown")
            position = str(room.get("position") or "").strip().lower()
            if room_type in {"hallway", "unknown"}:
                continue
            zone_bbox = self._bbox_from_position(content_bbox, position, room_type)
            zone_specs.append((room_type, zone_bbox))

        if not zone_specs:
            zone_specs = [
                ("living_room", self._bbox_from_position(content_bbox, "center", "living_room")),
                ("bedroom", self._bbox_from_position(content_bbox, "top_left", "bedroom")),
                ("kitchen", self._bbox_from_position(content_bbox, "bottom_right", "kitchen")),
                ("entrance", self._bbox_from_position(content_bbox, "bottom", "entrance")),
            ]

        zones = []
        seen = set()
        for room_type, zone_bbox in zone_specs:
            clipped = self._clip_bbox(zone_bbox, image_width, image_height)
            if clipped is None:
                continue
            key = tuple(clipped)
            if key in seen:
                continue
            seen.add(key)
            x1, y1, x2, y2 = clipped
            area = max(0, x2 - x1) * max(0, y2 - y1)
            if area <= 0:
                continue
            zones.append(
                {
                    "id": f"zone_{start_index + len(zones) + 1}",
                    "type": room_type,
                    "label": None,
                    "bbox": clipped,
                    "center_x": int(round((x1 + x2) / 2)),
                    "center_y": int(round((y1 + y2) / 2)),
                    "area": int(area),
                    "polygon": [[x1, y1], [x2, y1], [x2, y2], [x1, y2]],
                    "confidence": 0.35,
                    "needs_manual_review": True,
                }
            )
        return zones

    @staticmethod
    def _bbox_from_position(content_bbox: list[int], position: str, room_type: str) -> list[int]:
        x1, y1, x2, y2 = content_bbox
        width = max(1, x2 - x1)
        height = max(1, y2 - y1)
        presets = {
            "top": (0.18, 0.04, 0.82, 0.38),
            "bottom": (0.18, 0.68, 0.82, 0.96),
            "left": (0.04, 0.18, 0.48, 0.82),
            "right": (0.52, 0.18, 0.96, 0.82),
            "center": (0.28, 0.24, 0.72, 0.76),
            "top_left": (0.04, 0.04, 0.48, 0.42),
            "top_right": (0.52, 0.04, 0.96, 0.42),
            "bottom_left": (0.04, 0.58, 0.48, 0.96),
            "bottom_right": (0.52, 0.58, 0.96, 0.96),
        }
        if position not in presets:
            fallback_by_type = {
                "entrance": "bottom",
                "balcony": "right",
                "kitchen": "bottom_right",
                "bathroom": "bottom_right",
                "toilet": "bottom_right",
                "washroom": "bottom_right",
                "bedroom": "top_left",
                "living_room": "center",
            }
            position = fallback_by_type.get(room_type, "center")
        rx1, ry1, rx2, ry2 = presets[position]
        return [
            int(round(x1 + rx1 * width)),
            int(round(y1 + ry1 * height)),
            int(round(x1 + rx2 * width)),
            int(round(y1 + ry2 * height)),
        ]

    @staticmethod
    def _clip_bbox(value: list[int], width: int, height: int) -> list[int] | None:
        if len(value) != 4:
            return None
        x1, y1, x2, y2 = value
        x1 = max(0, min(width, int(x1)))
        x2 = max(0, min(width, int(x2)))
        y1 = max(0, min(height, int(y1)))
        y2 = max(0, min(height, int(y2)))
        if x2 <= x1 or y2 <= y1:
            return None
        return [x1, y1, x2, y2]

    @staticmethod
    def _classify_zone(
        zone_bbox: list[int],
        center_x: int,
        center_y: int,
        content_bbox: list[int],
        area: float,
        analysis: dict | None,
    ) -> str:
        x1, y1, x2, y2 = content_bbox
        content_width = max(1, x2 - x1)
        content_height = max(1, y2 - y1)
        rel_x = (center_x - x1) / content_width
        rel_y = (center_y - y1) / content_height
        area_ratio = area / float(content_width * content_height)

        if rel_y > 0.82 and rel_x < 0.25:
            return "entrance"
        if rel_x > 0.82 and 0.25 < rel_y < 0.8:
            return "balcony"
        if area_ratio > 0.12 and rel_x < 0.45:
            return "bedroom"
        if area_ratio > 0.18:
            return "living_room"
        if rel_y > 0.55 and rel_x > 0.55:
            return "kitchen"

        room_types = []
        if isinstance(analysis, dict):
            for room in analysis.get("rooms", []) if isinstance(analysis.get("rooms"), list) else []:
                room_type = str(room.get("type") or "")
                if room_type:
                    room_types.append(room_type)
        for preferred in ("kitchen", "bathroom", "toilet", "washroom"):
            if preferred in room_types and area_ratio < 0.1:
                return preferred
        return "unknown"

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
