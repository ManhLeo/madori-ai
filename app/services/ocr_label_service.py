from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image

from app.config import get_settings


logger = logging.getLogger(__name__)


class OCRLabelService:
    def extract_text_boxes(self, image_path: Path) -> dict:
        settings = get_settings()
        provider = settings.label_ocr_provider.strip().lower()
        if not settings.label_ocr_enabled:
            return self._empty_result(provider, image_path, "LABEL_OCR_ENABLED is false.")
        if provider == "none":
            return self._empty_result(provider, image_path, "LABEL_OCR_PROVIDER is none.")
        if provider == "google_vision":
            return self._extract_google_vision(image_path)
        if provider == "gemini":
            return self._empty_result(provider, image_path, "Gemini OCR provider is not implemented in Phase 2.5.")
        return self._empty_result(provider, image_path, f"Unsupported LABEL_OCR_PROVIDER: {provider}.")

    def _extract_google_vision(self, image_path: Path) -> dict:
        image_width, image_height = self._image_size(image_path)
        diagnostics = self._google_vision_diagnostics(initialization_mode=None)
        credential_warnings = self._credential_configuration_warnings()
        try:
            from google.cloud import vision
            from google.oauth2 import service_account
        except ImportError:
            return {
                "provider": "google_vision",
                "image_width": image_width,
                "image_height": image_height,
                "texts": [],
                "warnings": [*credential_warnings, "google-cloud-vision is not installed."],
                "diagnostics": diagnostics,
            }

        settings = get_settings()
        language_hints = [hint.strip() for hint in settings.label_ocr_language_hints.split(",") if hint.strip()]
        warnings = []
        try:
            client, initialization_mode, init_warnings = self._build_google_vision_client(vision, service_account)
            diagnostics = self._google_vision_diagnostics(initialization_mode=initialization_mode)
            warnings.extend(init_warnings)
            image = vision.Image(content=image_path.read_bytes())
            image_context = vision.ImageContext(language_hints=language_hints) if language_hints else None
            response = client.document_text_detection(image=image, image_context=image_context)
        except Exception as exc:
            return {
                "provider": "google_vision",
                "image_width": image_width,
                "image_height": image_height,
                "texts": [],
                "warnings": [*warnings, f"Google Vision OCR failed: {exc}"],
                "diagnostics": diagnostics,
            }

        if response.error.message:
            warnings.append(response.error.message)

        texts = []
        for page in response.full_text_annotation.pages:
            for block in page.blocks:
                for paragraph in block.paragraphs:
                    for word in paragraph.words:
                        text = "".join(symbol.text for symbol in word.symbols).strip()
                        if not text:
                            continue
                        vertices = word.bounding_box.vertices
                        xs = [vertex.x for vertex in vertices]
                        ys = [vertex.y for vertex in vertices]
                        confidence = float(getattr(word, "confidence", 0.0) or getattr(block, "confidence", 0.0) or 0.0)
                        texts.append(
                            {
                                "id": f"ocr_text_{len(texts) + 1}",
                                "text": text,
                                "bbox": [min(xs), min(ys), max(xs), max(ys)],
                                "confidence": round(confidence, 3),
                                "locale": self._word_locale(word),
                            }
                        )

        return {
            "provider": "google_vision",
            "image_width": image_width,
            "image_height": image_height,
            "texts": texts,
            "warnings": warnings,
            "diagnostics": diagnostics,
        }

    def _build_google_vision_client(self, vision, service_account):
        settings = get_settings()
        credential_path = getattr(settings, "GOOGLE_APPLICATION_CREDENTIALS", None)
        warnings = []
        if credential_path:
            path = Path(credential_path)
            if path.exists():
                credentials = service_account.Credentials.from_service_account_file(str(path))
                warnings.append("Google Vision client initialized with explicit service account credentials")
                logger.info("Google Vision OCR initialized with explicit service account credentials")
                return vision.ImageAnnotatorClient(credentials=credentials), "explicit_service_account", warnings

            warnings.append("GOOGLE_APPLICATION_CREDENTIALS path does not exist")
            logger.warning("Google Vision OCR credential path does not exist; falling back to ADC")
        else:
            warnings.append("GOOGLE_APPLICATION_CREDENTIALS is not set; falling back to ADC")
            logger.info("Google Vision OCR GOOGLE_APPLICATION_CREDENTIALS is not set; falling back to ADC")

        warnings.append("Google Vision client initialized with ADC")
        logger.info("Google Vision OCR initialized with ADC")
        return vision.ImageAnnotatorClient(), "adc", warnings

    @staticmethod
    def _credential_configuration_warnings() -> list[str]:
        settings = get_settings()
        credential_path = getattr(settings, "GOOGLE_APPLICATION_CREDENTIALS", None)
        if credential_path:
            try:
                if not Path(credential_path).exists():
                    return ["GOOGLE_APPLICATION_CREDENTIALS path does not exist"]
            except OSError:
                return ["GOOGLE_APPLICATION_CREDENTIALS path does not exist"]
            return []
        return ["GOOGLE_APPLICATION_CREDENTIALS is not set; falling back to ADC"]

    @staticmethod
    def _google_vision_diagnostics(initialization_mode: str | None) -> dict:
        settings = get_settings()
        provider = settings.label_ocr_provider.strip().lower()
        credential_path = getattr(settings, "GOOGLE_APPLICATION_CREDENTIALS", None)
        credentials_file_exists = False
        if credential_path:
            try:
                credentials_file_exists = Path(credential_path).exists()
            except OSError:
                credentials_file_exists = False
        return {
            "ocr_provider": provider,
            "ocr_enabled": bool(settings.label_ocr_enabled),
            "explicit_credentials_path_configured": bool(credential_path),
            "credentials_file_exists": credentials_file_exists,
            "initialization_mode": initialization_mode,
        }

    @staticmethod
    def _word_locale(word) -> str | None:
        for symbol in word.symbols:
            for detected_language in symbol.property.detected_languages:
                if detected_language.language_code:
                    return detected_language.language_code
        return None

    def _empty_result(self, provider: str, image_path: Path, warning: str) -> dict:
        image_width, image_height = self._image_size(image_path)
        return {
            "provider": provider,
            "image_width": image_width,
            "image_height": image_height,
            "texts": [],
            "warnings": [warning],
            "diagnostics": {
                "ocr_provider": provider,
                "ocr_enabled": bool(get_settings().label_ocr_enabled),
                "explicit_credentials_path_configured": bool(getattr(get_settings(), "GOOGLE_APPLICATION_CREDENTIALS", None)),
                "credentials_file_exists": False,
                "initialization_mode": None,
            },
        }

    @staticmethod
    def _image_size(image_path: Path) -> tuple[int | None, int | None]:
        try:
            with Image.open(image_path) as image:
                return image.size
        except OSError:
            return None, None
