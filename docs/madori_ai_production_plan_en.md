# Madori AI Production Plan

## 1. Product Direction

This project should not be positioned as a one-click AI system that automatically redraws a floorplan with guaranteed 100% accuracy.

The correct product direction is:

> **AI-assisted floorplan illustration production tool**

The system should use AI to support analysis, furniture suggestions, styling ideas, and draft generation. However, the final layout, English labels, furniture placement, and client delivery quality must be controlled by code, structured data, and human review.

This direction is necessary because the client requires:

- Final output in PNG or JPEG format
- Final image size: **1200px × 1200px**
- Floorplan layout, fixtures, and dimensions matching the original image with **100% accuracy**
- English room labels
- Watercolor illustration quality
- Furniture and floor colors based on real interior reference photos
- Sample approval before scaling production

AI image generation models such as Flux can create visually good results, but they may still change walls, doors, windows, room sizes, labels, or furniture positions. Therefore, Flux should not be the final authority for layout accuracy.

---

## 2. Core Principle

The production system should separate the work into controlled layers:

```text
Original floorplan
  → layout/reference layer
  → room and furniture object layer
  → English label layer
  → rendering/export layer
  → quality review layer
```

The final deliverable should be `output.png`, but the system must also keep structured artifacts that allow inspection, correction, and reproduction.

---

## 3. Required Run Artifacts

Each generation run should produce and store the following files:

```text
floorplan_original              Original uploaded floorplan
analysis_raw.json               Raw AI analysis result
analysis.json                   Normalized floorplan analysis
furniture_plan.json             Semantic furniture suggestions
furniture_layout.json           Editable furniture object layout
manual_labels.json              English label definitions
output_label_edit.json          Label post-processing metadata
quality_check.json              Client acceptance checklist metadata
generation_debug.json           Debug and pipeline metadata
output.png                      Final production image, 1200x1200
overlay_floorplan.png           Debug only
overlay_floorplan_debug.png     Debug only
```

Only this file should be treated as the final deliverable:

```text
output.png
```

The overlay files are internal debug artifacts only. They must not be shown as the final generated result and must not be delivered to the client.

---

## 4. High-Level Architecture

```text
Frontend
  → Upload floorplan and interior reference photos or listing URL
  → FastAPI backend
    → Save run files
    → Analyze floorplan
    → Analyze interior references
    → Build editable furniture layout
    → Allow manual review and correction
    → Render or generate final image
    → Apply English labels by code
    → Produce quality check artifacts
    → Download final output
```

Recommended long-term deployment architecture:

```text
Frontend: Vercel
Backend: Render / Railway / Fly.io / VPS
Image storage: Cloudinary / S3 / R2
Metadata storage: PostgreSQL / Supabase / MongoDB
```

Vercel is suitable for the frontend, but the backend should not rely on Vercel serverless storage for production because `/tmp` storage is temporary and serverless functions may time out during AI image generation.

---

## 5. Phase 0 — Requirement and Sample Preparation

### Goal

Prepare the first three client sample projects and define the acceptance checklist.

### Sample Inputs

The first sample set should include:

```text
madori_964113.webp
madori_782638.webp
madori_812340.webp
```

Each sample should be organized as:

```text
data/
  samples/
    madori_964113/
      floorplan.webp
      reference_photos/
      client_url.txt
      expected_notes.md
    madori_782638/
    madori_812340/
```

### Acceptance Checklist

Each sample should be checked against:

```text
1. Output is exactly 1200x1200
2. Floorplan layout matches the original
3. Walls, doors, windows, and room boundaries are unchanged
4. Kitchen, toilet, bathroom, entrance, closet, and fixtures are preserved
5. English labels are correct
6. Furniture is based on interior reference photos
7. Floor color is based on interior reference photos
8. Watercolor style matches the target sample
9. Nothing important is covered by labels or furniture
10. Final output is manually reviewed before delivery
```

### Done When

- The three initial sample datasets are organized.
- The acceptance checklist is defined.
- Ideal, NG, and acceptable sample references are available for comparison.

---

## 6. Phase 1 — Production Compliance Pipeline

### Goal

Ensure every run produces the required production artifacts and final output format.

### Required Configuration

```env
OUTPUT_SIZE_MODE=fixed
OUTPUT_WIDTH=1200
OUTPUT_HEIGHT=1200
OUTPUT_RESIZE_MODE=contain

OUTPUT_LABEL_EDIT_ENABLED=true
OUTPUT_LABEL_MODE=translate
OUTPUT_LABEL_LANGUAGE=en
```

### Backend Flow

