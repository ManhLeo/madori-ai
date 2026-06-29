from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python scripts/check_qa_feedback.py <qa_feedback.json>", file=sys.stderr)
        return 1

    artifact_path = Path(sys.argv[1])
    if not artifact_path.exists():
        print(f"qa_feedback artifact not found: {artifact_path}", file=sys.stderr)
        return 1

    try:
        payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"failed to read qa_feedback artifact: {exc}", file=sys.stderr)
        return 1

    run_id = payload.get("run_id")
    feedback_status = payload.get("feedback_status")
    if not run_id or not feedback_status:
        print("qa_feedback artifact is missing run_id or feedback_status", file=sys.stderr)
        return 1

    target_image = payload.get("target_image") or {}
    issues = payload.get("issues") or []
    correction_plan = payload.get("correction_plan") or {}
    warnings = payload.get("warnings") or []
    errors = payload.get("errors") or []

    highest_severity = "low"
    if any(issue.get("severity") == "high" for issue in issues):
        highest_severity = "high"
    elif any(issue.get("severity") == "medium" for issue in issues):
        highest_severity = "medium"

    print(f"run_id: {run_id}")
    print(f"feedback_status: {feedback_status}")
    print(f"target image: {target_image.get('target_image_type')}")
    print(f"target image path: {target_image.get('target_image_path')}")
    print(f"target image preview_url: {target_image.get('target_image_preview_url')}")
    print(f"target public_url: {target_image.get('target_public_url')}")
    print(f"issue count: {len(issues)}")
    print(f"highest severity: {highest_severity}")
    print("issues:")
    for issue in issues:
        if not isinstance(issue, dict):
            print("- invalid issue record")
            continue
        print(
            f"- type={issue.get('issue_type')} severity={issue.get('severity')} description={issue.get('description')} correction_instruction={issue.get('correction_instruction')}"
        )
    print("correction prompt guidance:")
    for item in correction_plan.get("prompt_guidance") or []:
        print(f"- {item}")
    print("negative guidance:")
    for item in correction_plan.get("negative_guidance") or []:
        print(f"- {item}")
    print(f"warnings: {warnings}")
    print(f"errors: {errors}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
