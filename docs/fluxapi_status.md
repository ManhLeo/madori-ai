# FluxAPI Status And Diagnostics

FluxAPI previously returned `successFlag=3` and `errorCode=500` during polling, even for minimal text-to-image requests.

Minimal diagnostic prompt:

```text
A simple Japanese watercolor apartment floorplan.
```

The failure was reproduced with:

- `flux-kontext-pro`
- `flux-kontext-max`
- no `inputImage`
- JPEG `inputImage`
- PNG `inputImage`

When minimal text-to-image fails, the problem is external to the Madori pipeline and is likely related to FluxAPI service health, model availability, account permissions, or API-key access.

## Current Verification

Re-test FluxAPI with:

```bash
python scripts/test_fluxapi_matrix.py inputs/madori.jpg --model flux-kontext-pro
python scripts/test_fluxapi_matrix.py inputs/madori.jpg --model flux-kontext-max
```

`scripts/test_fluxapi_matrix.py` is diagnostics-only. It is not used by `/api/generate`.

## Development Fallback

If FluxAPI is unavailable, continue backend/frontend development with:

```env
IMAGE_PROVIDER=stub
```

Stub mode creates a local `output.png` preview from the uploaded floorplan. It does not call FluxAPI.
