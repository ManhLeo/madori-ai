const form = document.querySelector("#generateForm");
const floorplanInput = document.querySelector("#floorplanInput");
const interiorPhotosInput = document.querySelector("#interiorPhotosInput");
const fileName = document.querySelector("#fileName");
const interiorFileNames = document.querySelector("#interiorFileNames");
const statusEl = document.querySelector("#formStatus");
const generateButton = document.querySelector("#generateButton");
const pipelinePanel = document.querySelector("#pipelinePanel");
const currentStepLabel = document.querySelector("#currentStepLabel");
const progressList = document.querySelector("#progressList");
const errorBox = document.querySelector("#errorBox");
const uploadDebugPanel = document.querySelector("#uploadDebugPanel");
const debugFloorplanName = document.querySelector("#debugFloorplanName");
const debugInteriorCount = document.querySelector("#debugInteriorCount");
const debugInteriorNames = document.querySelector("#debugInteriorNames");
const debugBackendInteriorCount = document.querySelector("#debugBackendInteriorCount");
const uploadDebugWarning = document.querySelector("#uploadDebugWarning");
const selectedFloorplanGrid = document.querySelector("#selectedFloorplanGrid");
const selectedFloorplanEmpty = document.querySelector("#selectedFloorplanEmpty");
const selectedInteriorGrid = document.querySelector("#selectedInteriorGrid");
const selectedInteriorEmpty = document.querySelector("#selectedInteriorEmpty");
const selectedInteriorCountBadge = document.querySelector("#selectedInteriorCountBadge");
const backendAssetsSection = document.querySelector("#backendAssetsSection");
const backendFloorplanGrid = document.querySelector("#backendFloorplanGrid");
const backendFloorplanEmpty = document.querySelector("#backendFloorplanEmpty");
const backendInteriorGrid = document.querySelector("#backendInteriorGrid");
const backendInteriorEmpty = document.querySelector("#backendInteriorEmpty");
const backendInteriorCountBadge = document.querySelector("#backendInteriorCountBadge");
const resultPanel = document.querySelector("#resultPanel");
const inputPreviewImage = document.querySelector("#inputPreviewImage");
const outputImage = document.querySelector("#outputImage");
const downloadOutputBtn = document.querySelector("#downloadOutputBtn");
const runIdText = document.querySelector("#runIdText");
const outputUrlLink = document.querySelector("#outputUrlLink");
const visualQaPanel = document.querySelector("#visualQaPanel");
const visualQaStatusText = document.querySelector("#visualQaStatusText");
const qaPassedBtn = document.querySelector("#qaPassedBtn");
const qaNeedsFixBtn = document.querySelector("#qaNeedsFixBtn");
const qaFailedBtn = document.querySelector("#qaFailedBtn");
const finalizeOutputBtn = document.querySelector("#finalizeOutputBtn");
const qaFeedbackPanel = document.querySelector("#qaFeedbackPanel");
const qaFeedbackStatusText = document.querySelector("#qaFeedbackStatusText");
const saveQaFeedbackBtn = document.querySelector("#saveQaFeedbackBtn");
const regenerateWithFeedbackBtn = document.querySelector("#regenerateWithFeedbackBtn");
const qaFeedbackNotesInput = document.querySelector("#qaFeedbackNotesInput");
const qaFeedbackPlanSummary = document.querySelector("#qaFeedbackPlanSummary");
const regeneratedOutputPanel = document.querySelector("#regeneratedOutputPanel");
const regeneratedOutputImage = document.querySelector("#regeneratedOutputImage");
const regeneratedOutputUrlLink = document.querySelector("#regeneratedOutputUrlLink");
const regeneratedOutputStatusText = document.querySelector("#regeneratedOutputStatusText");
const finalOutputPanel = document.querySelector("#finalOutputPanel");
const finalOutputImage = document.querySelector("#finalOutputImage");
const finalOutputUrlLink = document.querySelector("#finalOutputUrlLink");
const finalOutputStatusText = document.querySelector("#finalOutputStatusText");

function byId(id) {
  return document.getElementById(id);
}

function setText(id, value) {
  const el = byId(id);
  if (el) el.textContent = value ?? "";
}

function setHtml(id, value) {
  const el = byId(id);
  if (el) el.innerHTML = value ?? "";
}

function setDisplay(id, displayValue) {
  const el = byId(id);
  if (el) el.style.display = displayValue;
}

function setSrc(id, value) {
  const el = byId(id);
  if (el) el.src = value ?? "";
}

function setHidden(id, hidden) {
  const el = byId(id);
  if (el) el.hidden = Boolean(hidden);
}

function setHref(id, value) {
  const el = byId(id);
  if (el) {
    if (value) {
      el.href = value;
    } else {
      el.removeAttribute("href");
    }
  }
}

function setDisabled(id, disabled) {
  const el = byId(id);
  if (el) el.disabled = Boolean(disabled);
}

function setValue(id, value) {
  const el = byId(id);
  if (el) el.value = value ?? "";
}

const PIPELINE_STEPS = [
  ["Inspecting input", "inspect"],
  ["Preprocessing floorplan", "preprocess-floorplan"],
  ["Analyzing floorplan", "analyze-floorplan"],
  ["Validating floorplan", "validate-floorplan-analysis"],
  ["Analyzing interiors", "analyze-interiors"],
  ["Validating interiors", "validate-interior-analysis"],
  ["Creating layout", "create-initial-layout"],
  ["Validating layout", "validate-layout"],
  ["Planning furniture", "plan-furniture-placement"],
  ["Validating furniture", "validate-furniture-placement"],
  ["Creating render plan", "create-render-plan"],
  ["Creating prompt package", "create-prompt-package"],
  ["Previewing image generation request", "preview-image-generation-request"],
];

