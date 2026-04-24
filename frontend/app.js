/**
 * app.js — DDR AI System Frontend Logic
 * Handles: file drag-and-drop, form submission, job polling, progress UI
 */

// Auto-detect API base URL — works locally and in cloud deployments
const API_BASE = window.location.origin;

let inspectionFile = null;
let thermalFile = null;
let currentJobId = null;
let pollTimer = null;

// ── File Handling ──────────────────────────────────────────────────────────

function handleDragover(e) {
  e.preventDefault();
  e.currentTarget.classList.add("drag-over");
}

function removeDragOver(e) {
  e.currentTarget.classList.remove("drag-over");
}

function handleDrop(e, type) {
  e.preventDefault();
  e.currentTarget.classList.remove("drag-over");
  const file = e.dataTransfer.files[0];
  if (file && file.type === "application/pdf") {
    setFile(file, type);
  } else {
    alert("Please drop a PDF file.");
  }
}

function handleFileSelect(input, type) {
  const file = input.files[0];
  if (file) setFile(file, type);
}

function setFile(file, type) {
  if (type === "inspection") {
    inspectionFile = file;
    document.getElementById("inspection-fname").textContent = `✓ ${file.name}`;
    document.getElementById("inspection-fname").style.display = "inline-block";
    document.getElementById("dz-inspection").classList.add("has-file");
  } else {
    thermalFile = file;
    document.getElementById("thermal-fname").textContent = `✓ ${file.name}`;
    document.getElementById("thermal-fname").style.display = "inline-block";
    document.getElementById("dz-thermal").classList.add("has-file");
  }
  checkReadyState();
}

function checkReadyState() {
  const apiKey = document.getElementById("api-key-input").value.trim();
  const btn = document.getElementById("generate-btn");
  btn.disabled = !(inspectionFile && thermalFile && apiKey.length > 10);
}

// Listen for API key input
document.getElementById("api-key-input").addEventListener("input", checkReadyState);

// ── Form Submit ────────────────────────────────────────────────────────────

document.getElementById("upload-form").addEventListener("submit", async (e) => {
  e.preventDefault();

  if (!inspectionFile || !thermalFile) {
    alert("Please select both PDF files.");
    return;
  }

  const apiKey = document.getElementById("api-key-input").value.trim();
  if (!apiKey || apiKey.length < 10) {
    alert("Please enter your Gemini API key.");
    return;
  }

  // Store API key for this session (sent via custom header)
  sessionStorage.setItem("gemini_api_key", apiKey);

  showProgress();

  const formData = new FormData();
  formData.append("inspection_report", inspectionFile);
  formData.append("thermal_report", thermalFile);

  try {
    const res = await fetch(`${API_BASE}/generate`, {
      method: "POST",
      headers: { "X-Gemini-Key": apiKey },
      body: formData,
    });

    if (!res.ok) {
      const errData = await res.json().catch(() => ({}));
      throw new Error(errData.detail || `Server error: ${res.status}`);
    }

    const data = await res.json();
    currentJobId = data.job_id;
    startPolling(currentJobId);

  } catch (err) {
    showError(err.message);
  }
});

// ── Polling ────────────────────────────────────────────────────────────────

function startPolling(jobId) {
  if (pollTimer) clearInterval(pollTimer);

  pollTimer = setInterval(async () => {
    try {
      const res = await fetch(`${API_BASE}/status/${jobId}`);
      if (!res.ok) return;
      const data = await res.json();
      updateProgress(data);

      if (data.status === "completed") {
        clearInterval(pollTimer);
        showResult(jobId);
      } else if (data.status === "failed") {
        clearInterval(pollTimer);
        showError(data.message || "Pipeline failed");
      }
    } catch (err) {
      console.error("Polling error:", err);
    }
  }, 2000);
}

// ── Progress UI ────────────────────────────────────────────────────────────

