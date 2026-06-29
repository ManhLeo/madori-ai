from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python scripts/check_openai_floorplan_analysis_json.py <floorplan_analysis_raw.json>")
        return 1

    artifact_path = Path(sys.argv[1])
    if not artifact_path.exists():
        print(f"Missing file: {artifact_path}")
        return 1

    try:
        payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"Invalid JSON: {exc}")
        return 1

    run_id = payload.get("run_id")
    inferred_run_id = None
    if not run_id:
        try:
            inferred_run_id = artifact_path.resolve().parents[2].name
        except Exception:
            inferred_run_id = None
        if inferred_run_id:
            run_id = inferred_run_id
    provider = payload.get("provider")
    model = payload.get("model")
    attempts = payload.get("attempts") or []
    errors = payload.get("errors") or []

    if not run_id:
        print("Missing run_id")
        return 1
    if provider is None:
        print("Missing provider")
        return 1
    if model is None:
        print("Missing model")
        return 1
    if not isinstance(attempts, list):
        print("Invalid attempts field")
        return 1

    if inferred_run_id and not payload.get("run_id"):
        print(f"run_id: {run_id} (inferred from path)")
    else:
        print(f"run_id: {run_id}")
    print(f"provider: {provider}")
    print(f"model: {model}")
    print("attempts:")
    for attempt in attempts:
        if not isinstance(attempt, dict):
            print(" - invalid attempt record")
            continue
        print(
            " - attempt {attempt}: parse_status={parse_status}, response_length={response_length}, likely_truncated={likely_truncated}".format(
                attempt=attempt.get("attempt"),
                parse_status=attempt.get("parse_status"),
                response_length=attempt.get("response_length"),
                likely_truncated=attempt.get("likely_truncated"),
            )
        )
        if attempt.get("error"):
            print(f"   error: {attempt.get('error')}")
        if attempt.get("raw_response_path"):
            print(f"   raw_response_path: {attempt.get('raw_response_path')}")
    print(f"errors: {errors}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
