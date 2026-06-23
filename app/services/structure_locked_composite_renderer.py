from __future__ import annotations

import json
import random
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps

from app.schemas.run import (
    FurniturePlacementValidationArtifact,
    RenderPlanArtifact,
    RunMetadata,
    StructureLockedCompositeArtifact,
    StructureLockedCompositeSummary,
)


class StructureLockedCompositeRenderer:
    ALLOWED_LABELS = {
        "Living Room",
        "Kitchen",
        "Closet",
        "Toilet",
        "Entrance",
        "Bed Room",
        "Bath Room",
        "Wash Room",
        "Dining Kitchen",
        "Balcony",
        "Hallway",
        "Storage",
        "Unknown",
    }

    FLOOR_TONE_COLORS = {
        "white": (244, 240, 232, 52),
        "light_brown": (216, 191, 156, 60),
        "dark_brown": (162, 131, 98, 66),
        "unknown": (216, 191, 156, 60),
    }
    STRUCTURE_OVERLAY_OPACITY = 0.54
    KNOWN_LABEL_SOURCE_TEXT = {
        "living_room": "リビング",
        "bed_room": "洋室",
        "entrance": "玄関",
        "wash_area": "洗",
        "closet": "WIC",
        "kitchen": "K",
    }

    def __init__(self, storage_dir: Path, storage_runs_dir: Path) -> None:
        self.storage_dir = storage_dir
        self.storage_runs_dir = storage_runs_dir

    def render_structure_locked_composite(self, metadata: RunMetadata) -> StructureLockedCompositeArtifact:
        run_id = metadata.run_id
        warnings: list[str] = []
        images = self.load_required_images(run_id, warnings)
        layout = self.load_layout_furniture_validated(run_id)
        render_plan = self.load_render_plan(run_id)

        base = self.create_watercolor_paper_background(1200, 1200)
        base = self.paint_room_floor_tones(base, layout.rooms, render_plan.style if render_plan is not None else {}, warnings)
        base = self.draw_furniture_layer(base, layout.furniture, warnings)
        base = self.overlay_structure_lines(
            base,
            images.get("line_preview"),
            images.get("binary_mask"),
            images.get("edges"),
            warnings,
        )
        base = self.cover_original_japanese_labels(base, layout.labels, layout.rooms, warnings, after_overlay=True)
        base = self.draw_english_labels(base, layout.labels, layout.rooms, layout.furniture, warnings)

        output_info = self.save_composite_output(run_id, base)
        if any(item.placement_status == "suggested_unplaced" for item in layout.furniture):
            warnings.append("Some furniture remained suggested_unplaced and was skipped from deterministic composite rendering.")
        if not layout.quality.pixel_perfect_geometry:
            warnings.append("Composite rendering used semantic layout geometry and is not pixel-perfect.")
        artifact = self.build_artifact(run_id, layout, render_plan, output_info, images, warnings, [])
        self.write_structure_locked_composite_artifact(run_id, artifact)
        return artifact

    def load_required_images(self, run_id: str, warnings: list[str]) -> dict[str, Image.Image | None]:
        artifacts_dir = self._artifacts_dir(run_id)
        required = {
            "normalized_floorplan": artifacts_dir / "normalized_floorplan.png",
            "binary_mask": artifacts_dir / "binary_mask.png",
            "edges": artifacts_dir / "edges.png",
            "line_preview": artifacts_dir / "line_preview.png",
        }
        images: dict[str, Image.Image | None] = {}
        for key, path in required.items():
            if not path.exists():
                if key == "normalized_floorplan":
                    raise HTTPException(status_code=400, detail="normalized_floorplan.png is required for structure-locked composite rendering")
                warnings.append(f"{path.name} is missing; fallback structure overlay behavior was used.")
                images[key] = None
                continue
            try:
                image = Image.open(path).convert("RGBA")
            except OSError as exc:
                if key == "normalized_floorplan":
                    raise HTTPException(status_code=400, detail="normalized_floorplan.png could not be opened") from exc
                warnings.append(f"{path.name} could not be opened; fallback structure overlay behavior was used.")
                images[key] = None
                continue
            if image.size != (1200, 1200):
                warnings.append(f"{path.name} was resized to 1200x1200 for composite rendering.")
                image = image.resize((1200, 1200), Image.Resampling.LANCZOS)
            images[key] = image
        return images

    def load_layout_furniture_validated(self, run_id: str) -> FurniturePlacementValidationArtifact:
        path = self._artifacts_dir(run_id) / "layout_furniture_validated.json"
        if not path.exists():
            raise HTTPException(status_code=400, detail="layout_furniture_validated.json is required for structure-locked composite rendering")
        try:
            return FurniturePlacementValidationArtifact.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"invalid layout_furniture_validated.json: {exc}") from exc

    def load_render_plan(self, run_id: str) -> RenderPlanArtifact | None:
        path = self._artifacts_dir(run_id) / "render_plan.json"
        if not path.exists():
            return None
        try:
            return RenderPlanArtifact.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def create_watercolor_paper_background(self, width: int, height: int) -> Image.Image:
        rng = random.Random(1337)
        background = Image.new("RGBA", (width, height), (250, 246, 235, 255))
        noise = Image.new("L", (width, height), 0)
        noise_pixels = noise.load()
        for y in range(height):
            for x in range(width):
                noise_pixels[x, y] = 120 + rng.randint(-12, 12)
        noise = noise.filter(ImageFilter.GaussianBlur(radius=0.8))
        paper_tint = Image.new("RGBA", (width, height), (255, 255, 255, 0))
        paper_tint.putalpha(noise.point(lambda value: int((value - 96) * 0.22)))
        background = Image.alpha_composite(background, paper_tint)

        wash = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        wash_draw = ImageDraw.Draw(wash, "RGBA")
        for _ in range(18):
            left = rng.randint(-120, width - 160)
            top = rng.randint(-120, height - 160)
            right = left + rng.randint(220, 480)
            bottom = top + rng.randint(180, 420)
            alpha = rng.randint(10, 22)
            wash_draw.ellipse((left, top, right, bottom), fill=(242, 236, 226, alpha))
        wash = wash.filter(ImageFilter.GaussianBlur(radius=18))
        return Image.alpha_composite(background, wash)

    def paint_room_floor_tones(self, base: Image.Image, rooms: list, style: dict, warnings: list[str]) -> Image.Image:
        rng = random.Random(2026)
        layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer, "RGBA")
        for room in rooms:
            bbox = getattr(room, "bbox", None)
            if bbox is None:
                continue
            floor_tone = str(getattr(room, "floor_tone", None) or style.get("palette", {}).get("floor_tone") or "light_brown")
            if floor_tone not in self.FLOOR_TONE_COLORS:
                warnings.append(f"Unsupported floor tone '{floor_tone}' fell back to light_brown in composite rendering.")
                floor_tone = "unknown"
            fill = self.FLOOR_TONE_COLORS[floor_tone]
            polygon = getattr(room, "polygon", None)
            if polygon:
                draw.polygon([(int(point[0]), int(point[1])) for point in polygon], fill=fill)
            else:
                draw.rounded_rectangle(self._bbox_tuple(bbox), radius=16, fill=fill)
                wash = self._create_room_wash(bbox, fill, rng)
                layer = Image.alpha_composite(layer, wash)
        layer = layer.filter(ImageFilter.GaussianBlur(radius=3.0))
        return Image.alpha_composite(base, layer)

    def cover_original_japanese_labels(self, base: Image.Image, labels: list, rooms: list, warnings: list[str], after_overlay: bool = False) -> Image.Image:
        layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer, "RGBA")
        room_by_id = {room.id: room for room in rooms}
        for label in labels:
            bbox = getattr(label, "bbox", None)
            estimated = False
            room = room_by_id.get(getattr(label, "room_id", ""))
            room_type = str(getattr(room, "type", "") or "")
            if bbox is None:
                room_bbox = getattr(room, "bbox", None) if room is not None else None
                if room_bbox is None:
                    continue
                estimated = True
                bbox = self._estimated_label_bbox(room_bbox, room_type=room_type)
            pad_x, pad_y = self._label_cover_padding(room_type)
            fill = self._label_cover_fill(room)
            draw.rounded_rectangle(
                (
                    int(bbox.x_min) - pad_x,
                    int(bbox.y_min) - pad_y,
                    int(bbox.x_max) + pad_x,
                    int(bbox.y_max) + pad_y,
                ),
                radius=16,
                fill=fill,
            )
            if estimated:
                warnings.append(f"Label cover area for {label.id} was estimated from room center.")
        if after_overlay:
            layer = layer.filter(ImageFilter.GaussianBlur(radius=4.0))
        return Image.alpha_composite(base, layer)

    def draw_furniture_layer(self, base: Image.Image, furniture: list, warnings: list[str]) -> Image.Image:
        layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer, "RGBA")
        drawn = 0
        skipped = 0
        for item in furniture:
            if item.placement_status not in {"auto_placed", "manually_placed"}:
                skipped += 1
                continue
            if item.bbox is None:
                skipped += 1
                warnings.append(f"Furniture {item.id} was skipped because bbox is missing.")
                continue
            self._draw_single_furniture(draw, item.type, item.bbox, item.base_color, item.accent_colors or [])
            drawn += 1
        base.info["furniture_drawn_count"] = drawn
        base.info["furniture_skipped_count"] = skipped
        return Image.alpha_composite(base, layer)

    def draw_english_labels(self, base: Image.Image, labels: list, rooms: list, furniture: list, warnings: list[str]) -> Image.Image:
        layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer, "RGBA")
        room_by_id = {room.id: room for room in rooms}
        room_furniture = self._group_drawn_furniture_by_room(furniture)
        for label in labels:
            text = str(getattr(label, "text", "Unknown") or "Unknown")
            if text not in self.ALLOWED_LABELS:
                text = "Unknown"
            room = room_by_id.get(getattr(label, "room_id", ""))
            bbox = getattr(label, "bbox", None)
            if bbox is None and room is not None:
                bbox = self._estimated_label_bbox(room.bbox, room_type=str(getattr(room, "type", "") or ""))
            if bbox is None:
                continue
            target_bbox = self._label_target_bbox(bbox, room, room_furniture.get(getattr(room, "id", ""), []))
            font = self._load_font(self._label_font_size(target_bbox))
            text_bbox = draw.textbbox((0, 0), text, font=font)
            text_width = text_bbox[2] - text_bbox[0]
            text_height = text_bbox[3] - text_bbox[1]
            center_x = int((target_bbox.x_min + target_bbox.x_max) / 2)
            center_y = int((target_bbox.y_min + target_bbox.y_max) / 2)
            left = center_x - text_width // 2
            top = center_y - text_height // 2
            backing = (left - 18, top - 12, left + text_width + 18, top + text_height + 12)
            draw.rounded_rectangle(backing, radius=12, fill=(255, 252, 248, 208))
            draw.text((left, top), text, font=font, fill=(68, 64, 60, 255))
        return Image.alpha_composite(base, layer)

    def overlay_structure_lines(
        self,
        base: Image.Image,
        line_preview: Image.Image | None,
        binary_mask: Image.Image | None,
        edges: Image.Image | None,
        warnings: list[str],
    ) -> Image.Image:
        result = base
        overlay_used = False
        if line_preview is not None:
            result = self._apply_line_overlay(result, line_preview, (82, 78, 74, 255), self.STRUCTURE_OVERLAY_OPACITY)
            overlay_used = True
        if edges is not None:
            result = self._apply_line_overlay(result, edges, (88, 84, 80, 255), 0.22)
            overlay_used = True
        if binary_mask is not None:
            result = self._apply_mask_overlay(result, binary_mask)
            overlay_used = True
        if not overlay_used:
            warnings.append("No structure overlay artifact was available; normalized floorplan alignment could not be reinforced.")
        return result

    def save_composite_output(self, run_id: str, image: Image.Image) -> dict:
        outputs_dir = self._outputs_dir(run_id)
        outputs_dir.mkdir(parents=True, exist_ok=True)
        path = outputs_dir / f"{run_id}_structure_locked_composite.png"
        try:
            image.convert("RGB").save(path, format="PNG")
        except OSError as exc:
            raise HTTPException(status_code=500, detail="failed to save structure-locked composite output image") from exc
        return {
            "composite_image_path": self._relative_storage_path(path),
            "composite_image_preview_url": f"/{self._relative_storage_path(path)}",
            "width": 1200,
            "height": 1200,
            "format": "png",
            "furniture_drawn_count": int(image.info.get("furniture_drawn_count", 0)),
            "furniture_skipped_count": int(image.info.get("furniture_skipped_count", 0)),
        }

    def build_artifact(
        self,
        run_id: str,
        layout: FurniturePlacementValidationArtifact,
        render_plan: RenderPlanArtifact | None,
        output_info: dict,
        images: dict[str, Image.Image | None],
        warnings: list[str],
        errors: list[str],
    ) -> StructureLockedCompositeArtifact:
        furniture_drawn_count = int(output_info.get("furniture_drawn_count", 0))
        furniture_skipped_count = int(output_info.get("furniture_skipped_count", 0))
        furniture_drawn_count = furniture_drawn_count or 0
        furniture_skipped_count = furniture_skipped_count or 0
        if "furniture_drawn_count" not in output_info:
            furniture_drawn_count = sum(1 for item in layout.furniture if item.placement_status in {"auto_placed", "manually_placed"} and item.bbox is not None)
            furniture_skipped_count = sum(1 for item in layout.furniture if item.placement_status not in {"auto_placed", "manually_placed"} or item.bbox is None)

        composite_status = "created"
        if errors:
            composite_status = "failed"
        elif warnings or any(item.placement_status == "suggested_unplaced" for item in layout.furniture):
            composite_status = "created_with_warnings"

        return StructureLockedCompositeArtifact(
            run_id=run_id,
            generated_at=datetime.now(timezone.utc),
            composite_status=composite_status,
            source={
                "normalized_floorplan": self._relative_artifact_path(run_id, "normalized_floorplan.png"),
                "binary_mask": self._relative_artifact_path(run_id, "binary_mask.png"),
                "edges": self._relative_artifact_path(run_id, "edges.png"),
                "line_preview": self._relative_artifact_path(run_id, "line_preview.png"),
                "layout_furniture_validated": self._relative_artifact_path(run_id, "layout_furniture_validated.json"),
                "render_plan": self._relative_artifact_path(run_id, "render_plan.json"),
            },
            outputs=output_info,
            rendering={
                "renderer": "deterministic_structure_locked_composite_v1",
                "ai_provider_used": False,
                "structure_overlay_applied": any(images.get(key) is not None for key in ("line_preview", "binary_mask", "edges")),
                "watercolor_background_applied": True,
                "floor_tone_applied": True,
                "english_labels_drawn": True,
                "english_labels_drawn_last": True,
                "japanese_labels_covered": True,
                "japanese_label_cover_after_overlay": True,
                "structure_overlay_opacity": self.STRUCTURE_OVERLAY_OPACITY,
                "binary_mask_full_overlay_used": False,
                "furniture_drawn_count": furniture_drawn_count,
                "furniture_skipped_count": furniture_skipped_count,
            },
            quality={
                "needs_human_review": True,
                "layout_structure_locked": True,
                "uses_original_structure_lines": any(images.get(key) is not None for key in ("line_preview", "binary_mask", "edges")),
                "image_generation_done": False,
                "watercolor_rendering_done": True,
                "ready_for_visual_qa": True,
            },
            warnings=self._dedupe_keep_order(warnings),
            errors=self._dedupe_keep_order(errors),
        )

    def write_structure_locked_composite_artifact(self, run_id: str, artifact: StructureLockedCompositeArtifact) -> None:
        path = self._artifacts_dir(run_id) / "structure_locked_composite.json"
        try:
            with path.open("w", encoding="utf-8") as output_file:
                json.dump(artifact.model_dump(mode="json"), output_file, ensure_ascii=False, indent=2)
        except OSError as exc:
            raise HTTPException(status_code=500, detail="failed to write structure_locked_composite artifact") from exc

    def load_structure_locked_composite(self, run_id: str) -> StructureLockedCompositeArtifact:
        path = self._artifacts_dir(run_id) / "structure_locked_composite.json"
        if not path.exists():
            raise HTTPException(status_code=404, detail="structure_locked_composite artifact not found")
        try:
            return StructureLockedCompositeArtifact.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=500, detail="failed to read structure_locked_composite artifact") from exc

    def build_metadata_updates(self, metadata: RunMetadata, artifact: StructureLockedCompositeArtifact) -> dict:
        now = datetime.now(timezone.utc)
        return {
            "status": "structure_locked_composite_created",
            "run_status": "structure_locked_composite_created",
            "updated_at": now,
            "processing": metadata.processing.model_copy(
                update={
                    "structure_locked_composite_rendering": True,
                    "image_generation": False,
                    "watercolor_rendering": True,
                }
            ),
            "pipeline": {
                "current_phase": "phase_6a_structure_locked_composite_renderer",
                "next_phase": "phase_6b_visual_qa",
            },
            "structure_locked_composite_path": self._relative_artifact_path(metadata.run_id, "structure_locked_composite.json"),
            "structure_locked_composite_summary": StructureLockedCompositeSummary(
                composite_status=artifact.composite_status,
                composite_image_preview_url=artifact.outputs.get("composite_image_preview_url"),
                width=int(artifact.outputs.get("width") or 1200),
                height=int(artifact.outputs.get("height") or 1200),
                ai_provider_used=bool(artifact.rendering.get("ai_provider_used", False)),
                structure_overlay_applied=bool(artifact.rendering.get("structure_overlay_applied", False)),
                furniture_drawn_count=int(artifact.rendering.get("furniture_drawn_count", 0)),
                furniture_skipped_count=int(artifact.rendering.get("furniture_skipped_count", 0)),
                needs_human_review=bool(artifact.quality.get("needs_human_review", True)),
                warnings_count=len(artifact.warnings),
                errors_count=len(artifact.errors),
            ),
        }

    def _draw_single_furniture(self, draw: ImageDraw.ImageDraw, furniture_type: str, bbox, base_color: str | None, accent_colors: list[str]) -> None:
        x1, y1, x2, y2 = self._bbox_tuple(bbox)
        fill = self._fill_for_furniture(furniture_type, base_color)
        outline = (86, 78, 70, 176)
        if furniture_type in {"sofa_3_seater", "bed", "two_single_beds", "kitchen_counter", "cabinet", "tv_stand"}:
            draw.rounded_rectangle((x1, y1, x2, y2), radius=10, fill=fill, outline=outline, width=1)
        elif furniture_type in {"coffee_table", "dining_table", "rug", "bathtub"}:
            draw.rounded_rectangle((x1, y1, x2, y2), radius=12, fill=fill, outline=outline, width=1)
        elif furniture_type in {"sink", "stove", "potted_plant", "floor_lamp", "shower"}:
            draw.ellipse((x1, y1, x2, y2), fill=fill, outline=outline, width=1)
        elif furniture_type in {"tv", "wall_art"}:
            draw.rectangle((x1, y1, x2, y2), fill=fill, outline=outline, width=1)
        elif furniture_type in {"curtain", "towel", "blanket"}:
            draw.rounded_rectangle((x1, y1, x2, y2), radius=6, fill=fill, outline=outline, width=1)
        elif furniture_type == "chair":
            draw.rounded_rectangle((x1, y1, x2, y2), radius=6, fill=fill, outline=outline, width=1)
            draw.line((x1 + 4, y2 - 2, x1 + 4, min(1199, y2 + 8)), fill=outline, width=1)
            draw.line((x2 - 4, y2 - 2, x2 - 4, min(1199, y2 + 8)), fill=outline, width=1)
        elif furniture_type == "pillow":
            draw.rounded_rectangle((x1, y1, x2, y2), radius=8, fill=fill, outline=outline, width=1)
        else:
            draw.rounded_rectangle((x1, y1, x2, y2), radius=8, fill=fill, outline=outline, width=1)

        if furniture_type == "sofa_3_seater":
            third = max(6, (x2 - x1) // 3)
            for idx in range(1, 3):
                draw.line((x1 + third * idx, y1 + 8, x1 + third * idx, y2 - 8), fill=outline, width=1)
        elif furniture_type == "two_single_beds":
            mid = (x1 + x2) // 2
            draw.line((mid, y1 + 4, mid, y2 - 4), fill=outline, width=1)
        elif furniture_type == "bed":
            draw.line((x1 + 8, y1 + 10, x2 - 8, y1 + 10), fill=outline, width=1)
        elif furniture_type == "tv":
            draw.line((x1 + 6, y2 + 2, x2 - 6, y2 + 2), fill=outline, width=1)
        elif furniture_type == "floor_lamp":
            center_x = (x1 + x2) // 2
            draw.line((center_x, y1, center_x, y2), fill=outline, width=1)
        elif furniture_type == "curtain":
            for offset in range(x1 + 4, x2, 8):
                draw.line((offset, y1, offset, y2), fill=(168, 160, 150, 140), width=1)
        elif furniture_type == "sink":
            draw.ellipse((x1 + 4, y1 + 4, x2 - 4, y2 - 4), outline=outline, width=1)
        elif furniture_type == "stove":
            draw.ellipse((x1 + 3, y1 + 3, x1 + (x2 - x1) // 2, y1 + (y2 - y1) // 2), outline=outline, width=1)
            draw.ellipse((x1 + (x2 - x1) // 2, y1 + (y2 - y1) // 2, x2 - 3, y2 - 3), outline=outline, width=1)
        elif furniture_type == "potted_plant":
            draw.rectangle((x1 + 6, y2 - 12, x2 - 6, y2 - 2), fill=(154, 118, 92, 180), outline=outline, width=1)
        elif furniture_type == "towel":
            draw.line((x1 + 3, y1 + 3, x2 - 3, y2 - 3), fill=(120, 120, 120, 160), width=1)

    def _apply_line_overlay(self, base: Image.Image, source: Image.Image, color: tuple[int, int, int, int], strength: float) -> Image.Image:
        gray = ImageOps.grayscale(source)
        alpha = gray.point(lambda value: int((255 - value) * strength))
        line_layer = Image.new("RGBA", base.size, color)
        line_layer.putalpha(alpha)
        return Image.alpha_composite(base, line_layer)

    def _apply_mask_overlay(self, base: Image.Image, source: Image.Image) -> Image.Image:
        blurred = ImageOps.grayscale(source).filter(ImageFilter.GaussianBlur(radius=1.2))
        gray = blurred.point(lambda value: 36 if value < 92 else (12 if value < 128 else 0))
        mask_layer = Image.new("RGBA", base.size, (78, 74, 70, 0))
        mask_layer.putalpha(gray)
        return Image.alpha_composite(base, mask_layer)

    def _fill_for_furniture(self, furniture_type: str, base_color: str | None) -> tuple[int, int, int, int]:
        if furniture_type in {"sofa_3_seater", "bed", "two_single_beds", "pillow", "blanket"}:
            return (246, 244, 240, 188)
        if furniture_type in {"kitchen_counter", "sink", "stove", "cabinet"}:
            return (206, 196, 184, 170)
        if furniture_type in {"bathtub", "shower", "towel"}:
            return (224, 228, 230, 170)
        if furniture_type in {"rug"}:
            return (198, 181, 160, 140)
        if furniture_type in {"potted_plant"}:
            return (156, 183, 146, 165)
        if furniture_type in {"tv", "tv_stand", "wall_art"}:
            return (196, 198, 204, 160)
        if furniture_type in {"floor_lamp", "chair", "dining_table", "coffee_table"}:
            return (201, 184, 164, 155)
        return (228, 216, 198, 160)

    def _estimated_label_bbox(self, room_bbox, room_type: str = "") -> object:
        room_width = int(room_bbox.x_max - room_bbox.x_min)
        room_height = int(room_bbox.y_max - room_bbox.y_min)
        width_scale = 0.56 if room_type in {"living_room", "bed_room"} else 0.46
        width = max(110, min(240, int(room_width * width_scale)))
        height = max(38, min(68, int(room_height * 0.16)))
        center_x = int((room_bbox.x_min + room_bbox.x_max) / 2)
        center_y = int((room_bbox.y_min + room_bbox.y_max) / 2)
        return type(room_bbox)(
            x_min=max(0, center_x - width // 2),
            y_min=max(0, center_y - height // 2),
            x_max=min(1199, center_x + width // 2),
            y_max=min(1199, center_y + height // 2),
        )

    def _label_font_size(self, bbox) -> int:
        width = int(bbox.x_max - bbox.x_min)
        height = int(bbox.y_max - bbox.y_min)
        return max(22, min(42, int(min(width, height) * 0.62)))

    def _label_cover_fill(self, room) -> tuple[int, int, int, int]:
        room_type = str(getattr(room, "type", "") or "") if room is not None else ""
        tone = str(getattr(room, "floor_tone", None) or "light_brown")
        fill = self.FLOOR_TONE_COLORS.get(tone, self.FLOOR_TONE_COLORS["light_brown"])
        alpha = 212 if room_type in {"living_room", "bed_room"} else 196
        return fill[:3] + (alpha,)

    def _label_cover_padding(self, room_type: str) -> tuple[int, int]:
        if room_type in {"living_room", "bed_room"}:
            return 42, 24
        if room_type in {"closet", "kitchen", "toilet", "bath_room", "wash_area"}:
            return 24, 16
        return 28, 18

    def _group_drawn_furniture_by_room(self, furniture: list) -> dict[str, list]:
        grouped: dict[str, list] = {}
        for item in furniture:
            if item.placement_status not in {"auto_placed", "manually_placed"} or item.bbox is None:
                continue
            grouped.setdefault(item.room_id or "", []).append(item)
        return grouped

    def _label_target_bbox(self, bbox, room, furniture_items: list) -> object:
        if room is None or not furniture_items:
            return bbox
        room_bbox = room.bbox
        room_center_y = int((room_bbox.y_min + room_bbox.y_max) / 2)
        top_half_occupied = any(int(item.bbox.y_min) < room_center_y for item in furniture_items if item.bbox is not None)
        if not top_half_occupied:
            return bbox
        room_type = str(getattr(room, "type", "") or "")
        adjusted = self._estimated_label_bbox(room_bbox, room_type=room_type)
        offset = max(24, int((room_bbox.y_max - room_bbox.y_min) * 0.18))
        return type(bbox)(
            x_min=adjusted.x_min,
            y_min=max(int(room_bbox.y_min) + 12, adjusted.y_min - offset),
            x_max=adjusted.x_max,
            y_max=max(int(room_bbox.y_min) + 24, adjusted.y_max - offset),
        )

    def _create_room_wash(self, bbox, fill: tuple[int, int, int, int], rng: random.Random) -> Image.Image:
        layer = Image.new("RGBA", (1200, 1200), (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer, "RGBA")
        x1, y1, x2, y2 = self._bbox_tuple(bbox)
        width = x2 - x1
        height = y2 - y1
        for _ in range(5):
            inset_x = rng.randint(0, max(4, width // 10))
            inset_y = rng.randint(0, max(4, height // 10))
            alpha = max(12, fill[3] - rng.randint(20, 34))
            draw.rounded_rectangle(
                (x1 + inset_x, y1 + inset_y, x2 - inset_x, y2 - inset_y),
                radius=18,
                fill=fill[:3] + (alpha,),
            )
        return layer.filter(ImageFilter.GaussianBlur(radius=8.0))

    @staticmethod
    def _bbox_tuple(bbox) -> tuple[int, int, int, int]:
        return int(bbox.x_min), int(bbox.y_min), int(bbox.x_max), int(bbox.y_max)

    @staticmethod
    def _load_font(size: int) -> ImageFont.ImageFont:
        for font_name in ("DejaVuSans.ttf", "DejaVuSerif.ttf", "arial.ttf"):
            try:
                return ImageFont.truetype(font_name, size=size)
            except OSError:
                continue
        return ImageFont.load_default()

    def _artifacts_dir(self, run_id: str) -> Path:
        return self._safe_run_dir(run_id) / "artifacts"

    def _outputs_dir(self, run_id: str) -> Path:
        return self._safe_run_dir(run_id) / "outputs"

    def _safe_run_dir(self, run_id: str) -> Path:
        run_dir = (self.storage_runs_dir / run_id).resolve()
        try:
            run_dir.relative_to(self.storage_runs_dir.resolve())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid run path") from exc
        if not run_dir.exists():
            raise HTTPException(status_code=404, detail="run not found")
        return run_dir

    def _relative_artifact_path(self, run_id: str, filename: str) -> str:
        return f"storage/runs/{run_id}/artifacts/{filename}"

    def _relative_storage_path(self, path: Path) -> str:
        return path.relative_to(self.storage_dir.parent).as_posix()

    @staticmethod
    def _dedupe_keep_order(values: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for value in values:
            if value in seen:
                continue
            seen.add(value)
            result.append(value)
        return result
