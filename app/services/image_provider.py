from __future__ import annotations

import base64
import json
import logging
import os
import shutil
import socket
import time
import urllib.request
from pathlib import Path

from fastapi import HTTPException
from openai import OpenAI
from PIL import Image, ImageDraw, ImageFont
import requests

from app.config import get_settings


logger = logging.getLogger(__name__)

FLUXAPI_GENERATE_URL = "https://api.fluxapi.ai/api/v1/flux/kontext/generate"
FLUXAPI_RECORD_INFO_URL = "https://api.fluxapi.ai/api/v1/flux/kontext/record-info"


class ImageProvider:
    def generate(
        self,
        prompt: str,
        floorplan_path: Path,
        output_path: Path,
        input_image_url: str | None = None,
    ) -> Path:
        raise NotImplementedError


class StubImageProvider(ImageProvider):
    def generate(
        self,
        prompt: str,
        floorplan_path: Path,
        output_path: Path,
        input_image_url: str | None = None,
    ) -> Path:
        if not floorplan_path.exists():
            raise HTTPException(status_code=404, detail="floorplan image not found")

        output_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            with Image.open(floorplan_path) as source_image:
                image = source_image.convert("RGBA")
                draw = ImageDraw.Draw(image)
                font = ImageFont.load_default()
                watermark = "STUB PREVIEW - FluxAPI disabled"
                text_bbox = draw.textbbox((0, 0), watermark, font=font)
                text_width = text_bbox[2] - text_bbox[0]
                text_height = text_bbox[3] - text_bbox[1]
                padding = 8
                margin = 12
                left = max(margin, image.width - text_width - padding * 2 - margin)
                top = max(margin, image.height - text_height - padding * 2 - margin)
                box = (left, top, left + text_width + padding * 2, top + text_height + padding * 2)
                draw.rounded_rectangle(box, radius=6, fill=(255, 255, 255, 210), outline=(120, 90, 60, 230), width=1)
                draw.text((left + padding, top + padding), watermark, fill=(90, 60, 40, 255), font=font)
                image.convert("RGB").save(output_path, format="PNG")
        except OSError:
            shutil.copyfile(floorplan_path, output_path)
        except Exception as exc:
            raise HTTPException(status_code=500, detail="failed to save stub preview image") from exc

        if not output_path.exists():
            raise HTTPException(status_code=500, detail="failed to save stub preview image")

        try:
            # Ensure callers always receive output.png even when the source is another format.
            with Image.open(output_path) as saved_image:
                saved_image.convert("RGB").save(output_path, format="PNG")
        except OSError as exc:
            raise HTTPException(status_code=500, detail="failed to normalize stub preview image") from exc

        return output_path


class OpenAIImageProvider(ImageProvider):
    def generate(
        self,
        prompt: str,
        floorplan_path: Path,
        output_path: Path,
        input_image_url: str | None = None,
    ) -> Path:
        settings = get_settings()
        if not settings.openai_api_key:
            raise HTTPException(
                status_code=500,
                detail="OpenAI image generation is enabled but OPENAI_API_KEY is missing.",
            )

        if not floorplan_path.exists():
            raise HTTPException(status_code=404, detail="floorplan image not found")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        client = OpenAI(api_key=settings.openai_api_key)

        try:
            with floorplan_path.open("rb") as image_file:
                response = client.images.edit(
                    model=settings.openai_image_model,
                    image=image_file,
                    prompt=prompt,
                    size="auto",
                    quality="auto",
                )
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"OpenAI image generation failed: {exc}") from exc

        image_data = getattr(response, "data", None) or []
        if not image_data:
            raise HTTPException(status_code=502, detail="OpenAI returned no generated image data.")

        b64_json = getattr(image_data[0], "b64_json", None)
        if not b64_json:
            raise HTTPException(status_code=502, detail="OpenAI returned an empty image payload.")

        try:
            output_path.write_bytes(base64.b64decode(b64_json))
        except Exception as exc:
            raise HTTPException(status_code=500, detail="failed to save OpenAI generated image") from exc

        return output_path


