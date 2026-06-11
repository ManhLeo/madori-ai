# Kế hoạch triển khai Madori AI theo yêu cầu khách hàng

## 1. Mục tiêu

Dự án cần chuyển ảnh mặt bằng căn hộ Nhật Bản thành ảnh minh họa mặt bằng phong cách watercolor, nhưng vẫn đảm bảo độ chính xác cao theo yêu cầu khách hàng.

Yêu cầu cốt lõi:

- Output cuối cùng là ảnh `PNG` hoặc `JPEG`.
- Kích thước output: `1200px × 1200px`.
- Layout, tường, cửa, cửa sổ, thiết bị và kích thước phải khớp bản gốc.
- Label trong ảnh phải là tiếng Anh, không giữ chữ Nhật.
- Nội thất, màu sàn, màu sofa, màu giường, gối và cushion cần dựa theo ảnh nội thất tham khảo.
- Output phải có phong cách watercolor, không phải tô màu phẳng hoặc brown fill đơn giản.
- Với yêu cầu chính xác 100%, cần có bước manual review trước khi giao khách.

Định hướng sản phẩm đúng:

```text
AI-assisted floorplan illustration production tool
```

Không nên định vị là:

```text
AI tự động tạo ảnh mặt bằng chính xác 100%
```

AI nên hỗ trợ phân tích, gợi ý và tạo bản nháp. Layout, label và output giao khách cần được kiểm soát bằng code, editor và human review.

---

## 2. Nguyên tắc kỹ thuật bắt buộc

1. Không để Flux hoặc image model quyết định layout cuối cùng.
2. Không tin tuyệt đối bbox phòng do Gemini hoặc model vision trả về.
3. Không để AI image model viết chữ cuối cùng trong ảnh.
4. English labels phải do code hoặc manual editor kiểm soát.
5. Output cuối cùng luôn phải là `1200x1200`.
6. Mọi output giao khách phải qua manual review.
7. Debug overlay không phải final output.
8. Ảnh final duy nhất để download/giao khách là `output.png`.
9. Những phần cần chính xác như tường, cửa, phòng, label nên là object/layer hoặc được hậu xử lý bằng code.
10. AI chỉ nên dùng cho các tác vụ mềm: phân tích nội thất, gợi ý furniture, gợi ý style, tạo draft hoặc stylize nhẹ.

---

## 3. Kiến trúc tổng thể

```text
Frontend
  → Upload floorplan + interior reference photos / URL
  → FastAPI backend
    → Save run
    → Analyze floorplan
    → Analyze interior references
    → Build editable layout JSON
    → Manual review / editor
    → Render/export 1200x1200
    → Apply English labels
    → Quality check
    → Download final output
```

### Các lớp dữ liệu chính

```text
1. Source floorplan layer
2. Room/layout object layer
3. Furniture object layer
4. Label object layer
5. Render/export layer
6. Quality check layer
```

---

## 4. Artifact cần có cho mỗi run

Mỗi lần generate nên tạo thư mục run với các file:

```text
runs/{run_id}/
  floorplan.*                     # ảnh input gốc
  analysis_raw.json               # raw response từ model phân tích
  analysis.json                   # phân tích chuẩn hóa
  furniture_plan.json             # gợi ý nội thất semantic
  furniture_layout.json           # furniture dạng object/layer, dùng cho editor
  detected_label_boxes.json       # label boxes detect từ output
  manual_labels.json              # label tiếng Anh do user/operator chỉnh
  output_label_edit.json          # metadata sau khi apply label
  quality_check.json              # trạng thái nghiệm thu
  generation_debug.json           # debug metadata
  provider_status.json            # provider info
  output.png                      # final output 1200x1200
  overlay_floorplan.png           # debug only
  overlay_floorplan_debug.png     # debug only
```

Trong đó:

```text
output.png = ảnh final để hiển thị/download/giao khách
```

Không dùng `overlay_floorplan.png` hoặc `overlay_floorplan_debug.png` làm kết quả chính.

---

## 5. Phase 0 — Chuẩn hóa yêu cầu và dữ liệu mẫu

### Mục tiêu

Chuẩn hóa dữ liệu 3 sample đầu tiên và checklist nghiệm thu.

Theo tài liệu khách hàng, 3 sample đầu tiên gồm:

```text
madori_964113.webp
madori_782638.webp
madori_812340.webp
```

