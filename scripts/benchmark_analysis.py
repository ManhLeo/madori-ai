from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from datetime import datetime
from pathlib import Path


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


def _add_repo_root_to_path() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    repo_root_str = str(repo_root)
    if repo_root_str not in sys.path:
        sys.path.insert(0, repo_root_str)


def _collect_images(input_dir: Path) -> list[Path]:
    images = [path for path in input_dir.rglob("*") if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS]
    return sorted(images, key=lambda path: str(path).lower())


def _safe_model_dump_json(payload) -> str:
    if payload is None:
        return ""
    if hasattr(payload, "model_dump_json"):
        return payload.model_dump_json(indent=2)
    return json.dumps(payload, indent=2, ensure_ascii=False)


def _safe_model_dump(payload):
    if payload is None:
        return None
    if hasattr(payload, "model_dump"):
        return payload.model_dump(mode="json")
    return payload


def _join(values) -> str:
    if not values:
        return ""
    return ", ".join(str(value) for value in values)


def _build_summary_lines(
    *,
    filename: str,
    status: str,
    provider: str,
    apartment_type: str,
    room_count: int,
    room_types: str,
    room_positions: str,
    balcony_exists: str,
    balcony_position: str,
    door_count: int,
    window_count: int,
    latency_seconds: float | None,
    error: str,
) -> str:
    lines = [
        f"filename: {filename}",
        f"status: {status}",
        f"provider: {provider}",
        f"apartment_type: {apartment_type}",
        f"room_count: {room_count}",
        f"room_types: {room_types}",
        f"room_positions: {room_positions}",
        f"balcony_exists: {balcony_exists}",
        f"balcony_position: {balcony_position}",
        f"door_count: {door_count}",
        f"window_count: {window_count}",
        f"latency_seconds: {latency_seconds if latency_seconds is not None else ''}",
    ]
    if error:
        lines.append(f"error: {error}")
    return "\n".join(lines) + "\n"


def main() -> int:
    _add_repo_root_to_path()

    from app.services.vision_analyzer import VisionAnalyzer

    parser = argparse.ArgumentParser(description="Benchmark floorplan analysis quality and stability.")
    parser.add_argument(
        "input_folder",
        nargs="?",
        default="inputs",
        help="Folder to scan for floorplan images. Defaults to 'inputs'.",
    )
    args = parser.parse_args()

    input_dir = Path(args.input_folder)
    if not input_dir.exists() or not input_dir.is_dir():
        print(f"Error: input folder does not exist or is not a directory: {input_dir}", file=sys.stderr)
        return 1

    images = _collect_images(input_dir)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = Path("benchmark_runs") / timestamp
    output_dir.mkdir(parents=True, exist_ok=True)

    analyzer = VisionAnalyzer()
    rows: list[dict[str, str]] = []
    latencies: list[float] = []
    success_count = 0
    failed_count = 0

    for image_path in images:
        stem = image_path.stem
        raw_path = output_dir / f"{stem}_analysis_raw.json"
        analysis_path = output_dir / f"{stem}_analysis.json"
        summary_path = output_dir / f"{stem}_summary.txt"
        furniture_path = output_dir / f"{stem}_furniture_plan.json"

        status = "failed"
        provider = ""
        apartment_type = ""
        room_count = 0
        room_types = ""
        room_positions = ""
        balcony_exists = ""
        balcony_position = ""
        door_count = 0
        window_count = 0
        latency_seconds: float | None = None
        error = ""
        raw_payload = {"error": "analysis not run"}
        normalized_payload = {"error": "analysis not run"}
        furniture_payload = {"error": "analysis not run"}

        start_time = time.perf_counter()
        try:
            if hasattr(analyzer, "analyze_floorplan_with_raw"):
                raw_analysis, gemini_furniture_plan, raw_payload = analyzer.analyze_floorplan_design_with_raw(image_path)
                normalized_analysis = analyzer.normalize_floorplan_analysis(raw_analysis)
                furniture_payload = (
                    gemini_furniture_plan.model_dump(mode="json") if gemini_furniture_plan is not None else None
                )
            else:
                normalized_analysis = analyzer.analyze_floorplan(image_path)
                raw_payload = _safe_model_dump(normalized_analysis)
                furniture_payload = None
            latency_seconds = time.perf_counter() - start_time

            normalized_payload = _safe_model_dump(normalized_analysis)
            provider = str(raw_payload.get("provider", "")) if isinstance(raw_payload, dict) else ""
            apartment_type = normalized_analysis.apartment_type or ""
            room_count = len(normalized_analysis.rooms)
            room_types = _join([room.type for room in normalized_analysis.rooms])
            room_positions = _join([room.position or "unknown" for room in normalized_analysis.rooms])
            balcony_exists = (
                "true" if normalized_analysis.balcony and normalized_analysis.balcony.exists else "false"
                if normalized_analysis.balcony is not None
                else ""
            )
            balcony_position = normalized_analysis.balcony.position if normalized_analysis.balcony else ""
            door_count = len(normalized_analysis.doors)
            window_count = len(normalized_analysis.windows)
            status = "success"
            success_count += 1
            latencies.append(latency_seconds)
        except Exception as exc:
            latency_seconds = time.perf_counter() - start_time
            error = str(exc)
            failed_count += 1
            if hasattr(exc, "detail"):
                error = str(getattr(exc, "detail"))

        raw_path.write_text(_safe_model_dump_json(raw_payload), encoding="utf-8")
        analysis_path.write_text(_safe_model_dump_json(normalized_payload), encoding="utf-8")
        if furniture_payload is not None:
            furniture_path.write_text(_safe_model_dump_json(furniture_payload), encoding="utf-8")
        summary_path.write_text(
            _build_summary_lines(
                filename=image_path.name,
                status=status,
                provider=provider,
                apartment_type=apartment_type,
                room_count=room_count,
                room_types=room_types,
                room_positions=room_positions,
                balcony_exists=balcony_exists,
                balcony_position=balcony_position,
                door_count=door_count,
                window_count=window_count,
                latency_seconds=latency_seconds,
                error=error,
            ),
            encoding="utf-8",
        )

        rows.append(
            {
                "filename": image_path.name,
                "status": status,
                "provider": provider,
                "apartment_type": apartment_type,
                "room_count": str(room_count),
                "room_types": room_types,
                "room_positions": room_positions,
                "balcony_exists": balcony_exists,
                "balcony_position": balcony_position,
                "door_count": str(door_count),
                "window_count": str(window_count),
                "latency_seconds": f"{latency_seconds:.4f}" if latency_seconds is not None else "",
                "error": error,
            }
        )

    summary_csv = output_dir / "summary.csv"
    with summary_csv.open("w", encoding="utf-8", newline="") as csv_file:
        writer = csv.DictWriter(
            csv_file,
            fieldnames=[
                "filename",
                "status",
                "provider",
                "apartment_type",
                "room_count",
                "room_types",
                "room_positions",
                "balcony_exists",
                "balcony_position",
                "door_count",
                "window_count",
                "latency_seconds",
                "error",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    average_latency = sum(latencies) / len(latencies) if latencies else 0.0
    print(f"total images: {len(images)}")
    print(f"success count: {success_count}")
    print(f"failed count: {failed_count}")
    print(f"average latency: {average_latency:.4f} seconds")
    print(f"output directory: {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
