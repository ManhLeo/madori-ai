from __future__ import annotations

import hashlib
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from app.config import get_settings


class WatercolorBackground:
    def create(
        self,
        output_path: Path,
        width: int = 1200,
        height: int = 1200,
        seed: str | None = None,
    ) -> dict:
        settings = get_settings()
        strength = max(0.0, min(1.0, float(settings.watercolor_background_strength)))
        rng = random.Random(self._seed_value(seed or output_path.parent.name))
        base = Image.new("RGB", (width, height), (252, 247, 238))
        draw = ImageDraw.Draw(base, "RGBA")

        wash_count = max(8, int(42 * strength))
        alpha_scale = strength * 0.55
        for _ in range(wash_count):
            cx = rng.randint(-80, width + 80)
            cy = rng.randint(-80, height + 80)
            rx = rng.randint(90, 280)
            ry = rng.randint(70, 220)
            base_color = rng.choice(
                [
                    (238, 221, 196, 18),
                    (228, 238, 218, 12),
                    (232, 214, 190, 14),
                    (245, 231, 210, 16),
                ]
            )
            color = (*base_color[:3], max(1, int(base_color[3] * alpha_scale)))
            draw.ellipse((cx - rx, cy - ry, cx + rx, cy + ry), fill=color)

        noise = Image.new("L", (width, height))
        noise_pixels = noise.load()
        for y in range(height):
            for x in range(width):
                noise_pixels[x, y] = rng.randint(246, 255)
        noise = noise.filter(ImageFilter.GaussianBlur(radius=0.8))
        base = Image.blend(base, Image.merge("RGB", (noise, noise, noise)), 0.035 * strength)
        base = base.filter(ImageFilter.GaussianBlur(radius=0.25))

        if settings.watercolor_draw_frame:
            draw = ImageDraw.Draw(base, "RGBA")
            for inset, alpha in ((0, 10), (10, 6)):
                draw.rounded_rectangle(
                    (inset, inset, width - inset - 1, height - inset - 1),
                    radius=22,
                    outline=(155, 126, 87, alpha),
                    width=1,
                )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        base.save(output_path, format="PNG")
        return {
            "method": "pillow_soft_paper_watercolor",
            "mode": "soft_paper",
            "width": width,
            "height": height,
            "seed": seed or output_path.parent.name,
            "strength": strength,
            "draw_frame": bool(settings.watercolor_draw_frame),
            "warnings": [],
        }

    @staticmethod
    def _seed_value(value: str) -> int:
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
        return int(digest[:12], 16)
