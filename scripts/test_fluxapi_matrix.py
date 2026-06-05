from __future__ import annotations

"""FluxAPI provider diagnostics only.

This script isolates FluxAPI account/model/service issues by running minimal
text-to-image and image-edit payloads. It is not part of the normal
/api/generate flow.
"""

import argparse
import json
import sys
import time
from pathlib import Path
from uuid import uuid4

import requests
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
load_dotenv(ROOT_DIR / ".env")

from app.config import get_settings
from app.services.image_provider import FLUXAPI_GENERATE_URL, FLUXAPI_RECORD_INFO_URL
from app.services.public_image_service import upload_floorplan_to_cloudinary


TEXT_TO_IMAGE_PROMPT = "A simple Japanese watercolor furnished apartment floorplan."
MINIMAL_TEXT_TO_IMAGE_PROMPT = "A simple Japanese watercolor apartment floorplan."
IMAGE_EDIT_PROMPT = "Convert this floorplan into a Japanese watercolor furnished floorplan. Preserve the original layout."
MINIMAL_IMAGE_EDIT_PROMPT = "Convert this floorplan into a Japanese watercolor furnished floorplan."


def main() -> int:
    parser = argparse.ArgumentParser(description="Run FluxAPI diagnostic matrix.")
    parser.add_argument("image_path", type=Path, help="Local floorplan image path for Cloudinary JPEG/PNG tests.")
    parser.add_argument(
        "input_image_url",
        nargs="?",
        default=None,
        help="Optional public image URL for Case B. Defaults to FLUXAPI_INPUT_IMAGE_URL.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="FluxAPI model override, e.g. flux-kontext-pro or flux-kontext-max.",
    )
    args = parser.parse_args()

    settings = get_settings()
    model = args.model or settings.fluxapi_model
    if not settings.fluxapi_api_key:
        print("FLUXAPI_API_KEY is missing.")
        return 1
    if not args.image_path.exists():
        print(f"Input image not found: {args.image_path}")
        return 1

    print(f"Loaded FLUXAPI_ENABLE_TRANSLATION: {settings.fluxapi_enable_translation}")
    print(f"Using model: {model}")

    case_b_url = args.input_image_url or settings.fluxapi_input_image_url
    cases = [
        {
            "name": "Case A: text-to-image only",
            "payload": build_payload(
                model=model,
                prompt=TEXT_TO_IMAGE_PROMPT,
                aspect_ratio=True,
                enable_translation=bool(settings.fluxapi_enable_translation),
            ),
            "input_image_url": None,
        },
        {
            "name": "Case B: image edit with current Cloudinary URL",
            "payload": build_payload(
                model=model,
                prompt=IMAGE_EDIT_PROMPT,
                input_image_url=case_b_url,
                aspect_ratio=True,
                enable_translation=bool(settings.fluxapi_enable_translation),
            ),
            "input_image_url": case_b_url,
        },
    ]

    run_suffix = uuid4().hex[:10]
    try:
        jpg_url = upload_floorplan_to_cloudinary(
            args.image_path,
            f"matrix_{run_suffix}_jpg",
            format_for_flux="jpg",
        )
    except Exception as exc:
        jpg_url = None
        print(f"Case C upload failed: {exc}")

    try:
        png_url = upload_floorplan_to_cloudinary(
            args.image_path,
            f"matrix_{run_suffix}_png",
            format_for_flux="png",
        )
    except Exception as exc:
        png_url = None
        print(f"Case D upload failed: {exc}")

    cases.extend(
        [
            {
                "name": "Case C: image edit with uploaded image converted to JPEG",
                "payload": build_payload(
                    model=model,
                    prompt=IMAGE_EDIT_PROMPT,
                    input_image_url=jpg_url,
                    aspect_ratio=True,
                    enable_translation=bool(settings.fluxapi_enable_translation),
                ),
                "input_image_url": jpg_url,
            },
            {
                "name": "Case D: image edit with uploaded image converted to PNG",
                "payload": build_payload(
                    model=model,
                    prompt=IMAGE_EDIT_PROMPT,
                    input_image_url=png_url,
                    aspect_ratio=True,
                    enable_translation=bool(settings.fluxapi_enable_translation),
                ),
                "input_image_url": png_url,
            },
            {
                "name": "Case E: minimal text-to-image payload",
                "payload": build_payload(
                    model=model,
                    prompt=MINIMAL_TEXT_TO_IMAGE_PROMPT,
                ),
                "input_image_url": None,
            },
            {
                "name": "Case F: minimal text-to-image payload with aspectRatio only",
                "payload": build_payload(
                    model=model,
                    prompt=MINIMAL_TEXT_TO_IMAGE_PROMPT,
                    aspect_ratio=True,
                ),
                "input_image_url": None,
            },
            {
                "name": "Case G: minimal image edit JPEG",
                "payload": build_payload(
                    model=model,
                    prompt=MINIMAL_IMAGE_EDIT_PROMPT,
                    input_image_url=jpg_url,
                ),
                "input_image_url": jpg_url,
            },
        ]
    )

    for case in cases:
        print_case_separator(case["name"])
        if case["name"].startswith("Case B") and not case["input_image_url"]:
            print("SKIPPED: no inputImage URL. Set FLUXAPI_INPUT_IMAGE_URL or pass URL argument.")
            continue
        if case["name"].startswith(("Case C", "Case D")) and not case["input_image_url"]:
            print("SKIPPED: Cloudinary upload did not produce an inputImage URL.")
            continue
        run_case(
            api_key=settings.fluxapi_api_key,
            payload=case["payload"],
            input_image_url=case["input_image_url"],
            timeout_seconds=int(settings.fluxapi_timeout_seconds),
            poll_interval_seconds=int(settings.fluxapi_poll_interval_seconds),
        )

    return 0


