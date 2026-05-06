/**
 * popup.js — Experian Dispute Automation Extension
 * Handles the main popup UI: run button, live status, today's summary.
 */

const runBtn = document.getElementById("runBtn");
const statusCard = document.getElementById("statusCard");
const statusDot = document.getElementById("statusDot");
const statusLabel = document.getElementById("statusLabel");
const statusDetail = document.getElementById("statusDetail");
const summaryRow = document.getElementById("summaryRow");
const logsLink = document.getElementById("logsLink");
const optionsLink = document.getElementById("optionsLink");

// ── Status display ────────────────────────────────────────────────────────────

const STATUS_MAP = {
  starting: { label: "Starting", dot: "spinning" },
  fetching_crm: { label: "Fetching CRM", dot: "spinning" },
  crm_ok: { label: "CRM Record Loaded", dot: "spinning" },
  fetching_pdf: { label: "Fetching PDF", dot: "spinning" },
  pdf_ok: { label: "PDF Ready", dot: "spinning" },
  opening_experian: { label: "Opening Experian", dot: "spinning" },
  form_in_progress: { label: "Filling Form…", dot: "spinning" },
  skipped: { label: "Skipped (TIFF)", dot: "idle" },
  completed: { label: "✓ Completed", dot: "success" },
  failed: { label: "✗ Failed", dot: "error" },
  idle: { label: "Idle", dot: "idle" },
};

function updateStatus(status, detail = "") {
  const s = STATUS_MAP[status] || { label: status, dot: "idle" };

  statusCard.classList.add("visible");
  statusDot.className = `dot ${s.dot}`;
  statusLabel.textContent = s.label;
  statusDetail.textContent = detail;

  // Disable run button while job is running
  const running = ["starting", "fetching_crm", "crm_ok", "fetching_pdf", "pdf_ok", "opening_experian", "form_in_progress"];
  runBtn.disabled = running.includes(status);

  // Re-enable on terminal states
  if (["completed", "failed", "skipped", "idle"].includes(status)) {
    runBtn.disabled = false;
    loadTodaySummary(); // Refresh pills after completion
  }
}

// ── Today's summary pills ─────────────────────────────────────────────────────

async function loadTodaySummary() {
  return new Promise(resolve => {
    chrome.runtime.sendMessage({ type: "GET_SUMMARY" }, response => {
      if (!response?.summary) return resolve();

      const today = new Date().toISOString().substring(0, 10);
      const todayData = response.summary[today];
      if (!todayData) return resolve();

      let success = 0, errors = 0, skipped = 0;
      for (const [key, count] of Object.entries(todayData)) {
        if (key === "success") success += count;
        else if (key === "tiff_skipped") skipped += count;
        else errors += count;
      }

      summaryRow.innerHTML = "";
      if (success) summaryRow.innerHTML += `<span class="pill success">✓ ${success} Success</span>`;
      if (errors) summaryRow.innerHTML += `<span class="pill error">✗ ${errors} Error${errors > 1 ? "s" : ""}</span>`;
      if (skipped) summaryRow.innerHTML += `<span class="pill skip">⊘ ${skipped} Skipped</span>`;

      summaryRow.classList.toggle("visible", success + errors + skipped > 0);
      resolve();
    });
  });
}

// ── Load current state on popup open ─────────────────────────────────────────

async function init() {
  await loadTodaySummary();

  chrome.runtime.sendMessage({ type: "GET_STATE" }, response => {
    const state = response?.state;
    if (!state || !state.status) return;
    updateStatus(state.status, state.detail || "");
  });
}

// ── Run button ────────────────────────────────────────────────────────────────

runBtn.addEventListener("click", () => {
  runBtn.disabled = true;
  updateStatus("starting", "Initiating dispute job…");

  chrome.runtime.sendMessage({ type: "START_DISPUTE" }, response => {
    if (!response?.ok) {
      updateStatus("failed", "Failed to start — check background service worker.");
      runBtn.disabled = false;
    }
  });
});

// ── Listen for live status updates from background ───────────────────────────

chrome.runtime.onMessage.addListener(message => {
  if (message.type === "STATUS_UPDATE") {
    updateStatus(message.status, message.detail || "");
  }
});

// ── Navigation links ──────────────────────────────────────────────────────────

logsLink.addEventListener("click", () => {
  chrome.tabs.create({ url: chrome.runtime.getURL("logs.html") });
});

optionsLink.addEventListener("click", () => {
  chrome.runtime.openOptionsPage();
});

// ── Init ──────────────────────────────────────────────────────────────────────

init();
