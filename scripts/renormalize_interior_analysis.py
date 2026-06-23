from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.config import get_settings
from app.schemas.run import InteriorAnalysisSummary, InteriorStyleAnalysisArtifact, InteriorStyleReferenceAnalysisGroups
from app.services.furniture_placement_service import FurniturePlacementService
from app.services.interior_analysis_validation_service import InteriorAnalysisValidationService
from app.services.layout_creation_service import LayoutCreationService
from app.services.layout_validation_service import LayoutValidationService
from app.services.run_index_service import RunIndexService
from app.services.run_service import RunService
from app.services.vision_analyzer import VisionAnalyzer


@dataclass
class RepairContext:
    run_id: str
    run_dir: Path
    artifacts_dir: Path
    raw_source_name: str
    notes_char_split_fixed_count: int
    detected_objects_count_before: int
    detected_objects_count_after: int
    furniture_signals_summary: dict[str, list[str]]
    written_files: list[str]
    backup_dir: Path | None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Re-normalize old interior analysis runs locally.")
    parser.add_argument("--run-id", required=True, help="Run id to repair.")
    parser.add_argument("--validate", action="store_true", help="Rebuild interior_analysis_validated.json.")
    parser.add_argument("--recreate-layout", action="store_true", help="Rebuild layout_initial.json.")
    parser.add_argument("--validate-layout", action="store_true", help="Rebuild layout_validated.json.")
    parser.add_argument("--plan-furniture", action="store_true", help="Rebuild layout_furniture_planned.json.")
    parser.add_argument("--index", action="store_true", help="Rebuild artifact index and summaries.")
    parser.add_argument("--dry-run", action="store_true", help="Print what would change without writing files.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    settings = get_settings()
    run_service = RunService(settings.storage_runs_dir)

    try:
        metadata = run_service.load_metadata(args.run_id)
    except HTTPException as exc:
        print(f"Failed to load run metadata for {args.run_id}: {exc.detail}")
        return 1

    run_dir = settings.storage_runs_dir / args.run_id
    artifacts_dir = run_dir / "artifacts"
    if not run_dir.exists():
        print(f"Run directory not found: {run_dir}")
        return 1

    try:
        context, interior_artifact, validated_preview = _renormalize_interior_artifact(args.run_id, artifacts_dir)
    except FileNotFoundError as exc:
        print(str(exc))
        return 1

    print(f"run_id: {args.run_id}")
    print(f"source artifact used: {context.raw_source_name}")
    print(f"notes_char_split_fixed_count: {context.notes_char_split_fixed_count}")
    print(f"detected_objects_count_before: {context.detected_objects_count_before}")
    print(f"detected_objects_count_after: {context.detected_objects_count_after}")
    print(f"furniture_signals: {json.dumps(context.furniture_signals_summary, ensure_ascii=False)}")

    if args.dry_run:
        _print_dry_run_preview(args, artifacts_dir, interior_artifact, validated_preview, metadata)
        return 0

    backup_dir = _backup_existing_artifacts(artifacts_dir, args)
    if backup_dir:
        print(f"backup_dir: {backup_dir}")

    written_files: list[str] = []
    if _write_json_if_needed(artifacts_dir / "interior_analysis.json", interior_artifact.model_dump(mode="json")):
        written_files.append(str(artifacts_dir / "interior_analysis.json"))

    if args.validate:
        validation_service = InteriorAnalysisValidationService(settings.storage_dir, settings.storage_runs_dir)
        validated = validation_service.validate_run(metadata)
        metadata_updates = validation_service.build_metadata_updates(metadata, validated)
        run_service.apply_interior_validation_updates(metadata, metadata_updates)
        written_files.append(str(artifacts_dir / "interior_analysis_validated.json"))
        print(f"validated_room_keys: {json.dumps(sorted(validated.room_observations.keys()), ensure_ascii=False)}")
        print(f"validated_furniture_signals: {json.dumps(validated.furniture_signals, ensure_ascii=False)}")

    if args.recreate_layout:
        layout_service = LayoutCreationService(settings.storage_dir, settings.storage_runs_dir)
        metadata = run_service.load_metadata(args.run_id)
        layout = layout_service.create_initial_layout(metadata)
        metadata_updates = layout_service.build_metadata_updates(metadata, layout)
        run_service.apply_layout_creation_updates(metadata, metadata_updates)
        written_files.append(str(artifacts_dir / "layout_initial.json"))
        print(f"layout_furniture_count: {len(layout.furniture)}")

    if args.validate_layout:
        validation_service = LayoutValidationService(settings.storage_dir, settings.storage_runs_dir)
        metadata = run_service.load_metadata(args.run_id)
        layout_validated = validation_service.validate_layout(metadata)
        metadata_updates = validation_service.build_metadata_updates(metadata, layout_validated)
        run_service.apply_layout_validation_updates(metadata, metadata_updates)
        written_files.append(str(artifacts_dir / "layout_validated.json"))

    if args.plan_furniture:
        placement_service = FurniturePlacementService(settings.storage_dir, settings.storage_runs_dir)
        metadata = run_service.load_metadata(args.run_id)
        planned = placement_service.plan_furniture_placement(metadata)
        metadata_updates = placement_service.build_metadata_updates(metadata, planned)
        run_service.apply_furniture_placement_updates(metadata, metadata_updates)
        written_files.append(str(artifacts_dir / "layout_furniture_planned.json"))
        print(f"planned_furniture_count: {len(planned.furniture)}")
        print(f"planned_invalid_count: {int(planned.placement.get('invalid_count') or 0)}")

    if args.index:
        index_service = RunIndexService(settings.storage_dir, settings.storage_runs_dir)
        metadata = run_service.load_metadata(args.run_id)
        summary = index_service.index_run(metadata)
        run_service.update_metadata_summary(metadata, summary)
        written_files.extend(
            [
                str(artifacts_dir / "artifact_index.json"),
                str(artifacts_dir / "run_metadata_summary.json"),
            ]
        )

    if not args.validate and not args.recreate_layout and not args.validate_layout and not args.plan_furniture and not args.index:
        print("No follow-up flags provided. interior_analysis.json was normalized only.")

    print(f"written_files: {json.dumps(written_files, ensure_ascii=False)}")
    if backup_dir:
        print(f"backup_dir: {backup_dir}")
    return 0


def _renormalize_interior_artifact(
    run_id: str,
    artifacts_dir: Path,
) -> tuple[RepairContext, InteriorStyleAnalysisArtifact, object]:
    raw_path = artifacts_dir / "interior_analysis_raw.json"
    current_path = artifacts_dir / "interior_analysis.json"
    if raw_path.exists():
        source = json.loads(raw_path.read_text(encoding="utf-8"))
        raw_source_name = "interior_analysis_raw.json"
    elif current_path.exists():
        source = json.loads(current_path.read_text(encoding="utf-8"))
        raw_source_name = "interior_analysis.json"
    else:
        raise FileNotFoundError(
            f"No interior analysis artifact found for {run_id}. Expected interior_analysis_raw.json or interior_analysis.json."
        )

    analyzer = VisionAnalyzer()
    validation_service = InteriorAnalysisValidationService(get_settings().storage_dir, get_settings().storage_runs_dir)
    interior_photos, notes_fixed_count = _normalize_interior_photos_from_source(source, analyzer)
    style_groups, style_notes_fixed_count = _normalize_style_groups_from_source(source, analyzer)
    derived_profile = analyzer._derive_interior_style_profile(interior_photos, style_groups)
    summary = InteriorAnalysisSummary(
        provider=str(source.get("provider") or "gemini"),
        model=source.get("model"),
        interior_photo_count=len(interior_photos),
        style_reference_count=sum(len(getattr(style_groups, name)) for name in ("ideal", "acceptable", "ng")),
        preferred_floor_color=derived_profile.preferred_floor_color,
        inferred_bed_type=derived_profile.inferred_bed_type,
        accent_colors=derived_profile.accent_colors,
        style_positive_cues=derived_profile.style_positive_cues,
        style_avoid_cues=derived_profile.style_avoid_cues,
    )
    artifact = InteriorStyleAnalysisArtifact(
        run_id=run_id,
        generated_at=datetime.now(timezone.utc),
        provider=str(source.get("provider") or "gemini"),
        model=source.get("model"),
        interior_photos=interior_photos,
        style_references=style_groups,
        derived_profile=derived_profile,
        summary=summary,
        warnings=[str(item) for item in source.get("warnings") or [] if item is not None],
        errors=[str(item) for item in source.get("errors") or [] if item is not None],
    )
    validated_preview = validation_service.normalize_interior_analysis(artifact, run_id)
    detected_before = _count_detected_objects(source)
    detected_after = sum(len(photo.detected_objects) for photo in interior_photos)
    notes_fixed_total = notes_fixed_count + style_notes_fixed_count
    return (
        RepairContext(
            run_id=run_id,
            run_dir=artifacts_dir.parent,
            artifacts_dir=artifacts_dir,
            raw_source_name=raw_source_name,
            notes_char_split_fixed_count=notes_fixed_total,
            detected_objects_count_before=detected_before,
            detected_objects_count_after=detected_after,
            furniture_signals_summary=validated_preview.furniture_signals,
            written_files=[],
            backup_dir=None,
        ),
        artifact,
        validated_preview,
    )


def _normalize_interior_photos_from_source(source: dict, analyzer: VisionAnalyzer) -> tuple[list, int]:
    interior_photos_raw = source.get("interior_photos") or []
    normalized: list = []
    fixed_count = 0
    for item in interior_photos_raw:
        if not isinstance(item, dict):
            continue
        source_image = item.get("source_image") or {}
        payload = item.get("analysis")
        analysis_payload = item.get("analysis") if isinstance(item.get("analysis"), dict) else {}
        fixed_count += _count_char_split_fields(analysis_payload, ("notes",))

        response_text = item.get("response_text")
        if response_text:
            payload = analyzer._load_json_payload(response_text, "Gemini interior semantic analysis")
            record = analyzer._normalize_interior_photo_record(payload, source_image)
        elif isinstance(payload, dict):
            record = analyzer._normalize_interior_photo_record(payload, source_image)
        else:
            record = analyzer._normalize_interior_photo_record({}, source_image)
        normalized.append(record)
    return normalized, fixed_count


def _normalize_style_groups_from_source(source: dict, analyzer: VisionAnalyzer) -> tuple[InteriorStyleReferenceAnalysisGroups, int]:
    groups = InteriorStyleReferenceAnalysisGroups()
    fixed_count = 0
    style_sources = source.get("style_references") or {}
    for group_name in ("ideal", "acceptable", "ng"):
        records = []
        for item in style_sources.get(group_name, []) if isinstance(style_sources, dict) else []:
            if not isinstance(item, dict):
                continue
            source_image = item.get("source_image") or {}
            analysis_payload = item.get("analysis") if isinstance(item.get("analysis"), dict) else {}
            fixed_count += _count_char_split_fields(analysis_payload, ("notes", "positive_cues", "avoid_cues"))
            response_text = item.get("response_text")
            if response_text:
                payload = analyzer._load_json_payload(response_text, f"Gemini {group_name} style analysis")
                records.append(analyzer._normalize_style_reference_record(payload, group_name, source_image))
            elif isinstance(item.get("analysis"), dict):
                records.append(analyzer._normalize_style_reference_record(item["analysis"], group_name, source_image))
        setattr(groups, group_name, records)
    return groups, fixed_count


def _count_detected_objects(source: dict) -> int:
    count = 0
    for photo in source.get("interior_photos") or []:
        if isinstance(photo, dict):
            analysis = photo.get("analysis") or {}
            if isinstance(analysis, dict):
                count += len(analysis.get("detected_objects") or [])
    return count


def _count_char_split_fields(payload: dict, field_names: tuple[str, ...]) -> int:
    total = 0
    for field_name in field_names:
        value = payload.get(field_name)
        if isinstance(value, list) and value and all(isinstance(item, str) and len(item) == 1 for item in value):
            total += 1
    return total


def _backup_existing_artifacts(artifacts_dir: Path, args: argparse.Namespace) -> Path | None:
    if args.dry_run:
        return None
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = artifacts_dir / "_backup" / timestamp
    backed_up = False
    for filename in (
        "interior_analysis.json",
        "interior_analysis_validated.json",
        "layout_initial.json",
        "layout_validated.json",
        "layout_furniture_planned.json",
    ):
        path = artifacts_dir / filename
        if not path.exists():
            continue
        backup_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup_dir / filename)
        backed_up = True
    return backup_dir if backed_up else None


