/**
 * logs.js — Log viewer for Experian Dispute Automation Extension
 */

const datePicker = document.getElementById("datePicker");
const searchInput = document.getElementById("searchInput");
const loadBtn = document.getElementById("loadBtn");
const logBody = document.getElementById("logBody");
const logCount = document.getElementById("logCount");
const summaryBar = document.getElementById("summaryBar");

// Default to today
const today = new Date().toISOString().substring(0, 10);
datePicker.value = today;

// Step → colour class mapping
function stepClass(step) {
  const s = step.toUpperCase();
  if (s.includes("START") || s.includes("OPEN")) return "step-start";
  if (s.includes("SUCCESS") || s.includes("DONE") || s.includes("OK")) return "step-success";
  if (s.includes("ERROR") || s.includes("FAILED") || s.includes("EXCEPTION")) return "step-error";
  if (s.includes("WARNING") || s.includes("SKIP") || s.includes("MISSING")) return "step-warning";
  return "step-info";
}

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function renderLogs(logs, filter = "") {
  const q = filter.trim().toLowerCase();
  const filtered = q
    ? logs.filter(l =>
      l.step.toLowerCase().includes(q) ||
      l.detail.toLowerCase().includes(q) ||
      (l.jobId || "").toLowerCase().includes(q)
    )
    : logs;

  if (filtered.length === 0) {
    logBody.innerHTML = `<tr><td colspan="4" class="empty">No log entries found</td></tr>`;
    logCount.textContent = "0 entries";
    return;
  }

  logCount.textContent = `${filtered.length.toLocaleString()} entries`;

  logBody.innerHTML = filtered.map(entry => `
    <tr>
      <td class="ts">${escapeHtml(entry.ts || "")}</td>
      <td class="jobId">${escapeHtml(entry.jobId || "")}</td>
      <td class="step ${stepClass(entry.step || "")}">${escapeHtml(entry.step || "")}</td>
      <td class="detail">${escapeHtml(entry.detail || "")}</td>
    </tr>
  `).join("");
}

function renderSummary(summary, date) {
  const dayData = summary[date];
  if (!dayData) { summaryBar.innerHTML = ""; return; }

  let success = 0, errors = 0, skipped = 0;
  const errorKeys = [];

  for (const [key, count] of Object.entries(dayData)) {
    if (key === "success") success += count;
    else if (key === "tiff_skipped") skipped += count;
    else { errors += count; errorKeys.push(`${key}: ${count}`); }
  }

  let html = "";
  if (success) html += `<span class="pill success">✓ ${success} Success</span>`;
  if (errors) html += `<span class="pill error">✗ ${errors} Errors — ${errorKeys.slice(0, 3).join(", ")}</span>`;
  if (skipped) html += `<span class="pill skip">⊘ ${skipped} TIFF Skipped</span>`;

  summaryBar.innerHTML = html;
}

async function loadLogs() {
  const date = datePicker.value || today;

  logBody.innerHTML = `<tr><td colspan="4" class="empty">Loading…</td></tr>`;
  logCount.textContent = "";

  // Load logs
  const logsRes = await chrome.runtime.sendMessage({ type: "GET_LOGS", date });
  const logs = logsRes?.logs || [];

  // Load summary
  const sumRes = await chrome.runtime.sendMessage({ type: "GET_SUMMARY" });
  renderSummary(sumRes?.summary || {}, date);

  renderLogs(logs, searchInput.value);
}

loadBtn.addEventListener("click", loadLogs);
datePicker.addEventListener("change", loadLogs);

searchInput.addEventListener("input", async () => {
  const date = datePicker.value || today;
  const logsRes = await chrome.runtime.sendMessage({ type: "GET_LOGS", date });
  renderLogs(logsRes?.logs || [], searchInput.value);
});

// Load today's logs on open
loadLogs();

// IP Banner logic
const currentIpText = document.getElementById("currentIpText");
const refreshIpBtn = document.getElementById("refreshIpBtn");

async function fetchAndDisplayIp() {
  currentIpText.textContent = "Fetching...";
  try {
    const res = await fetch("https://api.ipify.org?format=json");
    if (res.ok) {
      const data = await res.json();
      currentIpText.textContent = data.ip;
    } else {
      currentIpText.textContent = "Error: " + res.status;
    }
  } catch (err) {
    currentIpText.textContent = "Failed to load IP";
  }
}

refreshIpBtn?.addEventListener("click", fetchAndDisplayIp);
fetchAndDisplayIp();
