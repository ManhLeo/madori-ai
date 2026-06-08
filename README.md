# Madori AI / イラスト間取りAI

FastAPI MVP for turning Japanese apartment floorplans into furnished real-estate style illustrations.

## Current Strategy

- Upload a Japanese apartment floorplan.
- Gemini analyzes the layout and furniture needs.
- The furniture plan is saved as structured JSON.
- The prompt builder creates a detailed room-by-room image-edit prompt.
- FluxAPI receives the original uploaded floorplan image through a Cloudinary public URL.
- FluxAPI generates realistic/stylized furniture itself.
- `output.png` is the final generated image.
- `overlay_floorplan.png` and `overlay_floorplan_debug.png` are debug artifacts only.

Furniture icon overlays are never sent to FluxAPI and are not the final output.

## Architecture

```text
Frontend
  -> FastAPI /api/generate
  -> Vision Analyzer
  -> Furniture Plan
  -> Prompt Builder
  -> Image Provider
  -> Run Artifacts
```

## Run Artifacts

Each run is saved under `runs/{run_id}/`:

- `floorplan.*` - uploaded input floorplan
- `analysis_raw.json` - raw provider analysis response
- `analysis.json` - normalized layout analysis
- `furniture_plan.json` - room-by-room furniture plan
- `prompt.txt` - final image prompt
- `output.png` - final generated image
- `overlay_floorplan.png` - debug-only furniture overlay
- `overlay_floorplan_debug.png` - debug-only overlay with room boxes/labels
- `provider_status.json` - selected image provider and generation mode
- `generation_debug.json` - prompt/debug quality checks
- `input_image_url.txt` - Cloudinary URL used for FluxAPI when applicable

## Setup

Use Python 3.11+.

```bash
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
copy .env.example .env
```

Configure `.env`.

### Development Mode

Use local preview output without external image generation:

```env
IMAGE_PROVIDER=stub
USE_GEMINI_ANALYSIS=false
```

Stub mode copies the original floorplan into `output.png` with a small watermark. This keeps API/frontend development unblocked.

### Gemini Analysis

```env
USE_GEMINI_ANALYSIS=true
GEMINI_API_KEY=your_key
GEMINI_MODEL=gemini-2.5-flash
```

If Gemini is disabled, the analyzer uses deterministic stub data.

### Cloudinary

FluxAPI image editing requires a public `inputImage` URL. The app uploads the original floorplan to Cloudinary.

```env
CLOUDINARY_CLOUD_NAME=your_cloud_name
CLOUDINARY_API_KEY=your_key
CLOUDINARY_API_SECRET=your_secret
```

### FluxAPI Production Mode

```env
IMAGE_PROVIDER=fluxapi
FLUXAPI_API_KEY=your_key
FLUXAPI_MODEL=flux-kontext-pro
FLUXAPI_INPUT_IMAGE_FORMAT=jpg
FLUXAPI_ENABLE_TRANSLATION=false
```

`FLUXAPI_INPUT_IMAGE_URL` is diagnostics-only for scripts. `/api/generate` uploads the actual user floorplan to Cloudinary and uses that URL.

## Run Locally

```bash
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Open:

```text
http://127.0.0.1:8000
```

Upload a floorplan and generate. The frontend displays `output.png` only as the final result.

## Deployment Notes

### Vercel / Serverless Runtime

Vercel serverless functions cannot write to the project directory at runtime. When the app detects Vercel with `VERCEL=1` or `VERCEL_ENV` set, it writes runtime files under:

```text
/tmp/madori-ai/uploads
/tmp/madori-ai/runs
/tmp/madori-ai/outputs
```

When simulating Vercel locally on Windows, the app uses the OS temp directory equivalent, for example `%TEMP%\madori-ai`.

The `/runs` static mount points to the configured runtime `runs_dir`, so generated files can still be opened during the same warm serverless runtime:

```text
/runs/{run_id}/output.png
```

Important limitation: `/tmp` is temporary and not persistent. Run files may disappear when the serverless instance is recycled. For real production persistence, upload final outputs to Cloudinary/S3/R2 and store run metadata in a database.

Check deployment/runtime configuration:

```bash
curl.exe http://127.0.0.1:8000/api/deployment-check
```

## API Usage

Generate:

```bash
curl.exe -X POST "http://127.0.0.1:8000/api/generate" ^
  -F "floorplan=@inputs/madori.jpg" ^
  -F "style=japanese_watercolor" ^
  -F "target_user=single_person" ^
  -F "interior_style=japanese_natural" ^
  -F "budget_level=medium" ^
  -F "lifestyle=work_from_home,likes_plants,needs_storage"
```

Inspect a run:

```bash
curl.exe http://127.0.0.1:8000/api/runs/{run_id}
```

Open run files:

```text
http://127.0.0.1:8000/runs/{run_id}/output.png
http://127.0.0.1:8000/runs/{run_id}/overlay_floorplan_debug.png
```

## Manual Diagnostics

Gemini analysis:

```bash
python scripts/test_analyze_floorplan.py inputs/madori.jpg
```

FluxAPI diagnostics:

```bash
python scripts/test_fluxapi_matrix.py inputs/madori.jpg --model flux-kontext-pro
python scripts/test_fluxapi_matrix.py inputs/madori.jpg --model flux-kontext-max
```

`scripts/test_fluxapi_matrix.py` is diagnostics-only and is not part of normal generation.

## Troubleshooting

### Output is a stub preview

Check:

```env
IMAGE_PROVIDER=stub
```

Switch to `IMAGE_PROVIDER=fluxapi` for production generation.

### FluxAPI returns 500/internal error

Run:

```bash
python scripts/test_fluxapi_matrix.py inputs/madori.jpg
```

If minimal text-to-image also fails, the issue is external to the Madori pipeline: model availability, account access, API key, or FluxAPI service health.

### Gemini quota or 429 errors

Disable Gemini during frontend/API development:

```env
USE_GEMINI_ANALYSIS=false
```

### Cloudinary upload issues

Verify:

```env
CLOUDINARY_CLOUD_NAME=
CLOUDINARY_API_KEY=
CLOUDINARY_API_SECRET=
```

FluxAPI mode requires Cloudinary because the image provider needs a public URL.

### Overlay icons appear

Overlay images are debug-only:

- `overlay_floorplan.png`
- `overlay_floorplan_debug.png`

The final generated image is always:

- `output.png`