def print_case_separator(name: str) -> None:
    print()
    print("=" * 80)
    print(name)
    print("=" * 80)


def run_case(
    api_key: str,
    payload: dict,
    input_image_url: str | None,
    timeout_seconds: int,
    poll_interval_seconds: int,
) -> None:
    print(f"model: {payload.get('model')}")
    print(f"inputImage URL: {input_image_url or '(none)'}")
    print(f"inputImage content-type: {get_content_type(input_image_url) if input_image_url else '(none)'}")
    print(f"payload: {json.dumps(payload, ensure_ascii=False, indent=2)}")

    try:
        generate_response = requests.post(
            FLUXAPI_GENERATE_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json=payload,
            timeout=60,
        )
    except requests.RequestException as exc:
        print(f"generate request failed: {exc}")
        return

    print(f"generate HTTP status: {generate_response.status_code}")
    print(f"full generate response: {generate_response.text}")
    generate_json = parse_json(generate_response.text)
    task_id = extract_task_id(generate_json)
    if not task_id:
        print_result_fields(generate_json, final_poll_text=None)
        print("No taskId returned; skipping polling.")
        return

    final_poll_json, final_poll_text, final_poll_status = poll_task(
        api_key=api_key,
        task_id=task_id,
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
    )
    print(f"final polling HTTP status: {final_poll_status}")
    print(f"final polling response: {final_poll_text}")
    print_result_fields(final_poll_json, final_poll_text)


def build_payload(
    model: str,
    prompt: str,
    input_image_url: str | None = None,
    aspect_ratio: bool = False,
    enable_translation: bool = False,
) -> dict:
    payload = {
        "model": model,
        "prompt": prompt,
    }
    if input_image_url:
        payload["inputImage"] = input_image_url
    if aspect_ratio:
        payload["aspectRatio"] = "4:3"
    if enable_translation:
        payload["enableTranslation"] = True
    return payload