### Cấu trúc dữ liệu đề xuất

```text
data/
  samples/
    madori_964113/
      floorplan.webp
      reference_photos/
      client_url.txt
      expected_notes.md
    madori_782638/
      floorplan.webp
      reference_photos/
      client_url.txt
      expected_notes.md
    madori_812340/
      floorplan.webp
      reference_photos/
      client_url.txt
      expected_notes.md
```

### Checklist nghiệm thu

```text
1. Output đúng 1200x1200
2. Layout không lệch so với floorplan gốc
3. Tường/cửa/cửa sổ không bị đổi
4. Thiết bị bếp/toilet/bath giữ đúng vị trí
5. Label tiếng Anh đúng
6. Nội thất giống ảnh tham khảo
7. Style watercolor đúng mẫu
8. Không che mất thông tin quan trọng
```

### Done khi

- Có đủ 3 bộ sample.
- Có checklist nghiệm thu.
- Có ảnh reference: ideal, NG, acceptable.

---

## 6. Phase 1 — Production compliance pipeline

### Mục tiêu

Đảm bảo mọi run đều ra đúng format production.

### Config đề xuất

```env
OUTPUT_SIZE_MODE=fixed
OUTPUT_WIDTH=1200
OUTPUT_HEIGHT=1200
OUTPUT_RESIZE_MODE=contain

OUTPUT_LABEL_EDIT_ENABLED=true
OUTPUT_LABEL_MODE=translate
OUTPUT_LABEL_LANGUAGE=en
```

### Backend flow

```text
POST /api/generate
  → save uploaded floorplan
  → analyze floorplan
  → build furniture_plan.json
  → call image provider hoặc stub
  → resize/canvas output thành 1200x1200
  → label processing
  → create quality_check.json
  → create generation_debug.json
  → return run_id
```

### quality_check.json

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

### Done khi

- `output.png` luôn đúng `1200x1200`.
- `/api/runs/{run_id}` trả được `quality_check`.
- Frontend hiển thị manual review badge.
- Download endpoint tải đúng `output.png`.

---

## 7. Phase 2 — English label chính xác

### Vấn đề

Không nên để Flux viết chữ vì có thể sai spelling, méo chữ hoặc làm lệch layout.
Không nên dựa vào bbox phòng của Gemini vì bbox có thể sai.

### Hướng tối ưu

```text
output.png 1200x1200
  → detect label rectangle boxes bằng OpenCV
  → save detected_label_boxes.json
  → create manual_labels.json
  → operator điền text tiếng Anh
  → apply labels bằng Pillow
  → update output.png
  → update quality_check.json
```

### Label tiếng Anh cần hỗ trợ

```text
Living Room
Kitchen
Closet
Toilet
Entrance
Bed Room
Bath Room
Balcony
Wash Room
```

### detected_label_boxes.json

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

### manual_labels.json

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

### API cần có

```text
GET  /api/runs/{run_id}/label-boxes
GET  /api/runs/{run_id}/manual-labels
PUT  /api/runs/{run_id}/manual-labels
POST /api/runs/{run_id}/apply-manual-labels
```

### Quy tắc để không che nội thất

- Chỉ vẽ trong bbox label cũ.
- Padding tối đa 2–4px.
- Không mở rộng box theo room bbox.
- Auto-shrink font.
- Nếu text dài thì wrap 2 dòng.
- Nếu vẫn không vừa thì mark `needs_review`.
- Không vẽ label ở trung tâm phòng nếu không detect được label box, trừ khi user bật fallback thủ công.

### Done khi

- Detect được label rectangles.
- User/operator nhập English labels.
- Apply xong `output.png` có chữ tiếng Anh rõ ràng.
- Label không che nội thất ngoài vùng box cũ.
- `quality_check.english_labels_status = done` chỉ sau khi apply label thành công.

---

## 8. Phase 3 — Furniture layout JSON

### Mục tiêu

Chuyển furniture từ prompt/ảnh chết thành object có thể chỉnh sửa.

