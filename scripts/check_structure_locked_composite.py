from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python scripts/check_structure_locked_composite.py <structure_locked_composite.json>")
        return 1

    path = Path(sys.argv[1])
    payload = json.loads(path.read_text(encoding="utf-8"))
    outputs = payload.get("outputs", {})
    rendering = payload.get("rendering", {})
    quality = payload.get("quality", {})
    output_path = Path(outputs.get("composite_image_path", ""))

    print(
        {
            "composite_status": payload.get("composite_status"),
            "output_path_exists": output_path.exists(),
            "width": outputs.get("width"),
            "height": outputs.get("height"),
            "ai_provider_used": rendering.get("ai_provider_used"),
            "structure_overlay_applied": rendering.get("structure_overlay_applied"),
            "watercolor_background_applied": rendering.get("watercolor_background_applied"),
            "english_labels_drawn": rendering.get("english_labels_drawn"),
            "japanese_labels_covered": rendering.get("japanese_labels_covered"),
            "furniture_drawn_count": rendering.get("furniture_drawn_count"),
            "furniture_skipped_count": rendering.get("furniture_skipped_count"),
            "image_generation_done": quality.get("image_generation_done"),
            "watercolor_rendering_done": quality.get("watercolor_rendering_done"),
        }
    )
    print("\n--- warnings ---\n")
    print(json.dumps(payload.get("warnings", []), ensure_ascii=False, indent=2))
    print("\n--- errors ---\n")
    print(json.dumps(payload.get("errors", []), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
