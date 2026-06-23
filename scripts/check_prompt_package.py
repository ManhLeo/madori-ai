from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python scripts/check_prompt_package.py <prompt_package.json>")
        return 1

    path = Path(sys.argv[1])
    payload = json.loads(path.read_text(encoding="utf-8"))
    quality = payload.get("prompt_quality", {})
    provider_readiness = payload.get("provider_readiness", {})
    combined_prompt = payload.get("prompts", {}).get("combined_prompt", "")
    negative_prompt = payload.get("prompts", {}).get("negative_prompt", "")

    print(
        {
            "prompt_package_status": payload.get("prompt_package_status"),
            "target_output": payload.get("target_output"),
            "ready_for_openai_image_api": provider_readiness.get("ready_for_openai_image_api"),
            "ready_for_manual_review": provider_readiness.get("ready_for_manual_review"),
            "combined_prompt_char_count": quality.get("combined_prompt_char_count"),
            "negative_prompt_char_count": quality.get("negative_prompt_char_count"),
            "drawable_furniture_count": quality.get("drawable_furniture_count"),
            "skipped_furniture_count": quality.get("skipped_furniture_count"),
            "room_count": quality.get("room_count"),
            "label_count": quality.get("label_count"),
        }
    )
    print("\n--- combined_prompt (first 1000 chars) ---\n")
    print(combined_prompt[:1000])
    print("\n--- negative_prompt ---\n")
    print(negative_prompt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