class FluxImageProvider(ImageProvider):
    def generate(
        self,
        prompt: str,
        floorplan_path: Path,
        output_path: Path,
        input_image_url: str | None = None,
    ) -> Path:
        settings = get_settings()
        if not settings.fal_api_key:
            raise HTTPException(
                status_code=500,
                detail="Flux image generation is enabled but FAL_API_KEY is missing.",
            )

        if not floorplan_path.exists():
            raise HTTPException(status_code=404, detail="floorplan image not found")

        output_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            os.environ.setdefault("FAL_KEY", settings.fal_api_key)
            import fal_client
        except Exception as exc:  # pragma: no cover - import depends on installed package
            raise HTTPException(status_code=500, detail=f"fal-client is not available: {exc}") from exc

        try:
            image_url = fal_client.upload_file(floorplan_path)
            result = fal_client.subscribe(
                settings.flux_model,
                arguments={
                    "prompt": prompt,
                    "image_url": image_url,
                },
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Flux image generation failed: {exc}") from exc

        try:
            image_items = None
            if isinstance(result, dict):
                image_items = result.get("images")
            else:
                image_items = getattr(result, "images", None)
            if not image_items:
                raise ValueError("no generated image returned")
            first_image = image_items[0]
            generated_url = first_image["url"] if isinstance(first_image, dict) else getattr(first_image, "url", None)
            if not generated_url:
                raise ValueError("generated image URL missing")
            with urllib.request.urlopen(generated_url) as response:
                output_path.write_bytes(response.read())
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Flux image download failed: {exc}") from exc

        return output_path


class FluxAPIImageProvider(ImageProvider):
    def generate(
        self,
        prompt: str,
        floorplan_path: Path,
        output_path: Path,
        input_image_url: str | None = None,
    ) -> Path:
        settings = get_settings()
        if not settings.fluxapi_api_key:
            raise HTTPException(
                status_code=500,
                detail="FluxAPI image generation is enabled but FLUXAPI_API_KEY is missing.",
            )

        if not floorplan_path.exists():
            raise HTTPException(status_code=404, detail="floorplan image not found")

        output_path.parent.mkdir(parents=True, exist_ok=True)

        if not input_image_url:
            raise HTTPException(
                status_code=500,
                detail=(
                    "FluxAPI image editing requires a public input image URL. "
                    "The generation pipeline should upload the original floorplan to public storage first."
                ),
            )

        logger.info("fluxapi inputImage=%s", input_image_url)
        task_id = self._start_generation(
            settings.fluxapi_api_key,
            settings.fluxapi_model,
            prompt,
            input_image_url,
            enable_translation=settings.fluxapi_enable_translation,
        )
        result = self._poll_for_result(settings.fluxapi_api_key, task_id)
        result_image_url = self._extract_fluxapi_result_url(result)
        if not result_image_url:
            raise HTTPException(status_code=502, detail="FluxAPI returned success but resultImageUrl is missing.")

        self._download_result_image(result_image_url, output_path)

        return output_path

    def _start_generation(
        self,
        api_key: str,
        model: str,
        prompt: str,
        image_url: str,
        enable_translation: bool = False,
    ) -> str:
        payload = {
            "prompt": prompt,
            "inputImage": image_url,
            "model": model,
            "aspectRatio": "4:3",
        }
        if enable_translation:
            payload["enableTranslation"] = True
        request_url = FLUXAPI_GENERATE_URL
        payload_keys = tuple(payload.keys())
        self._log_fluxapi_dns_diagnostic(request_url, payload_keys)
        logger.info("fluxapi generate request endpoint=%s repr=%r payload_keys=%s", request_url, request_url, list(payload_keys))
        try:
            response = requests.post(
                url=request_url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                json=payload,
                timeout=60,
            )
            logger.info("fluxapi generate response status=%s body=%s", response.status_code, response.text)
            response.raise_for_status()
        except requests.HTTPError as exc:
            detail = self._build_fluxapi_error_detail(
                "FluxAPI generate request failed",
                self._parse_response_json(exc.response),
                http_status=exc.response.status_code if exc.response is not None else None,
                response_text=exc.response.text if exc.response is not None else str(exc),
            )
            raise HTTPException(status_code=502, detail=detail) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"FluxAPI generate request failed: {exc}") from exc

        try:
            payload_data = response.json()
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"FluxAPI returned invalid JSON from generate request: {exc}") from exc

        logger.info("fluxapi generate response_json=%s", payload_data)
        logger.info("fluxapi generate response_json.keys()=%s", list(payload_data.keys()) if isinstance(payload_data, dict) else [])
        data_payload = payload_data.get("data", {}) if isinstance(payload_data, dict) else {}
        logger.info("fluxapi generate response_json.get('data')=%s", data_payload)
        logger.info(
            "fluxapi generate response_json.get('data', {}).keys()=%s",
            list(data_payload.keys()) if isinstance(data_payload, dict) else [],
        )

        success_flag = self._extract_from_fluxapi_payload(
            payload_data,
            ("successFlag", "successflag", "success_flag"),
        )
        status = self._extract_from_fluxapi_payload(payload_data, ("status",))
        if self._is_fluxapi_failure_flag(success_flag) or self._is_fluxapi_rejected_flag(success_flag):
            prefix = (
                "FluxAPI image generation was rejected"
                if self._is_fluxapi_rejected_flag(success_flag)
                else "FluxAPI image generation failed"
            )
            raise HTTPException(
                status_code=502,
                detail=self._build_fluxapi_error_detail(
                    prefix,
                    payload_data,
                    http_status=response.status_code,
                    response_text=response.text,
                ),
            )
        if isinstance(status, str) and status.strip().lower() in {"failed", "failure", "rejected", "reject", "error"}:
            prefix = "FluxAPI image generation was rejected" if "reject" in status.lower() else "FluxAPI image generation failed"
            raise HTTPException(
                status_code=502,
                detail=self._build_fluxapi_error_detail(
                    prefix,
                    payload_data,
                    http_status=response.status_code,
                    response_text=response.text,
                ),
            )

        task_id = None
        if isinstance(data_payload, dict):
            task_id = (
                data_payload.get("taskId")
                or data_payload.get("taskid")
                or data_payload.get("task_id")
                or data_payload.get("id")
            )

        logger.info("fluxapi parsed task_id=%r", task_id)
        if not task_id:
            payload_keys = sorted(payload_data.keys()) if isinstance(payload_data, dict) else []
            response_text = response.text
            raise HTTPException(
                status_code=502,
                detail=(
                    "FluxAPI generate response did not include taskId. "
                    f"HTTP {response.status_code}. "
                    f"response={response_text}. "
                    f"payload_keys={payload_keys}"
                ),
            )
        return str(task_id)

    def _poll_for_result(self, api_key: str, task_id: str) -> dict:
        settings = get_settings()
        timeout_seconds = int(settings.fluxapi_timeout_seconds)
        poll_interval_seconds = int(settings.fluxapi_poll_interval_seconds)
        deadline = time.monotonic() + timeout_seconds
        logger.info("fluxapi polling taskId=%s", task_id)
        poll_count = 0
        while True:
            if time.monotonic() >= deadline:
                logger.info("fluxapi timeout taskId=%s", task_id)
                raise HTTPException(status_code=504, detail=f"FluxAPI image generation timed out after {timeout_seconds} seconds.")

            result = self._fetch_record_info(api_key, task_id)
            poll_count += 1
            if poll_count <= 5:
                logger.info("fluxapi poll #%s full response text=%s", poll_count, result.get("_response_text"))

            data_payload = result.get("data") if isinstance(result, dict) else {}
            if not isinstance(data_payload, dict):
                data_payload = {}
            response_payload = data_payload.get("response") if isinstance(data_payload, dict) else {}
            if not isinstance(response_payload, dict):
                response_payload = {}
            info_payload = data_payload.get("info") if isinstance(data_payload, dict) else {}
            if not isinstance(info_payload, dict):
                info_payload = {}

            success_flag = self._extract_first_present(
                data_payload,
                ("successFlag", "successflag", "success_flag"),
            )
            status = self._extract_first_present(
                data_payload,
                ("status",),
            )
            error_message = self._extract_first_present(
                data_payload,
                ("errorMessage", "error_message"),
            )
            error_code = self._extract_first_present(
                data_payload,
                ("errorCode", "error_code"),
            )
            result_image_url = self._extract_fluxapi_result_url(result)
            is_success = self._is_fluxapi_success_flag(success_flag) or (isinstance(status, str) and status.lower() == "success")
            logger.info(
                "fluxapi poll status=%s body=%s successFlag=%s resultImageUrl=%s",
                result.get("_http_status"),
                result.get("_response_text"),
                success_flag if success_flag is not None else status,
                result_image_url,
            )
            logger.info(
                "fluxapi poll successFlag=%s status=%s errorMessage=%s errorCode=%s",
                success_flag,
                status,
                error_message,
                error_code,
            )

            if is_success:
                return result
            if self._is_fluxapi_failure_flag(success_flag):
                logger.info("fluxapi poll failure full response text=%s", result.get("_response_text"))
                raise HTTPException(
                    status_code=502,
                    detail=self._build_fluxapi_error_detail(
                        "FluxAPI image generation failed",
                        result,
                        task_id=task_id,
                        http_status=result.get("_http_status"),
                        response_text=result.get("_response_text"),
                    ),
                )
            if self._is_fluxapi_rejected_flag(success_flag):
                logger.info("fluxapi poll rejection full response text=%s", result.get("_response_text"))
                raise HTTPException(
                    status_code=502,
                    detail=self._build_fluxapi_error_detail(
                        "FluxAPI image generation was rejected",
                        result,
                        task_id=task_id,
                        http_status=result.get("_http_status"),
                        response_text=result.get("_response_text"),
                    ),
                )
            if isinstance(status, str) and status.strip().lower() in {"failed", "failure", "rejected", "reject", "error"}:
                logger.info("fluxapi poll status failure full response text=%s", result.get("_response_text"))
                prefix = "FluxAPI image generation was rejected" if "reject" in status.lower() else "FluxAPI image generation failed"
                raise HTTPException(
                    status_code=502,
                    detail=self._build_fluxapi_error_detail(
                        prefix,
                        result,
                        task_id=task_id,
                        http_status=result.get("_http_status"),
                        response_text=result.get("_response_text"),
                    ),
                )

            time.sleep(poll_interval_seconds)

    def _fetch_record_info(self, api_key: str, task_id: str) -> dict:
        request_url = f"{FLUXAPI_RECORD_INFO_URL}?taskId={task_id}"
        payload_keys = ("taskId",)
        self._log_fluxapi_dns_diagnostic(request_url, payload_keys)
        logger.info("fluxapi record-info endpoint=%s repr=%r payload_keys=%s", request_url, request_url, list(payload_keys))
        try:
            response = requests.get(
                url=FLUXAPI_RECORD_INFO_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Accept": "application/json",
                },
                params={"taskId": task_id},
                timeout=60,
            )
            logger.info("fluxapi record-info response status=%s body=%s", response.status_code, response.text)
            response.raise_for_status()
        except requests.HTTPError as exc:
            detail = self._build_fluxapi_error_detail(
                "FluxAPI record-info request failed",
                self._parse_response_json(exc.response),
                task_id=task_id,
                http_status=exc.response.status_code if exc.response is not None else None,
                response_text=exc.response.text if exc.response is not None else str(exc),
            )
            raise HTTPException(status_code=502, detail=detail) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"FluxAPI record-info request failed: {exc}") from exc

        try:
            payload = response.json()
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"FluxAPI returned invalid JSON from record-info: {exc}") from exc

        if not isinstance(payload, dict):
            raise HTTPException(status_code=502, detail="FluxAPI record-info response was not a JSON object.")
        logger.info("fluxapi record-info response_json=%s", payload)
        logger.info("fluxapi record-info response_json.keys()=%s", list(payload.keys()))
        payload["_http_status"] = response.status_code
        payload["_response_text"] = response.text
        return payload

    @staticmethod
    def _extract_first_present(payload: dict, keys: tuple[str, ...]) -> str | int | None:
        for key in keys:
            value = payload.get(key)
            if value is not None:
                return value
        return None

    @classmethod
    def _extract_from_fluxapi_payload(cls, payload: dict, keys: tuple[str, ...]) -> str | int | None:
        for container in cls._fluxapi_payload_containers(payload):
            value = cls._extract_first_present(container, keys)
            if value is not None:
                return value
        return None

    @staticmethod
    def _parse_response_json(response: requests.Response | None) -> dict | None:
        if response is None:
            return None
        try:
            parsed = response.json()
        except Exception:
            return None
        return parsed if isinstance(parsed, dict) else None

    @staticmethod
    def _is_fluxapi_success_flag(success_flag: object) -> bool:
        return str(success_flag).strip() == "1"

    @staticmethod
    def _is_fluxapi_failure_flag(success_flag: object) -> bool:
        return str(success_flag).strip() == "2"

    @staticmethod
    def _is_fluxapi_rejected_flag(success_flag: object) -> bool:
        return str(success_flag).strip() == "3"

    @staticmethod
    def _fluxapi_payload_containers(payload: dict | None) -> list[dict]:
        if not isinstance(payload, dict):
            return []

        containers: list[dict] = [payload]
        data_payload = payload.get("data")
        if isinstance(data_payload, dict):
            containers.append(data_payload)

            response_payload = data_payload.get("response")
            if isinstance(response_payload, dict):
                containers.append(response_payload)

            info_payload = data_payload.get("info")
            if isinstance(info_payload, dict):
                containers.append(info_payload)

        response_payload = payload.get("response")
        if isinstance(response_payload, dict):
            containers.append(response_payload)

        info_payload = payload.get("info")
        if isinstance(info_payload, dict):
            containers.append(info_payload)

        return containers

    @classmethod
    def _extract_fluxapi_result_url(cls, payload: dict) -> str | None:
        if not isinstance(payload, dict):
            return None

        containers = cls._fluxapi_payload_containers(payload)

        direct_keys = ("resultImageUrl", "result_image_url", "imageUrl", "image_url")
        list_keys = ("result_urls", "resultUrls", "result_url")

        for container in containers:
            direct_url = cls._extract_first_present(container, direct_keys)
            if direct_url:
                return str(direct_url)

            for list_key in list_keys:
                value = container.get(list_key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
                if isinstance(value, list) and value:
                    first_value = value[0]
                    if isinstance(first_value, str) and first_value.strip():
                        return first_value.strip()

        return None

    @classmethod
    def _build_fluxapi_error_detail(
        cls,
        prefix: str,
        payload: dict | None,
        task_id: str | None = None,
        http_status: int | None = None,
        response_text: str | None = None,
    ) -> str:
        parsed_task_id = task_id or cls._extract_from_fluxapi_payload(
            payload or {},
            ("taskId", "taskid", "task_id", "id"),
        )
        fields = {
            "http_status": http_status,
            "taskId": parsed_task_id,
            "errorMessage": cls._extract_from_fluxapi_payload(payload or {}, ("errorMessage", "error_message")),
            "errorCode": cls._extract_from_fluxapi_payload(payload or {}, ("errorCode", "error_code")),
            "msg": cls._extract_from_fluxapi_payload(payload or {}, ("msg",)),
            "code": cls._extract_from_fluxapi_payload(payload or {}, ("code",)),
            "status": cls._extract_from_fluxapi_payload(payload or {}, ("status",)),
            "resultImageUrl": cls._extract_fluxapi_result_url(payload or {}),
        }
        parts = [prefix]
        for key, value in fields.items():
            if value is not None:
                parts.append(f"{key}={value}")

        if response_text:
            parts.append(f"response_text={response_text}")
        if payload is not None:
            try:
                parsed_json = json.dumps(payload, ensure_ascii=False)
            except TypeError:
                parsed_json = str(payload)
            parts.append(f"parsed_json={parsed_json}")

        return "; ".join(parts)

    def _download_result_image(self, result_image_url: str, output_path: Path) -> None:
        last_error: Exception | None = None
        for attempt in range(1, 6):
            logger.info("fluxapi downloading result image url=%s attempt=%s", result_image_url, attempt)
            try:
                with requests.get(result_image_url, stream=True, timeout=60) as response:
                    response.raise_for_status()
                    with output_path.open("wb") as output_file:
                        for chunk in response.iter_content(chunk_size=8192):
                            if chunk:
                                output_file.write(chunk)
                return
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout, requests.exceptions.RequestException, OSError) as exc:
                last_error = exc
                if attempt < 5:
                    time.sleep(5)
                    continue
                break

        raise HTTPException(
            status_code=502,
            detail=f"FluxAPI image download failed for result image URL {result_image_url}: {last_error}",
        )

    def _log_fluxapi_dns_diagnostic(self, endpoint_url: str, payload_keys: tuple[str, ...]) -> None:
        dns_error: Exception | None = None
        for attempt in range(1, 4):
            logger.info(
                "fluxapi dns diagnostic attempt=%s endpoint=%s payload_keys=%s",
                attempt,
                endpoint_url,
                list(payload_keys),
            )
            try:
                result = socket.getaddrinfo("api.fluxapi.ai", 443)
                logger.info("fluxapi dns diagnostic result=%r", result)
                return
            except (socket.gaierror, OSError) as exc:
                dns_error = exc
                logger.info("fluxapi dns diagnostic failed attempt=%s error=%s", attempt, exc)
                if attempt < 3:
                    time.sleep(5)

        raise HTTPException(
            status_code=502,
            detail=f"FluxAPI DNS resolution failed for {endpoint_url}: {dns_error}",
        )

def get_image_provider() -> ImageProvider:
    settings = get_settings()
    provider_name = settings.image_provider.strip().lower()
    if provider_name == "stub":
        return StubImageProvider()
    if provider_name == "openai":
        return OpenAIImageProvider()
    if provider_name == "flux":
        return FluxImageProvider()
    if provider_name == "fluxapi":
        return FluxAPIImageProvider()
    raise HTTPException(
        status_code=500,
        detail=f"Unsupported image provider: {settings.image_provider}. Expected 'stub', 'openai', 'flux', or 'fluxapi'.",
    )