def _write_json_if_needed(path: Path, payload: dict) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return True


def _print_dry_run_preview(
    args: argparse.Namespace,
    artifacts_dir: Path,
    interior_artifact: InteriorStyleAnalysisArtifact,
    validated_preview,
    metadata,
) -> None:
    print("dry_run: true")
    print(f"would_write: {artifacts_dir / 'interior_analysis.json'}")
    if args.validate:
        print(f"would_write: {artifacts_dir / 'interior_analysis_validated.json'}")
    if args.recreate_layout:
        print(f"would_write: {artifacts_dir / 'layout_initial.json'}")
    if args.validate_layout:
        print(f"would_write: {artifacts_dir / 'layout_validated.json'}")
    if args.plan_furniture:
        print(f"would_write: {artifacts_dir / 'layout_furniture_planned.json'}")
    if args.index:
        print(f"would_write: {artifacts_dir / 'artifact_index.json'}")
        print(f"would_write: {artifacts_dir / 'run_metadata_summary.json'}")
    print(f"current_interior_photo_count: {len(interior_artifact.interior_photos)}")
    print(
        "current_style_reference_count: "
        f"{len(interior_artifact.style_references.ideal) + len(interior_artifact.style_references.acceptable) + len(interior_artifact.style_references.ng)}"
    )
    print(f"current_run_status: {metadata.run_status}")
    print(f"preview_furniture_signals: {json.dumps(getattr(validated_preview, 'furniture_signals', {}), ensure_ascii=False)}")


if __name__ == "__main__":
    raise SystemExit(main())