```text
POST /api/generate
  → save uploaded floorplan
  → analyze floorplan
  → build furniture_plan.json
  → call image provider or stub renderer
  → resize or canvas output to 1200x1200
  → run label processing
  → create quality_check.json
  → create generation_debug.json
  → return run_id
```

### quality_check.json Example

```json
{
  "output_size_required": "1200x1200",
  "output_size_actual": "1200x1200",
  "english_labels_required": true,
  "english_labels_status": "needs_review",
  "layout_accuracy_required": "100%",
  "layout_accuracy_status": "manual_review_required",
  "watercolor_quality_status": "manual_review_required",
  "needs_manual_review": true
}
```

### Done When

- `output.png` is always 1200x1200.
- `quality_check.json` is created for every run.
- `/api/runs/{run_id}` returns production compliance metadata.
- The frontend shows output size, label status, layout status, watercolor status, and manual review status.

---

## 7. Phase 2 — Accurate English Label Replacement

### Problem

The output image may contain Japanese room labels or existing label boxes. The client requires English labels such as:

```text
Living Room
Kitchen
Closet
Toilet
Entrance
Bed Room
Bath Room
```

Flux should not be used to write final text because AI-generated text can be misspelled, distorted, or misplaced.

Gemini or other vision models should not be trusted for final label placement because room bounding boxes may be inaccurate.

### Correct Approach

Detect the actual label rectangles directly in `output.png`, then draw English labels inside those existing boxes using code.

```text
output.png
  → detect rectangular label boxes using OpenCV
  → create detected_label_boxes.json
  → create manual_labels.json
  → operator fills or verifies label text
  → apply English labels using Pillow
  → update output.png
  → update quality_check.json
```

### Files to Add

```text
app/services/label_box_detector.py
app/services/output_text_editor.py
```

### detected_label_boxes.json Example

```json
{
  "method": "opencv_label_rectangle_detection",
  "image_width": 1200,
  "image_height": 1200,
  "boxes": [
    {
      "id": "label_box_1",
      "bbox": [430, 360, 620, 405],
      "width": 190,
      "height": 45,
      "center_x": 525,
      "center_y": 382,
      "confidence": 0.88
    }
  ],
  "warnings": []
}
```

### manual_labels.json Example

```json
{
  "version": "1.0",
  "source": "detected_label_boxes",
  "labels": [
    {
      "id": "label_1",
      "text": "Living Room",
      "bbox": [430, 360, 620, 405],
      "locked": false,
      "needs_text": false
    },
    {
      "id": "label_2",
      "text": "Bed Room",
      "bbox": [160, 320, 330, 365],
      "locked": false,
      "needs_text": false
    }
  ]
}
```

### API Endpoints

```text
GET  /api/runs/{run_id}/label-boxes
GET  /api/runs/{run_id}/manual-labels
PUT  /api/runs/{run_id}/manual-labels
POST /api/runs/{run_id}/apply-manual-labels
```

### Rules to Avoid Covering Furniture

- Draw only inside the detected label box.
- Do not expand the box by more than 2–4 px.
- Do not place labels based on room bounding boxes by default.
- Auto-shrink the font until the text fits.
- Wrap text into two lines if needed.
- If text still does not fit, skip and mark as `needs_review`.
- Never mark English labels as done unless they have been applied by code or manually approved.

### Done When

- Label boxes are detected from `output.png`.
- The operator can edit `manual_labels.json`.
- English labels can be applied to `output.png`.
- Labels stay inside the existing label boxes and do not cover furniture.
- `quality_check.json` marks `english_labels_status` as `done` only after successful application.

---

## 8. Phase 3 — Furniture Layout JSON

### Goal

Represent furniture as editable objects instead of treating furniture as a fixed part of a PNG image.

### File

```text
furniture_layout.json
```

### Schema Example

```json
{
  "version": "1.0",
  "canvas": {
    "width": 1200,
    "height": 1200
  },
  "rooms": [
    {
      "id": "living_room_1",
      "type": "living_room",
      "label": "Living Room",
      "bbox": [420, 260, 880, 620],
      "polygon": null
    }
  ],
  "furniture": [
    {
      "id": "sofa_1",
      "type": "sofa",
      "room_id": "living_room_1",
      "x": 560,
      "y": 460,
      "width": 180,
      "height": 80,
      "rotation": 0,
      "color": "white",
      "size_hint": "3_seater",
      "source": "ai_suggested"
    }
  ]
}
```

### AI Role

AI should suggest:

```text
- room types
- furniture types
- furniture style
- color hints
- size hints
```

