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
const qualityLayoutAccuracy = document.querySelector("#qualityLayoutAccuracy");
const qualityWatercolor = document.querySelector("#qualityWatercolor");
const manualLabelsJson = document.querySelector("#manualLabelsJson");
const saveManualLabelsBtn = document.querySelector("#saveManualLabelsBtn");
const applyManualLabelsBtn = document.querySelector("#applyManualLabelsBtn");
const manualLabelsStatus = document.querySelector("#manualLabelsStatus");

let currentInputPreviewUrl = null;
let currentDownloadUrl = "";
let currentRunId = "";

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

  outputImage.src = outputUrl;
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

  renderQualitySummary(runPayload.quality_check, runPayload.output_label_edit, runPayload.generation_debug);
  renderManualLabels(runPayload.manual_labels);

  resultPanel.hidden = false;
}

function renderGeneratedOutput(generatePayload) {
  const outputUrl = generatePayload.output_url || "";
  currentRunId = generatePayload.run_id || "";
  currentDownloadUrl = outputUrl;

  outputImage.src = outputUrl;
  outputImage.hidden = !outputUrl;
  downloadOutputBtn.hidden = !outputUrl;
  resultPanel.hidden = false;
}

function clearOutputPreview() {
  outputImage.removeAttribute("src");
  outputImage.hidden = true;
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

function renderQualitySummary(qualityCheck, outputLabelEdit, generationDebug) {
  if (!qualitySummaryPanel || !qualityCheck) return;

  const actualSize = qualityCheck.output_size_actual || formatOutputSize(generationDebug);
  const requiredSize = qualityCheck.output_size_required || "";
  qualityOutputSize.textContent = requiredSize && actualSize ? `${actualSize} / required ${requiredSize}` : actualSize || "-";
  qualityEnglishLabels.textContent = outputLabelEdit?.status || qualityCheck.english_labels_status || "-";
  qualityLayoutAccuracy.textContent = qualityCheck.layout_accuracy_status || "manual_review_required";
  qualityWatercolor.textContent = qualityCheck.watercolor_quality_status || "manual_review_required";

  if (manualReviewBadge) {
    manualReviewBadge.hidden = !qualityCheck.needs_manual_review;
  }
  qualitySummaryPanel.hidden = false;
}

function renderManualLabels(manualLabels) {
  if (!manualLabelsJson) return;
  manualLabelsJson.value = JSON.stringify(manualLabels || emptyManualLabels(), null, 2);
  setManualLabelsStatus("", false);
}

function clearQualitySummary() {
  if (!qualitySummaryPanel) return;
  qualitySummaryPanel.hidden = true;
  if (qualityOutputSize) qualityOutputSize.textContent = "-";
  if (qualityEnglishLabels) qualityEnglishLabels.textContent = "-";
  if (qualityLayoutAccuracy) qualityLayoutAccuracy.textContent = "Manual review required";
  if (qualityWatercolor) qualityWatercolor.textContent = "Manual review required";
}

function clearManualLabelsEditor() {
  if (manualLabelsJson) {
    manualLabelsJson.value = "";
  }
  setManualLabelsStatus("", false);
}

function formatOutputSize(generationDebug) {
  if (!generationDebug?.output_width || !generationDebug?.output_height) return "";
  return `${generationDebug.output_width}x${generationDebug.output_height}`;
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

function parseManualLabels() {
  try {
    return JSON.parse(manualLabelsJson?.value || "{}");
  } catch {
    throw new Error("Manual labels JSON is invalid.");
  }
}

async function putManualLabels(runId, payload) {
  const response = await fetch(`/api/runs/${encodeURIComponent(runId)}/labels`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const responsePayload = await readJsonResponse(response, `PUT /api/runs/${runId}/labels`);
  if (!response.ok) {
    throw new Error(responsePayload.detail || "Failed to save manual labels.");
  }
  return responsePayload;
}

async function applyManualLabels(runId) {
  const response = await fetch(`/api/runs/${encodeURIComponent(runId)}/apply-labels`, {
    method: "POST",
  });
  const responsePayload = await readJsonResponse(response, `POST /api/runs/${runId}/apply-labels`);
  if (!response.ok) {
    throw new Error(responsePayload.detail || "Failed to apply manual labels.");
  }
  return responsePayload;
}

function reloadOutputImage() {
  if (!outputImage?.src) return;
  const separator = outputImage.src.includes("?") ? "&" : "?";
  outputImage.src = `${outputImage.src}${separator}labels=${Date.now()}`;
}

function emptyManualLabels() {
  return { version: "1.0", labels: [] };
}

function setManualLabelsStatus(message, isError) {
  if (!manualLabelsStatus) return;
  manualLabelsStatus.textContent = message;
  manualLabelsStatus.classList.toggle("is-error", Boolean(isError));
}
