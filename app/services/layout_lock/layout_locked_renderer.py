from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException
from PIL import Image

from app.config import get_settings
from app.services.layout_lock.watercolor_background import WatercolorBackground


class LayoutLockedRenderer:
    def render(
        self,
        run_dir: Path,
        normalized_floorplan_path: Path,
        structure_layer_path: Path,
        output_path: Path,
        furniture_layout: dict | None = None,
        manual_labels: dict | None = None,
    ) -> dict:
        warnings: list[str] = []
        settings = get_settings()
        if not normalized_floorplan_path.exists():
            raise HTTPException(status_code=404, detail="normalized floorplan image not found for layout-locked rendering")
        if not structure_layer_path.exists():
            raise HTTPException(status_code=404, detail="structure layer not found for layout-locked rendering")

        background_path = run_dir / "watercolor_background.png"
        if settings.watercolor_background_enabled:
            background_metadata = WatercolorBackground().create(
                background_path,
                width=settings.output_width,
                height=settings.output_height,
                seed=run_dir.name,
            )
        else:
            Image.new("RGB", (settings.output_width, settings.output_height), (255, 253, 248)).save(
                background_path,
                format="PNG",
            )
            background_metadata = {
                "method": "plain_cream_background",
                "mode": settings.watercolor_background_mode,
                "width": settings.output_width,
                "height": settings.output_height,
                "warnings": ["Watercolor background is disabled by config."],
            }

        try:
            background = Image.open(background_path).convert("RGB")
            normalized = Image.open(normalized_floorplan_path).convert("RGB")
            structure_layer = Image.open(structure_layer_path).convert("RGBA")
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"failed to load layout-lock render layers: {exc}") from exc

        if background.size != normalized.size or background.size != structure_layer.size:
            raise HTTPException(status_code=500, detail="layout-lock render layers must have identical canvas sizes")

        opacity = max(0.0, min(1.0, float(settings.layout_lock_normalized_floorplan_opacity)))
        if settings.layout_lock_blend_normalized_floorplan:
            underlay = Image.blend(background, normalized, opacity)
        else:
            underlay = background.copy()
            if opacity > 0:
                underlay.paste(normalized)
        output = underlay.convert("RGBA")

        if furniture_layout:
            warnings.append("Furniture rendering is skipped in layout-lock MVP; structure preservation takes priority.")

        if settings.layout_lock_reapply_structure:
            output.alpha_composite(structure_layer)
        else:
            warnings.append("Structure layer re-application is disabled by config.")

        if manual_labels:
            warnings.append("Manual labels are handled by the existing label workflow, not the layout-lock renderer.")

        output.convert("RGB").save(output_path, format="PNG")

        width, height = output.size
        return {
            "method": "layout_locked_renderer",
            "layout_locked": True,
            "structure_reapplied": bool(settings.layout_lock_reapply_structure),
            "output_width": width,
            "output_height": height,
            "blend_normalized_floorplan": bool(settings.layout_lock_blend_normalized_floorplan),
            "normalized_floorplan_opacity": opacity,
            "background": background_metadata,
            "warnings": warnings,
        }
