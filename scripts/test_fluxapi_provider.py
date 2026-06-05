from __future__ import annotations

import argparse
import logging
import socket
import time
import sys
from pathlib import Path
import os

import requests

from fastapi import HTTPException


DEFAULT_PROMPT = (
    "Create a clean Japanese watercolor real-estate illustration from this floorplan. "
    "Preserve the layout, walls, doors, windows, and balcony."
)
FLUXAPI_GENERATE_URL = "https://api.fluxapi.ai/api/v1/flux/kontext/generate"
FLUXAPI_RECORD_INFO_URL = "https://api.fluxapi.ai/api/v1/flux/kontext/record-info"


def _add_repo_root_to_path() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    repo_root_str = str(repo_root)
    if repo_root_str not in sys.path:
        sys.path.insert(0, repo_root_str)


def main() -> int:
    _add_repo_root_to_path()

    from app.services.image_provider import get_image_provider

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    parser = argparse.ArgumentParser(description="Test the FluxAPI image provider.")
    parser.add_argument("floorplan_path", help="Path to the floorplan image.")
    parser.add_argument("prompt", nargs="?", default=DEFAULT_PROMPT, help="Optional image prompt.")
    args = parser.parse_args()

    try:
        print(f"socket.getaddrinfo('api.fluxapi.ai', 443) = {socket.getaddrinfo('api.fluxapi.ai', 443)}")
    except Exception as exc:
        print(f"DNS diagnostic failed: {exc}")
    print(f"requests.__version__ = {requests.__version__}")
    print(f"IMAGE_PROVIDER = {os.getenv('IMAGE_PROVIDER', '')}")
    print(f"FLUXAPI_MODEL = {os.getenv('FLUXAPI_MODEL', '')}")
    print(f"FLUXAPI_API_KEY set = {bool(os.getenv('FLUXAPI_API_KEY'))}")
    print(f"FLUXAPI_INPUT_IMAGE_URL set = {bool(os.getenv('FLUXAPI_INPUT_IMAGE_URL'))}")

    floorplan_path = Path(args.floorplan_path)
    if not floorplan_path.exists():
        print(f"Error: floorplan path does not exist: {floorplan_path}", file=sys.stderr)
        return 1

    output_path = Path("outputs") / "test_image_provider_output.png"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        if os.getenv("FLUXAPI_INPUT_IMAGE_URL"):
            provider = get_image_provider()
            result_path = provider.generate(
                args.prompt,
                floorplan_path,
                output_path,
                input_image_url=os.getenv("FLUXAPI_INPUT_IMAGE_URL"),
            )
        else:
            print("Running FluxAPI text-to-image test because FLUXAPI_INPUT_IMAGE_URL is not set.")
            result_path = _run_fluxapi_text_to_image_test(args.prompt, output_path)
    except HTTPException as exc:
        print(f"Error: provider failed ({exc.status_code}): {exc.detail}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error: unexpected failure during image generation: {exc}", file=sys.stderr)
        return 1

    print(str(result_path))
    return 0


def _run_fluxapi_text_to_image_test(prompt: str, output_path: Path) -> Path:
    api_key = os.getenv("FLUXAPI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="FluxAPI image generation is enabled but FLUXAPI_API_KEY is missing.")

    model = os.getenv("FLUXAPI_MODEL", "flux-kontext-pro")
    payload = {
        "prompt": prompt,
        "model": model,
        "aspectRatio": "4:3",
    }
    response = requests.post(
        url=FLUXAPI_GENERATE_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=60,
    )
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"FluxAPI generate request failed: {response.text}")

    response_json = response.json()
    data_payload = response_json.get("data", {}) if isinstance(response_json, dict) else {}
    task_id = None
    if isinstance(data_payload, dict):
        task_id = data_payload.get("taskId") or data_payload.get("taskid") or data_payload.get("task_id") or data_payload.get("id")
    if not task_id:
        raise HTTPException(status_code=502, detail=f"FluxAPI generate response did not include taskId: {response.text}")

    deadline = time.monotonic() + 600
    while True:
        if time.monotonic() >= deadline:
            raise HTTPException(status_code=504, detail=f"FluxAPI image generation timed out after 600 seconds for taskId={task_id}")

        poll = requests.get(
            url=FLUXAPI_RECORD_INFO_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
            },
            params={"taskId": task_id},
            timeout=60,
        )
        if poll.status_code >= 400:
            raise HTTPException(status_code=502, detail=f"FluxAPI record-info request failed: {poll.text}")

        result_json = poll.json()
        success_flag = (
            result_json.get("successFlag")
            or result_json.get("successflag")
            or result_json.get("success_flag")
        )
        image_url = (
            result_json.get("resultImageUrl")
            or result_json.get("result_image_url")
            or result_json.get("imageUrl")
            or result_json.get("image_url")
        )
        if success_flag == 1:
            if not image_url:
                raise HTTPException(status_code=502, detail="FluxAPI returned success but result image URL is missing.")
            image_response = requests.get(image_url, timeout=60)
            if image_response.status_code >= 400:
                raise HTTPException(status_code=502, detail=f"FluxAPI image download failed: {image_response.text}")
            output_path.write_bytes(image_response.content)
            return output_path
        if success_flag == 2:
            raise HTTPException(status_code=502, detail=f"FluxAPI image generation failed: {result_json}")
        if success_flag == 3:
            raise HTTPException(status_code=502, detail=f"FluxAPI image generation was rejected: {result_json}")

        time.sleep(5)


if __name__ == "__main__":
    raise SystemExit(main())