def poll_task(
    api_key: str,
    task_id: str,
    timeout_seconds: int,
    poll_interval_seconds: int,
) -> tuple[dict | None, str | None, int | None]:
    deadline = time.monotonic() + timeout_seconds
    last_json = None
    last_text = None
    last_status = None
    while time.monotonic() < deadline:
        try:
            response = requests.get(
                FLUXAPI_RECORD_INFO_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Accept": "application/json",
                },
                params={"taskId": task_id},
                timeout=60,
            )
        except requests.RequestException as exc:
            print(f"poll request failed: {exc}")
            time.sleep(poll_interval_seconds)
            continue

        last_status = response.status_code
        last_text = response.text
        last_json = parse_json(response.text)
        success_flag = extract_first(last_json, ("successFlag", "successflag", "success_flag"))
        status = extract_first(last_json, ("status",))
        if str(success_flag).strip() in {"1", "2", "3"}:
            return last_json, last_text, last_status
        if isinstance(status, str) and status.strip().lower() in {"success", "failed", "failure", "rejected", "reject", "error"}:
            return last_json, last_text, last_status
        time.sleep(poll_interval_seconds)

    print(f"Polling timed out after {timeout_seconds} seconds for taskId={task_id}.")
    return last_json, last_text, last_status


def get_content_type(url: str | None) -> str:
    if not url:
        return "(none)"
    try:
        response = requests.head(url, allow_redirects=True, timeout=30)
        content_type = response.headers.get("content-type")
        if content_type:
            return f"{response.status_code} {content_type}"
    except requests.RequestException as exc:
        head_error = str(exc)
    else:
        head_error = f"HEAD returned no content-type, status={response.status_code}"

    try:
        response = requests.get(url, stream=True, timeout=30)
        response.close()
        return f"{response.status_code} {response.headers.get('content-type', '(missing)')}"
    except requests.RequestException as exc:
        return f"content-type check failed: HEAD={head_error}; GET={exc}"


def parse_json(text: str | None) -> dict | None:
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def extract_task_id(payload: dict | None) -> str | None:
    value = extract_first(payload, ("taskId", "taskid", "task_id", "id"))
    return str(value) if value else None


def print_result_fields(payload: dict | None, final_poll_text: str | None) -> None:
    print(f"successFlag: {extract_first(payload, ('successFlag', 'successflag', 'success_flag'))}")
    print(f"errorCode: {extract_first(payload, ('errorCode', 'error_code'))}")
    print(f"errorMessage: {extract_first(payload, ('errorMessage', 'error_message'))}")
    print(f"resultImageUrl/result_urls: {extract_result_url(payload)}")
    if final_poll_text is None:
        print("final polling response: (none)")


def extract_first(payload: dict | None, keys: tuple[str, ...]):
    for container in payload_containers(payload):
        for key in keys:
            value = container.get(key)
            if value is not None:
                return value
    return None


def extract_result_url(payload: dict | None) -> str | None:
    for container in payload_containers(payload):
        for key in ("resultImageUrl", "result_image_url", "imageUrl", "image_url"):
            value = container.get(key)
            if value:
                return str(value)
        for key in ("result_urls", "resultUrls", "result_url"):
            value = container.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, list) and value:
                first_value = value[0]
                if isinstance(first_value, str) and first_value.strip():
                    return first_value.strip()
    return None


def payload_containers(payload: dict | None) -> list[dict]:
    if not isinstance(payload, dict):
        return []
    containers = [payload]
    data = payload.get("data")
    if isinstance(data, dict):
        containers.append(data)
        response = data.get("response")
        if isinstance(response, dict):
            containers.append(response)
        info = data.get("info")
        if isinstance(info, dict):
            containers.append(info)
    response = payload.get("response")
    if isinstance(response, dict):
        containers.append(response)
    info = payload.get("info")
    if isinstance(info, dict):
        containers.append(info)
    return containers


if __name__ == "__main__":
    raise SystemExit(main())