### furniture_layout.json

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
      "source": "ai_suggested",
      "locked": false
    }
  ]
}
```

### Vai trò AI

AI chỉ nên gợi ý:

```text
- phòng nào có nội thất gì
- loại sofa/bed/table
- màu sắc tương đối
- size_hint
```

Không dùng AI để quyết định tọa độ cuối cùng.

### Done khi

- Mỗi run có `furniture_layout.json`.
- `/api/runs/{run_id}` trả được `furniture_layout`.
- Dữ liệu đủ để frontend vẽ object layer.

---

## 9. Phase 4 — Canvas editor

### Mục tiêu

Cho operator chỉnh nội thất, label và layout trước khi xuất ảnh.

### Công nghệ đề xuất

Vì frontend hiện tại là static HTML/CSS/JS:

```text
Fabric.js
```

Nếu sau này chuyển sang React/Next.js:

```text
Konva.js / React-Konva
```

### Tính năng editor

```text
- Floorplan gốc làm background
- Furniture object layer
- Label object layer
- Select object
- Drag
- Resize
- Rotate
- Delete
- Duplicate
- Add furniture từ sidebar
- Save layout
- Render preview
```

### API cần có

```text
GET  /api/runs/{run_id}/layout
PUT  /api/runs/{run_id}/layout
POST /api/runs/{run_id}/render-layout
```

### Done khi

- Operator chỉnh được furniture trên UI.
- Save lại `furniture_layout.json`.
- Render được preview `1200x1200`.

---

## 10. Phase 5 — Phân tích ảnh nội thất tham khảo

### Mục tiêu

Dùng ảnh nội thất thật để xác định:

```text
- sofa size
- bed size
- pillow/cushion count
- floor color
- bed/sofa color
- cushion/pillow color
```

### Endpoint

```text
POST /api/analyze-interior-reference
```

### Input

```text
- listing URL
- hoặc interior room photos
```

### Output

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

### Quy tắc từ tài liệu

```text
1 pillow/cushion  = single bed
2 pillow/cushion  = semi-double bed
3–4 pillows       = double bed
```

### Done khi

- Upload/link ảnh nội thất.
- AI trả furniture/floor/color hints.
- Hints được merge vào `furniture_layout.json`.

---

## 11. Phase 6 — Render final chính xác

Có 2 hướng.

### Hướng A — Render bằng code/template

```text
floorplan/layout JSON/furniture JSON/labels
→ Pillow/SVG/Canvas renderer
→ output.png 1200x1200
```

Ưu điểm:

- Kiểm soát layout tốt.
- Chữ đúng.
- Furniture không chạy lung tung.
- Dễ nghiệm thu.

Nhược điểm:

- Watercolor style cần đầu tư renderer.
- Hình có thể kém tự nhiên hơn AI.

### Hướng B — AI stylize nhẹ

```text
render guide chính xác
→ AI stylize watercolor
→ kiểm tra layout
→ nếu lệch thì reject/manual
```

Ưu điểm:

- Ảnh đẹp hơn.

Nhược điểm:

- Vẫn có nguy cơ AI làm lệch layout.

### Khuyến nghị

Với yêu cầu khách hàng:

```text
Hướng A là chính.
Hướng B chỉ là optional enhancement.
```

---

## 12. Phase 7 — Quality gate và manual review

### quality_check.json mở rộng

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

### UI review cần có

```text
- Before/after comparison
- Zoom input/output
- Checklist panel
- Mark layout passed
- Mark labels passed
- Mark watercolor passed
- Export/download final
```

### Trạng thái run

```text
draft
needs_label_review
needs_layout_review
needs_style_review
approved
delivered
```

### Done khi

- Không output nào được coi là approved nếu chưa có người kiểm tra.
- `quality_check.json` lưu trạng thái review.

---

## 13. Phase 8 — Deploy production

### Kiến trúc deploy khuyến nghị

Không nên để backend chính trên Vercel lâu dài.

```text
Frontend: Vercel
Backend FastAPI: Render / Railway / Fly.io / VPS
Storage ảnh: Cloudinary / S3 / R2
Metadata: PostgreSQL / Supabase / MongoDB
```

### Lý do

Vercel không phù hợp làm backend chính cho tác vụ AI dài vì:

```text
- Serverless có timeout
- /tmp không persistent
- Generate ảnh AI có thể lâu
- Cần lưu nhiều artifact
```

### Storage production

Hiện local đang dùng:

```text
runs/
uploads/
outputs/
```

Production nên chuyển sang:

```text
Cloudinary/S3/R2:
  floorplan_original
  output.png
  preview images

Database:
  run_id
  artifact URLs
  quality status
  furniture_layout JSON
  manual_labels JSON
