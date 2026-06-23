from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.config import get_settings


def main() -> int:
    settings = get_settings()
    vision_provider = str(settings.vision_provider or "openai").strip().lower()
    payload = {
        "VISION_PROVIDER": vision_provider,
        "OPENAI_ANALYSIS_MODEL": settings.openai_analysis_model,
        "OPENAI_ANALYSIS_TIMEOUT_SECONDS": settings.openai_analysis_timeout_seconds,
        "OPENAI_ANALYSIS_MAX_OUTPUT_TOKENS": settings.openai_analysis_max_output_tokens,
        "OPENAI_API_KEY_exists": bool(settings.openai_api_key),
        "GEMINI_MODEL": settings.gemini_model,
        "GEMINI_API_KEY_exists": bool(settings.gemini_api_key),
        "USE_GEMINI_ANALYSIS_legacy": settings.use_gemini_analysis,
        "gemini_used_for_current_provider": vision_provider == "gemini",
        "openai_image_model_separate": settings.openai_image_model,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
