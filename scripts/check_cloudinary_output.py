from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python scripts/check_cloudinary_output.py <image_generation_draft.json>")
        return 1

    artifact_path = Path(sys.argv[1])
    if not artifact_path.exists():
        print(f"artifact_not_found: {artifact_path}")
        return 1

    try:
        payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"failed_to_read_artifact: {exc}")
        return 1

    cloudinary = payload.get("cloudinary") or {}
    draft = cloudinary.get("draft") or {}
    raw_draft = cloudinary.get("raw_draft") or {}

    print(f"cloudinary_enabled: {bool(cloudinary.get('enabled'))}")
    print(f"draft_uploaded: {bool(draft.get('uploaded'))}")
    print(f"raw_draft_uploaded: {bool(raw_draft.get('uploaded'))}")
    print(f"secure_url: {draft.get('secure_url')}")
    print(f"public_id: {draft.get('public_id')}")
    print(f"width: {draft.get('width')}")
    print(f"height: {draft.get('height')}")
    print(f"public_output_url: {payload.get('public_output_url')}")
    print(f"warnings: {payload.get('warnings') or []}")
    print(f"errors: {payload.get('errors') or []}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
