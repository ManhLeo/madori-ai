from __future__ import annotations

import json
from pathlib import Path

from fastapi import HTTPException

from app.services.room_zones.interior_area_detector import InteriorAreaDetector
from app.services.room_zones.room_zone_debug_renderer import RoomZoneDebugRenderer
from app.services.room_zones.room_zone_detector import RoomZoneDetector


EMPTY_ROOM_ZONES = {
    "version": "1.0",
    "source": "manual",
    "image_width": 1200,
    "image_height": 1200,
    "content_bbox": None,
    "zones": [],
    "warnings": [],
}


class RoomZoneService:
    def create_or_update(
        self,
        run_dir: Path,
        normalized_floorplan_path: Path,
        structure_mask_path: Path,
        content_bbox_path: Path | None = None,
        analysis: dict | None = None,
    ) -> dict:
        content_bbox_payload = self._read_json(content_bbox_path) if content_bbox_path else None
        content_bbox = content_bbox_payload.get("bbox") if isinstance(content_bbox_payload, dict) else None

        interior_mask_path = run_dir / "interior_area_mask.png"
        interior_metadata = InteriorAreaDetector().detect(
            normalized_floorplan_path=normalized_floorplan_path,
            structure_mask_path=structure_mask_path,
            content_bbox=content_bbox,
            output_path=interior_mask_path,
        )
        room_zones = RoomZoneDetector().detect(
            normalized_floorplan_path=normalized_floorplan_path,
            structure_mask_path=structure_mask_path,
            interior_area_mask_path=interior_mask_path,
            content_bbox=content_bbox,
            analysis=analysis,
        )
        self.save(run_dir, room_zones)
        RoomZoneDebugRenderer().render(
            normalized_floorplan_path=normalized_floorplan_path,
            interior_area_mask_path=interior_mask_path,
            room_zones=room_zones,
            interior_debug_path=run_dir / "interior_area_debug.png",
            room_zones_debug_path=run_dir / "room_zones_debug.png",
        )
        metadata = {
            "interior_area_detection": interior_metadata,
            "room_zone_detection": {
                "version": room_zones.get("version"),
                "source": room_zones.get("source"),
                "image_width": room_zones.get("image_width"),
                "image_height": room_zones.get("image_height"),
                "content_bbox": room_zones.get("content_bbox"),
                "zone_count": len(room_zones.get("zones", [])),
                "warnings": room_zones.get("warnings", []),
            },
            "room_zones": room_zones,
            "warnings": list(interior_metadata.get("warnings", [])) + list(room_zones.get("warnings", [])),
        }
        (run_dir / "room_zone_detection.json").write_text(
            json.dumps(metadata["room_zone_detection"], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (run_dir / "interior_area_detection.json").write_text(
            json.dumps(interior_metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return metadata

    def load(self, run_dir: Path) -> dict:
        path = run_dir / "room_zones.json"
        if not path.exists():
            return dict(EMPTY_ROOM_ZONES)
        payload = self._read_json(path)
        return payload if isinstance(payload, dict) else dict(EMPTY_ROOM_ZONES)

    def save(self, run_dir: Path, room_zones: dict) -> dict:
        normalized = self.validate(room_zones)
        path = run_dir / "room_zones.json"
        path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
        return normalized

    def reset(self, run_dir: Path) -> dict:
        return self.save(run_dir, dict(EMPTY_ROOM_ZONES))

    @staticmethod
    def validate(payload: dict) -> dict:
        if not isinstance(payload, dict):
            raise HTTPException(status_code=422, detail="room_zones payload must be a JSON object")
        zones = payload.get("zones", [])
        if not isinstance(zones, list):
            raise HTTPException(status_code=422, detail="room_zones.zones must be a list")
        normalized_zones = []
        for index, zone in enumerate(zones, start=1):
            if not isinstance(zone, dict):
                raise HTTPException(status_code=422, detail=f"zone {index} must be an object")
            bbox = zone.get("bbox")
            if not isinstance(bbox, list) or len(bbox) != 4:
                raise HTTPException(status_code=422, detail=f"zone {index} bbox must contain 4 numbers")
            try:
                normalized_bbox = [int(round(float(value))) for value in bbox]
            except (TypeError, ValueError) as exc:
                raise HTTPException(status_code=422, detail=f"zone {index} bbox values must be numbers") from exc
            if normalized_bbox[2] <= normalized_bbox[0] or normalized_bbox[3] <= normalized_bbox[1]:
                raise HTTPException(status_code=422, detail=f"zone {index} bbox must have positive width and height")
            normalized_zones.append(
                {
                    "id": str(zone.get("id") or f"zone_{index}"),
                    "type": str(zone.get("type") or "unknown"),
                    "label": zone.get("label"),
                    "bbox": normalized_bbox,
                    "center_x": int(zone.get("center_x") or (normalized_bbox[0] + normalized_bbox[2]) / 2),
                    "center_y": int(zone.get("center_y") or (normalized_bbox[1] + normalized_bbox[3]) / 2),
                    "area": int(zone.get("area") or 0),
                    "polygon": zone.get("polygon") if isinstance(zone.get("polygon"), list) else [],
                    "confidence": float(zone.get("confidence") or 0.0),
                    "needs_manual_review": bool(zone.get("needs_manual_review", True)),
                }
            )

        return {
            "version": str(payload.get("version") or "1.0"),
            "source": str(payload.get("source") or "manual"),
            "image_width": int(payload.get("image_width") or 1200),
            "image_height": int(payload.get("image_height") or 1200),
            "content_bbox": payload.get("content_bbox"),
            "zones": normalized_zones,
            "warnings": payload.get("warnings", []) if isinstance(payload.get("warnings", []), list) else [],
        }

    @staticmethod
    def _read_json(path: Path | None) -> dict | None:
        if not path or not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None