AI should not be the final source of exact coordinates.

### Done When

- Every run can produce `furniture_layout.json`.
- `/api/runs/{run_id}` returns the furniture layout.
- The data is sufficient for a future canvas editor.

---

## 9. Phase 4 — Canvas Editor

### Goal

Allow the operator to manually correct furniture placement, labels, and layout objects before final export.

### Recommended Library

Because the current frontend is static HTML/CSS/JS, use:

```text
Fabric.js
```

If the frontend later moves to React or Next.js, use:

```text
Konva.js / React-Konva
```

### Editor Features

```text
- Floorplan image as background
- Furniture object layer
- Label object layer
- Select object
- Drag object
- Resize object
- Rotate object
- Delete object
- Duplicate object
- Add furniture from sidebar
- Save layout
- Render preview
```

### API Endpoints

```text
GET  /api/runs/{run_id}/layout
PUT  /api/runs/{run_id}/layout
POST /api/runs/{run_id}/render-layout
```

### Done When

- The operator can visually edit furniture and labels.
- The edited layout is saved to `furniture_layout.json`.
- A 1200x1200 preview can be rendered from the edited layout.

---

## 10. Phase 5 — Interior Reference Photo Analysis

### Goal

Analyze real interior photos or listing URLs to determine furniture and floor style.

The client requires furniture and floor colors to be based on interior photos. For example:

- Sofa size should match the actual sofa, such as a 3-seater sofa.
- Floor color should be based on interior photos.
- Floor color can be simplified into white, light brown, or dark brown.
- Bed and sofa should generally use a white base.
- Cushion and pillow colors should follow the interior reference.
- Bed size should be inferred from pillow or cushion count.

### Endpoint

```text
POST /api/analyze-interior-reference
```

### Output Example

```json
{
  "floor_style": {
    "color_family": "light_brown",
    "material": "wood"
  },
  "furniture": [
    {
      "type": "sofa",
      "size_hint": "3_seater",
      "base_color": "white",
      "cushion_color": "beige",
      "suggested_room": "Living Room"
    },
    {
      "type": "bed",
      "bed_size": "double",
      "pillow_count": 4,
      "base_color": "white",
      "pillow_color": "white"
    }
  ]
}
```

### Model Options

Use a vision-capable model for semantic analysis:

```text
Gemini Vision
GPT-5 mini vision
Claude vision
```

The model should analyze furniture, colors, and size hints. It should not decide final layout positions.

### Done When

- Interior photos can be uploaded or referenced.
- The system extracts furniture and floor style hints.
- These hints can be merged into `furniture_layout.json`.

---

## 11. Phase 6 — Final Rendering Strategy

There are two possible rendering strategies.

### Strategy A — Code/Template Renderer

```text
layout JSON + furniture JSON + label JSON
  → Pillow/SVG/Canvas renderer
  → output.png 1200x1200
```

Pros:

```text
- Highest layout control
- Correct labels
- Furniture stays where the operator placed it
- Easier acceptance checking
```

Cons:

```text
- Watercolor style needs careful implementation
- Output may look less natural than AI-generated images
```

### Strategy B — AI Stylization

```text
accurate guide image
  → AI stylization
  → layout check
  → reject or review if changed
```

Pros:

```text
- More visually appealing
- Faster to reach watercolor-like style
```

Cons:

```text
- Still has risk of layout distortion
- Requires review and possibly regeneration
```

### Recommendation

For this client, use Strategy A as the primary production path and Strategy B only as an optional enhancement.

---

## 12. Phase 7 — Quality Gate and Manual Review

### Goal

No output should be marked as approved until it has been reviewed.

### quality_check.json Extended Example

```json
{
  "layout_accuracy_required": "100%",
  "layout_accuracy_status": "manual_review_required",
  "layout_checked_by": null,
  "layout_checked_at": null,
  "english_labels_required": true,
  "english_labels_status": "done",
  "watercolor_quality_status": "manual_review_required",
  "final_approval_status": "pending",
  "needs_manual_review": true
}
```

### Review Statuses

```text
draft
needs_label_review
needs_layout_review
needs_style_review
approved
delivered
```

### UI Requirements

```text
- Before/after comparison
- Zoomable input and output images
- Checklist panel
- Mark layout as passed
- Mark English labels as passed
- Mark watercolor quality as passed
- Final approval button
- Download final image
```

### Done When

- The operator can review and approve each output.
- Approval status is stored.
- The final image is not considered deliverable until approved.

---

## 13. Phase 8 — Production Deployment

### Recommended Architecture

