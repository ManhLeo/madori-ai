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
let selectedFloorplanPreviewUrl = null;
let selectedInteriorPreviewUrls = [];

initializeProgressList();
renderSelectedFloorplanPreview(null);
renderSelectedInteriorPreviews([]);

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
    throw new Error(detail || `${label} failed with HTTP ${response.status}`);
  }
  return payload;
}

function resolveDraftImageUrl(draft, runId) {
  const rawPreview =
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

  return toUrl(rawPreview);
}

function fallbackDraftUrl(runId) {
  return `/storage/runs/${runId}/outputs/${runId}_draft.png`;
}

function renderDraftResult(runId, imageUrl) {
  currentRunId = runId;
  currentOutputUrl = imageUrl;

  outputImage.src = imageUrl;
  outputImage.hidden = false;
  outputImage.onerror = () => {
    const fallbackUrl = fallbackDraftUrl(runId);
    if (outputImage.src.endsWith(fallbackUrl)) return;
    currentOutputUrl = fallbackUrl;
    outputImage.src = fallbackUrl;
    renderOutputUrl(fallbackUrl);
  };

  renderOutputUrl(imageUrl);
  runIdText.textContent = `run_id: ${runId}`;
  runIdText.hidden = false;
  downloadOutputBtn.hidden = false;
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
  outputUrlLink.href = imageUrl;
  outputUrlLink.textContent = imageUrl;
  outputUrlLink.hidden = false;
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
  outputImage.removeAttribute("src");
  outputImage.hidden = true;
  outputImage.onerror = null;
  downloadOutputBtn.hidden = true;
  runIdText.textContent = "";
  runIdText.hidden = true;
  outputUrlLink.removeAttribute("href");
  outputUrlLink.textContent = "";
  outputUrlLink.hidden = true;
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
  pipelinePanel.hidden = true;
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
  pipelinePanel.hidden = false;
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
  const item = progressList.querySelector(`[data-step="${cssEscape(label)}"]`);
  if (!item) return;
  item.className = "is-running";
}

function markStepDone(label) {
  const item = progressList.querySelector(`[data-step="${cssEscape(label)}"]`);
  if (!item) return;
  item.className = "is-done";
}

function markStepFailed(label) {
  const item = progressList.querySelector(`[data-step="${cssEscape(label)}"]`);
  if (!item) return;
  item.className = "is-failed";
}

function setRunning(isRunning) {
  generateButton.disabled = isRunning;
  generateButton.textContent = isRunning ? "Generating..." : "Generate Draft";
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
    message.includes("OPENAI_API_KEY is required")
  ) {
    return "OpenAI generation is disabled on the backend. Enable ENABLE_OPENAI_IMAGE_GENERATION=true and OPENAI_IMAGE_DRY_RUN=false, then restart the backend.";
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

function cssEscape(value) {
  if (window.CSS?.escape) {
    return CSS.escape(value);
  }
  return String(value).replace(/"/g, '\\"');
}
