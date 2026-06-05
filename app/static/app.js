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

let currentInputPreviewUrl = null;
let currentDownloadUrl = "";

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

    const runPayload = await fetchRunInspection(runId);
    renderRunResult(runPayload, generatePayload);
    setStatus(`生成結果を表示しました。Run ID: ${runId}`, false);
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
  const outputUrl = toRunUrl(files.output) || generatePayload.output_url || "";
  currentDownloadUrl = runPayload.download_url || "";

  outputImage.src = outputUrl;
  outputImage.hidden = !outputUrl;
  downloadOutputBtn.hidden = !outputUrl || !currentDownloadUrl;

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

  resultPanel.hidden = false;
}

function clearOutputPreview() {
  outputImage.removeAttribute("src");
  outputImage.hidden = true;
  currentDownloadUrl = "";
  downloadOutputBtn.hidden = true;

  if (overlayImage) {
    overlayImage.removeAttribute("src");
    overlayImage.hidden = true;
  }

  if (overlayDebugImage) {
    overlayDebugImage.removeAttribute("src");
    overlayDebugImage.hidden = true;
  }
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

downloadOutputBtn?.addEventListener("click", () => {
  if (currentDownloadUrl) {
    window.location.href = currentDownloadUrl;
  }
});
