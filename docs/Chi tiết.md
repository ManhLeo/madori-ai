1. Định hướng sản phẩm

AI-assisted floorplan illustration production tool

Tức là:

AI hỗ trợ phân tích + gợi ý nội thất + tạo bản nháp
Code/editor kiểm soát layout, label, output size
Người vận hành review/chỉnh trước khi giao khách

Đây là hướng thực tế nhất vì khách yêu cầu độ chính xác cao, còn Flux hoặc image model vẫn có nguy cơ làm lệch tường, cửa, phòng, chữ.

2. Output cuối cùng cần đạt

Mỗi căn hộ sau khi xử lý phải có bộ artifact:

output.png                         final image 1200x1200
floorplan_original                 ảnh gốc
analysis.json                      phân tích phòng
furniture_plan.json                gợi ý nội thất
furniture_layout.json              nội thất dạng object/layer
manual_labels.json                 label tiếng Anh
output_label_edit.json             metadata sau khi apply label
quality_check.json                 checklist nghiệm thu
generation_debug.json              debug metadata

Ảnh giao khách là:

output.png

Không giao:

overlay_floorplan.png
overlay_floorplan_debug.png

Hai file đó chỉ dùng debug nội bộ.

3. Kiến trúc tổng thể
Frontend
  → Upload floorplan + interior photos/link
  → FastAPI backend
    → Save run
    → Analyze floorplan
    → Analyze interior references
    → Build editable layout JSON
    → Manual editor/review
    → Render/export 1200x1200
    → Apply English labels
    → Quality check
    → Download final output

Về lâu dài, không nên để output chỉ là một ảnh PNG. Cần có object data để chỉnh sửa:

rooms
doors
windows
fixtures
furniture
labels
floor style
review status
4. Phase 0 — Chuẩn hóa yêu cầu và dữ liệu mẫu
Mục tiêu

Xác định rõ 3 sample đầu tiên và tiêu chuẩn nghiệm thu.

Theo tài liệu, 3 sample ban đầu là:

madori_964113.webp
madori_782638.webp
madori_812340.webp

Sau khi thống nhất chất lượng sample thì mới mở rộng số lượng.

Việc cần làm

Tạo thư mục dữ liệu chuẩn:

data/
  samples/
    madori_964113/
      floorplan.webp
      reference_photos/
      client_url.txt
      expected_notes.md
    madori_782638/
    madori_812340/

Tạo checklist chuẩn:

1. Output đúng 1200x1200
2. Layout không lệch so với floorplan gốc
3. Tường/cửa/cửa sổ không bị đổi
4. Thiết bị bếp/toilet/bath giữ đúng vị trí
5. Label tiếng Anh đúng
6. Nội thất giống ảnh tham khảo
7. Style watercolor đúng mẫu
8. Không che mất thông tin quan trọng
Done khi
- Có đủ 3 bộ sample
- Có checklist nghiệm thu
- Có ảnh sample lý tưởng, NG, acceptable để so sánh
5. Phase 1 — Compliance pipeline

Phase này bạn đã làm phần lớn rồi. Nhiệm vụ là đảm bảo mọi run đều ra đúng format production.

Mục tiêu
output.png luôn là 1200x1200
quality_check.json luôn được tạo
manual_review_required luôn rõ ràng
label tiếng Anh có trạng thái done/needs_review
Config production
OUTPUT_SIZE_MODE=fixed
OUTPUT_WIDTH=1200
OUTPUT_HEIGHT=1200
OUTPUT_RESIZE_MODE=contain

OUTPUT_LABEL_EDIT_ENABLED=true
OUTPUT_LABEL_MODE=translate
OUTPUT_LABEL_LANGUAGE=en
Backend flow
/api/generate
  → save floorplan
  → analyze floorplan
  → build furniture_plan
  → call image provider hoặc render stub
  → resize/canvas output thành 1200x1200
  → label processing
  → create quality_check.json
  → return run_id
quality_check.json
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
Done khi
- Generate xong luôn có output.png 1200x1200
- /api/runs/{run_id} xem được quality_check
- Frontend hiển thị manual review badge
- Download được output.png
6. Phase 2 — Label tiếng Anh chính xác

Đây là phase nên làm ngay tiếp theo.

Lý do

Không nên để Flux viết chữ. Flux có thể viết sai hoặc méo text. Không nên dựa vào bbox phòng của Gemini vì bbox có thể sai.

Cách tốt nhất là:

