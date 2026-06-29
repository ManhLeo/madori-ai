from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python scripts/check_final_output.py <final_output.json>", file=sys.stderr)
        return 1

    artifact_path = Path(sys.argv[1]).resolve()
    if not artifact_path.exists():
        print(f"final_output artifact not found: {artifact_path}", file=sys.stderr)
        return 1

    try:
        payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"failed to read final_output artifact: {exc}", file=sys.stderr)
        return 1

    run_id = payload.get("run_id")
    final_status = payload.get("final_status")
    final = payload.get("final") if isinstance(payload.get("final"), dict) else {}
    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    qa = payload.get("qa") if isinstance(payload.get("qa"), dict) else {}
    generation = payload.get("generation") if isinstance(payload.get("generation"), dict) else {}
    cloudinary = payload.get("cloudinary") if isinstance(payload.get("cloudinary"), dict) else {}
    warnings = payload.get("warnings") if isinstance(payload.get("warnings"), list) else []
    errors = payload.get("errors") if isinstance(payload.get("errors"), list) else []

    final_image_path = final.get("final_image_path")
    if not run_id or not final_status or not final_image_path:
        print("final_output artifact is missing required fields", file=sys.stderr)
        return 1

    repo_root = Path(__file__).resolve().parent.parent
    final_image_fs_path = repo_root / Path(final_image_path)
    final_image_exists = final_image_fs_path.exists()
    if not final_image_exists:
        print(f"referenced final image does not exist: {final_image_fs_path}", file=sys.stderr)
        return 1

    print(f"run_id: {run_id}")
    print(f"final_status: {final_status}")
    print(f"final image exists: {final_image_exists}")
    print(f"final image path: {final_image_path}")
    print(f"final_image_preview_url: {final.get('final_image_preview_url')}")
    print(f"public_output_url: {final.get('public_output_url')}")
    print(f"width: {final.get('width')}")
    print(f"height: {final.get('height')}")
    print(f"format: {final.get('format')}")
    print(f"source image path: {source.get('source_image_path')}")
    print(f"source_type: {source.get('source_type')}")
    print(f"qa_status: {qa.get('qa_status')}")
    print(f"visual_qa_report_path: {qa.get('visual_qa_report_path')}")
    print(f"prompt_mode: {generation.get('prompt_mode')}")
    print(f"provider: {generation.get('provider')}")
    print(f"model: {generation.get('model')}")
    print(f"cloudinary enabled: {cloudinary.get('enabled')}")
    print(f"cloudinary final uploaded: {cloudinary.get('final', {}).get('uploaded') if isinstance(cloudinary.get('final'), dict) else None}")
    print(f"cloudinary secure_url: {cloudinary.get('final', {}).get('secure_url') if isinstance(cloudinary.get('final'), dict) else None}")
    print(f"final public_output_url: {final.get('public_output_url')}")
    print(f"warnings: {warnings}")
    print(f"errors: {errors}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
