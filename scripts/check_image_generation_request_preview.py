from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python scripts/check_image_generation_request_preview.py <image_generation_request_preview.json>")
        return 1

    path = Path(sys.argv[1])
    payload = json.loads(path.read_text(encoding="utf-8"))
    provider = payload.get("provider", {})
    controls = payload.get("safety_and_cost_controls", {})
    quality = payload.get("request_quality", {})
    reference_inputs = payload.get("reference_inputs", {})
    request_payload_preview = payload.get("request_payload_preview", {})
    postprocess_plan = payload.get("postprocess_plan", {})
    provider_size_policy = payload.get("provider_size_policy", {})
    preview_warnings = payload.get("preview_warnings", [])
    upstream_warnings = payload.get("upstream_warnings", [])
    all_warnings = payload.get("warnings", [])
    input_image_count = int(quality.get("input_image_count") or 0)

    print(
        {
            "preview_status": payload.get("preview_status"),
            "provider": provider,
            "model": provider.get("model"),
            "request_will_be_sent": provider.get("request_will_be_sent"),
            "api_call_performed": provider.get("api_call_performed"),
            "requires_manual_approval": controls.get("requires_manual_approval"),
            "dry_run_only": controls.get("dry_run_only"),
            "allow_provider_call": controls.get("allow_provider_call"),
            "target_output": payload.get("target_output"),
            "provider_requested_size": request_payload_preview.get("size"),
            "final_delivery_size": (payload.get("target_output") or {}).get("final_delivery_size"),
            "provider_size_supported": request_payload_preview.get("provider_size_supported"),
            "postprocess_plan": postprocess_plan,
            "provider_size_policy": provider_size_policy,
            "prompt_char_count": quality.get("prompt_char_count"),
            "input_image_count": input_image_count,
            "preview_warnings_count": len(preview_warnings),
            "upstream_warnings_count": len(upstream_warnings),
            "warnings_count": len(all_warnings),
            "reference_inputs_summary": {
                "normalized_floorplan": bool((reference_inputs.get("normalized_floorplan") or {}).get("preview_url")),
                "style_references": {
                    "ideal": len((reference_inputs.get("style_references") or {}).get("ideal", [])),
                    "acceptable": len((reference_inputs.get("style_references") or {}).get("acceptable", [])),
                    "ng": len((reference_inputs.get("style_references") or {}).get("ng", [])),
                },
                "interior_photos": len(reference_inputs.get("interior_photos") or []),
            },
        }
    )
    if input_image_count > 5:
        print(f"\n[warning] input_image_count exceeds QA threshold: {input_image_count} > 5\n")
    print("\n--- request_payload_preview.prompt ---\n")
    print(request_payload_preview.get("prompt", ""))
    print("\n--- preview_warnings ---\n")
    print(json.dumps(preview_warnings, ensure_ascii=False, indent=2))
    print("\n--- upstream_warnings ---\n")
    print(json.dumps(upstream_warnings, ensure_ascii=False, indent=2))
    print("\n--- warnings ---\n")
    print(json.dumps(all_warnings, ensure_ascii=False, indent=2))
    print("\n--- errors ---\n")
    print(json.dumps(payload.get("errors", []), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
