from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python scripts/check_regeneration_attempt.py <regeneration_attempt.json>", file=sys.stderr)
        return 1

    artifact_path = Path(sys.argv[1])
    if not artifact_path.exists():
        print(f"regeneration attempt artifact not found: {artifact_path}", file=sys.stderr)
        return 1

    try:
        payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"failed to read regeneration attempt artifact: {exc}", file=sys.stderr)
        return 1

    run_id = payload.get("run_id")
    status = payload.get("status")
    attempt = payload.get("attempt")
    outputs = payload.get("outputs") if isinstance(payload.get("outputs"), dict) else {}
    source_feedback = payload.get("source_feedback") if isinstance(payload.get("source_feedback"), dict) else {}
    cloudinary = payload.get("cloudinary") if isinstance(payload.get("cloudinary"), dict) else {}
    regenerated = cloudinary.get("regenerated") if isinstance(cloudinary.get("regenerated"), dict) else {}
    output_image_path = outputs.get("output_image_path")

    if not run_id or not status:
        print("regeneration attempt artifact is missing run_id or status", file=sys.stderr)
        return 1
    if status != "completed":
        print(f"regeneration attempt status is not completed: {status}", file=sys.stderr)
        return 1
    if not output_image_path:
        print("regeneration attempt artifact is missing outputs.output_image_path", file=sys.stderr)
        return 1

    output_path = Path(output_image_path)
    if not output_path.is_absolute():
        repo_root = Path(__file__).resolve().parents[1]
        output_path = (repo_root / output_image_path).resolve()
    if not output_path.exists():
        print(f"referenced regenerated output image does not exist: {output_path}", file=sys.stderr)
        return 1

    print(f"run_id: {run_id}")
    print(f"attempt: {attempt}")
    print(f"status: {status}")
    print(f"provider: {payload.get('provider')}")
    print(f"model: {payload.get('model')}")
    print(f"prompt_mode: {payload.get('prompt_mode')}")
    print("openai image attempts:")
    for item in payload.get("openai_image_attempts") or []:
        if not isinstance(item, dict):
            print(" - invalid attempt record")
            continue
        print(
            " - attempt {attempt}: status={status}, error_type={error_type}, message={message}, timeout_seconds={timeout_seconds}, input_image_count={input_image_count}".format(
                attempt=item.get("attempt"),
                status=item.get("status"),
                error_type=item.get("error_type"),
                message=item.get("message"),
                timeout_seconds=item.get("timeout_seconds"),
                input_image_count=item.get("input_image_count"),
            )
        )
        for input_image in item.get("input_images") or []:
            if isinstance(input_image, dict):
                print(
                    f"   - {input_image.get('filename')} [{input_image.get('mime_type')}] size={input_image.get('size_bytes')} path={input_image.get('path')}"
                )
    print(f"source feedback path: {source_feedback.get('qa_feedback_path')}")
    print(f"issues_count: {source_feedback.get('issues_count')}")
    print(f"highest_severity: {source_feedback.get('highest_severity')}")
    print("correction guidance used:")
    for item in payload.get("correction_guidance_used") or []:
        print(f" - {item}")
    print("negative guidance used:")
    for item in payload.get("negative_guidance_used") or []:
        print(f" - {item}")
    correction_text = "\n".join(str(item) for item in payload.get("correction_guidance_used") or [])
    negative_text = "\n".join(str(item) for item in payload.get("negative_guidance_used") or [])
    combined_guidance = f"{correction_text}\n{negative_text}".lower()
    print(f"bright palette guidance used: {'brighter overall palette' in combined_guidance or 'light warm beige' in combined_guidance}")
    print(f"light wall guidance used: {'light neutral wall tones' in combined_guidance or 'dark wall fills' in combined_guidance}")
    print(f"washing machine wash guidance used: {'washing machine' in combined_guidance and 'wash room' in combined_guidance}")
    print(
        f"furniture orientation guidance used: {'tv faces sofa' in combined_guidance or 'furniture orientation' in combined_guidance or 'coffee table sits between sofa and tv' in combined_guidance or 'orient furniture naturally' in combined_guidance}"
    )
    print("openai input images:")
    for item in payload.get("openai_input_images") or []:
        if isinstance(item, dict):
            print(f" - {item.get('filename')} [{item.get('mime_type')}] role={item.get('role')}")
    print(f"output image path: {output_image_path}")
    print(f"output image exists: {output_path.exists()}")
    print(f"public_output_url: {outputs.get('public_output_url')}")
    print(f"cloudinary enabled: {cloudinary.get('enabled')}")
    print(f"cloudinary uploaded: {regenerated.get('uploaded')}")
    print(f"warnings: {payload.get('warnings') or []}")
    print(f"errors: {payload.get('errors') or []}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
