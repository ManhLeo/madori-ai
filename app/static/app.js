const form = document.querySelector("#generateForm");
const fileInput = document.querySelector("#floorplanInput");
const fileName = document.querySelector("#fileName");
const statusEl = document.querySelector("#formStatus");
const resultPanel = document.querySelector("#resultPanel");
const inputPreviewImage = document.querySelector("#inputPreviewImage");
const outputImage = document.querySelector("#outputImage");
const downloadOutputBtn = document.querySelector("#downloadOutputBtn");
const overlayImage = document.querySelector("#overlayImage");
const overlayDebugImage = document.querySelector("#overlayDebugImage");
const qualitySummaryPanel = document.querySelector("#qualitySummaryPanel");
const manualReviewBadge = document.querySelector("#manualReviewBadge");
const qualityOutputSize = document.querySelector("#qualityOutputSize");
const qualityEnglishLabels = document.querySelector("#qualityEnglishLabels");
const detectedLabelBoxCount = document.querySelector("#detectedLabelBoxCount");
const ocrTextBoxCount = document.querySelector("#ocrTextBoxCount");
const autoLabelSuggestionCount = document.querySelector("#autoLabelSuggestionCount");
const qualityLayoutAccuracy = document.querySelector("#qualityLayoutAccuracy");
const layoutLockStatus = document.querySelector("#layoutLockStatus");
const layoutGuardScore = document.querySelector("#layoutGuardScore");
const layoutGuardCompareRegion = document.querySelector("#layoutGuardCompareRegion");
const layoutLockPreview = document.querySelector("#layoutLockPreview");
const normalizedFloorplanLink = document.querySelector("#normalizedFloorplanLink");
const structureMaskLink = document.querySelector("#structureMaskLink");
const structureLayerLink = document.querySelector("#structureLayerLink");
const layoutDiffLink = document.querySelector("#layoutDiffLink");
const layoutGuardReferenceCropLink = document.querySelector("#layoutGuardReferenceCropLink");
const layoutGuardOutputCropLink = document.querySelector("#layoutGuardOutputCropLink");
const aiDraftOutputLink = document.querySelector("#aiDraftOutputLink");
const normalizedFloorplanImage = document.querySelector("#normalizedFloorplanImage");
const layoutDiffImage = document.querySelector("#layoutDiffImage");
const qualityWatercolor = document.querySelector("#qualityWatercolor");
const manualLabelsJson = document.querySelector("#manualLabelsJson");
const saveManualLabelsBtn = document.querySelector("#saveManualLabelsBtn");
const applyManualLabelsBtn = document.querySelector("#applyManualLabelsBtn");
const autoDetectLabelsBtn = document.querySelector("#autoDetectLabelsBtn");
const manualLabelsStatus = document.querySelector("#manualLabelsStatus");
const manualLabelTextInput = document.querySelector("#manualLabelTextInput");
const addLabelBoxBtn = document.querySelector("#addLabelBoxBtn");
const labelImageEditor = document.querySelector("#labelImageEditor");
const labelEditorImage = document.querySelector("#labelEditorImage");
const labelEditorOverlay = document.querySelector("#labelEditorOverlay");

let currentInputPreviewUrl = null;
let currentDownloadUrl = "";
let currentRunId = "";
let labelDrawMode = false;
let labelDraft = null;

fileInput?.addEventListener("change", () => {
  const file = fileInput.files?.[0];
  fileName.textContent = file ? file.name : "ファイル未選択";
  clearOutputPreview();

  if (file) {
    showInputPreview(file);
    resultPanel.hidden = false;
  } else {
    clearInputPreview();
    resultPanel.hidden = true;
  }
});

form?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const file = fileInput.files?.[0];
  if (!file) {
    setStatus("間取り図ファイルを選択してください。", true);
    return;
  }

  const formData = new FormData(form);
  setStatus("生成中です。AIが間取り図を解析し、イラストを作成しています...", false);
  clearOutputPreview();
  resultPanel.hidden = false;

  try {
    const generatePayload = await postGenerate(formData);
    const runId = generatePayload.run_id;
    if (!runId) {
      throw new Error("生成レスポンスにrun_idが含まれていません。");
    }

    renderGeneratedOutput(generatePayload);
    setStatus(`生成結果を表示しました。Run ID: ${runId}`, false);

    try {
      const runPayload = await fetchRunInspection(runId);
      renderRunResult(runPayload, generatePayload);
    } catch (inspectionError) {
      console.warn("Run inspection failed; keeping generated output_url preview.", inspectionError);
    }
  } catch (error) {
    setStatus(error instanceof Error ? error.message : "生成に失敗しました。", true);
  }
});