function showProgress() {
  document.getElementById("upload-card").style.display = "none";
  document.getElementById("progress-card").style.display = "block";
  document.getElementById("result-card").style.display = "none";
  document.getElementById("error-card").style.display = "none";
  updateProgressUI(0, "Starting pipeline...", "Connecting to server...");
}

function updateProgress(data) {
  const pct = data.progress || 0;
  const title = data.message || "Processing...";
  updateProgressUI(pct, title, data.status || "");

  // Update step indicators
  const status = data.status || "";
  const steps = { ps_extract: false, ps_ai: false, ps_merge: false, ps_render: false };

  if (pct >= 10) steps.ps_extract = "active";
  if (pct >= 30) steps.ps_extract = "done";
  if (pct >= 35) steps.ps_ai = "active";
  if (pct >= 70) steps.ps_ai = "done";
  if (pct >= 70) steps.ps_merge = "active";
  if (pct >= 85) steps.ps_merge = "done";
  if (pct >= 85) steps.ps_render = "active";
  if (pct >= 100) steps.ps_render = "done";

  for (const [id, state] of Object.entries(steps)) {
    const el = document.getElementById(id.replace("_", "-"));
    if (!el) continue;
    el.classList.remove("active", "done");
    if (state) el.classList.add(state);
  }
}

function updateProgressUI(pct, title, sub) {
  document.getElementById("progress-pct").textContent = `${pct}%`;
  document.getElementById("progress-title").textContent = title;
  document.getElementById("progress-sub").textContent = sub;
  document.getElementById("progress-bar").style.width = `${pct}%`;
}

// ── Result / Error UI ──────────────────────────────────────────────────────

function showResult(jobId) {
  document.getElementById("progress-card").style.display = "none";
  document.getElementById("result-card").style.display = "block";

  document.getElementById("btn-preview").href = `${API_BASE}/report/${jobId}`;
  document.getElementById("btn-download-html").href = `${API_BASE}/download/${jobId}/html`;
  document.getElementById("btn-download-pdf").href = `${API_BASE}/download/${jobId}/pdf`;
}

function showError(message) {
  document.getElementById("progress-card").style.display = "none";
  document.getElementById("upload-card").style.display = "block";
  document.getElementById("error-card").style.display = "block";

  const isQuota = message.includes("quota") || message.includes("QUOTA") || message.includes("429") || message.includes("RESOURCE_EXHAUSTED");

  if (isQuota) {
    document.getElementById("error-msg").innerHTML = `
      <strong>Gemini API quota exhausted.</strong><br/>
      The free tier has daily request limits. Here’s what you can do:<br/><br/>
      <span style="color:#94a3b8">
        1️⃣ Wait a few minutes and try again (per-minute limit)<br/>
        2️⃣ Wait until midnight (Pacific Time) for daily quota reset<br/>
        3️⃣ Use a paid Gemini API key — <a href="https://aistudio.google.com/" target="_blank" style="color:#60a5fa">Get one here ↗</a>
      </span>
    `;
  } else {
    document.getElementById("error-msg").textContent = message;
  }
}

// ── Reset ──────────────────────────────────────────────────────────────────

function resetForm() {
  inspectionFile = null;
  thermalFile = null;
  currentJobId = null;
  if (pollTimer) clearInterval(pollTimer);

  // Reset UI
  document.getElementById("upload-card").style.display = "block";
  document.getElementById("progress-card").style.display = "none";
  document.getElementById("result-card").style.display = "none";
  document.getElementById("error-card").style.display = "none";

  document.getElementById("dz-inspection").classList.remove("has-file");
  document.getElementById("dz-thermal").classList.remove("has-file");
  document.getElementById("inspection-fname").style.display = "none";
  document.getElementById("thermal-fname").style.display = "none";
  document.getElementById("inspection-file").value = "";
  document.getElementById("thermal-file").value = "";
  document.getElementById("generate-btn").disabled = true;
}
