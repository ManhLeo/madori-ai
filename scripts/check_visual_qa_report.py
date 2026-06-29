from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python scripts/check_visual_qa_report.py <visual_qa_report.json>")
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

    run_id = payload.get("run_id")
    qa_status = payload.get("qa_status")
    if not run_id:
        print("invalid_artifact: missing run_id")
        return 1
    if not qa_status:
        print("invalid_artifact: missing qa_status")
        return 1

    checks = payload.get("checks") or {}
    issues = payload.get("issues") or []
    source_draft = payload.get("source_draft") or {}

    print(f"run_id: {run_id}")
    print(f"qa_status: {qa_status}")
    for key in (
        "layout_preserved",
        "english_labels_correct",
        "room_roles_correct",
        "furniture_arrangement_correct",
        "bedroom_bed_count_correct",
        "dining_location_correct",
        "sofa_tv_arrangement_correct",
    ):
        print(f"{key}: {checks.get(key)}")
    print(f"final_usable_for_demo: {payload.get('final_usable_for_demo')}")
    print(f"issues_count: {len(issues)}")
    print(f"source_draft.draft_artifact_path: {source_draft.get('draft_artifact_path')}")
    print(f"source_draft.draft_image_path: {source_draft.get('draft_image_path')}")
    print(f"source_draft.public_output_url: {source_draft.get('public_output_url')}")
    print(f"source_draft.cloudinary_url: {source_draft.get('cloudinary_url')}")
    print(f"warnings: {payload.get('warnings') or []}")
    print(f"errors: {payload.get('errors') or []}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