async function postGenerate(formData) {
  const response = await fetch("/api/generate", {
    method: "POST",
    body: formData,
  });
  const payload = await readJsonResponse(response, "POST /api/generate");
  if (!response.ok) {
    throw new Error(payload.detail || "POST /api/generate が失敗しました。");
  }
  return payload;
}

async function fetchRunInspection(runId) {
  const response = await fetch(`/api/runs/${encodeURIComponent(runId)}`);
  const payload = await readJsonResponse(response, `GET /api/runs/${runId}`);
  if (!response.ok) {
    throw new Error(payload.detail || `GET /api/runs/${runId} が失敗しました。`);
  }
  return payload;
}

async function readJsonResponse(response, label) {
  try {
    return await response.json();
  } catch {
    throw new Error(`${label} のJSON解析に失敗しました。`);
  }
}

function showInputPreview(file) {
  clearInputPreview();
  currentInputPreviewUrl = URL.createObjectURL(file);
  inputPreviewImage.src = currentInputPreviewUrl;
  inputPreviewImage.hidden = false;
}

function clearInputPreview() {
  if (currentInputPreviewUrl) {
    URL.revokeObjectURL(currentInputPreviewUrl);
    currentInputPreviewUrl = null;
  }
  inputPreviewImage.removeAttribute("src");
  inputPreviewImage.hidden = true;
}

function renderRunResult(runPayload, generatePayload) {
  const files = runPayload.files || {};
  const outputUrl = runPayload.output_url || generatePayload.output_url || toRunUrl(files.output) || "";
  currentRunId = runPayload.run_id || generatePayload.run_id || "";
  currentDownloadUrl = outputUrl;

  setOutputPreviewUrl(outputUrl);
  outputImage.hidden = !outputUrl;
  downloadOutputBtn.hidden = !outputUrl;

  if (overlayImage) {
    const overlayUrl = toRunUrl(files.overlay);
    overlayImage.src = overlayUrl;
    overlayImage.hidden = !overlayUrl;
  }

  if (overlayDebugImage) {
    const overlayDebugUrl = toRunUrl(files.overlay_debug);
    overlayDebugImage.src = overlayDebugUrl;
    overlayDebugImage.hidden = !overlayDebugUrl;
  }

  renderQualitySummary(
    runPayload.quality_check,
    runPayload.output_label_edit,
    runPayload.generation_debug,
    runPayload.detected_label_boxes,
    runPayload.ocr_text_boxes,
    runPayload.auto_label_suggestions,
    files,
  );
  renderManualLabels(runPayload.manual_labels);

  resultPanel.hidden = false;
}

function renderGeneratedOutput(generatePayload) {
  const outputUrl = generatePayload.output_url || "";
  currentRunId = generatePayload.run_id || "";
  currentDownloadUrl = outputUrl;

  setOutputPreviewUrl(outputUrl);
  outputImage.hidden = !outputUrl;
  downloadOutputBtn.hidden = !outputUrl;
  resultPanel.hidden = false;
}

function clearOutputPreview() {
  outputImage.removeAttribute("src");
  outputImage.hidden = true;
  if (labelEditorImage) {
    labelEditorImage.removeAttribute("src");
  }
  if (labelImageEditor) {
    labelImageEditor.hidden = true;
  }
  currentDownloadUrl = "";
  currentRunId = "";
  downloadOutputBtn.hidden = true;

  if (overlayImage) {
    overlayImage.removeAttribute("src");
    overlayImage.hidden = true;
  }

  if (overlayDebugImage) {
    overlayDebugImage.removeAttribute("src");
    overlayDebugImage.hidden = true;
  }

  clearQualitySummary();
  clearManualLabelsEditor();
}