let currentInputPreviewUrl = null;
let currentOutputUrl = "";
let currentRunId = "";
let currentFinalOutputUrl = "";
let selectedFloorplanPreviewUrl = null;
let selectedInteriorPreviewUrls = [];
let visualQaRequestInFlight = false;
let finalizeRequestInFlight = false;
let qaFeedbackRequestInFlight = false;
let regenerateRequestInFlight = false;
let qaFeedbackSavedForRunId = "";
let latestRegenerationResult = null;

initializeProgressList();
renderSelectedFloorplanPreview(null);
renderSelectedInteriorPreviews([]);
syncPostResultControls();
clearFinalOutputResult();
clearRegeneratedOutputResult();

floorplanInput?.addEventListener("change", () => {
  const file = floorplanInput.files?.[0];
  fileName.textContent = file ? file.name : "No floorplan selected";
  clearRunArtifacts();
  renderSelectedFloorplanPreview(file || null);
  if (file) {
    showInputPreview(file);
    resultPanel.hidden = false;
  } else {
    clearInputPreview();
  }
});

interiorPhotosInput?.addEventListener("change", () => {
  const files = Array.from(interiorPhotosInput.files || []);
  interiorFileNames.textContent = files.length
    ? files.map((file) => file.name).join(", ")
    : "No interior photos selected. Interior photos are optional.";
  clearBackendAssetPreviews();
  renderSelectedInteriorPreviews(files);
});

qaPassedBtn?.addEventListener("click", () => {
  void submitVisualQa("passed");
});

qaNeedsFixBtn?.addEventListener("click", () => {
  void submitVisualQa("needs_fix");
});

qaFailedBtn?.addEventListener("click", () => {
  void submitVisualQa("failed");
});

finalizeOutputBtn?.addEventListener("click", () => {
  void finalizeCurrentRunOutput();
});

saveQaFeedbackBtn?.addEventListener("click", () => {
  void submitQaFeedback();
});

regenerateWithFeedbackBtn?.addEventListener("click", () => {
  void regenerateWithFeedback();
});

form?.addEventListener("submit", async (event) => {
  event.preventDefault();

  const floorplan = floorplanInput.files?.[0];
  if (!floorplan) {
    const message = "Please upload one floorplan image before starting.";
    showError(message);
    setStatus(message, true);
    return;
  }

  clearRunArtifacts();
  resetProgress();
  setRunning(true);
  hideError();
  resultPanel.hidden = false;

  try {
    if (!interiorPhotosInput.files?.length) {
      setStatus("No interior photos selected. Continuing with the floorplan only.", false);
    }

    const createPayload = await runStep("Creating run", async () => {
      const interiorFiles = Array.from(interiorPhotosInput.files || []);
      renderUploadDebug(floorplan, interiorFiles, null);

      const formData = new FormData();
      formData.append("floorplan", floorplan);
      for (const photo of interiorFiles) {
        formData.append("interior_photos", photo);
      }
      logCreateRunFormData(floorplan, interiorFiles, formData);
      console.log("[flow] using staged endpoint: POST /api/runs");
      return postForm("/api/runs", formData);
    });

    const runId = createPayload.run_id;
    if (!runId) {
      throw new Error("Backend did not return run_id after creating the run.");
    }
    currentRunId = runId;

    const interiorFiles = Array.from(interiorPhotosInput.files || []);
    const metadata = await resolveRunMetadata(runId);
    const uploadedInteriorCount = getInteriorPhotoCount(metadata);
    renderUploadDebug(floorplan, interiorFiles, uploadedInteriorCount);
    renderBackendAssetPreviews(runId, metadata);
    if (interiorFiles.length > 0 && uploadedInteriorCount === 0) {
      showUploadWarning("Interior photos were selected but backend metadata shows 0 uploaded. Check multipart field name interior_photos.");
    }

    for (const [label, endpoint] of PIPELINE_STEPS) {
      await runStep(label, () => postJson(`/api/runs/${encodeURIComponent(runId)}/${endpoint}`));
    }

    const draft = await runStep("Generating OpenAI draft", () =>
      postJson(`/api/runs/${encodeURIComponent(runId)}/generate-image-draft`, {
        confirm_generation: true,
        provider: "openai",
        output_format: "png",
        use_reference_images: true,
        max_reference_images: 3,
      }),
    );

    const loadedDraft = await runStep("Loading output image", async () => {
      if (draft?.outputs) return draft;
      return getJson(`/api/runs/${encodeURIComponent(runId)}/artifacts/image_generation_draft`);
    });

    const imageUrl = resolveDraftImageUrl(loadedDraft, runId);
    renderDraftResult(runId, imageUrl);
    markStepDone("Done");
    setCurrentStep("Done");
    setStatus("Generated draft is ready.", false);
  } catch (error) {
    const message = friendlyErrorMessage(error);
    showError(message);
    setStatus(message, true);
  } finally {
    setRunning(false);
  }
});

async function runStep(label, fn) {
  setCurrentStep(label);
  markStepRunning(label);
  try {
    const result = await fn();
    markStepDone(label);
    return result;
  } catch (error) {
    markStepFailed(label);
    throw error;
  }
}

async function postForm(url, formData) {
  const response = await fetch(url, {
    method: "POST",
    body: formData,
  });
  return readCheckedJson(response, url);
}