Detect trực tiếp các ô chữ nhật label trong output.png
→ cho operator map text tiếng Anh
→ dùng code vẽ text vào đúng box
Service cần thêm
app/services/label_box_detector.py
app/services/output_text_editor.py
Flow
output.png 1200x1200
  → detect label boxes bằng OpenCV
  → save detected_label_boxes.json
  → create manual_labels.json
  → operator điền Living Room / Bed Room / Kitchen...
  → apply labels bằng Pillow
  → update output.png
  → update quality_check.json
detected_label_boxes.json
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
      "confidence": 0.88
    }
  ]
}
manual_labels.json
{
  "version": "1.0",
  "labels": [
    {
      "id": "label_1",
      "text": "Living Room",
      "bbox": [430, 360, 620, 405],
      "locked": false
    },
    {
      "id": "label_2",
      "text": "Bed Room",
      "bbox": [160, 320, 330, 365],
      "locked": false
    }
  ]
}
API cần có
GET  /api/runs/{run_id}/label-boxes
GET  /api/runs/{run_id}/manual-labels
PUT  /api/runs/{run_id}/manual-labels
POST /api/runs/{run_id}/apply-manual-labels
Frontend cần có

Trong debug/production panel:

- số label box detect được
- textarea manual_labels.json
- nút Save labels
- nút Apply labels
- reload output image sau khi apply
Quy tắc để không che nội thất
- Chỉ vẽ trong bbox label cũ
- Padding tối đa 2–4px
- Không mở rộng box theo room bbox
- Auto shrink font
- Nếu text không vừa thì xuống 2 dòng
- Nếu vẫn không vừa thì needs_review
Done khi
- Detect được các label rectangle
- Người dùng nhập English labels
- Apply labels xong output.png có chữ tiếng Anh rõ ràng
- Không che nội thất quá vùng label cũ
- quality_check english_labels_status = done
7. Phase 3 — Furniture layout JSON
Mục tiêu

Biến furniture thành object có thể chỉnh sửa, không phải chỉ là prompt hoặc ảnh chết.

File cần tạo
furniture_layout.json
Schema đề xuất
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
Cách tạo ban đầu

AI/Gemini/GPT chỉ nên gợi ý:

- phòng nào có nội thất gì
- loại sofa/bed/table
- màu sắc tương đối
- size_hint

Không tin tuyệt đối tọa độ AI.

Tọa độ ban đầu có thể dùng rule:

Bedroom → bed ở trung tâm
Living Room → sofa/table ở vùng giữa
Kitchen → không thêm đồ lớn
Entrance → shoe cabinet/rug

Sau đó operator chỉnh.

Done khi
- Mỗi run có furniture_layout.json
- /api/runs/{run_id} trả furniture_layout
- Dữ liệu đủ để frontend vẽ object layer
8. Phase 4 — Canvas editor
Mục tiêu

Cho người vận hành chỉnh layout/nội thất/label trước khi xuất ảnh.

Công nghệ

Vì frontend hiện tại là static HTML/CSS/JS, nên dùng:

Fabric.js

Nếu sau này chuyển sang React/Next.js thì dùng:

Konva.js / React-Konva
UI editor cần có
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
API cần có
GET  /api/runs/{run_id}/layout
PUT  /api/runs/{run_id}/layout
POST /api/runs/{run_id}/render-layout
Render layout

Ban đầu render bằng Pillow hoặc frontend canvas export:

floorplan background
+ furniture object symbols
+ labels
→ preview_layout.png
Done khi
- Operator chỉnh được furniture trên UI
- Save lại furniture_layout.json
- Render preview 1200x1200
9. Phase 5 — Phân tích ảnh nội thất tham khảo
Lý do

Tài liệu yêu cầu nội thất và sàn dựa theo ảnh phòng thực tế: sofa 3 người, màu sàn, màu giường/sofa, màu cushion/pillow, kích thước giường dựa theo số gối.

Input
- listing URL
- hoặc upload interior photos
Endpoint
POST /api/analyze-interior-reference
Output
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
Model dùng

Có thể dùng:

Gemini Vision / GPT-5 mini vision / Claude vision

Nhưng chỉ để phân tích:

có đồ gì, màu gì, size_hint gì

Không dùng model để quyết định layout cuối.

Done khi
- Upload ảnh nội thất
- AI trả furniture/floor/color hints
- Dữ liệu được merge vào furniture_layout.json
10. Phase 6 — Render final chính xác

Có 2 hướng.

Hướng A — Render bằng code/template

Đây là hướng chính xác hơn.

floorplan/layout JSON/furniture JSON/labels
→ Pillow/SVG renderer
→ output.png 1200x1200

Ưu điểm:

- kiểm soát layout
- chữ đúng
- furniture không chạy lung tung
- dễ nghiệm thu

Nhược điểm:

- watercolor style cần đầu tư renderer
- hình có thể kém tự nhiên hơn AI
Hướng B — AI stylize nhẹ
render guide chính xác
→ AI stylize watercolor
→ kiểm tra layout
→ nếu lệch thì reject

Ưu điểm:

- đẹp hơn

Nhược điểm:

- vẫn có nguy cơ lệch layout

Với khách hàng này, nên dùng:

A là chính
B là optional enhancement
11. Phase 7 — Quality gate và manual review
Review checklist

Mỗi run cần có trạng thái:

{
  "layout_accuracy_status": "manual_review_required",
  "layout_checked_by": null,
  "layout_checked_at": null,
  "english_labels_status": "done",
  "watercolor_quality_status": "manual_review_required",
  "final_approval_status": "pending"
}
UI cần có
- Before/after comparison
- Zoom ảnh input và output
- Checklist panel
- Mark layout passed
- Mark label passed
- Mark watercolor passed
- Export/download final
Trạng thái
draft
needs_label_review
needs_layout_review
needs_style_review
approved
delivered
Done khi
- Không output nào được coi là approved nếu chưa có người check
- Có quality_check.json lưu trạng thái
12. Phase 8 — Deploy production
Kiến trúc deploy khuyến nghị

Không nên để backend chính trên Vercel lâu dài.

Frontend: Vercel
Backend FastAPI: Render / Railway / Fly.io / VPS
Storage ảnh: Cloudinary / S3 / R2
Metadata: PostgreSQL / Supabase / MongoDB

Vercel chỉ nên dùng frontend vì:

- serverless có timeout
- /tmp không persistent
- xử lý AI có thể lâu
- cần lưu artifact nhiều file
Storage

Hiện tại bạn đang lưu:

runs/
uploads/
outputs/

Production nên chuyển thành:

Cloudinary/S3:
  floorplan_original
  output.png
  preview images

Database:
  run_id
  artifact URLs
  quality status
  furniture_layout JSON
  manual_labels JSON
Done khi
- File không mất sau redeploy/cold start
- Download dùng Cloudinary/S3 URL hoặc backend FileResponse từ storage
- Có user/session hoặc admin page
13. Quy trình làm 3 sample đầu tiên

Đây là quy trình nên áp dụng ngay với khách.

Bước 1 — Import data
Import madori_964113
Import madori_782638
Import madori_812340
Bước 2 — Tạo bản nháp
AI phân tích phòng
AI gợi ý furniture
Output 1200x1200
Bước 3 — Label
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
Bước 4 — Furniture review
Mở editor
Chỉnh sofa/bed/table đúng ảnh nội thất
Chỉnh màu sàn
Chỉnh màu cushion/pillow
Bước 5 — Render final
Render output.png 1200x1200
Apply labels
Update quality_check
Bước 6 — Kiểm tra
So sánh input/output
Check tường/cửa/cửa sổ
Check equipment
Check label
Check watercolor style
Bước 7 — Gửi khách
Gửi 3 file output.png
Gửi note nếu có điểm cần xác nhận
Chờ feedback
14. Thứ tự triển khai kỹ thuật cụ thể

Từ trạng thái code hiện tại, nên làm theo thứ tự này:

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
15. Mốc thời gian đề xuất
Tuần 1
- Chốt Phase 1
- Label box detector
- Manual labels API
- Apply English labels
- Frontend label editor đơn giản

Kết quả: output có English labels chính xác.

Tuần 2
- furniture_layout.json
- API save/load layout
- Renderer preview đơn giản

Kết quả: có dữ liệu object để chỉnh furniture.

Tuần 3
- Fabric.js canvas editor
- Drag/resize/rotate furniture
- Save layout
- Render preview 1200x1200

Kết quả: operator chỉnh được nội thất.

Tuần 4
- Interior reference analyzer
- Gợi ý màu sàn/sofa/bed
- Quality review UI

Kết quả: gần sát yêu cầu tài liệu.

Tuần 5+
- Cloud storage/database
- Admin workflow
- Production deploy
- Batch processing nhiều căn
16. Nguyên tắc kỹ thuật bắt buộc
1. Không tin Flux cho layout chính xác 100%
2. Không tin Gemini bbox là tọa độ cuối
3. Không để AI viết chữ cuối cùng
4. English labels phải do code/manual kiểm soát
5. Output luôn 1200x1200
6. Mọi output giao khách phải qua manual review
7. Debug overlay không phải final output
8. final output chỉ là output.png