function toRunUrl(path) {
  if (!path) return "";
  if (/^https?:\/\//.test(path)) return path;
  return path.startsWith("/") ? path : `/${path}`;
}

function setStatus(message, isError) {
  statusEl.textContent = message;
  statusEl.classList.toggle("is-error", Boolean(isError));
}

function renderQualitySummary(qualityCheck, outputLabelEdit, generationDebug, detectedLabelBoxes, ocrTextBoxes, autoLabelSuggestions, files = {}) {
  if (!qualitySummaryPanel || !qualityCheck) return;

  const actualSize = qualityCheck.output_size_actual || formatOutputSize(generationDebug);
  const requiredSize = qualityCheck.output_size_required || "";
  qualityOutputSize.textContent = requiredSize && actualSize ? `${actualSize} / required ${requiredSize}` : actualSize || "-";
  qualityEnglishLabels.textContent = outputLabelEdit?.status || qualityCheck.english_labels_status || "-";
  if (detectedLabelBoxCount) {
    detectedLabelBoxCount.textContent = String(detectedLabelBoxes?.boxes?.length ?? 0);
  }
  if (ocrTextBoxCount) {
    ocrTextBoxCount.textContent = String(ocrTextBoxes?.texts?.length ?? qualityCheck.ocr_text_count ?? 0);
  }
  if (autoLabelSuggestionCount) {
    autoLabelSuggestionCount.textContent = String(autoLabelSuggestions?.labels?.length ?? qualityCheck.auto_label_suggestion_count ?? 0);
  }
  qualityLayoutAccuracy.textContent = qualityCheck.layout_accuracy_status || "manual_review_required";
  if (layoutLockStatus) {
    const lockEnabled = qualityCheck.layout_lock_enabled ?? Boolean(generationDebug?.layout_locked_render);
    const guardStatus = qualityCheck.layout_guard_status || generationDebug?.layout_guard?.status || "not_run";
    layoutLockStatus.textContent = lockEnabled ? `enabled / ${guardStatus}` : "disabled";
  }
  if (layoutGuardScore) {
    const score = qualityCheck.layout_guard_score ?? generationDebug?.layout_guard?.score;
    layoutGuardScore.textContent = score === null || score === undefined ? "-" : String(score);
  }
  if (layoutGuardCompareRegion) {
    layoutGuardCompareRegion.textContent = qualityCheck.layout_guard_compare_region || generationDebug?.layout_guard_compare_region || "-";
  }
  renderLayoutLockPreview(files);
  qualityWatercolor.textContent = qualityCheck.watercolor_quality_status || "manual_review_required";

  if (manualReviewBadge) {
    manualReviewBadge.hidden = !qualityCheck.needs_manual_review;
  }
  qualitySummaryPanel.hidden = false;
}

function renderManualLabels(manualLabels) {
  if (!manualLabelsJson) return;
  manualLabelsJson.value = JSON.stringify(manualLabels || emptyManualLabels(), null, 2);
  renderLabelOverlayBoxes();
  setManualLabelsStatus("", false);
}

function clearQualitySummary() {
  if (!qualitySummaryPanel) return;
  qualitySummaryPanel.hidden = true;
  if (qualityOutputSize) qualityOutputSize.textContent = "-";
  if (qualityEnglishLabels) qualityEnglishLabels.textContent = "-";
  if (detectedLabelBoxCount) detectedLabelBoxCount.textContent = "-";
  if (ocrTextBoxCount) ocrTextBoxCount.textContent = "-";
  if (autoLabelSuggestionCount) autoLabelSuggestionCount.textContent = "-";
  if (qualityLayoutAccuracy) qualityLayoutAccuracy.textContent = "Manual review required";
  if (layoutLockStatus) layoutLockStatus.textContent = "-";
  if (layoutGuardScore) layoutGuardScore.textContent = "-";
  if (layoutGuardCompareRegion) layoutGuardCompareRegion.textContent = "-";
  clearLayoutLockPreview();
  if (qualityWatercolor) qualityWatercolor.textContent = "Manual review required";
}

function renderLayoutLockPreview(files) {
  if (!layoutLockPreview) return;
  const links = [
    [normalizedFloorplanLink, files.normalized_floorplan],
    [structureMaskLink, files.structure_mask],
    [structureLayerLink, files.structure_layer],
    [layoutDiffLink, files.layout_diff],
    [layoutGuardReferenceCropLink, files.layout_guard_reference_crop],
    [layoutGuardOutputCropLink, files.layout_guard_output_crop],
    [aiDraftOutputLink, files.ai_draft_output],
  ];
  let hasAny = false;
  for (const [element, path] of links) {
    if (!element) continue;
    const url = toRunUrl(path);
    element.href = url || "#";
    element.hidden = !url;
    hasAny = hasAny || Boolean(url);
  }
  const normalizedUrl = toRunUrl(files.normalized_floorplan);
  if (normalizedFloorplanImage) {
    normalizedFloorplanImage.src = normalizedUrl;
    normalizedFloorplanImage.hidden = !normalizedUrl;
  }
  const diffUrl = toRunUrl(files.layout_diff);
  if (layoutDiffImage) {
    layoutDiffImage.src = diffUrl;
    layoutDiffImage.hidden = !diffUrl;
  }
  layoutLockPreview.hidden = !hasAny;
}

function clearLayoutLockPreview() {
  if (!layoutLockPreview) return;
  layoutLockPreview.hidden = true;
  for (const element of [
    normalizedFloorplanLink,
    structureMaskLink,
    structureLayerLink,
    layoutDiffLink,
    layoutGuardReferenceCropLink,
    layoutGuardOutputCropLink,
    aiDraftOutputLink,
  ]) {
    if (!element) continue;
    element.removeAttribute("href");
    element.hidden = true;
  }
  for (const image of [normalizedFloorplanImage, layoutDiffImage]) {
    if (!image) continue;
    image.removeAttribute("src");
    image.hidden = true;
  }
}

function clearManualLabelsEditor() {
  if (manualLabelsJson) {
    manualLabelsJson.value = "";
  }
  if (labelEditorOverlay) {
    labelEditorOverlay.innerHTML = "";
  }
  labelDrawMode = false;
  setManualLabelsStatus("", false);
}

function formatOutputSize(generationDebug) {
  if (!generationDebug?.output_width || !generationDebug?.output_height) return "";
  return `${generationDebug.output_width}x${generationDebug.output_height}`;
}

function setOutputPreviewUrl(outputUrl) {
  if (outputImage) {
    outputImage.src = outputUrl;
  }
  if (labelEditorImage && labelImageEditor) {
    labelEditorImage.src = outputUrl;
    labelImageEditor.hidden = !outputUrl;
    labelEditorImage.addEventListener("load", renderLabelOverlayBoxes, { once: true });
  }
}

downloadOutputBtn?.addEventListener("click", async () => {
  if (!currentDownloadUrl) return;

  try {
    const response = await fetch(currentDownloadUrl);
    if (!response.ok) {
      throw new Error("download request failed");
    }
    const blob = await response.blob();
    const objectUrl = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = objectUrl;
    link.download = `madori-ai-${currentRunId || "output"}.png`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(objectUrl);
  } catch (error) {
    console.warn("Blob download failed; falling back to direct navigation.", error);
    window.location.href = toAttachmentUrl(currentDownloadUrl, currentRunId);
  }
});

function toAttachmentUrl(url, runId) {
  if (!url || !url.includes("res.cloudinary.com") || !url.includes("/upload/")) {
    return url;
  }
  const filename = `madori-ai-${runId || "output"}`;
  return url.replace("/upload/", `/upload/fl_attachment:${encodeURIComponent(filename)}/`);
}

saveManualLabelsBtn?.addEventListener("click", async () => {
  if (!currentRunId) return;
  try {
    const payload = parseManualLabels();
    const saved = await putManualLabels(currentRunId, payload);
    renderManualLabels(saved);
    setManualLabelsStatus("Manual labels saved.", false);
  } catch (error) {
    setManualLabelsStatus(error instanceof Error ? error.message : "Failed to save manual labels.", true);
  }
});

applyManualLabelsBtn?.addEventListener("click", async () => {
  if (!currentRunId) return;
  try {
    const payload = parseManualLabels();
    await putManualLabels(currentRunId, payload);
    const applied = await applyManualLabels(currentRunId);
    setManualLabelsStatus("Manual labels applied to output image.", false);
    const runPayload = await fetchRunInspection(currentRunId);
    renderRunResult(runPayload, { run_id: currentRunId, output_url: applied.output_url || currentDownloadUrl });
    reloadOutputImage();
  } catch (error) {
    setManualLabelsStatus(error instanceof Error ? error.message : "Failed to apply manual labels.", true);
  }
});

autoDetectLabelsBtn?.addEventListener("click", async () => {
  if (!currentRunId) return;
  try {
    setManualLabelsStatus("Running OCR label detection...", false);
    const detected = await autoDetectLabels(currentRunId);
    if (detected.manual_labels) {
      renderManualLabels(detected.manual_labels);
    }
    const runPayload = await fetchRunInspection(currentRunId);
    renderRunResult(runPayload, { run_id: currentRunId, output_url: currentDownloadUrl });
    setManualLabelsStatus(
      `Auto detect complete. OCR texts: ${detected.ocr_text_count || 0}, suggestions: ${detected.auto_label_suggestion_count || 0}.`,
      false,
    );
  } catch (error) {
    setManualLabelsStatus(error instanceof Error ? error.message : "Failed to auto detect labels.", true);
  }
});

function parseManualLabels() {
  try {
    return JSON.parse(manualLabelsJson?.value || "{}");
  } catch {
    throw new Error("Manual labels JSON is invalid.");
  }
}

async function putManualLabels(runId, payload) {
  const response = await fetch(`/api/runs/${encodeURIComponent(runId)}/manual-labels`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const responsePayload = await readJsonResponse(response, `PUT /api/runs/${runId}/manual-labels`);
  if (!response.ok) {
    throw new Error(responsePayload.detail || "Failed to save manual labels.");
  }
  return responsePayload;
}

async function applyManualLabels(runId) {
  const response = await fetch(`/api/runs/${encodeURIComponent(runId)}/apply-manual-labels`, {
    method: "POST",
  });
  const responsePayload = await readJsonResponse(response, `POST /api/runs/${runId}/apply-manual-labels`);
  if (!response.ok) {
    throw new Error(responsePayload.detail || "Failed to apply manual labels.");
  }
  return responsePayload;
}

async function autoDetectLabels(runId) {
  const response = await fetch(`/api/runs/${encodeURIComponent(runId)}/auto-detect-labels`, {
    method: "POST",
  });
  const responsePayload = await readJsonResponse(response, `POST /api/runs/${runId}/auto-detect-labels`);
  if (!response.ok) {
    throw new Error(responsePayload.detail || "Failed to auto detect labels.");
  }
  return responsePayload;
}

function reloadOutputImage() {
  if (!outputImage?.src) return;
  const separator = outputImage.src.includes("?") ? "&" : "?";
  const refreshedUrl = `${outputImage.src}${separator}labels=${Date.now()}`;
  outputImage.src = refreshedUrl;
  if (labelEditorImage) {
    labelEditorImage.src = refreshedUrl;
    labelEditorImage.addEventListener("load", renderLabelOverlayBoxes, { once: true });
  }
}

function emptyManualLabels() {
  return { version: "1.0", source: "manual", needs_manual_review: true, labels: [] };
}

function setManualLabelsStatus(message, isError) {
  if (!manualLabelsStatus) return;
  manualLabelsStatus.textContent = message;
  manualLabelsStatus.classList.toggle("is-error", Boolean(isError));
}

document.querySelectorAll("[data-label-preset]").forEach((button) => {
  button.addEventListener("click", () => {
    if (manualLabelTextInput) {
      manualLabelTextInput.value = button.dataset.labelPreset || "";
    }
  });
});

addLabelBoxBtn?.addEventListener("click", () => {
  if (!labelEditorImage?.src) {
    setManualLabelsStatus("Generate an output image before drawing labels.", true);
    return;
  }
  labelDrawMode = true;
  labelEditorOverlay?.classList.remove("is-idle");
  setManualLabelsStatus("Drag on the output image to create a label box.", false);
});

labelEditorOverlay?.addEventListener("pointerdown", (event) => {
  if (!labelDrawMode || !labelEditorImage?.naturalWidth) return;
  const point = imagePointFromEvent(event);
  labelDraft = {
    start: point,
    element: document.createElement("div"),
  };
  labelDraft.element.className = "label-editor-box is-draft";
  labelEditorOverlay.appendChild(labelDraft.element);
  labelEditorOverlay.setPointerCapture(event.pointerId);
});

labelEditorOverlay?.addEventListener("pointermove", (event) => {
  if (!labelDraft) return;
  updateDraftBox(labelDraft.start, imagePointFromEvent(event), labelDraft.element);
});

labelEditorOverlay?.addEventListener("pointerup", (event) => {
  if (!labelDraft) return;
  const end = imagePointFromEvent(event);
  const bbox = bboxFromPoints(labelDraft.start, end);
  labelDraft.element.remove();
  labelDraft = null;
  labelDrawMode = false;
  labelEditorOverlay.classList.add("is-idle");

  if (bbox[2] - bbox[0] < 8 || bbox[3] - bbox[1] < 8) {
    setManualLabelsStatus("Label box is too small. Try drawing a larger rectangle.", true);
    return;
  }

  const manualLabels = parseManualLabelsOrEmpty();
  const text = (manualLabelTextInput?.value || "").trim();
  manualLabels.labels.push({
    id: `label_${Date.now()}`,
    text,
    bbox,
    locked: false,
    needs_text: !text,
  });
  manualLabels.source = "manual";
  manualLabels.needs_manual_review = true;
  manualLabelsJson.value = JSON.stringify(manualLabels, null, 2);
  renderLabelOverlayBoxes();
  setManualLabelsStatus("Label box added. Save labels before applying.", false);
});

function imagePointFromEvent(event) {
  const rect = labelEditorImage.getBoundingClientRect();
  const x = Math.max(0, Math.min(rect.width, event.clientX - rect.left));
  const y = Math.max(0, Math.min(rect.height, event.clientY - rect.top));
  return {
    x: Math.round((x / rect.width) * labelEditorImage.naturalWidth),
    y: Math.round((y / rect.height) * labelEditorImage.naturalHeight),
  };
}

function bboxFromPoints(start, end) {
  return [
    Math.min(start.x, end.x),
    Math.min(start.y, end.y),
    Math.max(start.x, end.x),
    Math.max(start.y, end.y),
  ];
}

function updateDraftBox(start, end, element) {
  const bbox = bboxFromPoints(start, end);
  positionOverlayBox(element, bbox);
}

function renderLabelOverlayBoxes() {
  if (!labelEditorOverlay || !labelEditorImage?.naturalWidth) return;
  labelEditorOverlay.innerHTML = "";
  const manualLabels = parseManualLabelsOrEmpty();
  for (const label of manualLabels.labels || []) {
    if (!Array.isArray(label.bbox) || label.bbox.length !== 4) continue;
    const box = document.createElement("div");
    box.className = "label-editor-box";
    box.title = label.text || "Needs text";
    positionOverlayBox(box, label.bbox);
    labelEditorOverlay.appendChild(box);
  }
  if (!labelDrawMode) {
    labelEditorOverlay.classList.add("is-idle");
  }
}

function positionOverlayBox(element, bbox) {
  const rect = labelEditorImage.getBoundingClientRect();
  const scaleX = rect.width / labelEditorImage.naturalWidth;
  const scaleY = rect.height / labelEditorImage.naturalHeight;
  const [x0, y0, x1, y1] = bbox;
  element.style.left = `${x0 * scaleX}px`;
  element.style.top = `${y0 * scaleY}px`;
  element.style.width = `${Math.max(1, (x1 - x0) * scaleX)}px`;
  element.style.height = `${Math.max(1, (y1 - y0) * scaleY)}px`;
}

function parseManualLabelsOrEmpty() {
  try {
    const parsed = JSON.parse(manualLabelsJson?.value || "{}");
    if (!Array.isArray(parsed.labels)) {
      parsed.labels = [];
    }
    if (!parsed.version) parsed.version = "1.0";
    if (!parsed.source) parsed.source = "manual";
    return parsed;
  } catch {
    return emptyManualLabels();
  }
}