async function postJson(url, body) {
  const response = await fetch(url, {
    method: "POST",
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  return readCheckedJson(response, url);
}

async function getJson(url) {
  const response = await fetch(url);
  return readCheckedJson(response, url);
}

async function resolveRunMetadata(runId) {
  const metadataPayload = await getJson(`/api/runs/${encodeURIComponent(runId)}/metadata`);
  return getMetadataPayload(metadataPayload) || metadataPayload;
}

async function readCheckedJson(response, label) {
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    payload = {};
  }

  if (!response.ok) {
    const detail = Array.isArray(payload.detail) ? payload.detail[0] : payload.detail;
    const message =
      typeof detail === "string"
        ? detail
        : detail && typeof detail === "object" && detail.message
          ? detail.message
          : `${label} failed with HTTP ${response.status}`;
    throw new Error(message);
  }
  return payload;
}

function resolveDraftImageUrl(draft, runId) {
  const preferredUrl =
    pickDisplayImageUrl(draft) ||
    draft?.outputs?.generated_draft_raw_preview_url ||
    draft?.outputs?.generated_draft_raw_url ||
    draft?.outputs?.raw_image_preview_url ||
    draft?.outputs?.raw_image_path ||
    draft?.outputs?.output_preview_url ||
    draft?.outputs?.draft_image_preview_url ||
    draft?.outputs?.draft_image_path ||
    draft?.generated_draft_raw_preview_url ||
    draft?.output_preview_url ||
    `/storage/runs/${runId}/artifacts/generated_draft_raw.png`;

  return toUrl(preferredUrl);
}

function fallbackDraftUrl(runId) {
  return `/storage/runs/${runId}/outputs/${runId}_draft.png`;
}

function renderDraftResult(runId, imageUrl) {
  currentRunId = runId;
  const resolvedImageUrl = resolveAssetUrl(imageUrl);
  currentOutputUrl = resolvedImageUrl;

  outputImage.src = resolvedImageUrl;
  outputImage.hidden = false;
  outputImage.onerror = () => {
    const fallbackUrl = resolveAssetUrl(fallbackDraftUrl(runId));
    if (outputImage.src.endsWith(fallbackUrl)) return;
    currentOutputUrl = fallbackUrl;
    outputImage.src = fallbackUrl;
    renderOutputUrl(fallbackUrl);
  };

  renderOutputUrl(resolvedImageUrl);
  if (runIdText) {
    runIdText.textContent = `run_id: ${runId}`;
    runIdText.hidden = false;
  }
  if (downloadOutputBtn) {
    downloadOutputBtn.hidden = false;
  }
  if (visualQaPanel) {
    visualQaPanel.hidden = false;
  }
  if (qaFeedbackPanel) {
    qaFeedbackPanel.hidden = false;
  }
  clearFinalOutputResult();
  clearRegeneratedOutputResult();
  setVisualQaStatus(`Run ${runId} ready for manual QA.`, false);
  setQaFeedbackStatus("Save QA feedback to create a corrected regeneration attempt.", false);
  syncPostResultControls();
  resultPanel.hidden = false;
}

function renderUploadDebug(floorplanFile, interiorFiles, backendInteriorCount) {
  if (!uploadDebugPanel) return;

  debugFloorplanName.textContent = floorplanFile?.name || "-";
  debugInteriorCount.textContent = String(interiorFiles.length);
  debugInteriorNames.textContent = interiorFiles.length ? interiorFiles.map((file) => file.name).join(", ") : "-";
  debugBackendInteriorCount.textContent = backendInteriorCount === null || backendInteriorCount === undefined ? "-" : String(backendInteriorCount);
  uploadDebugPanel.hidden = false;
  if (!interiorFiles.length) {
    showUploadWarning("No interior photos selected. This is allowed, but furniture signals may be weaker.");
  } else {
    hideUploadWarning();
  }
}

function renderSelectedFloorplanPreview(file) {
  if (selectedFloorplanPreviewUrl) {
    URL.revokeObjectURL(selectedFloorplanPreviewUrl);
    selectedFloorplanPreviewUrl = null;
  }

  if (!selectedFloorplanGrid || !selectedFloorplanEmpty) return;
  selectedFloorplanGrid.innerHTML = "";

  if (!file) {
    selectedFloorplanGrid.hidden = true;
    selectedFloorplanEmpty.hidden = false;
    return;
  }

  selectedFloorplanPreviewUrl = URL.createObjectURL(file);
  selectedFloorplanGrid.appendChild(
    createAssetCard({
      previewUrl: selectedFloorplanPreviewUrl,
      name: file.name,
      metaLines: [formatFileSize(file.size)],
      kind: "local",
    }),
  );
  selectedFloorplanGrid.hidden = false;
  selectedFloorplanEmpty.hidden = true;
}

function renderSelectedInteriorPreviews(files) {
  revokeSelectedInteriorPreviewUrls();
  if (!selectedInteriorGrid || !selectedInteriorEmpty || !selectedInteriorCountBadge) return;

  selectedInteriorGrid.innerHTML = "";
  selectedInteriorCountBadge.textContent = String(files.length);

  if (!files.length) {
    selectedInteriorGrid.hidden = true;
    selectedInteriorEmpty.hidden = false;
    return;
  }

  for (const file of files) {
    const previewUrl = URL.createObjectURL(file);
    selectedInteriorPreviewUrls.push(previewUrl);
    selectedInteriorGrid.appendChild(
      createAssetCard({
        previewUrl,
        name: file.name,
        metaLines: [formatFileSize(file.size)],
        kind: "local",
      }),
    );
  }

  selectedInteriorGrid.hidden = false;
  selectedInteriorEmpty.hidden = true;
}

function renderBackendAssetPreviews(runId, metadata) {
  if (!backendAssetsSection) return;

  const floorplan = getFloorplanAsset(metadata);
  const interiorPhotos = getInteriorPhotoAssets(metadata);
  backendAssetsSection.hidden = false;

  renderBackendAssetGrid({
    grid: backendFloorplanGrid,
    emptyEl: backendFloorplanEmpty,
    assets: floorplan ? [floorplan] : [],
    runId,
  });
  renderBackendAssetGrid({
    grid: backendInteriorGrid,
    emptyEl: backendInteriorEmpty,
    assets: interiorPhotos,
    runId,
  });

  if (backendInteriorCountBadge) {
    backendInteriorCountBadge.textContent = String(interiorPhotos.length);
  }
}

function renderBackendAssetGrid({ grid, emptyEl, assets, runId }) {
  if (!grid || !emptyEl) return;
  grid.innerHTML = "";

  if (!assets.length) {
    grid.hidden = true;
    emptyEl.hidden = false;
    return;
  }

  for (const asset of assets) {
    grid.appendChild(
      createAssetCard({
        previewUrl: resolveAssetPreviewUrl(asset, runId),
        name: asset.original_filename || asset.filename || asset.stored_filename || "Uploaded asset",
        metaLines: [
          asset.mime_type || asset.content_type || null,
          typeof asset.size_bytes === "number" ? formatFileSize(asset.size_bytes) : null,
        ].filter(Boolean),
        kind: "backend",
      }),
    );
  }

  grid.hidden = false;
  emptyEl.hidden = true;
}

function createAssetCard({ previewUrl, name, metaLines, kind }) {
  const card = document.createElement("article");
  card.className = "asset-card";

  const imageWrap = document.createElement("div");
  imageWrap.className = "asset-thumb-wrap";

  const image = document.createElement("img");
  image.className = "asset-thumb";
  image.alt = name || `${kind} asset preview`;
  image.loading = "lazy";

  const fallback = document.createElement("div");
  fallback.className = "asset-thumb-fallback";
  fallback.textContent = "Preview unavailable";
  fallback.hidden = true;

  image.onerror = () => {
    image.hidden = true;
    fallback.hidden = false;
  };

  if (previewUrl) {
    image.src = previewUrl;
    image.hidden = false;
  } else {
    image.hidden = true;
    fallback.hidden = false;
  }

  imageWrap.append(image, fallback);

  const content = document.createElement("div");
  content.className = "asset-card-body";

  const title = document.createElement("p");
  title.className = "asset-name";
  title.textContent = name || "Unnamed file";

  const meta = document.createElement("p");
  meta.className = "asset-meta";
  meta.textContent = metaLines.length ? metaLines.join(" • ") : "Metadata unavailable";

  content.append(title, meta);
  card.append(imageWrap, content);
  return card;
}

function showUploadWarning(message) {
  if (!uploadDebugWarning) return;
  uploadDebugWarning.textContent = message;
  uploadDebugWarning.hidden = false;
}

function hideUploadWarning() {
  if (!uploadDebugWarning) return;
  uploadDebugWarning.textContent = "";
  uploadDebugWarning.hidden = true;
}

function logCreateRunFormData(floorplanFile, interiorFiles, formData) {
  console.log("[upload] floorplan:", floorplanFile?.name || null);
  console.log("[upload] interior count:", interiorFiles.length);
  console.log("[upload] interiors:", interiorFiles.map((file) => file.name));
  console.log("[upload] formData entries:");
  for (const [key, value] of formData.entries()) {
    console.log(" -", key, value instanceof File ? value.name : value);
  }
}

function getInteriorPhotoCount(payload) {
  const assets = getInteriorPhotoAssets(payload);
  return assets ? assets.length : null;
}

function renderOutputUrl(imageUrl) {
  renderUrlLink(outputUrlLink, imageUrl, "Hosted output URL", "Local output URL");
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

function clearResult() {
  clearRunArtifacts();
  clearSelectedAssetPreviews();
  clearInputPreview();
  resultPanel.hidden = true;
}

function clearRunArtifacts() {
  currentOutputUrl = "";
  currentRunId = "";
  currentFinalOutputUrl = "";
  qaFeedbackSavedForRunId = "";
  latestRegenerationResult = null;
  if (outputImage) {
    outputImage.removeAttribute("src");
    outputImage.hidden = true;
    outputImage.onerror = null;
  }
  if (downloadOutputBtn) {
    downloadOutputBtn.hidden = true;
  }
  if (runIdText) {
    runIdText.textContent = "";
    runIdText.hidden = true;
  }
  if (outputUrlLink) {
    outputUrlLink.removeAttribute("href");
    outputUrlLink.textContent = "";
    outputUrlLink.hidden = true;
  }
  clearFinalOutputResult();
  clearRegeneratedOutputResult();
  if (visualQaPanel) {
    visualQaPanel.hidden = true;
  }
  if (qaFeedbackPanel) {
    qaFeedbackPanel.hidden = true;
  }
  setVisualQaStatus("No active run.", false);
  setQaFeedbackStatus("No active run.", false);
  clearQaFeedbackForm();
  syncPostResultControls();
  if (uploadDebugPanel) {
    uploadDebugPanel.hidden = true;
  }
  if (debugFloorplanName) {
    debugFloorplanName.textContent = "-";
  }
  if (debugInteriorCount) {
    debugInteriorCount.textContent = "0";
  }
  if (debugInteriorNames) {
    debugInteriorNames.textContent = "-";
  }
  if (debugBackendInteriorCount) {
    debugBackendInteriorCount.textContent = "-";
  }
  if (pipelinePanel) {
    pipelinePanel.hidden = true;
  }
  hideUploadWarning();
  hideError();
}

function clearSelectedAssetPreviews() {
  renderSelectedFloorplanPreview(null);
  renderSelectedInteriorPreviews([]);
}

function clearBackendAssetPreviews() {
  if (backendAssetsSection) {
    backendAssetsSection.hidden = true;
  }
  if (backendFloorplanGrid) {
    backendFloorplanGrid.innerHTML = "";
    backendFloorplanGrid.hidden = true;
  }
  if (backendInteriorGrid) {
    backendInteriorGrid.innerHTML = "";
    backendInteriorGrid.hidden = true;
  }
  if (backendFloorplanEmpty) {
    backendFloorplanEmpty.hidden = false;
  }
  if (backendInteriorEmpty) {
    backendInteriorEmpty.hidden = false;
  }
  if (backendInteriorCountBadge) {
    backendInteriorCountBadge.textContent = "0";
  }
}

function revokeSelectedInteriorPreviewUrls() {
  for (const previewUrl of selectedInteriorPreviewUrls) {
    URL.revokeObjectURL(previewUrl);
  }
  selectedInteriorPreviewUrls = [];
}

downloadOutputBtn?.addEventListener("click", () => {
  if (!currentOutputUrl) return;
  const link = document.createElement("a");
  link.href = currentOutputUrl;
  link.download = `madori-ai-${currentRunId || "draft"}.png`;
  document.body.appendChild(link);
  link.click();
  link.remove();
});

function initializeProgressList() {
  if (!progressList) return;
  const labels = ["Creating run", ...PIPELINE_STEPS.map(([label]) => label), "Generating OpenAI draft", "Loading output image", "Done"];
  progressList.innerHTML = "";
  for (const label of labels) {
    const item = document.createElement("li");
    item.dataset.step = label;
    item.textContent = label;
    progressList.appendChild(item);
  }
}

function resetProgress() {
  if (pipelinePanel) {
    pipelinePanel.hidden = false;
  }
  if (!progressList) return;
  for (const item of progressList.querySelectorAll("li")) {
    item.className = "";
  }
  setCurrentStep("Creating run");
}

function setCurrentStep(label) {
  if (currentStepLabel) {
    currentStepLabel.textContent = label;
  }
}

function markStepRunning(label) {
  if (!progressList) return;
  const item = progressList.querySelector(`[data-step="${cssEscape(label)}"]`);
  if (!item) return;
  item.className = "is-running";
}

function markStepDone(label) {
  if (!progressList) return;
  const item = progressList.querySelector(`[data-step="${cssEscape(label)}"]`);
  if (!item) return;
  item.className = "is-done";
}

function markStepFailed(label) {
  if (!progressList) return;
  const item = progressList.querySelector(`[data-step="${cssEscape(label)}"]`);
  if (!item) return;
  item.className = "is-failed";
}

function setRunning(isRunning) {
  if (generateButton) {
    generateButton.disabled = isRunning;
    generateButton.textContent = isRunning ? "Generating..." : "Generate Draft";
  }
}

function setStatus(message, isError) {
  statusEl.textContent = message;
  statusEl.classList.toggle("is-error", Boolean(isError));
}

function showError(message) {
  errorBox.textContent = message;
  errorBox.hidden = false;
}

function hideError() {
  errorBox.textContent = "";
  errorBox.hidden = true;
}

function friendlyErrorMessage(error) {
  const message = error instanceof Error ? error.message : "Generation failed.";
  if (
    message.includes("ENABLE_OPENAI_IMAGE_GENERATION=true") ||
    message.includes("OPENAI_IMAGE_DRY_RUN=false") ||
    message.includes("image API key is required") ||
    message.includes("API key is required")
  ) {
    return "OpenAI generation is disabled on the backend. Enable image generation, disable dry-run mode, and make sure the backend image credentials are configured before restarting.";
  }
  return message;
}

function getMetadataPayload(payload) {
  if (!payload || typeof payload !== "object") return null;
  if (payload.metadata && typeof payload.metadata === "object") {
    return payload.metadata;
  }
  return payload;
}

function getFloorplanAsset(payload) {
  const metadata = getMetadataPayload(payload);
  if (!metadata || typeof metadata !== "object") return null;
  return metadata.floorplan || metadata.inputs?.floorplan || null;
}

function getInteriorPhotoAssets(payload) {
  const metadata = getMetadataPayload(payload);
  if (!metadata || typeof metadata !== "object") return [];
  const candidates = [metadata.interior_photos, metadata.inputs?.interior_photos];
  for (const candidate of candidates) {
    if (Array.isArray(candidate)) {
      return candidate;
    }
  }
  return [];
}

function resolveAssetPreviewUrl(asset, runId) {
  if (!asset || typeof asset !== "object") return "";
  if (asset.preview_url) {
    return toUrl(asset.preview_url);
  }
  if (asset.relative_path) {
    const relativePath = String(asset.relative_path).replace(/^\/+/, "");
    const storagePrefix = `storage/runs/${runId}/`;
    if (relativePath.startsWith(storagePrefix)) {
      return `/${relativePath}`;
    }
    return `/storage/runs/${runId}/${relativePath}`;
  }
  return "";
}

function formatFileSize(bytes) {
  if (typeof bytes !== "number" || !Number.isFinite(bytes) || bytes < 0) {
    return "Size unknown";
  }
  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1)} KB`;
  }
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

function toUrl(path) {
  if (!path) return "";
  if (/^https?:\/\//.test(path)) return path;
  return path.startsWith("/") ? path : `/${path}`;
}

function getApiBaseUrl() {
  return window.location.origin.replace(/\/$/, "");
}

function resolveAssetUrl(url) {
  if (!url) return "";
  if (/^https?:\/\//i.test(url)) return url;
  const base = getApiBaseUrl ? getApiBaseUrl() : "";
  if (!base) return url;
  if (url.startsWith("/")) return `${base}${url}`;
  return `${base}/${url}`;
}

function pickDisplayImageUrl(result) {
  if (!result || typeof result !== "object") return "";
  return (
    result.public_output_url ||
    result.cloudinary_url ||
    result.cloudinary?.draft?.secure_url ||
    result.outputs?.public_output_url ||
    result.outputs?.cloudinary_url ||
    result.outputs?.output_preview_url ||
    result.outputs?.draft_image_preview_url ||
    result.output_preview_url ||
    result.output_url ||
    result.preview_url ||
    ""
  );
}

function pickFinalImageUrl(result) {
  if (!result || typeof result !== "object") return "";
  return (
    result?.final?.public_output_url ||
    result?.cloudinary?.final?.secure_url ||
    result?.public_output_url ||
    result?.final?.final_image_preview_url ||
    result?.preview_url ||
    ""
  );
}

function pickRegeneratedImageUrl(result) {
  if (!result || typeof result !== "object") return "";
  return (
    result?.outputs?.public_output_url ||
    result?.cloudinary?.regenerated?.secure_url ||
    result?.public_output_url ||
    result?.outputs?.output_preview_url ||
    result?.preview_url ||
    ""
  );
}

function isHostedOutputUrl(url) {
  return /^https:\/\/res\.cloudinary\.com\//.test(String(url || ""));
}

function renderUrlLink(linkEl, url, hostedLabel, localLabel) {
  if (!linkEl) return;
  const resolvedUrl = resolveAssetUrl(url);
  if (!resolvedUrl) {
    linkEl.removeAttribute("href");
    linkEl.textContent = "";
    linkEl.hidden = true;
    return;
  }
  linkEl.href = resolvedUrl;
  linkEl.textContent = `${isHostedOutputUrl(resolvedUrl) ? hostedLabel : localLabel}: ${resolvedUrl}`;
  linkEl.hidden = false;
}

function setVisualQaStatus(message, isError) {
  if (!visualQaStatusText) return;
  visualQaStatusText.textContent = message || "";
  visualQaStatusText.classList.toggle("is-error", Boolean(isError));
}

function setQaFeedbackStatus(message, isError) {
  if (!qaFeedbackStatusText) return;
  qaFeedbackStatusText.textContent = message || "";
  qaFeedbackStatusText.classList.toggle("is-error", Boolean(isError));
}

function syncPostResultControls() {
  const hasRun = Boolean(currentRunId);
  const disableQa = !hasRun || visualQaRequestInFlight || finalizeRequestInFlight;
  const disableFinalize = !hasRun || finalizeRequestInFlight || visualQaRequestInFlight || qaFeedbackRequestInFlight || regenerateRequestInFlight;
  const disableSaveFeedback = !hasRun || qaFeedbackRequestInFlight || regenerateRequestInFlight || finalizeRequestInFlight || visualQaRequestInFlight;
  const disableRegenerate =
    !hasRun ||
    regenerateRequestInFlight ||
    qaFeedbackRequestInFlight ||
    finalizeRequestInFlight ||
    visualQaRequestInFlight ||
    qaFeedbackSavedForRunId !== currentRunId;

  if (qaPassedBtn) qaPassedBtn.disabled = disableQa;
  if (qaNeedsFixBtn) qaNeedsFixBtn.disabled = disableQa;
  if (qaFailedBtn) qaFailedBtn.disabled = disableQa;
  if (finalizeOutputBtn) finalizeOutputBtn.disabled = disableFinalize;
  if (saveQaFeedbackBtn) saveQaFeedbackBtn.disabled = disableSaveFeedback;
  if (regenerateWithFeedbackBtn) regenerateWithFeedbackBtn.disabled = disableRegenerate;
}

function clearFinalOutputResult() {
  currentFinalOutputUrl = "";
  if (finalOutputImage) {
    finalOutputImage.removeAttribute("src");
    finalOutputImage.hidden = true;
    finalOutputImage.onerror = null;
  }
  if (finalOutputUrlLink) {
    finalOutputUrlLink.removeAttribute("href");
    finalOutputUrlLink.textContent = "";
    finalOutputUrlLink.hidden = true;
  }
  if (finalOutputStatusText) {
    finalOutputStatusText.textContent = "No final output yet.";
  }
  if (finalOutputPanel) {
    finalOutputPanel.hidden = true;
  }
}

function clearRegeneratedOutputResult() {
  latestRegenerationResult = null;
  if (regeneratedOutputImage) {
    regeneratedOutputImage.removeAttribute("src");
    regeneratedOutputImage.hidden = true;
    regeneratedOutputImage.onerror = null;
  }
  if (regeneratedOutputUrlLink) {
    regeneratedOutputUrlLink.removeAttribute("href");
    regeneratedOutputUrlLink.textContent = "";
    regeneratedOutputUrlLink.hidden = true;
  }
  if (regeneratedOutputStatusText) {
    regeneratedOutputStatusText.textContent = "No regenerated output yet.";
  }
  if (regeneratedOutputPanel) {
    regeneratedOutputPanel.hidden = true;
  }
}

function renderFinalOutputResult(result) {
  const preferredUrl = pickFinalImageUrl(result);
  const resolvedUrl = resolveAssetUrl(preferredUrl);
  const localFallbackUrl = resolveAssetUrl(result?.final?.final_image_preview_url || result?.preview_url || "");
  const finalStatus = result?.final_status || "finalized";
  const qaStatus = result?.qa?.qa_status || "unknown";

  currentFinalOutputUrl = resolvedUrl || localFallbackUrl;

  if (finalOutputImage) {
    finalOutputImage.src = currentFinalOutputUrl;
    finalOutputImage.hidden = !currentFinalOutputUrl;
    finalOutputImage.onerror = () => {
      if (!localFallbackUrl || finalOutputImage.src.endsWith(localFallbackUrl)) return;
      currentFinalOutputUrl = localFallbackUrl;
      finalOutputImage.src = localFallbackUrl;
      renderUrlLink(finalOutputUrlLink, localFallbackUrl, "Hosted final URL", "Local final URL");
    };
  }

  renderUrlLink(finalOutputUrlLink, currentFinalOutputUrl, "Hosted final URL", "Local final URL");
  if (finalOutputStatusText) {
    finalOutputStatusText.textContent = `Final status: ${finalStatus}. Visual QA: ${qaStatus}.`;
  }
  if (finalOutputPanel) {
    finalOutputPanel.hidden = false;
  }
}

function renderRegeneratedOutputResult(result) {
  latestRegenerationResult = result || null;
  const preferredUrl = pickRegeneratedImageUrl(result);
  const resolvedUrl = resolveAssetUrl(preferredUrl);
  const localFallbackUrl = resolveAssetUrl(result?.outputs?.output_preview_url || result?.preview_url || "");
  const attempt = result?.attempt ?? "?";
  const status = result?.status || "completed";

  if (regeneratedOutputImage) {
    regeneratedOutputImage.src = resolvedUrl || localFallbackUrl;
    regeneratedOutputImage.hidden = !(resolvedUrl || localFallbackUrl);
    regeneratedOutputImage.onerror = () => {
      if (!localFallbackUrl || regeneratedOutputImage.src.endsWith(localFallbackUrl)) return;
      regeneratedOutputImage.src = localFallbackUrl;
      renderUrlLink(regeneratedOutputUrlLink, localFallbackUrl, "Hosted regenerated URL", "Local regenerated URL");
    };
  }

  renderUrlLink(regeneratedOutputUrlLink, resolvedUrl || localFallbackUrl, "Hosted regenerated URL", "Local regenerated URL");
  if (regeneratedOutputStatusText) {
    regeneratedOutputStatusText.textContent = `Attempt ${attempt}. Status: ${status}.`;
  }
  if (regeneratedOutputPanel) {
    regeneratedOutputPanel.hidden = false;
  }
}

function buildVisualQaPayload(qaStatus) {
  if (qaStatus === "passed") {
    return {
      qa_status: "passed",
      layout_preserved: "pass",
      english_labels_correct: "pass",
      room_roles_correct: "pass",
      furniture_arrangement_correct: "pass",
      bedroom_bed_count_correct: "pass",
      dining_location_correct: "pass",
      sofa_tv_arrangement_correct: "pass",
      final_usable_for_demo: true,
      notes: "Manual QA passed from frontend.",
      issues: [],
    };
  }

  if (qaStatus === "needs_fix") {
    return {
      qa_status: "needs_fix",
      layout_preserved: "needs_review",
      english_labels_correct: "needs_review",
      room_roles_correct: "needs_review",
      furniture_arrangement_correct: "needs_review",
      bedroom_bed_count_correct: "needs_review",
      dining_location_correct: "needs_review",
      sofa_tv_arrangement_correct: "needs_review",
      final_usable_for_demo: false,
      notes: "Manual QA marked as needs fix from frontend.",
      issues: [
        {
          issue_type: "manual_review_needed",
          severity: "medium",
          description: "Reviewer marked this draft as needing fixes.",
        },
      ],
    };
  }

  return {
    qa_status: "failed",
    layout_preserved: "fail",
    english_labels_correct: "fail",
    room_roles_correct: "fail",
    furniture_arrangement_correct: "fail",
    bedroom_bed_count_correct: "fail",
    dining_location_correct: "fail",
    sofa_tv_arrangement_correct: "fail",
    final_usable_for_demo: false,
    notes: "Manual QA failed from frontend.",
    issues: [
      {
        issue_type: "manual_qa_failed",
        severity: "high",
        description: "Reviewer marked this draft as failed.",
      },
    ],
  };
}

async function submitVisualQa(qaStatus) {
  if (!currentRunId) {
    setVisualQaStatus("No active run.", true);
    syncPostResultControls();
    return;
  }

  visualQaRequestInFlight = true;
  setVisualQaStatus("Saving visual QA status...", false);
  syncPostResultControls();

  try {
    const result = await postJson(`/api/runs/${encodeURIComponent(currentRunId)}/visual-qa`, buildVisualQaPayload(qaStatus));
    const savedStatus = result?.qa_status || qaStatus;
    setVisualQaStatus(`Visual QA saved: ${savedStatus}.`, false);
  } catch (error) {
    setVisualQaStatus(friendlyErrorMessage(error), true);
  } finally {
    visualQaRequestInFlight = false;
    syncPostResultControls();
  }
}

function clearQaFeedbackForm() {
  const issueCheckboxIds = [
    "issueLayoutDrift",
    "issueWrongRoomRole",
    "issueDiningWrongRoom",
    "issueSofaTvWrongRoom",
    "issueWrongBedCount",
    "issueLabelsWrong",
    "issueFurnitureTooMuch",
    "issueFurnitureMissing",
    "issueStyleNotWatercolor",
    "issuePaletteTooDark",
    "issueWallsTooDark",
    "issueWashingMachineWrongPosition",
    "issueFurnitureOrientationWrong",
    "issueOther",
  ];
  for (const id of issueCheckboxIds) {
    const checkbox = byId(id);
    if (checkbox) checkbox.checked = false;
  }
  setValue("qaFeedbackNotesInput", "");
  setText("qaFeedbackPlanSummary", "");
  setHidden("qaFeedbackPlanSummary", true);
}

function collectQaFeedbackIssues() {
  const issueConfigs = [
    ["issueLayoutDrift", "layout_drift", "high", "The generated image appears to drift from the original floorplan layout."],
    ["issueWrongRoomRole", "wrong_room_role", "high", "The room functions do not match the assigned room roles."],
    ["issueDiningWrongRoom", "dining_wrong_room", "high", "The dining table should stay in the living/dining area, not in another room."],
    ["issueSofaTvWrongRoom", "sofa_tv_wrong_room", "high", "The sofa and TV should be placed in the assigned lounge/media room."],
    ["issueWrongBedCount", "wrong_bed_count", "high", "The bedroom should contain only one bed or two single beds."],
    ["issueLabelsWrong", "labels_wrong", "medium", "The labels should be correct English room labels."],
    ["issueFurnitureTooMuch", "furniture_too_much", "medium", "The furniture density should be reduced to the essential pieces only."],
    ["issueFurnitureMissing", "furniture_missing", "medium", "Important furniture appears to be missing from the assigned rooms."],
    ["issueStyleNotWatercolor", "style_not_watercolor", "medium", "The output should use a softer Japanese watercolor style."],
    ["issuePaletteTooDark", "palette_too_dark", "medium", "The output palette is too dark. Please brighten the overall image with lighter warm neutral tones."],
    ["issueWallsTooDark", "walls_too_dark", "medium", "Wall and partition areas are too dark or black. Please use lighter neutral wall tones instead."],
    ["issueWashingMachineWrongPosition", "washing_machine_wrong_position", "high", "The washing machine should be placed in the Wash Room at the marked Wash / 洗 location."],
    ["issueFurnitureOrientationWrong", "furniture_orientation_wrong", "medium", "Furniture orientation needs correction. Sofa and TV should face each other, coffee table should be between them, beds should align to walls, and dining furniture should be neatly aligned."],
    ["issueOther", "other", "medium", "The output needs additional manual corrections."],
  ];

  return issueConfigs
    .filter(([id]) => Boolean(byId(id)?.checked))
    .map(([, issueType, severity, description]) => ({
      issue_type: issueType,
      severity,
      description,
    }));
}

function buildQaFeedbackPayload() {
  const notes = String(qaFeedbackNotesInput?.value || "").trim();
  const issues = collectQaFeedbackIssues();
  if (!issues.length && notes) {
    issues.push({
      issue_type: "other",
      severity: "medium",
      description: notes,
    });
  }

  return {
    feedback_status: "needs_regeneration",
    target_image: "latest_draft",
    issues,
    freeform_feedback: notes || null,
  };
}

function backendErrorMessage(error) {
  const message = error instanceof Error ? error.message : "Request failed.";
  if (message.includes("qa_feedback.json is required before regeneration")) {
    return "QA feedback is required before regeneration. Save QA Feedback first.";
  }
  if (message.includes("confirm_generation must be true")) {
    return "Generation confirmation is required.";
  }
  if (message.includes("prompt_package.json or image_generation_request_preview.json is required")) {
    return "Prompt package or preview request is missing. Generate/preview a draft first.";
  }
  if (message.includes("Only provider=openai is supported")) {
    return "Only OpenAI generation is supported.";
  }
  return message;
}

async function submitQaFeedback() {
  if (!currentRunId) {
    setQaFeedbackStatus("No active run.", true);
    syncPostResultControls();
    return;
  }

  const payload = buildQaFeedbackPayload();
  if (!payload.issues.length && !String(payload.freeform_feedback || "").trim()) {
    setQaFeedbackStatus("Select at least one issue or enter feedback before saving.", true);
    return;
  }

  qaFeedbackRequestInFlight = true;
  setQaFeedbackStatus("Saving QA feedback...", false);
  syncPostResultControls();

  try {
    const result = await postJson(`/api/runs/${encodeURIComponent(currentRunId)}/qa-feedback`, payload);
    qaFeedbackSavedForRunId = currentRunId;
    setQaFeedbackStatus("QA feedback saved.", false);
    const correctionSummary = result?.correction_plan?.summary || "Correction plan created.";
    setText("qaFeedbackPlanSummary", correctionSummary);
    setHidden("qaFeedbackPlanSummary", false);
  } catch (error) {
    setQaFeedbackStatus(backendErrorMessage(error), true);
  } finally {
    qaFeedbackRequestInFlight = false;
    syncPostResultControls();
  }
}

async function regenerateWithFeedback() {
  if (!currentRunId) {
    setQaFeedbackStatus("No active run.", true);
    syncPostResultControls();
    return;
  }

  regenerateRequestInFlight = true;
  setQaFeedbackStatus("Regenerating with feedback...", false);
  syncPostResultControls();

  try {
    const result = await postJson(`/api/runs/${encodeURIComponent(currentRunId)}/regenerate-with-feedback`, {
      confirm_generation: true,
      feedback_source: "latest",
      use_reference_images: true,
      max_reference_images: 4,
      output_format: "png",
    });
    renderRegeneratedOutputResult(result);
    setQaFeedbackStatus(`Regenerated output created. Attempt ${result?.attempt ?? "?"}.`, false);
  } catch (error) {
    setQaFeedbackStatus(backendErrorMessage(error), true);
  } finally {
    regenerateRequestInFlight = false;
    syncPostResultControls();
  }
}

async function finalizeCurrentRunOutput() {
  if (!currentRunId) {
    setVisualQaStatus("No active run.", true);
    syncPostResultControls();
    return;
  }

  finalizeRequestInFlight = true;
  setVisualQaStatus("Finalizing output...", false);
  syncPostResultControls();

  try {
    const result = await postJson(`/api/runs/${encodeURIComponent(currentRunId)}/finalize-output`, {
      source: "auto",
      force: false,
    });
    renderFinalOutputResult(result);
    setVisualQaStatus("Final output created.", false);
  } catch (error) {
    const message = friendlyErrorMessage(error);
    if (message.includes("pass visual QA before finalizing")) {
      setVisualQaStatus(
        "Visual QA must pass before finalizing. Mark QA Passed first, or use force from backend/debug.",
        true,
      );
    } else {
      setVisualQaStatus(message, true);
    }
  } finally {
    finalizeRequestInFlight = false;
    syncPostResultControls();
  }
}

function cssEscape(value) {
  if (window.CSS?.escape) {
    return CSS.escape(value);
  }
  return String(value).replace(/"/g, '\\"');
}
