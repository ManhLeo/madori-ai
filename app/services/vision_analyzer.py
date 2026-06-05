from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Iterable

from fastapi import HTTPException
from openai import OpenAI

from app.config import get_settings
from app.schemas import (
    BalconyInfo,
    DoorInfo,
    FloorplanAnalysis,
    FloorplanDesignAnalysis,
    FurnitureItem,
    FurniturePlan,
    RoomFurniturePlan,
    RoomInfo,
    WindowInfo,
)


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
    return coords


class VisionAnalyzer:
    DEFAULT_OPENROUTER_MODELS = [
        "moonshotai/kimi-k2.6:free",
        "google/gemma-4-26b-a4b-it:free",
        "google/gemma-4-31b-it:free",
        "nvidia/nemotron-nano-12b-v2-vl:free",
    ]

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

    def analyze_floorplan_design_with_raw(
        self, image_path: Path
    ) -> tuple[FloorplanAnalysis, FurniturePlan | None, dict]:
        settings = get_settings()
        if settings.use_gemini_analysis:
            return self.analyze_floorplan_gemini(image_path)
        if settings.use_openrouter_analysis:
            analysis, raw_payload = self._analyze_floorplan_openrouter_with_raw(image_path)
            return analysis, None, raw_payload
        if settings.use_openai_analysis:
            analysis, raw_payload = self._analyze_floorplan_openai_with_raw(image_path)
            return analysis, None, raw_payload

        analysis = self.analyze_floorplan_stub(image_path)
        return analysis, None, {
            "provider": "stub",
            "analysis": analysis.model_dump(mode="json"),
        }

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

        try:
            response = client.models.generate_content(
                model=settings.gemini_model,
                contents=[
                    types.Content(
                        role="user",
                        parts=[
                            types.Part.from_text(text=prompt),
                            types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                        ],
                    )
                ],
                config=types.GenerateContentConfig(
                    temperature=0,
                    response_mime_type="application/json",
                    response_schema=schema,
                ),
            )
            response_text = self._extract_gemini_text(response)
        except Exception as structured_exc:
            try:
                response = client.models.generate_content(
                    model=settings.gemini_model,
                    contents=[
                        types.Content(
                            role="user",
                            parts=[
                                types.Part.from_text(text=prompt),
                                types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                            ],
                        )
                    ],
                    config=types.GenerateContentConfig(
                        temperature=0,
                        response_mime_type="application/json",
                    ),
                )
                response_text = self._extract_gemini_text(response)
            except Exception as exc:
                raise HTTPException(status_code=502, detail=f"Gemini floorplan analysis failed: {exc}") from exc

            if not response_text:
                raise HTTPException(
                    status_code=502,
                    detail="Gemini returned an empty floorplan analysis response.",
                )

            analysis, furniture_plan = self._parse_floorplan_design_json(response_text, provider="Gemini")
            raw_payload = {
                "provider": "gemini",
                "model": settings.gemini_model,
                "response_text": response_text,
                "parse_mode": "json_only",
                "analysis": analysis.model_dump(mode="json"),
                "furniture_plan": furniture_plan.model_dump(mode="json") if furniture_plan else None,
            }
            return analysis, furniture_plan, raw_payload

        if not response_text:
            raise HTTPException(
                status_code=502,
                detail="Gemini returned an empty floorplan analysis response.",
            )

        analysis, furniture_plan = self._parse_floorplan_design_json(response_text, provider="Gemini")
        raw_payload = {
            "provider": "gemini",
            "model": settings.gemini_model,
            "response_text": response_text,
            "parse_mode": "structured_json",
            "analysis": analysis.model_dump(mode="json"),
            "furniture_plan": furniture_plan.model_dump(mode="json") if furniture_plan else None,
        }
        return analysis, furniture_plan, raw_payload

    def analyze_floorplan_openai(self, image_path: Path) -> FloorplanAnalysis:
        analysis, _ = self._analyze_floorplan_openai_with_raw(image_path)
        return analysis

    def analyze_floorplan_openrouter(self, image_path: Path) -> FloorplanAnalysis:
        analysis, _ = self._analyze_floorplan_openrouter_with_raw(image_path)
        return analysis

    def _analyze_floorplan_openai_with_raw(self, image_path: Path) -> tuple[FloorplanAnalysis, dict]:
        settings = get_settings()
        if not settings.openai_api_key:
            raise HTTPException(
                status_code=500,
                detail="OpenAI floorplan analysis is enabled but OPENAI_API_KEY is missing.",
            )

        if not image_path.exists():
            raise HTTPException(status_code=404, detail="floorplan image not found")

        client = OpenAI(api_key=settings.openai_api_key)
        image_url = self._build_data_url(image_path)
        instructions = self._floorplan_prompt()

        try:
            response = client.responses.parse(
                model=settings.openai_vision_model,
                instructions=instructions,
                input=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": (
                                    "Inspect the floorplan image and return a structured floorplan analysis matching the schema. "
                                    "Keep the descriptions grounded in visible evidence only."
                                ),
                            },
                            {
                                "type": "input_image",
                                "image_url": image_url,
                                "detail": "high",
                            },
                        ],
                    }
                ],
                text_format=FloorplanAnalysis,
                temperature=0,
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"OpenAI floorplan analysis failed: {exc}") from exc

        parsed = response.output_parsed
        if parsed is None:
            raise HTTPException(
                status_code=502,
                detail="OpenAI returned no structured floorplan analysis.",
            )

        if isinstance(parsed, FloorplanAnalysis):
            analysis = parsed
        else:
            try:
                analysis = FloorplanAnalysis.model_validate(parsed)
            except Exception as exc:
                raise HTTPException(
                    status_code=502,
                    detail=f"OpenAI returned an invalid floorplan analysis payload: {exc}",
                ) from exc

        raw_payload = {
            "provider": "openai",
            "model": settings.openai_vision_model,
            "response_text": getattr(response, "output_text", None) or self._model_dump_json(analysis),
            "parse_mode": "structured_json",
        }
        return analysis, raw_payload

    def _analyze_floorplan_openrouter_with_raw(self, image_path: Path) -> tuple[FloorplanAnalysis, dict]:
        settings = get_settings()
        if not settings.openrouter_api_key:
            raise HTTPException(
                status_code=500,
                detail="OpenRouter floorplan analysis is enabled but OPENROUTER_API_KEY is missing.",
            )

        if not image_path.exists():
            raise HTTPException(status_code=404, detail="floorplan image not found")

        errors: list[str] = []
        for model_name in self._openrouter_model_candidates(settings):
            try:
                return self._analyze_floorplan_openrouter_with_model(image_path, model_name, settings.openrouter_api_key)
            except HTTPException as exc:
                errors.append(f"{model_name}: {exc.detail}")
            except Exception as exc:
                errors.append(f"{model_name}: {exc}")

        raise HTTPException(
            status_code=502,
            detail="OpenRouter floorplan analysis failed across all fallback models: " + " | ".join(errors),
        )

    def _analyze_floorplan_openrouter_with_model(
        self,
        image_path: Path,
        model_name: str,
        api_key: str,
    ) -> tuple[FloorplanAnalysis, dict]:
        client = OpenAI(
            api_key=api_key,
            base_url="https://openrouter.ai/api/v1",
            default_headers={
                "HTTP-Referer": "http://localhost",
                "X-OpenRouter-Title": get_settings().app_name,
            },
        )
        image_url = self._build_data_url(image_path)
        instructions = self._floorplan_prompt()

        try:
            completion = client.chat.completions.parse(
                model=model_name,
                messages=[
                    {"role": "system", "content": instructions},
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    "Inspect the floorplan image and return a structured floorplan analysis matching the schema. "
                                    "Keep the descriptions grounded in visible evidence only."
                                ),
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": image_url,
                                    "detail": "high",
                                },
                            },
                        ],
                    },
                ],
                response_format=FloorplanAnalysis,
                temperature=0,
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"OpenRouter floorplan analysis failed: {exc}") from exc

        try:
            message = completion.choices[0].message
            if message.parsed is not None:
                if isinstance(message.parsed, FloorplanAnalysis):
                    analysis = message.parsed
                else:
                    analysis = FloorplanAnalysis.model_validate(message.parsed)
            else:
                content = message.content
                if not content:
                    raise ValueError("empty response content")
                if not isinstance(content, str):
                    content = json.dumps(content, ensure_ascii=False)
                analysis = FloorplanAnalysis.model_validate_json(content)
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"OpenRouter returned an invalid floorplan analysis payload: {exc}",
            ) from exc

        raw_payload = {
            "provider": "openrouter",
            "model": model_name,
            "response_text": self._extract_openrouter_text(completion),
            "parse_mode": "structured_json",
        }
        return analysis, raw_payload

    def _openrouter_model_candidates(self, settings) -> list[str]:
        if settings.openrouter_vision_models:
            candidates = self._split_model_list(settings.openrouter_vision_models)
        else:
            candidates = [settings.openrouter_vision_model, *self.DEFAULT_OPENROUTER_MODELS]

        return self._dedupe_preserve_order(candidates)

    @staticmethod
    def _split_model_list(models: str) -> list[str]:
        return [model.strip() for model in models.split(",") if model.strip()]

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
            )
            for door in analysis.doors
        ]
        normalized_windows = [
            WindowInfo(
                position=self._normalize_position(window.position),
                room=self._normalize_room_type(window.room) if window.room else None,
            )
            for window in analysis.windows
        ]

        normalized_balcony = None
        if analysis.balcony is not None:
            normalized_balcony = BalconyInfo(
                exists=analysis.balcony.exists,
                position=self._normalize_position(analysis.balcony.position),
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
        if room.bounding_box:
            inferred = self._infer_position_from_bbox(room.bounding_box)
            if inferred != "unknown":
                return inferred
        return "unknown"

    def _infer_position_from_bbox(self, bbox: list[float] | None) -> str:
        if not bbox or len(bbox) < 4:
            return "unknown"
        try:
            x1, y1, x2, y2 = [float(value) for value in bbox[:4]]
        except (TypeError, ValueError):
            return "unknown"

        center_x = (x1 + x2) / 2
        center_y = (y1 + y2) / 2

        # Works best for normalized [0, 1] coordinates.
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
                "room_name": room.get("room_name") or room.get("name") or room.get("label"),
                "position": room.get("position") or room.get("pos") or room.get("location"),
                "size": room.get("size"),
                "bounding_box": _coerce_bbox(room.get("bounding_box") or room.get("bbox") or room.get("bounds")),
                "connected_to": coerce_list(connected_to),
            }

        def coerce_door(door: dict) -> dict:
            if not isinstance(door, dict):
                return {
                    "position": None,
                    "connects": [],
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
            }

        def coerce_window(window: dict) -> dict:
            if not isinstance(window, dict):
                return {
                    "position": None,
                    "room": None,
                }
            window = window or {}
            return {
                "position": window.get("position") or window.get("pos") or window.get("location"),
                "room": window.get("room") or window.get("label") or window.get("belongs_to"),
            }

        balcony = payload.get("balcony")
        if balcony is not None and isinstance(balcony, dict):
            balcony = {
                "exists": bool(balcony.get("exists", balcony.get("present", False))),
                "position": balcony.get("position") or balcony.get("pos") or balcony.get("location"),
            }
        elif balcony is None:
            balcony = None
        else:
            balcony = {"exists": bool(balcony), "position": None}

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
    def _build_data_url(image_path: Path) -> str:
        mime_type = VisionAnalyzer._mime_type_for_path(image_path)
        encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
        return f"data:{mime_type};base64,{encoded}"

    @staticmethod
    def _extract_openrouter_text(completion) -> str:
        try:
            message = completion.choices[0].message
            content = message.content
            if isinstance(content, str):
                return content
            if content is None:
                return ""
            return json.dumps(content, ensure_ascii=False)
        except Exception:
            return ""

    @staticmethod
    def _model_dump_json(analysis: FloorplanAnalysis) -> str:
        return analysis.model_dump_json(indent=2)

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
