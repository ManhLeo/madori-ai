from __future__ import annotations

import base64
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from fastapi import HTTPException

from app.config import get_settings
from app.schemas import (
    BalconyInfo,
    BedSemanticRecord,
    DerivedInteriorStyleProfile,
    DoorInfo,
    FloorplanAnalysis,
    FloorplanDesignAnalysis,
    FurnitureItem,
    FurniturePlan,
    InteriorAnalysisSummary,
    InteriorObjectSemanticRecord,
    InteriorPhotoSemanticRecord,
    InteriorStyleAnalysisArtifact,
    InteriorStyleReferenceAnalysisGroups,
    RoomFurniturePlan,
    RoomInfo,
    SofaSemanticRecord,
    StyleReferenceSemanticRecord,
    WindowInfo,
)

logger = logging.getLogger(__name__)


class OpenAIJSONParseError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        response_text: str,
        likely_truncated: bool = False,
        attempts: list[dict] | None = None,
        raw_payload: dict | None = None,
        response_excerpt_limit: int = 500,
    ) -> None:
        super().__init__(message)
        cleaned = response_text or ""
        self.response_text = cleaned
        self.response_length = len(cleaned)
        self.first_500 = cleaned[:response_excerpt_limit]
        self.last_500 = cleaned[-response_excerpt_limit:] if cleaned else ""
        self.likely_truncated = likely_truncated
        self.attempts = attempts or []
        self.raw_payload = raw_payload or {}


