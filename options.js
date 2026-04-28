/**
 * options.js — Settings page for Experian Dispute Automation Extension
 */

const DEFAULTS = {
  PDF_BASE_URL: "http://207.244.236.188/PdfFiles/",
  CRM_FETCH_URL: "https://crm.creditfreedomrestoration.com/import_api.php?mode=get_experian_dispute_data",
  CRM_SUCCESS_URL: "https://crm.creditfreedomrestoration.com/import_api.php?mode=experian_dispute_error_api_success",
  CRM_ERROR_URL: "https://crm.creditfreedomrestoration.com/import_api.php?mode=experian_dispute_error_api_error",
  COMPRESS_THRESHOLD_MB: 5,
};

const fields = {
  pdfBaseUrl: "PDF_BASE_URL",
  crmFetchUrl: "CRM_FETCH_URL",
  crmSuccessUrl: "CRM_SUCCESS_URL",
  crmErrorUrl: "CRM_ERROR_URL",
  compressThreshold: "COMPRESS_THRESHOLD_MB",
};

// Load saved config into form
async function loadConfig() {
  const stored = await chrome.storage.sync.get("config");
  const config = { ...DEFAULTS, ...(stored.config || {}) };

  for (const [fieldId, configKey] of Object.entries(fields)) {
    const el = document.getElementById(fieldId);
    if (el) el.value = config[configKey] ?? DEFAULTS[configKey];
  }
}

// Save form values to chrome.storage.sync
async function saveConfig() {
  const config = {};
  for (const [fieldId, configKey] of Object.entries(fields)) {
    const el = document.getElementById(fieldId);
    if (!el) continue;
    config[configKey] = el.type === "number" ? Number(el.value) : el.value.trim();
  }
  await chrome.storage.sync.set({ config });

  const saveMsg = document.getElementById("saveMsg");
  saveMsg.classList.add("visible");
  setTimeout(() => saveMsg.classList.remove("visible"), 2500);
}

// Reset to defaults
async function resetConfig() {
  await chrome.storage.sync.remove("config");
  await loadConfig();
}

document.getElementById("saveBtn").addEventListener("click", saveConfig);
document.getElementById("resetBtn").addEventListener("click", resetConfig);

loadConfig();