```text
Frontend: Vercel
Backend FastAPI: Render / Railway / Fly.io / VPS
Image storage: Cloudinary / S3 / R2
Metadata database: PostgreSQL / Supabase / MongoDB
```

### Why Not Backend on Vercel Long-Term

Vercel serverless has limitations:

```text
- Runtime filesystem is read-only except /tmp
- /tmp is not persistent
- AI generation may exceed function timeout
- Run artifacts may disappear after cold start or redeploy
```

### Required Production Storage

Move these from local folders to persistent storage:

```text
floorplan_original
output.png
preview images
analysis.json
furniture_layout.json
manual_labels.json
quality_check.json
```

### Done When

- Generated files do not disappear after redeploy or cold start.
- Downloads work from persistent storage.
- Run metadata is stored in a database.
- The frontend can reload previous runs.

---

## 14. Production Workflow for the First Three Samples

### Step 1 — Import Data

```text
Import madori_964113
Import madori_782638
Import madori_812340
```

### Step 2 — Generate Draft

```text
AI analyzes floorplan
AI suggests furniture
System produces 1200x1200 draft output
```

### Step 3 — Apply English Labels

```text
Detect label boxes
Create manual_labels.json
Map labels:
  Living Room
  Kitchen
  Closet
  Toilet
  Entrance
  Bed Room
  Bath Room
Apply labels by code
```

### Step 4 — Furniture Review

```text
Open editor
Adjust sofa, bed, table, storage, plants
Match furniture size and color to interior reference photos
Adjust floor color
```

### Step 5 — Render Final

```text
Render output.png 1200x1200
Apply labels
Update quality_check.json
```

### Step 6 — Manual Quality Check

```text
Compare input and output
Check walls, doors, windows, and room boundaries
Check kitchen, bath, toilet, entrance, closet
Check English labels
Check furniture style and color
Check watercolor quality
```

### Step 7 — Client Delivery

```text
Deliver 3 approved output images
Collect feedback
Adjust style and workflow
Scale to more units only after approval
```

---

## 15. Technical Implementation Order

From the current codebase, the recommended order is:

```text
1. Commit Phase 1 compliance work
2. Implement label box detector using OpenCV
3. Implement manual_labels.json and label APIs
4. Add frontend manual label editor
5. Add furniture_layout.json
6. Add layout save/load APIs
7. Add basic Fabric.js canvas editor
8. Add render-layout preview endpoint
9. Add interior reference analyzer
10. Merge reference hints into furniture_layout.json
11. Add quality review UI
12. Move storage to Cloudinary/S3/R2
13. Add database-backed run metadata
14. Deploy production architecture
```

---

## 16. Suggested Timeline

### Week 1

```text
- Finalize Phase 1 compliance
- Add label box detector
- Add manual label APIs
- Add apply-labels function
- Add simple frontend label editor
```

Expected result:

```text
English labels can be accurately applied to output images.
```

### Week 2

```text
- Add furniture_layout.json
- Add layout save/load APIs
- Add simple renderer preview
```

Expected result:

```text
Furniture has an editable object format.
```

### Week 3

```text
- Add Fabric.js canvas editor
- Drag, resize, rotate furniture
- Save edited layout
- Render 1200x1200 preview
```

Expected result:

```text
Operator can correct furniture placement manually.
```

### Week 4

```text
- Add interior reference analyzer
- Extract sofa, bed, floor, color, and size hints
- Add quality review UI
```

Expected result:

```text
Furniture and floor style can follow interior reference photos.
```

### Week 5+

```text
- Move files to persistent storage
- Add database
- Add admin workflow
- Deploy production system
- Support batch processing
```

---

## 17. Non-Negotiable Technical Rules

```text
1. Do not trust Flux to preserve layout with 100% accuracy.
2. Do not trust Gemini bounding boxes as final geometry.
3. Do not let AI write final English labels.
4. English labels must be controlled by code or manual review.
5. Final output must be 1200x1200.
6. Debug overlays are not final outputs.
7. Every deliverable must pass manual review.
8. The final deliverable is output.png only.
9. Furniture should be editable as objects, not baked into an uncontrolled image.
10. AI should assist production, not replace final QA.
```

---

## 18. Final Recommendation

The correct implementation path is:

```text
AI assists analysis and drafting.
Code controls output size and labels.
Object layers control furniture and layout.
Human review guarantees delivery quality.
```

The immediate next technical priority is:

```text
Label box detection + manual English label apply
```

After that, the next major milestone is:

```text
furniture_layout.json + canvas editor
```

This is the safest path to satisfy the client requirement of accurate floorplan illustration production.
