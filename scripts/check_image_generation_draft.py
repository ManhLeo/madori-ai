from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python scripts/check_image_generation_draft.py <image_generation_draft.json>")
        return 1

    path = Path(sys.argv[1])
    payload = json.loads(path.read_text(encoding="utf-8"))
    provider = payload.get("provider", {})
    request = payload.get("request", {})
    outputs = payload.get("outputs", {})
    postprocess = payload.get("postprocess", {})
    quality = payload.get("quality", {})

    raw_image_path = Path(outputs.get("raw_image_path", ""))
    draft_image_path = Path(outputs.get("draft_image_path", ""))

    print(
        {
            "draft_status": payload.get("draft_status"),
            "provider": provider.get("provider_name"),
            "api_call_performed": provider.get("api_call_performed"),
            "provider_size": request.get("provider_size"),
            "final_delivery_size": request.get("final_delivery_size"),
            "selected_reference_roles": [item.get("role") for item in request.get("selected_reference_images", []) if isinstance(item, dict)],
            "raw_image_path_exists": raw_image_path.exists(),
            "draft_image_path_exists": draft_image_path.exists(),
            "draft_width": outputs.get("width"),
            "draft_height": outputs.get("height"),
            "postprocess_done": postprocess.get("postprocess_done"),
            "needs_human_review": quality.get("needs_human_review"),
            "ready_for_visual_qa": quality.get("ready_for_visual_qa"),
        }
    )
    print("\n--- warnings ---\n")
    print(json.dumps(payload.get("warnings", []), ensure_ascii=False, indent=2))
    print("\n--- errors ---\n")
    print(json.dumps(payload.get("errors", []), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