def _coerce_float(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_bbox(value) -> list[float] | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            return None
    if isinstance(value, dict):
        value = [
            value.get("x_min", value.get("left")),
            value.get("y_min", value.get("top")),
            value.get("x_max", value.get("right")),
            value.get("y_max", value.get("bottom")),
        ]
    if not isinstance(value, (list, tuple)):
        return None
    coords: list[float] = []
    for coordinate in value[:4]:
        coerced = _coerce_float(coordinate)
        if coerced is None:
            return None
        coords.append(coerced)
    if len(coords) != 4:
        return None
    return {
        "x_min": coords[0],
        "y_min": coords[1],
        "x_max": coords[2],
        "y_max": coords[3],
    }


def _coerce_polygon(value) -> list[list[float]] | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            return None
    if not isinstance(value, (list, tuple)):
        return None
    polygon: list[list[float]] = []
    for point in value:
        if isinstance(point, dict):
            x_value = point.get("x")
            y_value = point.get("y")
            point = [x_value, y_value]
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            return None
        x_coord = _coerce_float(point[0])
        y_coord = _coerce_float(point[1])
        if x_coord is None or y_coord is None:
            return None
        polygon.append([x_coord, y_coord])
    return polygon or None


def _strip_json_fences(text: str) -> str:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").strip()
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
    return cleaned


def _extract_first_json_object(text: str) -> str | None:
    start = text.find("{")
    if start < 0:
        return None

    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
                continue
            if char == "\\":
                escaped = True
                continue
            if char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char == "{":
            depth += 1
            continue
        if char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return text[start:]


def _looks_likely_truncated_json(text: str, error_message: str | None = None) -> bool:
    cleaned = (text or "").rstrip()
    error_text = (error_message or "").lower()
    if not cleaned:
        return True
    if cleaned[-1] not in {"}", "]"}:
        return True
    if "unterminated string" in error_text:
        return True
    if "expecting value" in error_text and len(cleaned) < 200:
        return True
    if "expecting ',' delimiter" in error_text and len(cleaned) < 200:
        return True
    return False


def parse_openai_json_response(text: str) -> dict:
    cleaned = _strip_json_fences(text)
    candidate = _extract_first_json_object(cleaned) or cleaned
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise OpenAIJSONParseError(
            f"OpenAI returned invalid JSON: {exc}",
            response_text=cleaned,
            likely_truncated=_looks_likely_truncated_json(cleaned, str(exc)),
        ) from exc

    if not isinstance(parsed, dict):
        raise OpenAIJSONParseError(
            "OpenAI returned JSON that was not an object.",
            response_text=cleaned,
            likely_truncated=_looks_likely_truncated_json(cleaned),
        )
    return parsed


class VisionAnalyzer:
    NORMALIZED_ROOM_TYPES = {
        "living_room",
        "bedroom",
        "kitchen",
        "dining_kitchen",
        "bathroom",
        "toilet",
        "washroom",
        "closet",
        "walk_in_closet",
        "entrance",
        "balcony",
        "hallway",
        "storage",
        "unknown",
    }

    NORMALIZED_POSITIONS = {
        "top",
        "bottom",
        "left",
        "right",
        "center",
        "top_left",
        "top_right",
        "bottom_left",
        "bottom_right",
        "unknown",
    }

    NORMALIZED_INTERIOR_OBJECT_TYPES = {
        "bed",
        "sofa",
        "coffee_table",
        "dining_table",
        "chair",
        "tv",
        "tv_stand",
        "refrigerator",
        "wardrobe",
        "desk",
        "plant",
        "potted_plant",
        "curtain",
        "rug",
        "storage",
        "shelf",
        "lamp",
        "floor_lamp",
        "wall_art",
        "two_single_beds",
        "pillow",
        "blanket",
        "kitchen_counter",
        "sink",
        "stove",
        "cabinet",
        "bathtub",
        "shower",
        "towel",
        "toilet",
        "washbasin",
        "unknown",
    }

    NORMALIZED_COLORS = {
        "white",
        "beige",
        "gray",
        "light_brown",
        "dark_brown",
        "brown",
        "green",
        "blue",
        "pink",
        "yellow",
        "black",
        "wood",
        "unknown",
    }

    NORMALIZED_MATERIALS = {
        "wood",
        "fabric",
        "leather",
        "metal",
        "tile",
        "stone",
        "glass",
        "unknown",
    }

    FLOOR_COLOR_CATEGORIES = {
        "white",
        "light_brown",
        "dark_brown",
        "unknown",
    }

    def analyze_floorplan_stub(self, image_path: Path) -> FloorplanAnalysis:
        if not image_path.exists():
            raise HTTPException(status_code=404, detail="floorplan image not found")

        return FloorplanAnalysis(
            apartment_type="2LDK",
            layout_description=(
                "Compact Japanese apartment with a central entry hall, a living-dining-kitchen "
                "area on one side, two private rooms along the perimeter, and a balcony off the living room."
            ),
            rooms=[
                RoomInfo(
                    type="entrance",
                    position="bottom",
                    size="small",
                    connected_to=["hallway"],
                ),
                RoomInfo(
                    type="living_room",
                    position="left",
                    size="large",
                    connected_to=["hallway", "bedroom", "balcony"],
                ),
                RoomInfo(
                    type="bedroom",
                    position="top_left",
                    size="medium",
                    connected_to=["living_room"],
                ),
                RoomInfo(
                    type="bedroom",
                    position="top_right",
                    size="medium",
                    connected_to=["living_room"],
                ),
                RoomInfo(
                    type="hallway",
                    position="center",
                    size="narrow",
                    connected_to=["entrance", "living_room"],
                ),
            ],
            doors=[
                DoorInfo(position="bottom", connects=["entrance", "hallway"]),
                DoorInfo(position="left", connects=["hallway", "living_room"]),
                DoorInfo(position="top_left", connects=["living_room", "bedroom"]),
                DoorInfo(position="top_right", connects=["living_room", "bedroom"]),
            ],
            windows=[
                WindowInfo(position="top", room="bedroom"),
                WindowInfo(position="top", room="bedroom"),
                WindowInfo(position="right", room="living_room"),
            ],
            balcony=BalconyInfo(exists=True, position="right"),
            constraints=[
                "Main circulation is concentrated around a central hallway.",
                "Bedrooms appear separated from the main living space for privacy.",
                "Balcony access is likely from the primary living area.",
            ],
        )

    def analyze_interior_style_semantic_with_raw(
        self,
        interior_images: list[tuple[Path, str, dict]],
        style_reference_images: dict[str, list[tuple[Path, str, dict]]],
    ) -> tuple[InteriorStyleAnalysisArtifact, dict]:
        settings = get_settings()
        provider = self._vision_provider()
        if provider == "openai":
            return self._analyze_interior_style_openai_with_raw(interior_images, style_reference_images)
        if provider == "gemini":
            return self._analyze_interior_style_gemini_with_raw(interior_images, style_reference_images)
        if provider != "stub":
            raise HTTPException(status_code=500, detail=f"Unsupported VISION_PROVIDER: {provider}")

        return self._analyze_interior_style_stub_with_raw(interior_images, style_reference_images)

    def analyze_floorplan_semantic_with_raw(
        self,
        image_path: Path,
        *,
        run_id: str | None = None,
        artifacts_dir: Path | None = None,
    ) -> tuple[FloorplanAnalysis, dict]:
        settings = get_settings()
        provider = self._vision_provider()
        if provider == "openai":
            return self._analyze_floorplan_openai_semantic_with_raw(
                image_path,
                run_id=run_id,
                artifacts_dir=artifacts_dir,
            )
        if provider == "gemini":
            return self._analyze_floorplan_gemini_semantic_with_raw(image_path)
        if provider != "stub":
            raise HTTPException(status_code=500, detail=f"Unsupported VISION_PROVIDER: {provider}")

        analysis = self.analyze_floorplan_stub(image_path)
        return analysis, {
            "provider": "stub",
            "mode": "semantic_only",
            "analysis": analysis.model_dump(mode="json"),
        }

    def analyze_floorplan_design_with_raw(
        self, image_path: Path
    ) -> tuple[FloorplanAnalysis, FurniturePlan | None, dict]:
        settings = get_settings()
        provider = self._vision_provider()
        if provider == "openai":
            analysis, raw = self._analyze_floorplan_openai_semantic_with_raw(image_path)
            return analysis, None, raw
        if provider == "gemini":
            return self.analyze_floorplan_gemini(image_path)
        if provider != "stub":
            raise HTTPException(status_code=500, detail=f"Unsupported VISION_PROVIDER: {provider}")

        analysis = self.analyze_floorplan_stub(image_path)
        return analysis, None, {
            "provider": "stub",
            "analysis": analysis.model_dump(mode="json"),
        }

    def _analyze_interior_style_stub_with_raw(
        self,
        interior_images: list[tuple[Path, str, dict]],
        style_reference_images: dict[str, list[tuple[Path, str, dict]]],
    ) -> tuple[InteriorStyleAnalysisArtifact, dict]:
        interior_records = [
            self._build_stub_interior_record(path, source_metadata)
            for path, _, source_metadata in interior_images
        ]
        style_groups = InteriorStyleReferenceAnalysisGroups(
            ideal=[
                self._build_stub_style_reference_record(path, "ideal", source_metadata)
                for path, _, source_metadata in style_reference_images.get("ideal", [])
            ],
            acceptable=[
                self._build_stub_style_reference_record(path, "acceptable", source_metadata)
                for path, _, source_metadata in style_reference_images.get("acceptable", [])
            ],
            ng=[
                self._build_stub_style_reference_record(path, "ng", source_metadata)
                for path, _, source_metadata in style_reference_images.get("ng", [])
            ],
        )
        derived_profile = self._derive_interior_style_profile(interior_records, style_groups)
        summary = InteriorAnalysisSummary(
            provider="stub",
            model=None,
            interior_photo_count=len(interior_records),
            style_reference_count=sum(
                len(getattr(style_groups, group_name))
                for group_name in ("ideal", "acceptable", "ng")
            ),
            preferred_floor_color=derived_profile.preferred_floor_color,
            inferred_bed_type=derived_profile.inferred_bed_type,
            accent_colors=derived_profile.accent_colors,
            style_positive_cues=derived_profile.style_positive_cues,
            style_avoid_cues=derived_profile.style_avoid_cues,
        )
        artifact = InteriorStyleAnalysisArtifact(
            run_id="pending",
            generated_at=datetime.now(timezone.utc),
            provider="stub",
            model=None,
            interior_photos=interior_records,
            style_references=style_groups,
            derived_profile=derived_profile,
            summary=summary,
            warnings=[],
            errors=[],
        )
        raw_payload = {
            "provider": "stub",
            "mode": "semantic_only",
            "interior_photos": [record.model_dump(mode="json") for record in interior_records],
            "style_references": style_groups.model_dump(mode="json"),
            "derived_profile": derived_profile.model_dump(mode="json"),
        }
        return artifact, raw_payload

    def analyze_floorplan_with_raw(self, image_path: Path) -> tuple[FloorplanAnalysis, dict]:
        analysis, furniture_plan, raw_payload = self.analyze_floorplan_design_with_raw(image_path)
        if furniture_plan is not None and isinstance(raw_payload, dict):
            raw_payload = dict(raw_payload)
            raw_payload["furniture_plan"] = furniture_plan.model_dump(mode="json")
        return analysis, raw_payload

    def analyze_floorplan_raw(self, image_path: Path) -> FloorplanAnalysis:
        analysis, _ = self.analyze_floorplan_with_raw(image_path)
        return analysis

    def analyze_floorplan(self, image_path: Path) -> FloorplanAnalysis:
        raw_analysis, _ = self.analyze_floorplan_with_raw(image_path)
        return self.normalize_floorplan_analysis(raw_analysis)

    def analyze_floorplan_gemini(
        self, image_path: Path
    ) -> tuple[FloorplanAnalysis, FurniturePlan | None, dict]:
        analysis, furniture_plan, raw_payload = self._analyze_floorplan_gemini_with_raw(image_path)
        return analysis, furniture_plan, raw_payload

    def _vision_provider(self) -> str:
        settings = get_settings()
        provider = str(getattr(settings, "vision_provider", "") or "").strip().lower()
        if not provider:
            provider = "openai"
        if provider == "gemini" or (provider == "stub" and settings.use_gemini_analysis):
            raise HTTPException(
                status_code=400,
                detail="VISION_PROVIDER=gemini is deprecated in this project. Use VISION_PROVIDER=openai.",
            )
        return provider

    def _require_openai_analysis_client(self):
        settings = get_settings()
        if not settings.openai_api_key:
            raise HTTPException(
                status_code=500,
                detail="VISION_PROVIDER=openai requires OPENAI_API_KEY for semantic analysis.",
            )
        try:
            from openai import OpenAI
        except Exception as exc:  # pragma: no cover - depends on installed package
            raise HTTPException(status_code=500, detail=f"openai SDK is not available: {exc}") from exc
        return OpenAI(api_key=settings.openai_api_key, timeout=settings.openai_analysis_timeout_seconds)

    def _analyze_floorplan_openai_semantic_with_raw(
        self,
        image_path: Path,
        *,
        run_id: str | None = None,
        artifacts_dir: Path | None = None,
    ) -> tuple[FloorplanAnalysis, dict]:
        settings = get_settings()
        if not image_path.exists():
            raise HTTPException(status_code=404, detail="floorplan image not found")
        client = self._require_openai_analysis_client()
        started = time.monotonic()
        prompt = self._openai_floorplan_semantic_prompt()
        attempts: list[dict] = []
        response_text = ""
        parsed: dict | None = None
        parse_mode = "responses_output_text"
        retry_attempts = max(1, int(settings.openai_analysis_json_retry_attempts))

        for attempt in range(1, retry_attempts + 1):
            attempt_prompt = prompt if attempt == 1 else self._openai_floorplan_semantic_retry_prompt(attempt, attempts[-1] if attempts else None)
            response_text, parse_mode = self._generate_openai_json_text(
                client=client,
                model=settings.openai_analysis_model,
                prompt=attempt_prompt,
                image_paths=[image_path],
                failure_detail="OpenAI floorplan semantic analysis failed",
                response_text_format=self._openai_floorplan_semantic_response_format(),
            )
            try:
                parsed = parse_openai_json_response(response_text)
                normalized_payload = self._coerce_openai_floorplan_payload(parsed)
                analysis = FloorplanAnalysis.model_validate(self._coerce_floorplan_payload(normalized_payload))
            except OpenAIJSONParseError as exc:
                failed_path = self._write_failed_openai_response(
                    run_id=run_id,
                    artifacts_dir=artifacts_dir,
                    attempt=attempt,
                    response_text=exc.response_text,
                )
                attempts.append(
                    {
                        "attempt": attempt,
                        "parse_status": "failed",
                        "error": str(exc),
                        "response_length": exc.response_length,
                        "likely_truncated": exc.likely_truncated,
                        "raw_response_path": failed_path,
                    }
                )
                if attempt < retry_attempts:
                    continue
                failure_payload = {
                    "run_id": run_id,
                    "provider": "openai",
                    "model": settings.openai_analysis_model,
                    "mode": "semantic_only",
                    "analysis_type": "floorplan_semantic",
                    "geometry_precision": "approximate_semantic_only",
                    "parse_mode": parse_mode,
                    "attempts": attempts,
                    "duration_seconds": round(time.monotonic() - started, 3),
                    "warnings": [
                        "OpenAI floorplan semantic analysis returned invalid JSON after retry.",
                    ],
                    "errors": [
                        {
                            "error": "openai_invalid_json",
                            "message": "OpenAI floorplan semantic analysis returned invalid JSON after retry.",
                            "details": {
                                "attempts": len(attempts),
                                "likely_truncated": any(item.get("likely_truncated") for item in attempts),
                            },
                        }
                    ],
                }
                raise OpenAIJSONParseError(
                    "OpenAI floorplan semantic analysis returned invalid JSON after retry.",
                    response_text=exc.response_text,
                    likely_truncated=any(item.get("likely_truncated") for item in attempts),
                    attempts=attempts,
                    raw_payload=failure_payload,
                ) from exc
            except HTTPException:
                raise
            except Exception as exc:
                failed_path = self._write_failed_openai_response(
                    run_id=run_id,
                    artifacts_dir=artifacts_dir,
                    attempt=attempt,
                    response_text=response_text,
                )
                attempts.append(
                    {
                        "attempt": attempt,
                        "parse_status": "failed",
                        "error": str(exc),
                        "response_length": len(response_text or ""),
                        "likely_truncated": _looks_likely_truncated_json(response_text, str(exc)),
                        "raw_response_path": failed_path,
                    }
                )
                if attempt < retry_attempts:
                    continue
                failure_payload = {
                    "run_id": run_id,
                    "provider": "openai",
                    "model": settings.openai_analysis_model,
                    "mode": "semantic_only",
                    "analysis_type": "floorplan_semantic",
                    "geometry_precision": "approximate_semantic_only",
                    "parse_mode": parse_mode,
                    "attempts": attempts,
                    "duration_seconds": round(time.monotonic() - started, 3),
                    "warnings": [
                        "OpenAI floorplan semantic analysis returned invalid JSON after retry.",
                    ],
                    "errors": [
                        {
                            "error": "openai_invalid_json",
                            "message": "OpenAI floorplan semantic analysis returned invalid JSON after retry.",
                            "details": {
                                "attempts": len(attempts),
                                "likely_truncated": any(item.get("likely_truncated") for item in attempts),
                            },
                        }
                    ],
                }
                raise OpenAIJSONParseError(
                    "OpenAI floorplan semantic analysis returned invalid JSON after retry.",
                    response_text=response_text,
                    likely_truncated=any(item.get("likely_truncated") for item in attempts),
                    attempts=attempts,
                    raw_payload=failure_payload,
                ) from exc
            else:
                attempts.append(
                    {
                        "attempt": attempt,
                        "parse_status": "passed",
                        "response_length": len(response_text or ""),
                        "likely_truncated": False,
                    }
                )
                normalized = self.normalize_floorplan_analysis(analysis)
                raw_payload = {
                    "run_id": run_id,
                    "provider": "openai",
                    "model": settings.openai_analysis_model,
                    "mode": "semantic_only",
                    "analysis_type": "floorplan_semantic",
                    "geometry_precision": "approximate_semantic_only",
                    "parse_mode": parse_mode,
                    "attempts": attempts,
                    "response_text": response_text,
                    "parsed_response": parsed,
                    "analysis": normalized.model_dump(mode="json"),
                    "duration_seconds": round(time.monotonic() - started, 3),
                    "warnings": parsed.get("warnings", []) if isinstance(parsed, dict) else [],
                    "errors": parsed.get("errors", []) if isinstance(parsed, dict) else [],
                }
                return normalized, raw_payload

    def _analyze_interior_style_openai_with_raw(
        self,
        interior_images: list[tuple[Path, str, dict]],
        style_reference_images: dict[str, list[tuple[Path, str, dict]]],
    ) -> tuple[InteriorStyleAnalysisArtifact, dict]:
        settings = get_settings()
        client = self._require_openai_analysis_client()
        started = time.monotonic()
        interior_records: list[InteriorPhotoSemanticRecord] = []
        interior_raw_records: list[dict] = []
        for image_path, _, source_metadata in interior_images:
            if not image_path.exists():
                raise HTTPException(status_code=404, detail=f"interior image not found: {image_path.name}")
            response_text, parse_mode = self._generate_openai_json_text(
                client=client,
                model=settings.openai_analysis_model,
                prompt=self._openai_interior_semantic_prompt(source_metadata),
                image_paths=[image_path],
                failure_detail="OpenAI interior semantic analysis failed",
            )
            payload = self._load_json_payload(response_text, "OpenAI interior semantic analysis")
            photo_payload = self._coerce_openai_interior_photo_payload(payload, source_metadata)
            record = self._normalize_interior_photo_record(photo_payload, source_metadata)
            interior_records.append(record)
            interior_raw_records.append(
                {
                    "source_image": source_metadata,
                    "parse_mode": parse_mode,
                    "response_text": response_text,
                    "parsed_response": payload,
                    "analysis": record.model_dump(mode="json"),
                }
            )

        style_groups = InteriorStyleReferenceAnalysisGroups(
            ideal=[
                self._build_stub_style_reference_record(path, "ideal", source_metadata)
                for path, _, source_metadata in style_reference_images.get("ideal", [])
            ],
            acceptable=[
                self._build_stub_style_reference_record(path, "acceptable", source_metadata)
                for path, _, source_metadata in style_reference_images.get("acceptable", [])
            ],
            ng=[
                self._build_stub_style_reference_record(path, "ng", source_metadata)
                for path, _, source_metadata in style_reference_images.get("ng", [])
            ],
        )
        derived_profile = self._derive_interior_style_profile(interior_records, style_groups)
        summary = InteriorAnalysisSummary(
            provider="openai",
            model=settings.openai_analysis_model,
            interior_photo_count=len(interior_records),
            style_reference_count=sum(len(getattr(style_groups, group_name)) for group_name in ("ideal", "acceptable", "ng")),
            preferred_floor_color=derived_profile.preferred_floor_color,
            inferred_bed_type=derived_profile.inferred_bed_type,
            accent_colors=derived_profile.accent_colors,
            style_positive_cues=derived_profile.style_positive_cues,
            style_avoid_cues=derived_profile.style_avoid_cues,
        )
        artifact = InteriorStyleAnalysisArtifact(
            run_id="pending",
            generated_at=datetime.now(timezone.utc),
            provider="openai",
            model=settings.openai_analysis_model,
            interior_photos=interior_records,
            style_references=style_groups,
            derived_profile=derived_profile,
            summary=summary,
            warnings=[],
            errors=[],
        )
        raw_payload = {
            "provider": "openai",
            "model": settings.openai_analysis_model,
            "mode": "semantic_only",
            "analysis_type": "interior_semantic",
            "interior_photos": interior_raw_records,
            "style_references": style_groups.model_dump(mode="json"),
            "derived_profile": derived_profile.model_dump(mode="json"),
            "duration_seconds": round(time.monotonic() - started, 3),
            "warnings": [],
            "errors": [],
        }
        return artifact, raw_payload

    def _analyze_floorplan_gemini_with_raw(
        self, image_path: Path
    ) -> tuple[FloorplanAnalysis, FurniturePlan | None, dict]:
        settings = get_settings()
        if not settings.gemini_api_key:
            raise HTTPException(
                status_code=500,
                detail="Gemini floorplan analysis is enabled but GEMINI_API_KEY is missing.",
            )

        if not image_path.exists():
            raise HTTPException(status_code=404, detail="floorplan image not found")

        try:
            import google.genai as genai
            from google.genai import types
        except Exception as exc:  # pragma: no cover - import depends on installed package
            raise HTTPException(
                status_code=500,
                detail=f"google-genai is not available: {exc}",
            ) from exc

        client = genai.Client(api_key=settings.gemini_api_key)
        mime_type = self._mime_type_for_path(image_path)
        image_bytes = image_path.read_bytes()
        prompt = self._floorplan_design_prompt()
        schema = FloorplanDesignAnalysis.model_json_schema()

        response_text, parse_mode = self._generate_gemini_json_text(
            client=client,
            model=settings.gemini_model,
            prompt=prompt,
            image_bytes=image_bytes,
            mime_type=mime_type,
            schema=schema,
            failure_detail="Gemini floorplan analysis failed",
        )

        analysis, furniture_plan = self._parse_floorplan_design_json(response_text, provider="Gemini")
        raw_payload = {
            "provider": "gemini",
            "model": settings.gemini_model,
            "response_text": response_text,
            "parse_mode": parse_mode,
            "analysis": analysis.model_dump(mode="json"),
            "furniture_plan": furniture_plan.model_dump(mode="json") if furniture_plan else None,
        }
        return analysis, furniture_plan, raw_payload

    def _analyze_floorplan_gemini_semantic_with_raw(
        self,
        image_path: Path,
    ) -> tuple[FloorplanAnalysis, dict]:
        settings = get_settings()
        if not settings.gemini_api_key:
            raise HTTPException(
                status_code=500,
                detail="Gemini floorplan analysis is enabled but GEMINI_API_KEY is missing.",
            )

        if not image_path.exists():
            raise HTTPException(status_code=404, detail="floorplan image not found")

        try:
            import google.genai as genai
            from google.genai import types
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"google-genai is not available: {exc}",
            ) from exc

        client = genai.Client(api_key=settings.gemini_api_key)
        mime_type = self._mime_type_for_path(image_path)
        image_bytes = image_path.read_bytes()
        prompt = self._floorplan_semantic_prompt()
        schema = FloorplanAnalysis.model_json_schema()

        response_text, parse_mode = self._generate_gemini_json_text(
            client=client,
            model=settings.gemini_model,
            prompt=prompt,
            image_bytes=image_bytes,
            mime_type=mime_type,
            schema=schema,
            failure_detail="Gemini floorplan analysis failed",
        )

        try:
            analysis = FloorplanAnalysis.model_validate(self._coerce_floorplan_payload(json.loads(self._strip_json_fences(response_text))))
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Gemini returned invalid JSON for semantic floorplan analysis: {exc}",
            ) from exc

        normalized = self.normalize_floorplan_analysis(analysis)
        raw_payload = {
            "provider": "gemini",
            "model": settings.gemini_model,
            "mode": "semantic_only",
            "parse_mode": parse_mode,
            "response_text": response_text,
            "analysis": normalized.model_dump(mode="json"),
        }
        return normalized, raw_payload

    def _analyze_interior_style_gemini_with_raw(
        self,
        interior_images: list[tuple[Path, str, dict]],
        style_reference_images: dict[str, list[tuple[Path, str, dict]]],
    ) -> tuple[InteriorStyleAnalysisArtifact, dict]:
        settings = get_settings()
        if not settings.gemini_api_key:
            raise HTTPException(
                status_code=500,
                detail="Gemini interior/style analysis is enabled but GEMINI_API_KEY is missing.",
            )

        try:
            import google.genai as genai
            from google.genai import types
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"google-genai is not available: {exc}",
            ) from exc

        client = genai.Client(api_key=settings.gemini_api_key)
        interior_records: list[InteriorPhotoSemanticRecord] = []
        interior_raw_records: list[dict] = []
        for image_path, _, source_metadata in interior_images:
            if not image_path.exists():
                raise HTTPException(status_code=404, detail=f"interior image not found: {image_path.name}")
            mime_type = self._mime_type_for_path(image_path)
            image_bytes = image_path.read_bytes()
            prompt = self._interior_semantic_prompt()
            schema = {
                "type": "object",
                "properties": {
                    "room_context": {"type": "string"},
                    "floor_color_category": {"type": "string"},
                    "detected_objects": {"type": "array"},
                    "dominant_colors": {"type": "array"},
                    "dominant_materials": {"type": "array"},
                    "bed": {"type": "object"},
                    "sofa": {"type": "object"},
                    "notes": {"type": "array"},
                },
            }
            response_text, parse_mode = self._generate_gemini_json_text(
                client=client,
                model=settings.gemini_model,
                prompt=prompt,
                image_bytes=image_bytes,
                mime_type=mime_type,
                schema=schema,
                failure_detail="Gemini interior semantic analysis failed",
            )
            payload = self._load_json_payload(response_text, "Gemini interior semantic analysis")
            record = self._normalize_interior_photo_record(payload, source_metadata)
            interior_records.append(record)
            interior_raw_records.append(
                {
                    "source_image": source_metadata,
                    "response_text": response_text,
                    "parse_mode": parse_mode,
                    "analysis": record.model_dump(mode="json"),
                }
            )

        style_groups = InteriorStyleReferenceAnalysisGroups()
        style_raw_records: dict[str, list[dict]] = {"ideal": [], "acceptable": [], "ng": []}
        for reference_type in ("ideal", "acceptable", "ng"):
            for image_path, _, source_metadata in style_reference_images.get(reference_type, []):
                if not image_path.exists():
                    raise HTTPException(status_code=404, detail=f"style reference image not found: {image_path.name}")
                mime_type = self._mime_type_for_path(image_path)
                image_bytes = image_path.read_bytes()
                prompt = self._style_reference_semantic_prompt(reference_type)
                schema = {
                    "type": "object",
                    "properties": {
                        "watercolor_strength": {"type": "string"},
                        "linework_style": {"type": "string"},
                        "palette_keywords": {"type": "array"},
                        "positive_cues": {"type": "array"},
                        "avoid_cues": {"type": "array"},
                        "notes": {"type": "array"},
                    },
                }
                response_text, parse_mode = self._generate_gemini_json_text(
                    client=client,
                    model=settings.gemini_model,
                    prompt=prompt,
                    image_bytes=image_bytes,
                    mime_type=mime_type,
                    schema=schema,
                    failure_detail="Gemini style reference semantic analysis failed",
                )
                payload = self._load_json_payload(response_text, "Gemini style reference semantic analysis")
                record = self._normalize_style_reference_record(payload, reference_type, source_metadata)
                getattr(style_groups, reference_type).append(record)
                style_raw_records[reference_type].append(
                    {
                        "source_image": source_metadata,
                        "response_text": response_text,
                        "parse_mode": parse_mode,
                        "analysis": record.model_dump(mode="json"),
                    }
                )

        derived_profile = self._derive_interior_style_profile(interior_records, style_groups)
        summary = InteriorAnalysisSummary(
            provider="gemini",
            model=settings.gemini_model,
            interior_photo_count=len(interior_records),
            style_reference_count=sum(
                len(getattr(style_groups, group_name))
                for group_name in ("ideal", "acceptable", "ng")
            ),
            preferred_floor_color=derived_profile.preferred_floor_color,
            inferred_bed_type=derived_profile.inferred_bed_type,
            accent_colors=derived_profile.accent_colors,
            style_positive_cues=derived_profile.style_positive_cues,
            style_avoid_cues=derived_profile.style_avoid_cues,
        )
        artifact = InteriorStyleAnalysisArtifact(
            run_id="pending",
            generated_at=datetime.now(timezone.utc),
            provider="gemini",
            model=settings.gemini_model,
            interior_photos=interior_records,
            style_references=style_groups,
            derived_profile=derived_profile,
            summary=summary,
            warnings=[],
            errors=[],
        )
        raw_payload = {
            "provider": "gemini",
            "model": settings.gemini_model,
            "mode": "semantic_only",
            "interior_photos": interior_raw_records,
            "style_references": style_raw_records,
            "derived_profile": derived_profile.model_dump(mode="json"),
        }
        return artifact, raw_payload

    def _generate_gemini_json_text(
        self,
        *,
        client,
        model: str,
        prompt: str,
        image_bytes: bytes,
        mime_type: str,
        schema: dict,
        failure_detail: str,
    ) -> tuple[str, str]:
        from google.genai import types
        settings = get_settings()

        def run_with_retry(request_config):
            attempts = max(1, int(settings.gemini_retry_attempts))
            last_exc = None
            for attempt in range(1, attempts + 1):
                try:
                    response = client.models.generate_content(
                        model=model,
                        contents=[
                            types.Content(
                                role="user",
                                parts=[
                                    types.Part.from_text(text=prompt),
                                    types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                                ],
                            )
                        ],
                        config=request_config,
                    )
                    if attempt > 1:
                        logger.info(
                            "Gemini request succeeded after retry; provider=gemini model=%s attempts=%s",
                            model,
                            attempt,
                        )
                    return response
                except Exception as exc:
                    last_exc = exc
                    if not self._is_retryable_gemini_error(exc) or attempt >= attempts:
                        if self._is_retryable_gemini_error(exc) and attempt >= attempts:
                            logger.warning(
                                "Gemini request failed after all retries; provider=gemini model=%s attempts=%s error=%s",
                                model,
                                attempt,
                                self._short_retryable_error(exc),
                            )
                        raise
                    logger.warning(
                        "Gemini request failed with retryable error; provider=gemini model=%s attempt=%s/%s delay_seconds=%s error=%s",
                        model,
                        attempt,
                        attempts,
                        settings.gemini_retry_delay_seconds,
                        self._short_retryable_error(exc),
                    )
                    time.sleep(max(0.0, float(settings.gemini_retry_delay_seconds)))
            if last_exc is not None:
                raise last_exc

        try:
            response = run_with_retry(
                types.GenerateContentConfig(
                    temperature=0,
                    response_mime_type="application/json",
                    response_schema=schema,
                )
            )
            response_text = self._extract_gemini_text(response)
            parse_mode = "structured_json"
        except Exception:
            try:
                response = run_with_retry(
                    types.GenerateContentConfig(
                        temperature=0,
                        response_mime_type="application/json",
                    )
                )
                response_text = self._extract_gemini_text(response)
                parse_mode = "json_only"
            except Exception as exc:
                raise HTTPException(status_code=502, detail=f"{failure_detail}: {exc}") from exc

        if not response_text:
            raise HTTPException(status_code=502, detail=f"{failure_detail}: empty response")
        return response_text, parse_mode

    @staticmethod
    def _is_retryable_gemini_error(exc: Exception) -> bool:
        message = str(exc).lower()
        retry_markers = (
            "503",
            "unavailable",
            "resource_exhausted",
            "temporarily unavailable",
            "high demand",
        )
        return any(marker in message for marker in retry_markers)

    @staticmethod
    def _short_retryable_error(error: Exception) -> str:
        text = str(error)
        upper = text.upper()

        if "RESOURCE_EXHAUSTED" in upper:
            return "RESOURCE_EXHAUSTED"
        if "UNAVAILABLE" in upper or "503" in upper:
            return "503_UNAVAILABLE"
        if "HIGH DEMAND" in upper:
            return "HIGH_DEMAND"

        return error.__class__.__name__

    def _load_json_payload(self, response_text: str, provider_label: str) -> dict:
        try:
            return json.loads(self._strip_json_fences(response_text))
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"{provider_label} returned invalid JSON: {exc}",
            ) from exc

    def _generate_openai_json_text(
        self,
        *,
        client,
        model: str,
        prompt: str,
        image_paths: list[Path],
        failure_detail: str,
        response_text_format: dict | None = None,
    ) -> tuple[str, str]:
        settings = get_settings()
        content: list[dict] = [{"type": "input_text", "text": prompt}]
        for image_path in image_paths:
            mime_type = self._mime_type_for_path(image_path)
            image_bytes = image_path.read_bytes()
            encoded = base64.b64encode(image_bytes).decode("ascii")
            content.append({"type": "input_image", "image_url": f"data:{mime_type};base64,{encoded}"})
        response_kwargs = {
            "model": model,
            "input": [{"role": "user", "content": content}],
            "max_output_tokens": settings.openai_analysis_max_output_tokens,
        }
        if response_text_format is not None:
            response_kwargs["text"] = {"format": response_text_format, "verbosity": "low"}
        try:
            response = client.responses.create(**response_kwargs)
        except Exception as exc:
            if response_text_format is not None:
                fallback_kwargs = dict(response_kwargs)
                fallback_kwargs.pop("text", None)
                try:
                    response = client.responses.create(**fallback_kwargs)
                except Exception as fallback_exc:
                    short_error = self._short_retryable_error(fallback_exc)
                    raise HTTPException(status_code=502, detail=f"{failure_detail}: {short_error}") from fallback_exc
            else:
                short_error = self._short_retryable_error(exc)
                raise HTTPException(status_code=502, detail=f"{failure_detail}: {short_error}") from exc

        text = getattr(response, "output_text", None)
        if isinstance(text, str) and text.strip():
            return text.strip(), "responses_output_text"

        try:
            response_dict = response.model_dump()
            chunks: list[str] = []
            for output in response_dict.get("output", []) or []:
                for item in output.get("content", []) or []:
                    if item.get("type") in {"output_text", "text"} and item.get("text"):
                        chunks.append(str(item["text"]))
            if chunks:
                return "".join(chunks).strip(), "responses_output_content"
        except Exception:
            pass

        raise HTTPException(status_code=502, detail=f"{failure_detail}: empty response text")

    @staticmethod
    def _write_failed_openai_response(
        run_id: str | None,
        artifacts_dir: Path | None,
        *,
        attempt: int,
        response_text: str,
    ) -> str | None:
        if run_id is None or artifacts_dir is None:
            return None
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        failed_path = artifacts_dir / f"floorplan_analysis_raw_failed_attempt_{attempt}.txt"
        try:
            failed_path.write_text(response_text or "", encoding="utf-8")
        except OSError:
            return None
        return str(failed_path)

    @staticmethod
    def _openai_floorplan_semantic_response_format() -> dict:
        return {
            "type": "json_schema",
            "name": "floorplan_semantic_compact",
            "strict": True,
            "description": "Compact JSON for floorplan semantic analysis.",
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "schema_version",
                    "provider",
                    "model",
                    "analysis_type",
                    "geometry_precision",
                    "canvas",
                    "apartment_type",
                    "layout_description",
                    "rooms",
                    "doors",
                    "windows",
                    "fixtures",
                    "labels",
                    "warnings",
                    "errors",
                ],
                "properties": {
                    "schema_version": {"type": "string"},
                    "provider": {"type": "string"},
                    "model": {"type": ["string", "null"]},
                    "analysis_type": {"type": "string"},
                    "geometry_precision": {"type": "string"},
                    "canvas": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["width", "height"],
                        "properties": {
                            "width": {"type": "integer"},
                            "height": {"type": "integer"},
                        },
                    },
                    "apartment_type": {"type": ["string", "null"]},
                    "layout_description": {"type": "string"},
                    "rooms": {
                        "type": "array",
                        "maxItems": 24,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": [
                                "id",
                                "source_label",
                                "label_english",
                                "room_type",
                                "functional_hint",
                                "bbox",
                                "confidence",
                            ],
                            "properties": {
                                "id": {"type": "string"},
                                "source_label": {"type": ["string", "null"]},
                                "label_english": {"type": ["string", "null"]},
                                "room_type": {"type": "string"},
                                "functional_hint": {"type": ["string", "null"]},
                                "bbox": {
                                    "type": ["array", "null"],
                                    "minItems": 4,
                                    "maxItems": 4,
                                    "items": {"type": "integer"},
                                },
                                "confidence": {"type": "number"},
                            },
                        },
                    },
                    "doors": {
                        "type": "array",
                        "maxItems": 24,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["position", "connects", "bbox", "confidence"],
                            "properties": {
                                "position": {"type": ["string", "null"]},
                                "connects": {"type": "array", "items": {"type": "string"}},
                                "bbox": {
                                    "type": ["array", "null"],
                                    "minItems": 4,
                                    "maxItems": 4,
                                    "items": {"type": "integer"},
                                },
                                "confidence": {"type": "number"},
                            },
                        },
                    },
                    "windows": {
                        "type": "array",
                        "maxItems": 24,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["position", "room", "bbox", "confidence"],
                            "properties": {
                                "position": {"type": ["string", "null"]},
                                "room": {"type": ["string", "null"]},
                                "bbox": {
                                    "type": ["array", "null"],
                                    "minItems": 4,
                                    "maxItems": 4,
                                    "items": {"type": "integer"},
                                },
                                "confidence": {"type": "number"},
                            },
                        },
                    },
                    "fixtures": {
                        "type": "array",
                        "maxItems": 24,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["id", "type", "room_type", "bbox", "confidence"],
                            "properties": {
                                "id": {"type": "string"},
                                "type": {"type": "string"},
                                "room_type": {"type": ["string", "null"]},
                                "bbox": {
                                    "type": ["array", "null"],
                                    "minItems": 4,
                                    "maxItems": 4,
                                    "items": {"type": "integer"},
                                },
                                "confidence": {"type": "number"},
                            },
                        },
                    },
                    "labels": {
                        "type": "array",
                        "maxItems": 32,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["text", "english", "bbox"],
                            "properties": {
                                "text": {"type": ["string", "null"]},
                                "english": {"type": ["string", "null"]},
                                "bbox": {
                                    "type": ["array", "null"],
                                    "minItems": 4,
                                    "maxItems": 4,
                                    "items": {"type": "integer"},
                                },
                            },
                        },
                    },
                    "warnings": {"type": "array", "maxItems": 20, "items": {"type": "string"}},
                    "errors": {"type": "array", "maxItems": 20, "items": {"type": "string"}},
                },
            },
        }

    def _openai_floorplan_semantic_prompt(self) -> str:
        return (
            "Analyze this Japanese apartment floorplan for semantic understanding only. Do not generate images.\n"
            "Return only valid JSON. No markdown. No comments. No trailing commas. No explanation text before or after JSON.\n"
            "Use double quotes for all JSON strings.\n"
            "If uncertain, use null or \"unknown\".\n"
            "Keep all string values short.\n"
            "Use these top-level keys only: schema_version, provider, model, analysis_type, geometry_precision, canvas, apartment_type, layout_description, rooms, doors, windows, fixtures, labels, warnings, errors.\n"
            "Do not include long reasoning, long notes, or repeated descriptions.\n"
            "For each room, return only: id, source_label, label_english, room_type, functional_hint, bbox, confidence.\n"
            "For each label, return only: text, english, bbox.\n"
            "For each door or window, keep fields short and compact.\n"
            "Use empty arrays when labels or fixtures are not clearly needed.\n"
            "Use room_type values like living_room, bed_room, kitchen, dining_kitchen, bath_room, toilet, wash_room, closet, entrance, balcony, hallway, storage, western_room, unknown.\n"
            "Use short functional_hint values such as main_living, western_lounge, bedroom, kitchen, bath, toilet, wash, closet, circulation, balcony, unknown.\n"
            "Any bbox must be approximate semantic guidance only and use [x, y, w, h] or null.\n"
            "If multiple 洋室 rooms exist, use context; one may be bed_room and another western_room. If uncertain use western_room and add a warning.\n"
            "Keep warnings and errors as short strings.\n"
        )

    def _openai_floorplan_semantic_retry_prompt(self, attempt: int, previous_attempt: dict | None = None) -> str:
        if previous_attempt and previous_attempt.get("likely_truncated"):
            extra = "The previous response was likely truncated. Return a much shorter JSON object."
        else:
            extra = "The previous response was invalid JSON. Return a shorter valid JSON object only."
        return (
            f"{extra}\n"
            "Return only compact JSON.\n"
            "No markdown. No explanations.\n"
            "Keep strings short.\n"
            "Use null or \"unknown\" when unsure.\n"
            "Do not add extra keys.\n"
            "Preserve the same top-level structure and key names.\n"
        )

    def _openai_interior_semantic_prompt(self, source_metadata: dict) -> str:
        original = source_metadata.get("original_filename") or source_metadata.get("stored_filename") or "unknown"
        return (
            "Analyze this interior reference photo independently for semantic guidance only. Do not generate images.\n"
            "Return strict JSON only, no markdown.\n"
            f"Image original filename: {original}.\n"
            "Return keys: stored_filename, original_filename, relative_path, room_hint, detected_objects, style_cues, color_cues, arrangement_hints, confidence, notes, floor_color_category, dominant_materials, bed, sofa, warnings, errors.\n"
            "Allowed room_hint values: living_room, bed_room, western_room, dining, kitchen, bath_room, toilet, wash_room, closet, decoration, unknown.\n"
            "Normalize object labels where possible: sofa, sofa_3_seater, tv, tv_stand, coffee_table, dining_table, chair, bed, two_single_beds, pillow, blanket, wardrobe, curtain, wall_art, potted_plant, floor_lamp, kitchen_counter, sink, stove, cabinet, bathtub, shower, towel, toilet, washbasin, refrigerator, rug.\n"
            "If a bedroom clearly shows two separate beds, include two_single_beds, bed, pillow, blanket. If unclear include bed only and add a warning.\n"
            "If sofa and TV are visible, include arrangement_hints: sofa_tv_opposite_with_coffee_table_between_when_possible.\n"
            "Use floor_color_category one of white, light_brown, dark_brown, unknown.\n"
        )

    def _coerce_openai_floorplan_payload(self, payload: dict) -> dict:
        if not isinstance(payload, dict):
            raise TypeError("OpenAI floorplan response must be a JSON object")

        def coerce_compact_bbox(value):
            if value is None:
                return None
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except Exception:
                    return None
            if isinstance(value, dict):
                if any(key in value for key in ("x", "y", "w", "h")):
                    x = _coerce_float(value.get("x"))
                    y = _coerce_float(value.get("y"))
                    w = _coerce_float(value.get("w"))
                    h = _coerce_float(value.get("h"))
                    if None in {x, y, w, h}:
                        return None
                    return {
                        "x_min": x,
                        "y_min": y,
                        "x_max": x + w,
                        "y_max": y + h,
                    }
                if any(key in value for key in ("x_min", "y_min", "x_max", "y_max", "left", "top", "right", "bottom")):
                    x_min = _coerce_float(value.get("x_min", value.get("left")))
                    y_min = _coerce_float(value.get("y_min", value.get("top")))
                    x_max = _coerce_float(value.get("x_max", value.get("right")))
                    y_max = _coerce_float(value.get("y_max", value.get("bottom")))
                    if None in {x_min, y_min, x_max, y_max}:
                        return None
                    return {
                        "x_min": x_min,
                        "y_min": y_min,
                        "x_max": x_max,
                        "y_max": y_max,
                    }
            if isinstance(value, (list, tuple)) and len(value) >= 4:
                x = _coerce_float(value[0])
                y = _coerce_float(value[1])
                w = _coerce_float(value[2])
                h = _coerce_float(value[3])
                if None in {x, y, w, h}:
                    return None
                return {
                    "x_min": x,
                    "y_min": y,
                    "x_max": x + w,
                    "y_max": y + h,
                }
            return None

        warnings = self._coerce_text_list(payload.get("warnings"))
        errors = self._coerce_text_list(payload.get("errors"))
        if payload.get("geometry_precision") != "approximate_semantic_only":
            warnings.append("OpenAI geometry is approximate semantic-only and not CAD-accurate.")

        rooms = []
        for index, room in enumerate(payload.get("rooms") or [], start=1):
            if not isinstance(room, dict):
                continue
            source_label = room.get("source_label") or room.get("label_original") or room.get("label") or room.get("name")
            label_english = room.get("label_english") or room.get("english") or room.get("label_en")
            room_type = room.get("room_type") or room.get("type") or label_english or source_label or "unknown"
            functional_hint = room.get("functional_hint") or room.get("position") or "unknown"
            bbox = coerce_compact_bbox(room.get("bbox") or room.get("approx_bbox") or room.get("bounding_box"))
            geometry_notes = self._coerce_text_list(room.get("geometry_notes") or room.get("notes"))
            if not geometry_notes:
                geometry_notes = ["Approximate semantic bbox only; not CAD-accurate."]
            rooms.append(
                {
                    "type": room_type,
                    "room_name": source_label or label_english or room_type,
                    "position": functional_hint,
                    "size": room.get("size"),
                    "approx_bbox": bbox,
                    "bounding_box": bbox,
                    "confidence": room.get("confidence"),
                    "geometry_confidence": min(_coerce_float(room.get("geometry_confidence")) or 0.3, 0.3),
                    "geometry_notes": geometry_notes,
                    "connected_to": self._coerce_text_list(
                        room.get("connected_to") or room.get("connections") or room.get("adjacent_rooms")
                    ),
                }
            )

        doors = []
        for door in payload.get("doors") or []:
            if not isinstance(door, dict):
                continue
            connects = self._coerce_text_list(
                door.get("connects") or door.get("connected_rooms") or door.get("rooms") or door.get("labels")
            )
            doors.append(
                {
                    "position": door.get("position") or door.get("direction") or door.get("location"),
                    "connects": connects,
                    "bounding_box": coerce_compact_bbox(door.get("bbox") or door.get("approx_bbox") or door.get("bounding_box")),
                    "approx_bbox": coerce_compact_bbox(door.get("bbox") or door.get("approx_bbox") or door.get("bounding_box")),
                    "confidence": door.get("confidence"),
                    "geometry_confidence": min(_coerce_float(door.get("geometry_confidence")) or 0.3, 0.3),
                    "geometry_notes": self._coerce_text_list(door.get("geometry_notes") or door.get("notes")),
                }
            )

        windows = []
        for window in payload.get("windows") or []:
            if not isinstance(window, dict):
                continue
            windows.append(
                {
                    "position": window.get("position") or window.get("direction") or window.get("location"),
                    "room": window.get("room") or window.get("room_type") or window.get("room_id"),
                    "bounding_box": coerce_compact_bbox(window.get("bbox") or window.get("approx_bbox") or window.get("bounding_box")),
                    "approx_bbox": coerce_compact_bbox(window.get("bbox") or window.get("approx_bbox") or window.get("bounding_box")),
                    "confidence": window.get("confidence"),
                    "geometry_confidence": min(_coerce_float(window.get("geometry_confidence")) or 0.3, 0.3),
                    "geometry_notes": self._coerce_text_list(window.get("geometry_notes") or window.get("notes")),
                }
            )

        labels = []
        for index, label in enumerate(payload.get("labels") or [], start=1):
            if not isinstance(label, dict):
                continue
            bbox = coerce_compact_bbox(label.get("bbox") or label.get("approx_bbox") or label.get("bounding_box"))
            labels.append(
                {
                    "id": f"label_{index:03d}",
                    "text": label.get("text") or label.get("source_text") or label.get("label_original"),
                    "english": label.get("english") or label.get("approved_text") or label.get("label_english"),
                    "bbox": bbox,
                }
            )

        return {
            "apartment_type": payload.get("apartment_type") or payload.get("apartmentType") or "unknown",
            "layout_description": payload.get("layout_description")
            or payload.get("summary")
            or "OpenAI semantic floorplan analysis; geometry is approximate only.",
            "rooms": rooms,
            "doors": doors,
            "windows": windows,
            "balcony": self._coerce_openai_balcony(payload),
            "constraints": self._dedupe_preserve_order(
                warnings + ["OpenAI bbox/geometry signals are approximate semantic-only and not CAD-accurate."]
            ),
            "labels": labels,
            "warnings": warnings,
            "errors": errors,
        }

    def _coerce_openai_balcony(self, payload: dict):
        balcony = payload.get("balcony")
        if isinstance(balcony, dict):
            return balcony
        rooms = payload.get("rooms") or []
        has_balcony = any(isinstance(room, dict) and str(room.get("room_type") or "").lower() == "balcony" for room in rooms)
        return {"exists": has_balcony, "position": None} if has_balcony else None

    def _coerce_openai_interior_photo_payload(self, payload: dict, source_metadata: dict) -> dict:
        if not isinstance(payload, dict):
            raise TypeError("OpenAI interior response must be a JSON object")
        if isinstance(payload.get("photos"), list) and payload["photos"]:
            first = payload["photos"][0]
            if isinstance(first, dict):
                payload = first
        detected_objects = payload.get("detected_objects") or payload.get("objects") or []
        if isinstance(detected_objects, list):
            detected_objects = [
                {"object_type": item} if isinstance(item, str) else item
                for item in detected_objects
                if item is not None
            ]
        else:
            detected_objects = []
        room_hint = payload.get("room_hint") or payload.get("room_context") or "unknown"
        return {
            "source_image": source_metadata,
            "stored_filename": payload.get("stored_filename") or source_metadata.get("stored_filename"),
            "original_filename": payload.get("original_filename") or source_metadata.get("original_filename"),
            "relative_path": payload.get("relative_path") or source_metadata.get("relative_path"),
            "room_context": self._openai_room_hint_to_legacy_context(room_hint),
            "floor_color_category": payload.get("floor_color_category") or payload.get("floor_tone") or "unknown",
            "detected_objects": detected_objects,
            "dominant_colors": payload.get("color_cues") or payload.get("dominant_colors") or [],
            "dominant_materials": payload.get("dominant_materials") or [],
            "bed": payload.get("bed"),
            "sofa": payload.get("sofa"),
            "notes": self._dedupe_preserve_order(
                self._coerce_text_list(payload.get("notes"))
                + self._coerce_text_list(payload.get("style_cues"))
                + self._coerce_text_list(payload.get("arrangement_hints"))
                + self._coerce_text_list(payload.get("warnings"))
            ),
        }

    @staticmethod
    def _openai_room_hint_to_legacy_context(value: str | None) -> str:
        raw = (value or "unknown").strip().lower()
        mapping = {
            "bed_room": "bedroom",
            "western_room": "bedroom",
            "bath_room": "bathroom",
            "wash_room": "washroom",
            "dining": "living_room",
            "decoration": "living_room",
        }
        return mapping.get(raw, raw)

    def _build_stub_interior_record(self, image_path: Path, source_metadata: dict) -> InteriorPhotoSemanticRecord:
        room_context = "bedroom" if "bed" in image_path.name.lower() else "living_room"
        objects = [
            InteriorObjectSemanticRecord(object_type="bed" if room_context == "bedroom" else "sofa", color="white", material="fabric", count=1),
            InteriorObjectSemanticRecord(object_type="plant", color="green", material="unknown", count=1),
        ]
        bed = None
        sofa = None
        if room_context == "bedroom":
            bed = BedSemanticRecord(
                present=True,
                pillow_count=2,
                inferred_bed_type="semi_double_bed",
                base_color="white",
                cushion_colors=["beige", "green"],
            )
        else:
            sofa = SofaSemanticRecord(
                present=True,
                base_color="white",
                cushion_colors=["beige", "green"],
            )
        return InteriorPhotoSemanticRecord(
            source_image=self._coerce_image_inspection_metadata(source_metadata),
            room_context=room_context,
            floor_color_category="light_brown",
            detected_objects=objects,
            dominant_colors=["white", "beige", "green"],
            dominant_materials=["wood", "fabric"],
            bed=bed,
            sofa=sofa,
            notes=["Deterministic stub interior analysis."],
        )

    def _build_stub_style_reference_record(
        self,
        image_path: Path,
        reference_type: str,
        source_metadata: dict,
    ) -> StyleReferenceSemanticRecord:
        positive = ["soft watercolor wash", "clean readable walls"]
        avoid = ["heavy dark framing"] if reference_type == "ng" else []
        if reference_type == "ideal":
            positive.append("warm natural palette")
        if reference_type == "acceptable":
            positive.append("balanced furniture detail")
        if reference_type == "ng":
            avoid.extend(["muddy colors", "overly dense texture"])
        return StyleReferenceSemanticRecord(
            source_image=self._coerce_image_inspection_metadata(source_metadata),
            reference_type=reference_type,
            watercolor_strength="medium",
            linework_style="clean",
            palette_keywords=["beige", "light_brown", "white"],
            positive_cues=positive if reference_type != "ng" else [],
            avoid_cues=avoid,
            notes=[f"Deterministic stub style analysis for {reference_type} reference."],
        )

    def _normalize_interior_photo_record(self, payload: dict, source_metadata: dict) -> InteriorPhotoSemanticRecord:
        room_context = self._normalize_room_context(payload.get("room_context"))
        floor_color = self._normalize_floor_color(payload.get("floor_color_category"))
        detected_objects = [
            self._normalize_interior_object(item)
            for item in (payload.get("detected_objects") or payload.get("objects") or [])
        ]
        dominant_colors = self._dedupe_preserve_order(
            [self._normalize_color(color) for color in self._coerce_text_list(payload.get("dominant_colors"))]
        )
        dominant_materials = self._dedupe_preserve_order(
            [self._normalize_material(material) for material in self._coerce_text_list(payload.get("dominant_materials"))]
        )
        bed = self._normalize_bed_record(payload.get("bed"))
        sofa = self._normalize_sofa_record(payload.get("sofa"))
        notes = self._coerce_text_list(payload.get("notes"))
        record = InteriorPhotoSemanticRecord(
            source_image=self._coerce_image_inspection_metadata(source_metadata),
            room_context=room_context,
            floor_color_category=floor_color,
            detected_objects=detected_objects,
            dominant_colors=dominant_colors,
            dominant_materials=dominant_materials,
            bed=bed,
            sofa=sofa,
            notes=notes,
        )
        return self._enrich_interior_photo_record(record)

    def _normalize_style_reference_record(
        self,
        payload: dict,
        reference_type: str,
        source_metadata: dict,
    ) -> StyleReferenceSemanticRecord:
        palette_keywords = self._dedupe_preserve_order(
            [self._normalize_color(color) for color in self._coerce_text_list(payload.get("palette_keywords"))]
        )
        positive_cues = self._coerce_text_list(payload.get("positive_cues"))
        avoid_cues = self._coerce_text_list(payload.get("avoid_cues"))
        notes = self._coerce_text_list(payload.get("notes"))
        return StyleReferenceSemanticRecord(
            source_image=self._coerce_image_inspection_metadata(source_metadata),
            reference_type=reference_type,
            watercolor_strength=str(payload.get("watercolor_strength") or "medium"),
            linework_style=str(payload.get("linework_style") or "clean"),
            palette_keywords=palette_keywords,
            positive_cues=positive_cues,
            avoid_cues=avoid_cues,
            notes=notes,
        )

    def _derive_interior_style_profile(
        self,
        interior_records: list[InteriorPhotoSemanticRecord],
        style_groups: InteriorStyleReferenceAnalysisGroups,
    ) -> DerivedInteriorStyleProfile:
        floor_colors = [record.floor_color_category for record in interior_records if record.floor_color_category != "unknown"]
        preferred_floor_color = floor_colors[0] if floor_colors else "unknown"

        inferred_bed_type = None
        accent_colors: list[str] = []
        cushion_colors: list[str] = []
        preferred_materials: list[str] = []
        for record in interior_records:
            accent_colors.extend(color for color in record.dominant_colors if color not in {"white", "unknown"})
            preferred_materials.extend(material for material in record.dominant_materials if material != "unknown")
            if record.bed and record.bed.inferred_bed_type and inferred_bed_type is None:
                inferred_bed_type = record.bed.inferred_bed_type
            if record.bed:
                cushion_colors.extend(record.bed.cushion_colors)
            if record.sofa:
                cushion_colors.extend(record.sofa.cushion_colors)

        positive_cues: list[str] = []
        acceptable_cues: list[str] = []
        avoid_cues: list[str] = []
        for item in style_groups.ideal:
            positive_cues.extend(item.positive_cues)
        for item in style_groups.acceptable:
            acceptable_cues.extend(item.positive_cues)
        for item in style_groups.ng:
            avoid_cues.extend(item.avoid_cues)

        return DerivedInteriorStyleProfile(
            preferred_floor_color=self._normalize_floor_color(preferred_floor_color),
            bed_base_color="white",
            sofa_base_color="white",
            inferred_bed_type=inferred_bed_type,
            accent_colors=self._dedupe_preserve_order([self._normalize_color(color) for color in accent_colors if color]),
            cushion_colors=self._dedupe_preserve_order([self._normalize_color(color) for color in cushion_colors if color]),
            preferred_materials=self._dedupe_preserve_order([self._normalize_material(material) for material in preferred_materials if material]),
            style_positive_cues=self._dedupe_preserve_order([cue for cue in positive_cues if cue]),
            style_acceptable_cues=self._dedupe_preserve_order([cue for cue in acceptable_cues if cue]),
            style_avoid_cues=self._dedupe_preserve_order([cue for cue in avoid_cues if cue]),
        )

    def _normalize_interior_object(self, payload: dict) -> InteriorObjectSemanticRecord:
        if not isinstance(payload, dict):
            return InteriorObjectSemanticRecord(
                object_type=self._normalize_interior_object_type(str(payload) if payload is not None else None),
                color="unknown",
                material="unknown",
                count=1,
            )
        count = payload.get("count")
        try:
            count = int(count)
        except (TypeError, ValueError):
            count = 1
        count = max(1, count)
        return InteriorObjectSemanticRecord(
            object_type=self._normalize_interior_object_type(
                payload.get("object_type") or payload.get("type") or payload.get("label")
            ),
            color=self._normalize_color(payload.get("color")),
            material=self._normalize_material(payload.get("material")),
            count=count,
            notes="; ".join(self._coerce_text_list(payload.get("notes"))) or None,
            source=self._safe_string(payload.get("source")),
        )

    def _normalize_bed_record(self, payload: dict | None) -> BedSemanticRecord | None:
        if payload is None:
            return None
        if not isinstance(payload, dict):
            return BedSemanticRecord(present=bool(payload), base_color="white")
        if not payload:
            return BedSemanticRecord(present=False, base_color="white")
        pillow_count = payload.get("pillow_count") or payload.get("pillows") or payload.get("cushion_count")
        try:
            pillow_count = int(pillow_count) if pillow_count is not None else None
        except (TypeError, ValueError):
            pillow_count = None
        inferred = self._infer_bed_type_from_pillows(pillow_count)
        cushion_colors = self._dedupe_preserve_order(
            [
                self._normalize_color(color)
                for color in self._coerce_text_list(payload.get("cushion_colors") or payload.get("pillow_colors"))
            ]
        )
        return BedSemanticRecord(
            present=bool(payload.get("present", True)),
            pillow_count=pillow_count,
            inferred_bed_type=inferred,
            base_color="white",
            cushion_colors=cushion_colors,
        )

    def _normalize_sofa_record(self, payload: dict | None) -> SofaSemanticRecord | None:
        if payload is None:
            return None
        if not isinstance(payload, dict):
            return SofaSemanticRecord(present=bool(payload), base_color="white")
        if not payload:
            return SofaSemanticRecord(present=False, base_color="white")
        cushion_colors = self._dedupe_preserve_order(
            [self._normalize_color(color) for color in self._coerce_text_list(payload.get("cushion_colors"))]
        )
        return SofaSemanticRecord(
            present=bool(payload.get("present", True)),
            base_color="white",
            cushion_colors=cushion_colors,
        )

    def _coerce_text_list(self, value) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            text = value.strip()
            return [text] if text else []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return [str(value).strip()] if str(value).strip() else []

    def _enrich_interior_photo_record(self, record: InteriorPhotoSemanticRecord) -> InteriorPhotoSemanticRecord:
        notes_text = " ".join(record.notes).lower()
        filename_text = " ".join(
            filter(
                None,
                [
                    (record.source_image.original_filename or "").lower(),
                    (record.source_image.stored_filename or "").lower(),
                ],
            )
        )

        known_objects = [obj for obj in record.detected_objects if obj.object_type != "unknown"]
        should_enrich = not record.detected_objects or len(known_objects) <= max(1, len(record.detected_objects) // 2)
        if not should_enrich:
            return record

        detected_objects = list(record.detected_objects)
        existing_types = {obj.object_type for obj in detected_objects}

        def add_object(object_type: str, *, count: int = 1, color: str | None = None, material: str | None = None) -> None:
            if object_type in existing_types:
                return
            detected_objects.append(
                InteriorObjectSemanticRecord(
                    object_type=object_type,
                    color=color or "unknown",
                    material=material or "unknown",
                    count=max(1, int(count)),
                    notes="Inferred from interior analysis notes or filename.",
                    source="deterministic_postprocess",
                )
            )
            existing_types.add(object_type)

        combined_text = f"{notes_text} {filename_text}".strip()

        if record.sofa and record.sofa.present:
            add_object(
                "sofa",
                color=self._normalize_color(record.sofa.base_color),
                material="fabric" if record.sofa.cushion_colors else "unknown",
            )
        if record.bed and record.bed.present:
            bed_type = record.bed.inferred_bed_type or "bed"
            add_object(bed_type if bed_type in {"single_bed", "semi_double_bed", "double_bed"} else "bed", color="white")

        if any(token in combined_text for token in ("television", " tv ", "tv", "media console")):
            add_object("tv")
            add_object("tv_stand")
        if "dining table" in combined_text:
            add_object("dining_table")
        if "coffee table" in combined_text:
            add_object("coffee_table")
        if "four chairs" in combined_text:
            add_object("chair", count=4)
        elif "chair" in combined_text:
            add_object("chair")
        if any(token in combined_text for token in ("potted plant", " plant", "plant ", "ficus")):
            add_object("potted_plant", color="green")
        if "curtain" in combined_text:
            add_object("curtain", color="white")
        if any(token in combined_text for token in ("wall art", "framed picture", "painting")):
            add_object("wall_art")
        if "desk" in combined_text:
            add_object("desk")
        if "shelf" in combined_text:
            add_object("shelf")
        if any(token in combined_text for token in ("floor lamp", "standing lamp")):
            add_object("floor_lamp")

        return record.model_copy(update={"detected_objects": detected_objects})

    def _coerce_image_inspection_metadata(self, payload: dict):
        from app.schemas.run import ImageInspectionMetadata

        return ImageInspectionMetadata.model_validate(payload)

    def _normalize_room_context(self, value: str | None) -> str:
        room_type = self._normalize_room_type(value)
        return room_type if room_type in {"living_room", "bedroom", "kitchen", "dining_kitchen", "bathroom", "toilet", "washroom", "entrance"} else "unknown"

    def _normalize_floor_color(self, value: str | None) -> str:
        if not value:
            return "unknown"
        raw = self._normalize_text(value)
        if raw in self.FLOOR_COLOR_CATEGORIES:
            return raw
        if "white" in raw or "ivory" in raw:
            return "white"
        if "dark" in raw and "brown" in raw:
            return "dark_brown"
        if "light" in raw and "brown" in raw:
            return "light_brown"
        if "wood" in raw and "dark" in raw:
            return "dark_brown"
        if "wood" in raw or "brown" in raw:
            return "light_brown"
        return "unknown"

    def _normalize_interior_object_type(self, value: str | None) -> str:
        if not value:
            return "unknown"
        raw = self._normalize_text(value)
        alias = {
            "bed": "bed",
            "single bed": "bed",
            "two single beds": "two_single_beds",
            "two_single_beds": "two_single_beds",
            "twin beds": "two_single_beds",
            "double bed": "bed",
            "semi double bed": "bed",
            "single_bed": "bed",
            "semi_double_bed": "bed",
            "double_bed": "bed",
            "sofa": "sofa",
            "couch": "sofa",
            "coffee table": "coffee_table",
            "table": "coffee_table",
            "dining table": "dining_table",
            "dining chairs": "chair",
            "dining chair": "chair",
            "chairs": "chair",
            "chair": "chair",
            "tv": "tv",
            "television": "tv",
            "tv stand": "tv_stand",
            "media console": "tv_stand",
            "refrigerator": "refrigerator",
            "fridge": "refrigerator",
            "wardrobe": "wardrobe",
            "closet": "wardrobe",
            "desk": "desk",
            "plant": "plant",
            "plants": "plant",
            "potted plant": "potted_plant",
            "potted plants": "potted_plant",
            "curtain": "curtain",
            "curtains": "curtain",
            "rug": "rug",
            "area rug": "rug",
            "storage": "storage",
            "shelf": "storage",
            "wall art": "wall_art",
            "painting": "wall_art",
            "framed picture": "wall_art",
            "decorative items": "wall_art",
            "lamp": "lamp",
            "floor lamp": "floor_lamp",
            "pillow": "pillow",
            "pillows": "pillow",
            "blanket": "blanket",
            "bedding": "blanket",
            "kitchen counter": "kitchen_counter",
            "counter": "kitchen_counter",
            "sink": "sink",
            "kitchen sink": "sink",
            "stove": "stove",
            "cooktop": "stove",
            "cabinet": "cabinet",
            "cabinets": "cabinet",
            "bathtub": "bathtub",
            "tub": "bathtub",
            "shower": "shower",
            "towel": "towel",
            "toilet": "toilet",
            "washbasin": "washbasin",
            "wash basin": "washbasin",
            "vanity": "washbasin",
        }
        return alias.get(raw, raw if raw in self.NORMALIZED_INTERIOR_OBJECT_TYPES else "unknown")

    def _normalize_color(self, value: str | None) -> str:
        if not value:
            return "unknown"
        raw = self._normalize_text(value)
        alias = {
            "off white": "white",
            "cream": "beige",
            "ivory": "white",
            "light wood": "light_brown",
            "dark wood": "dark_brown",
            "wood": "wood",
            "brown": "brown",
            "beige": "beige",
            "gray": "gray",
            "grey": "gray",
            "green": "green",
            "blue": "blue",
            "pink": "pink",
            "yellow": "yellow",
            "black": "black",
            "white": "white",
        }
        if raw in alias:
            return alias[raw]
        if "white" in raw:
            return "white"
        if "beige" in raw or "cream" in raw:
            return "beige"
        if "light" in raw and "brown" in raw:
            return "light_brown"
        if "dark" in raw and "brown" in raw:
            return "dark_brown"
        if "brown" in raw:
            return "brown"
        if "green" in raw:
            return "green"
        if "blue" in raw:
            return "blue"
        if "pink" in raw:
            return "pink"
        if "yellow" in raw:
            return "yellow"
        if "black" in raw:
            return "black"
        if "wood" in raw:
            return "wood"
        return "unknown"

    def _normalize_material(self, value: str | None) -> str:
        if not value:
            return "unknown"
        raw = self._normalize_text(value)
        alias = {
            "wood": "wood",
            "fabric": "fabric",
            "textile": "fabric",
            "leather": "leather",
            "metal": "metal",
            "tile": "tile",
            "stone": "stone",
            "glass": "glass",
        }
        return alias.get(raw, raw if raw in self.NORMALIZED_MATERIALS else "unknown")

    @staticmethod
    def _infer_bed_type_from_pillows(pillow_count: int | None) -> str | None:
        if pillow_count is None:
            return None
        if pillow_count <= 1:
            return "single_bed"
        if pillow_count == 2:
            return "semi_double_bed"
        if pillow_count in {3, 4}:
            return "double_bed"
        return "double_bed"

    @staticmethod
    def _dedupe_preserve_order(values: list[str]) -> list[str]:
        seen: set[str] = set()
        deduped: list[str] = []
        for value in values:
            if value not in seen:
                seen.add(value)
                deduped.append(value)
        return deduped

    def normalize_floorplan_analysis(self, analysis: FloorplanAnalysis) -> FloorplanAnalysis:
        normalized_rooms = [
            RoomInfo(
                type=self._normalize_room_type(room.type),
                room_name=room.room_name,
                position=self._normalize_position_for_room(room),
                size=room.size,
                bounding_box=room.bounding_box,
                approx_bbox=room.approx_bbox,
                polygon=room.polygon,
                confidence=room.confidence,
                geometry_confidence=room.geometry_confidence,
                geometry_notes=list(room.geometry_notes or []),
                connected_to=self._dedupe_preserve_order(
                    [self._normalize_room_type(label) for label in room.connected_to]
                ),
            )
            for room in analysis.rooms
        ]
        normalized_doors = [
            DoorInfo(
                position=self._normalize_position(door.position),
                connects=self._dedupe_preserve_order(
                    [self._normalize_room_type(label) for label in door.connects]
                ),
                bounding_box=door.bounding_box,
                approx_bbox=door.approx_bbox,
                polygon=door.polygon,
                confidence=door.confidence,
                geometry_confidence=door.geometry_confidence,
                geometry_notes=list(door.geometry_notes or []),
            )
            for door in analysis.doors
        ]
        normalized_windows = [
            WindowInfo(
                position=self._normalize_position(window.position),
                room=self._normalize_room_type(window.room) if window.room else None,
                bounding_box=window.bounding_box,
                approx_bbox=window.approx_bbox,
                polygon=window.polygon,
                confidence=window.confidence,
                geometry_confidence=window.geometry_confidence,
                geometry_notes=list(window.geometry_notes or []),
            )
            for window in analysis.windows
        ]

        normalized_balcony = None
        if analysis.balcony is not None:
            normalized_balcony = BalconyInfo(
                exists=analysis.balcony.exists,
                position=self._normalize_position(analysis.balcony.position),
                bounding_box=analysis.balcony.bounding_box,
                approx_bbox=analysis.balcony.approx_bbox,
                polygon=analysis.balcony.polygon,
                confidence=analysis.balcony.confidence,
                geometry_confidence=analysis.balcony.geometry_confidence,
                geometry_notes=list(analysis.balcony.geometry_notes or []),
            )

        apartment_type = analysis.apartment_type
        if not apartment_type:
            apartment_type = self._infer_apartment_type(normalized_rooms)

        return FloorplanAnalysis(
            apartment_type=apartment_type,
            layout_description=analysis.layout_description,
            rooms=normalized_rooms,
            doors=normalized_doors,
            windows=normalized_windows,
            balcony=normalized_balcony,
            constraints=analysis.constraints,
        )

    def _normalize_position_for_room(self, room: RoomInfo) -> str:
        if room.position:
            return self._normalize_position(room.position)
        inferred = self._infer_position_from_bbox(room.bounding_box or room.approx_bbox)
        if inferred != "unknown":
            return inferred
        return "unknown"

    def _infer_position_from_bbox(self, bbox) -> str:
        if bbox is None:
            return "unknown"
        if hasattr(bbox, "model_dump"):
            bbox = bbox.model_dump(mode="json")
        if isinstance(bbox, dict):
            bbox = [
                bbox.get("x_min"),
                bbox.get("y_min"),
                bbox.get("x_max"),
                bbox.get("y_max"),
            ]
        if not bbox or len(bbox) < 4:
            return "unknown"
        try:
            x1, y1, x2, y2 = [float(value) for value in bbox[:4]]
        except (TypeError, ValueError):
            return "unknown"

        max_coord = max(abs(x1), abs(y1), abs(x2), abs(y2))
        if max_coord > 1.5:
            center_x = ((x1 + x2) / 2) / 1200.0
            center_y = ((y1 + y2) / 2) / 1200.0
        else:
            center_x = (x1 + x2) / 2
            center_y = (y1 + y2) / 2

        if center_x <= 0.33 and center_y <= 0.33:
            return "top_left"
        if center_x >= 0.67 and center_y <= 0.33:
            return "top_right"
        if center_x <= 0.33 and center_y >= 0.67:
            return "bottom_left"
        if center_x >= 0.67 and center_y >= 0.67:
            return "bottom_right"
        if center_y <= 0.33:
            return "top"
        if center_y >= 0.67:
            return "bottom"
        if center_x <= 0.33:
            return "left"
        if center_x >= 0.67:
            return "right"
        return "center"

    def _normalize_room_type(self, value: str | None) -> str:
        if not value:
            return "unknown"

        raw = self._normalize_text(value)
        alias = {
            "wic": "walk_in_closet",
            "walk in closet": "walk_in_closet",
            "walkin closet": "walk_in_closet",
            "walk in closet / wic": "walk_in_closet",
            "walk in closet wic": "walk_in_closet",
            "bathroom wash area": "washroom",
            "bathroom washroom": "washroom",
            "bath wash area": "washroom",
            "wash area": "washroom",
            "washroom": "washroom",
            "bath": "bathroom",
            "bathroom": "bathroom",
            "toilet wc": "toilet",
            "toilet/wc": "toilet",
            "wc": "toilet",
            "toilet": "toilet",
            "玄関": "entrance",
            "genkan": "entrance",
            "洋室": "bedroom",
            "western room": "bedroom",
            "western_room": "bedroom",
            "ldk": "living_room",
            "living dining kitchen": "living_room",
            "living dining kitchen area": "living_room",
            "living room": "living_room",
            "living": "living_room",
            "dk": "dining_kitchen",
            "dining kitchen": "dining_kitchen",
            "dining kitchen area": "dining_kitchen",
            "kitchen": "kitchen",
            "bedroom": "bedroom",
            "bed room": "bedroom",
            "closet": "closet",
            "storage": "storage",
            "storeroom": "storage",
            "utility": "storage",
            "hallway": "hallway",
            "corridor": "hallway",
            "balcony": "balcony",
            "entry": "entrance",
            "entrance": "entrance",
        }

        if raw in alias:
            return alias[raw]

        if "収納" in value or "納戸" in value:
            return "storage"
        if "wic" in raw or "walk in closet" in raw:
            return "walk_in_closet"
        if "wash" in raw and "bath" in raw:
            return "washroom"
        if "wash" in raw:
            return "washroom"
        if "bath" in raw:
            return "bathroom"
        if "toilet" in raw or "wc" in raw:
            return "toilet"
        if "玄関" in value or "genkan" in raw:
            return "entrance"
        if "洋室" in value:
            return "bedroom"
        if "western" in raw:
            return "bedroom"
        if "ldk" in raw:
            return "living_room"
        if "living" in raw and "dining" in raw and "kitchen" in raw:
            return "living_room"
        if raw == "dk" or raw.startswith("dk ") or raw.endswith(" dk"):
            return "dining_kitchen"
        if "dining" in raw and "kitchen" in raw:
            return "dining_kitchen"
        if "living" in raw:
            return "living_room"
        if "bed" in raw:
            return "bedroom"
        if "kitchen" in raw:
            return "kitchen"
        if "closet" in raw:
            return "closet"
        if "hall" in raw or "corridor" in raw:
            return "hallway"
        if "balcony" in raw:
            return "balcony"
        if "storage" in raw or "storeroom" in raw:
            return "storage"
        if "entry" in raw or "entrance" in raw:
            return "entrance"
        return "unknown"

    def _normalize_position(self, value: str | None) -> str:
        if not value:
            return "unknown"

        raw = self._normalize_text(value)
        if raw in self.NORMALIZED_POSITIONS:
            return raw
        if "top" in raw and "left" in raw:
            return "top_left"
        if "top" in raw and "right" in raw:
            return "top_right"
        if "bottom" in raw and "left" in raw:
            return "bottom_left"
        if "bottom" in raw and "right" in raw:
            return "bottom_right"
        if "top" in raw:
            return "top"
        if "bottom" in raw:
            return "bottom"
        if "left" in raw:
            return "left"
        if "right" in raw:
            return "right"
        if "center" in raw or "centre" in raw or "middle" in raw:
            return "center"
        return "unknown"

    def _infer_apartment_type(self, rooms: Iterable[RoomInfo]) -> str:
        room_types = [room.type for room in rooms]
        bedroom_count = sum(1 for room_type in room_types if room_type == "bedroom")
        has_living = any(room_type == "living_room" for room_type in room_types)
        has_dk = any(room_type == "dining_kitchen" for room_type in room_types)
        has_kitchen = any(room_type == "kitchen" for room_type in room_types)

        if bedroom_count == 2 and (has_living or has_dk or has_kitchen):
            return "2LDK"
        if bedroom_count == 1:
            if has_living:
                return "1LDK"
            if has_dk:
                return "1DK"
            if has_kitchen:
                return "1K"
        if bedroom_count == 0 and has_kitchen:
            return "1K"
        return "unknown"

    @staticmethod
    def _normalize_text(value: str) -> str:
        normalized = (
            value.strip()
            .lower()
            .replace("／", " ")
            .replace("/", " ")
            .replace("-", " ")
            .replace("_", " ")
            .replace("(", " ")
            .replace(")", " ")
            .replace(",", " ")
            .replace(".", " ")
            .replace(":", " ")
        )
        return " ".join(normalized.split())

    @staticmethod
    def _safe_string(value) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def _floorplan_design_prompt(self) -> str:
        return (
            "Analyze the uploaded Japanese apartment floorplan image and return JSON only.\n"
            "Return a top-level object with keys: analysis and furniture_plan.\n"
            "analysis must include rooms with room_type, room_name, bounding_box, doors, windows, and fixtures when visible.\n"
            "furniture_plan must include rooms with room_type, room_name, and furniture_items.\n"
            "Preserve spatial positions as accurately as possible.\n"
            "Do not invent missing rooms or features.\n"
            "If uncertain, use null or empty lists.\n"
            "Use normalized room types where possible: living_room, bedroom, kitchen, dining_kitchen, bathroom, toilet, washroom, closet, walk_in_closet, entrance, balcony, hallway, storage, unknown.\n"
            "Use normalized positions where possible: top, bottom, left, right, center, top_left, top_right, bottom_left, bottom_right, unknown.\n"
            "Furniture planning rules: furniture must fit inside existing rooms, not cross walls or doors, keep circulation clear, and avoid inventing rooms.\n"
            "Bedrooms should include bed and storage. Living room should include seating, table, and TV shelf. Kitchen/dining should include compact dining furniture if space allows.\n"
            "Entrance should include shoe cabinet or rug. Bathroom/toilet/washroom should keep fixtures visible and may include only small storage or plants.\n"
            "Closets/WIC should contain shelves or wardrobe elements. Balcony, if present, may include plants or a chair.\n"
            "For small rooms, use compact furniture. For large rooms, use richer furniture.\n"
            "For each furniture item, include furniture_type and a short position_hint such as against bottom wall, near balcony door, center of room, next to closet, or beside kitchen counter.\n"
            "Do not estimate furniture relative_x, relative_y, or rotation values.\n"
            "Return valid JSON only. No markdown."
        )

    def _floorplan_semantic_prompt(self) -> str:
        return (
            "Analyze the uploaded Japanese apartment floorplan image and return JSON only.\n"
            "Return a single object matching this structure: apartment_type, layout_description, rooms, doors, windows, balcony, constraints.\n"
            "Focus only on semantic floorplan analysis.\n"
            "Do not generate a furniture plan.\n"
            "Do not estimate furniture items.\n"
            "Preserve spatial positions as accurately as possible.\n"
            "Identify room types, approximate room positions, likely doors, likely windows, balcony presence, and layout constraints.\n"
            "Use a normalized 1200x1200 canvas for approximate geometry.\n"
            "Coordinate system: origin is top-left, x increases left-to-right, y increases top-to-bottom, and all coordinates must be integers from 0 to 1199.\n"
            "For each visible room, include: id, type, label_original, label_english, position, approx_bbox, polygon, confidence, geometry_confidence, geometry_notes.\n"
            "For doors, windows, and balcony, include approx_bbox, polygon, confidence, geometry_confidence, and geometry_notes when visible.\n"
            "approx_bbox must use x_min, y_min, x_max, y_max. Estimate approximate_bbox only from the visible floorplan.\n"
            "If uncertain, set approx_bbox to null and explain uncertainty in geometry_notes.\n"
            "Do not invent precise geometry if uncertain.\n"
            "Do not invent missing rooms or features.\n"
            "If uncertain, use null or empty lists.\n"
            "Use normalized room types where possible: living_room, bedroom, kitchen, dining_kitchen, bathroom, toilet, washroom, closet, walk_in_closet, entrance, balcony, hallway, storage, unknown.\n"
            "Use normalized positions where possible: top, bottom, left, right, center, top_left, top_right, bottom_left, bottom_right, unknown.\n"
            "Use approved English labels where possible: Living Room, Kitchen, Closet, Toilet, Entrance, Bed Room, Bath Room, Wash Room, Dining Kitchen, Balcony, Hallway, Storage, Unknown.\n"
            "Return valid JSON only. No markdown."
        )

    def _interior_semantic_prompt(self) -> str:
        return (
            "Analyze the uploaded interior reference photo and return JSON only.\n"
            "This is semantic analysis only. Do not generate images.\n"
            "Return keys: room_context, floor_color_category, detected_objects, dominant_colors, dominant_materials, bed, sofa, notes.\n"
            "floor_color_category must be one of: white, light_brown, dark_brown, unknown.\n"
            "For bed and sofa, focus on visible semantics only.\n"
            "Bed and sofa base colors should be normalized to white when present.\n"
            "Estimate pillow or cushion colors when visible.\n"
            "Infer bed type only from visible pillow count using: 1 pillow = single_bed, 2 pillows = semi_double_bed, 3-4 pillows = double_bed.\n"
            "Do not invent objects that are not visible.\n"
            "Return valid JSON only. No markdown."
        )

    def _style_reference_semantic_prompt(self, reference_type: str) -> str:
        reference_guidance = {
            "ideal": "Extract positive watercolor cues to emulate.",
            "acceptable": "Extract cues that are acceptable but not necessarily ideal.",
            "ng": "Extract cues to avoid in later watercolor rendering.",
        }.get(reference_type, "Analyze this style reference image.")
        return (
            "Analyze the uploaded watercolor style reference image and return JSON only.\n"
            "This is semantic style analysis only. Do not generate images.\n"
            f"Reference type: {reference_type}. {reference_guidance}\n"
            "Return keys: watercolor_strength, linework_style, palette_keywords, positive_cues, avoid_cues, notes.\n"
            "Focus on palette, line clarity, texture density, readability, and overall watercolor feel.\n"
            "Return valid JSON only. No markdown."
        )

    def _parse_floorplan_design_json(
        self, response_text: str, provider: str
    ) -> tuple[FloorplanAnalysis, FurniturePlan | None]:
        cleaned = self._strip_json_fences(response_text)
        try:
            payload = json.loads(cleaned)
        except Exception:
            try:
                combined = FloorplanDesignAnalysis.model_validate_json(cleaned)
                return self._normalize_design_result(combined)
            except Exception:
                try:
                    analysis = FloorplanAnalysis.model_validate_json(cleaned)
                    return self.normalize_floorplan_analysis(analysis), None
                except Exception:
                    raise HTTPException(
                        status_code=502,
                        detail=f"{provider} returned invalid JSON for floorplan analysis.",
                    )

        try:
            return self._parse_floorplan_design_payload(payload)
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"{provider} returned invalid JSON for floorplan analysis: {exc}",
            ) from exc

    def _parse_floorplan_design_payload(
        self, payload: dict
    ) -> tuple[FloorplanAnalysis, FurniturePlan | None]:
        if not isinstance(payload, dict):
            raise TypeError("floorplan payload must be a JSON object")

        if "analysis" in payload or "furniture_plan" in payload:
            analysis_payload = payload.get("analysis")
            furniture_payload = payload.get("furniture_plan")
            if not isinstance(analysis_payload, dict):
                raise TypeError("combined analysis payload must include an analysis object")
            analysis = FloorplanAnalysis.model_validate(self._coerce_floorplan_payload(analysis_payload))
            furniture_plan = None
            if isinstance(furniture_payload, dict):
                furniture_plan = FurniturePlan.model_validate(self._coerce_furniture_plan_payload(furniture_payload))
            elif furniture_payload is not None:
                raise TypeError("furniture_plan must be an object when provided")
            return self.normalize_floorplan_analysis(analysis), self._normalize_furniture_plan(furniture_plan, analysis) if furniture_plan else None

        analysis = FloorplanAnalysis.model_validate(self._coerce_floorplan_payload(payload))
        return self.normalize_floorplan_analysis(analysis), None

    def _normalize_design_result(
        self, combined: FloorplanDesignAnalysis
    ) -> tuple[FloorplanAnalysis, FurniturePlan | None]:
        analysis = self.normalize_floorplan_analysis(combined.analysis)
        furniture_plan = combined.furniture_plan
        if furniture_plan is None:
            return analysis, None
        return analysis, self._normalize_furniture_plan(furniture_plan, analysis)

    def _coerce_furniture_plan_payload(self, payload: dict) -> dict:
        if not isinstance(payload, dict):
            raise TypeError("furniture plan payload must be a JSON object")

        def coerce_item(item: dict) -> dict:
            if not isinstance(item, dict):
                return {
                    "item": str(item),
                    "room": "unknown",
                    "size": None,
                    "position_hint": None,
                    "reason": None,
                    "relative_x": None,
                    "relative_y": None,
                    "rotation": None,
                }
            return {
                "item": item.get("item") or item.get("furniture_type") or item.get("name") or item.get("label") or "unknown",
                "room": item.get("room") or item.get("room_type") or "unknown",
                "size": item.get("size"),
                "position_hint": item.get("position_hint") or item.get("position"),
                "reason": item.get("reason"),
                "relative_x": _coerce_float(item.get("relative_x")),
                "relative_y": _coerce_float(item.get("relative_y")),
                "rotation": _coerce_float(item.get("rotation")),
            }

        def coerce_room_plan(room_plan: dict) -> dict:
            if not isinstance(room_plan, dict):
                return {
                    "room_type": str(room_plan),
                    "room_name": None,
                    "room_position": None,
                    "items": [],
                }
            items = room_plan.get("items") or room_plan.get("furniture_items") or room_plan.get("furniture") or []
            return {
                "room_type": room_plan.get("room_type") or room_plan.get("room") or room_plan.get("type") or "unknown",
                "room_name": room_plan.get("room_name") or room_plan.get("name") or room_plan.get("label"),
                "room_position": room_plan.get("room_position") or room_plan.get("position") or room_plan.get("location"),
                "items": [coerce_item(item) for item in items],
            }

        global_rules = payload.get("global_rules")
        if global_rules is None:
            global_rules = []
        elif isinstance(global_rules, str):
            global_rules = [global_rules]
        elif isinstance(global_rules, list):
            global_rules = [str(item) for item in global_rules if item is not None]
        else:
            global_rules = [str(global_rules)]

        return {
            "style": payload.get("style") or payload.get("interior_style") or "unspecified",
            "target_user": payload.get("target_user"),
            "budget_level": payload.get("budget_level"),
            "room_plans": [
                coerce_room_plan(room_plan)
                for room_plan in (payload.get("room_plans") or payload.get("rooms") or [])
            ],
            "global_rules": global_rules,
        }

    def _normalize_furniture_plan(
        self,
        furniture_plan: FurniturePlan | None,
        analysis: FloorplanAnalysis,
    ) -> FurniturePlan | None:
        if furniture_plan is None:
            return None

        valid_rooms = {room.type for room in analysis.rooms}
        normalized_room_plans: list[RoomFurniturePlan] = []
        for room_plan in furniture_plan.room_plans:
            normalized_room_type = self._normalize_room_type(room_plan.room_type)
            normalized_room_position = self._normalize_position(room_plan.room_position)
            items: list[FurnitureItem] = []
            for item in room_plan.items:
                item_room = self._normalize_room_type(item.room)
                if item_room == "unknown" and normalized_room_type != "unknown":
                    item_room = normalized_room_type
                if item_room not in valid_rooms and normalized_room_type in valid_rooms:
                    item_room = normalized_room_type
                items.append(
                    FurnitureItem(
                        item=item.item,
                        room=item_room,
                        size=item.size,
                        position_hint=item.position_hint,
                        reason=item.reason,
                        relative_x=None,
                        relative_y=None,
                        rotation=None,
                    )
                )
            normalized_room_plans.append(
                RoomFurniturePlan(
                    room_type=normalized_room_type,
                    room_name=room_plan.room_name,
                    room_position=normalized_room_position,
                    items=items,
                )
            )

        return FurniturePlan(
            style=furniture_plan.style,
            target_user=furniture_plan.target_user,
            budget_level=furniture_plan.budget_level,
            room_plans=normalized_room_plans,
            global_rules=furniture_plan.global_rules,
        )

    @staticmethod
    def _strip_json_fences(text: str) -> str:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`").strip()
            if cleaned.lower().startswith("json"):
                cleaned = cleaned[4:].strip()
        return cleaned

    @staticmethod
    def _coerce_floorplan_payload(payload: dict) -> dict:
        if not isinstance(payload, dict):
            raise TypeError("floorplan payload must be a JSON object")

        def coerce_list(value) -> list[str]:
            if value is None:
                return []
            if isinstance(value, str):
                return [value]
            if isinstance(value, list):
                return [str(item) for item in value if item is not None]
            return [str(value)]

        def coerce_room(room: dict) -> dict:
            if not isinstance(room, dict):
                return {
                    "type": str(room),
                    "room_name": None,
                    "position": None,
                    "size": None,
                    "bounding_box": None,
                    "approx_bbox": None,
                    "polygon": None,
                    "confidence": 0.0,
                    "geometry_confidence": 0.0,
                    "geometry_notes": [],
                    "connected_to": [],
                }
            room = room or {}
            connected_to = (
                room.get("connected_to")
                or room.get("connects_to")
                or room.get("labels")
                or room.get("connections")
                or room.get("adjacent_rooms")
                or []
            )
            return {
                "type": room.get("type") or room.get("room_type") or room.get("label") or room.get("name") or "unknown",
                "room_name": room.get("room_name") or room.get("name") or room.get("label_original") or room.get("label"),
                "position": room.get("position") or room.get("pos") or room.get("location"),
                "size": room.get("size"),
                "bounding_box": _coerce_bbox(room.get("bounding_box") or room.get("bbox") or room.get("bounds")),
                "approx_bbox": _coerce_bbox(
                    room.get("approx_bbox")
                    or room.get("approximate_bbox")
                    or room.get("bounding_box")
                    or room.get("bbox")
                    or room.get("bounds")
                ),
                "polygon": _coerce_polygon(room.get("polygon")),
                "confidence": _coerce_float(room.get("confidence")) or 0.0,
                "geometry_confidence": _coerce_float(room.get("geometry_confidence")) or 0.0,
                "geometry_notes": coerce_list(room.get("geometry_notes")),
                "connected_to": coerce_list(connected_to),
            }

        def coerce_door(door: dict) -> dict:
            if not isinstance(door, dict):
                return {
                    "position": None,
                    "connects": [],
                    "bounding_box": None,
                    "approx_bbox": None,
                    "polygon": None,
                    "confidence": 0.0,
                    "geometry_confidence": 0.0,
                    "geometry_notes": [],
                }
            door = door or {}
            connects = (
                door.get("connects")
                or door.get("connects_to")
                or door.get("connected_to")
                or door.get("labels")
                or door.get("connections")
                or []
            )
            return {
                "position": door.get("position") or door.get("pos") or door.get("location"),
                "connects": coerce_list(connects),
                "bounding_box": _coerce_bbox(door.get("bounding_box") or door.get("bbox")),
                "approx_bbox": _coerce_bbox(door.get("approx_bbox") or door.get("approximate_bbox") or door.get("bbox") or door.get("bounding_box")),
                "polygon": _coerce_polygon(door.get("polygon")),
                "confidence": _coerce_float(door.get("confidence")) or 0.0,
                "geometry_confidence": _coerce_float(door.get("geometry_confidence")) or 0.0,
                "geometry_notes": coerce_list(door.get("geometry_notes")),
            }

        def coerce_window(window: dict) -> dict:
            if not isinstance(window, dict):
                return {
                    "position": None,
                    "room": None,
                    "bounding_box": None,
                    "approx_bbox": None,
                    "polygon": None,
                    "confidence": 0.0,
                    "geometry_confidence": 0.0,
                    "geometry_notes": [],
                }
            window = window or {}
            return {
                "position": window.get("position") or window.get("pos") or window.get("location"),
                "room": window.get("room") or window.get("label") or window.get("belongs_to"),
                "bounding_box": _coerce_bbox(window.get("bounding_box") or window.get("bbox")),
                "approx_bbox": _coerce_bbox(window.get("approx_bbox") or window.get("approximate_bbox") or window.get("bbox") or window.get("bounding_box")),
                "polygon": _coerce_polygon(window.get("polygon")),
                "confidence": _coerce_float(window.get("confidence")) or 0.0,
                "geometry_confidence": _coerce_float(window.get("geometry_confidence")) or 0.0,
                "geometry_notes": coerce_list(window.get("geometry_notes")),
            }

        balcony = payload.get("balcony")
        if balcony is not None and isinstance(balcony, dict):
            balcony = {
                "exists": bool(balcony.get("exists", balcony.get("present", False))),
                "position": balcony.get("position") or balcony.get("pos") or balcony.get("location"),
                "bounding_box": _coerce_bbox(balcony.get("bounding_box") or balcony.get("bbox")),
                "approx_bbox": _coerce_bbox(balcony.get("approx_bbox") or balcony.get("approximate_bbox") or balcony.get("bbox") or balcony.get("bounding_box")),
                "polygon": _coerce_polygon(balcony.get("polygon")),
                "confidence": _coerce_float(balcony.get("confidence")) or 0.0,
                "geometry_confidence": _coerce_float(balcony.get("geometry_confidence")) or 0.0,
                "geometry_notes": coerce_list(balcony.get("geometry_notes")),
            }
        elif balcony is None:
            balcony = None
        else:
            balcony = {
                "exists": bool(balcony),
                "position": None,
                "bounding_box": None,
                "approx_bbox": None,
                "polygon": None,
                "confidence": 0.0,
                "geometry_confidence": 0.0,
                "geometry_notes": [],
            }

        constraints = payload.get("constraints")
        if constraints is None:
            constraints = []
        elif isinstance(constraints, str):
            constraints = [constraints]
        elif isinstance(constraints, list):
            constraints = [str(item) for item in constraints if item is not None]
        else:
            constraints = [str(constraints)]

        return {
            "apartment_type": payload.get("apartment_type") or payload.get("apartmentType"),
            "layout_description": payload.get("layout_description")
            or payload.get("layoutDescription")
            or payload.get("description")
            or "",
            "rooms": [coerce_room(room) for room in (payload.get("rooms") or [])],
            "doors": [coerce_door(door) for door in (payload.get("doors") or [])],
            "windows": [coerce_window(window) for window in (payload.get("windows") or [])],
            "balcony": balcony,
            "constraints": constraints,
        }

    @staticmethod
    def _mime_type_for_path(image_path: Path) -> str:
        mime_type = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
        }.get(image_path.suffix.lower())

        if mime_type is None:
            raise HTTPException(status_code=415, detail="unsupported floorplan image type")
        return mime_type

    @staticmethod
    def _extract_gemini_text(response) -> str:
        text = getattr(response, "text", None)
        if isinstance(text, str) and text.strip():
            return text.strip()

        parsed = getattr(response, "parsed", None)
        if parsed is not None:
            if hasattr(parsed, "model_dump"):
                return json.dumps(parsed.model_dump(mode="json"), ensure_ascii=False)
            if isinstance(parsed, dict):
                return json.dumps(parsed, ensure_ascii=False)
            return str(parsed)

        candidates = getattr(response, "candidates", None)
        if candidates:
            try:
                content = candidates[0].content
                parts = getattr(content, "parts", None) or []
                collected: list[str] = []
                for part in parts:
                    part_text = getattr(part, "text", None)
                    if part_text:
                        collected.append(part_text)
                if collected:
                    return "".join(collected).strip()
            except Exception:
                pass

        return ""


vision_analyzer = VisionAnalyzer()