```

### Done khi

- File không mất sau redeploy/cold start.
- Download dùng URL ổn định từ storage.
- Có metadata database.
- Có admin/review page.

---

## 14. Quy trình sản xuất 3 sample đầu tiên

### Bước 1 — Import data

```text
Import madori_964113
Import madori_782638
Import madori_812340
```

### Bước 2 — Tạo bản nháp

```text
AI phân tích phòng
AI gợi ý furniture
Output 1200x1200
```

### Bước 3 — English labels

```text
Detect label boxes
Manual map:
  Living Room
  Kitchen
  Closet
  Toilet
  Entrance
  Bed Room
  Bath Room
Apply labels bằng code
```

### Bước 4 — Furniture review

```text
Mở editor
Chỉnh sofa/bed/table đúng ảnh nội thất
Chỉnh màu sàn
Chỉnh màu cushion/pillow
```

### Bước 5 — Render final

```text
Render output.png 1200x1200
Apply labels
Update quality_check
```

### Bước 6 — Kiểm tra

```text
So sánh input/output
Check tường/cửa/cửa sổ
Check equipment
Check label
Check watercolor style
```

### Bước 7 — Gửi khách

```text
Gửi 3 file output.png
Gửi note nếu có điểm cần xác nhận
Chờ feedback
```

---

## 15. Thứ tự triển khai kỹ thuật cụ thể

Từ trạng thái code hiện tại, nên làm theo thứ tự:

```text
1. Commit Phase 1 hiện tại
2. Label box detector bằng OpenCV
3. manual_labels.json + API save/apply
4. Frontend manual label JSON editor
5. furniture_layout.json
6. API save/load furniture layout
7. Canvas editor cơ bản bằng Fabric.js
8. Render layout preview
9. Interior reference analyzer
10. Merge reference hints vào furniture layout
11. Quality review UI
12. Cloud storage/database
13. Production deployment
```

---

## 16. Timeline đề xuất

### Tuần 1

```text
- Chốt Phase 1
- Label box detector
- Manual labels API
- Apply English labels
- Frontend label editor đơn giản
```

Kết quả: output có English labels chính xác.

### Tuần 2

```text
- furniture_layout.json
- API save/load layout
- Renderer preview đơn giản
```

Kết quả: có dữ liệu object để chỉnh furniture.

### Tuần 3

```text
- Fabric.js canvas editor
- Drag/resize/rotate furniture
- Save layout
- Render preview 1200x1200
```

Kết quả: operator chỉnh được nội thất.

### Tuần 4

```text
- Interior reference analyzer
- Gợi ý màu sàn/sofa/bed
- Quality review UI
```

Kết quả: gần sát yêu cầu tài liệu.

### Tuần 5+

```text
- Cloud storage/database
- Admin workflow
- Production deploy
- Batch processing nhiều căn
```

---

## 17. Rủi ro và cách xử lý

| Rủi ro | Nguyên nhân | Cách xử lý |
|---|---|---|
| Flux làm lệch layout | Image model không phải CAD engine | Không dùng Flux làm nguồn layout cuối |
| Chữ tiếng Anh bị sai | AI image model viết text kém | Vẽ label bằng code/Pillow |
| Gemini bbox sai | Vision LLM ước lượng không chuẩn | Không dùng bbox AI làm ground truth |
| Furniture sai vị trí | AI chỉ gợi ý semantic | Dùng furniture_layout + manual editor |
| Output không đúng 1200x1200 | Provider output size khác nhau | Post-process fixed canvas 1200x1200 |
| File mất khi deploy Vercel | `/tmp` không persistent | Dùng Cloudinary/S3/R2 + database |
| Khách yêu cầu chỉnh nhiều | Chưa có editor | Làm canvas editor sớm |

---

## 18. Kết luận

Plan đúng với tài liệu khách hàng là:

```text
AI hỗ trợ phân tích và gợi ý
Code kiểm soát output size và labels
Editor kiểm soát furniture/layout
Người vận hành review trước khi giao
```

Không nên tiếp tục chỉ sửa prompt Flux, vì prompt không thể đảm bảo layout chính xác 100%.

Ưu tiên gần nhất:

```text
1. Label box detection + manual labels apply
2. furniture_layout.json
3. canvas editor
4. quality review workflow
5. cloud storage/database
```
