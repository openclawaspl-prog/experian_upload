/**
 * background.js — Experian Dispute Automation Extension
 * ───────────────────────────────────────────────────────
 * Flow:
 *   1. Popup sends START_DISPUTE
 *   2. Fetch dispute record from CRM API
 *   3. Download PDF → Downloads/<filename>  (flat, no subfolder)
 *      Wait until chrome.downloads reports "complete"
 *   4. Open Experian tab — content script fills all pages
 *      Content script fetches the PDF from the original server URL
 *      (http://207.244.236.188 is in host_permissions) and forces
 *      MIME type "application/pdf" so Experian's file input accepts it
 *   5. On FORM_RESULT: post to CRM, delete the downloaded file, clean up
 */

// ── Config ────────────────────────────────────────────────────────────────────

const DEFAULT_CONFIG = {
   CRM_FETCH_URL: "https://crm.creditfreedomrestoration.com/autodataimport.php?mode=get_experian_dispute_data",
  CRM_SUCCESS_URL: "https://crm.creditfreedomrestoration.com/autodataimport.php?mode=experian_dispute_error_api_success",
  CRM_ERROR_URL: "https://crm.creditfreedomrestoration.com/autodataimport.php?mode=experian_dispute_error_api_error",
  PDF_BASE_URL: "http://207.244.236.188/PdfFiles/",
  EXPERIAN_URL: "https://www.experian.com/consumer/upload/",
  COMPRESS_THRESHOLD_MB: 5,
};

async function getConfig() {
  const stored = await chrome.storage.sync.get("config");
  return { ...DEFAULT_CONFIG, ...(stored.config || {}) };
}


// ── Logging ───────────────────────────────────────────────────────────────────

async function writeLog(jobId, step, detail, isFinal = false) {
  const ts = new Date().toISOString().replace("T", " ").substring(0, 19);
  const today = new Date().toISOString().substring(0, 10);
  const key = `log_${today}`;

  const stored = await chrome.storage.local.get(key);
  const logs = stored[key] || [];
  logs.push({ ts, jobId, step, detail, isFinal });

  const trimmed = logs.length > 2000 ? logs.slice(-2000) : logs;
  await chrome.storage.local.set({ [key]: trimmed });
  console.log(`[${ts}] [${jobId}] [${step}] ${detail}`);
}

async function updateSummary(event, rawMessage = "") {
  const today = new Date().toISOString().substring(0, 10);
  const stored = await chrome.storage.local.get("log_summary");
  const summary = stored.log_summary || {};
  if (!summary[today]) summary[today] = {};

  let key;
  if (event === "success") key = "success";
  else if (event === "skip") key = "tiff_skipped";
  else key = (rawMessage.trim() || "unknown_error").substring(0, 200);

  summary[today][key] = (summary[today][key] || 0) + 1;
  await chrome.storage.local.set({ log_summary: summary });
}


// ── CRM Helpers ───────────────────────────────────────────────────────────────

async function fetchDisputeFromCRM(config) {
  const res = await fetch(config.CRM_FETCH_URL, { cache: "no-store" });
  if (!res.ok) throw new Error(`CRM fetch failed — HTTP ${res.status} | ${(await res.text()).substring(0, 200)}`);
  return res.json();
}

async function postResultToCRM(config, disputeErrorId, resultMessage, isSuccess) {
  const url = isSuccess ? config.CRM_SUCCESS_URL : config.CRM_ERROR_URL;
  const payload = { experian_dispute_error_id: disputeErrorId, error_message: resultMessage };
  try {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "User-Agent": "Mozilla/5.0" },
      body: JSON.stringify(payload),
    });
    return `HTTP ${res.status} | ${(await res.text()).substring(0, 300)}`;
  } catch (err) {
    return `REQUEST FAILED: ${err.message}`;
  }
}


// ── PDF Download ──────────────────────────────────────────────────────────────
//
// Saves the PDF directly into Downloads/<filename> (no subfolder).
// Blocks until chrome.downloads fires state "complete" or "interrupted".
// Returns the completed DownloadItem (.id, .filename, .fileSize).

async function downloadPDF(pdfUrl, filename, jobId) {
  await writeLog(jobId, "PDF_DOWNLOAD_START", `Downloading: ${pdfUrl} → Downloads/${filename}`);

  return new Promise((resolve, reject) => {
    let settled = false;
    let targetDownloadId = null;

    function finish(result, isError) {
      if (settled) return;
      settled = true;
      chrome.downloads.onChanged.removeListener(onChanged);
      if (isError) reject(result);
      else resolve(result);
    }

    // Register listener BEFORE starting download to avoid race condition
    function onChanged(delta) {
      if (targetDownloadId === null || delta.id !== targetDownloadId) return;

      if (delta.state?.current === "complete") {
        chrome.downloads.search({ id: targetDownloadId }, (items) => {
          if (items?.[0]) finish(items[0], false);
          else finish(new Error("Download completed but DownloadItem not found"), true);
        });
      } else if (delta.state?.current === "interrupted") {
        finish(new Error(`Download interrupted — ${delta.error?.current || "unknown"}`), true);
      }
    }

    chrome.downloads.onChanged.addListener(onChanged);

    chrome.downloads.download(
      {
        url: pdfUrl,
        filename: filename,      // flat — goes straight into Downloads/
        conflictAction: "overwrite",
        saveAs: false,
      },
      (downloadId) => {
        if (chrome.runtime.lastError) {
          return finish(new Error(chrome.runtime.lastError.message), true);
        }
        if (downloadId === undefined) {
          return finish(new Error("chrome.downloads.download returned no downloadId"), true);
        }

        targetDownloadId = downloadId;

        // Safety net: check if already completed (race condition)
        chrome.downloads.search({ id: downloadId }, (items) => {
          if (settled) return;
          if (items?.[0]?.state === "complete") {
            finish(items[0], false);
          } else if (items?.[0]?.state === "interrupted") {
            finish(new Error(`Download interrupted — ${items[0].error || "unknown"}`), true);
          }
          // else still "in_progress" → onChanged listener will handle it
        });
      }
    );
  });
}


// ── Delete Downloaded File ────────────────────────────────────────────────────

async function deleteDownloadedFile(downloadId, jobId) {
  if (!downloadId) return;
  try {
    await new Promise(resolve => {
      chrome.downloads.removeFile(downloadId, () => {
        if (chrome.runtime.lastError)
          console.warn("[deleteDownload] removeFile:", chrome.runtime.lastError.message);
        resolve();
      });
    });
    await new Promise(resolve => {
      chrome.downloads.erase({ id: downloadId }, () => resolve());
    });
    await writeLog(jobId, "PDF_DELETED", `Download id=${downloadId} removed from disk and history`);
  } catch (err) {
    await writeLog(jobId, "PDF_DELETE_WARNING", `Could not delete: ${err.message}`);
  }
}


// ── Job State ─────────────────────────────────────────────────────────────────

async function setJobState(update) {
  const stored = await chrome.storage.session.get("job_state");
  const current = stored.job_state || {};
  await chrome.storage.session.set({ job_state: { ...current, ...update } });
}

async function getJobState() {
  const stored = await chrome.storage.session.get("job_state");
  return stored.job_state || {};
}

async function broadcastStatus(jobId, status, detail = "") {
  await setJobState({ status, detail, updatedAt: new Date().toISOString() });
  try { await chrome.runtime.sendMessage({ type: "STATUS_UPDATE", jobId, status, detail }); }
  catch (_) { /* popup closed */ }
}


// ── Main Pipeline ─────────────────────────────────────────────────────────────

async function runDisputePipeline() {
  const config = await getConfig();
  const jobId = new Date().toISOString().replace(/[:.T]/g, "").substring(0, 15);

  await broadcastStatus(jobId, "starting", "Job started");
  await writeLog(jobId, "JOB_START", `Dispute job started | id=${jobId}`);

  try {
    // ── 1. Fetch CRM record ───────────────────────────────────────────────────
    await broadcastStatus(jobId, "fetching_ip", "Fetching current IP…");
    
    try {
      const ipRes = await fetch("https://api.ipify.org?format=json");
      if (ipRes.ok) {
        const ipData = await ipRes.json();
        await writeLog(jobId, "IP_FETCHED", `Current IP: ${ipData.ip}`);
      }
    } catch (ipErr) {
      await writeLog(jobId, "IP_FETCH_FAILED", `Failed to get IP: ${ipErr.message}`);
    }

    await broadcastStatus(jobId, "fetching_crm", "Fetching dispute from CRM…");
    await writeLog(jobId, "CRM_FETCHING", "Calling CRM API…");

    const clientData = await fetchDisputeFromCRM(config);
    if (!clientData || Object.keys(clientData).length === 0)
      throw new Error("CRM returned empty response — no pending disputes");

    const disputeErrorId = String(clientData.experian_dispute_error_id || "");
    await writeLog(jobId, "CRM_FETCHED",
      `Client: ${clientData.firstname} ${clientData.lastname} | ` +
      `dispute_error_id: ${disputeErrorId} | client_id: ${clientData.client_id}`
    );
    await broadcastStatus(jobId, "crm_ok",
      `Got: ${clientData.firstname} ${clientData.lastname} | ID: ${disputeErrorId}`
    );

    // ── 2. Resolve filename ───────────────────────────────────────────────────
    const rawFilename = String(clientData.filename || clientData.filepath || "")
      .split(/[/\\]/).pop().trim();

    if (!rawFilename) throw new Error("No filename in CRM record");

    // Skip TIFF files — Experian doesn't accept them
    if (/\.tiff?$/i.test(rawFilename)) {
      await writeLog(jobId, "JOB_SKIPPED", `TIFF file — skipping: ${rawFilename}`, true);
      await updateSummary("skip");
      await broadcastStatus(jobId, "skipped", `TIFF skipped: ${rawFilename}`);
      return;
    }

    // Always ensure .pdf extension — Experian only accepts PDFs.
    // The server always serves a PDF regardless of the original extension in the CRM record.
    const pdfFilename = rawFilename.replace(/\.[^.]+$/, "") + ".pdf";

    // ── 3. Check for local file first (C:\inetpub\wwwroot\PdfFiles via IIS) ───
    let pdfUrl = config.PDF_BASE_URL + encodeURIComponent(rawFilename);
    const localUrl = `http://localhost/PdfFiles/${encodeURIComponent(rawFilename)}`;
    let isLocal = false;
    let sizeMB = 0;

    try {
      const checkRes = await fetch(localUrl, { method: "HEAD" });
      if (checkRes.ok) {
        pdfUrl = localUrl;
        isLocal = true;
        const length = checkRes.headers.get("Content-Length");
        if (length) sizeMB = parseInt(length) / (1024 * 1024);
        await writeLog(jobId, "LOCAL_FILE_FOUND", `Found in C:\\inetpub\\wwwroot\\PdfFiles\\ (via http://localhost/)`);
      }
    } catch (err) {
      // Local check failed (IIS offline or directory missing) - falling back to remote
    }

    await broadcastStatus(jobId, "fetching_pdf", isLocal ? `Using local PDF: ${pdfFilename}` : `Downloading PDF: ${pdfFilename}…`);

    let downloadItem = null;
    if (!isLocal) {
      try {
        downloadItem = await downloadPDF(pdfUrl, pdfFilename, jobId);
        sizeMB = (downloadItem.fileSize || 0) / (1024 * 1024);
      } catch (pdfErr) {
        const errMsg = `PDF download failed: ${pdfFilename} — ${pdfErr.message}`;
        await writeLog(jobId, "FILE_NOT_FOUND", errMsg);
        await updateSummary("error", errMsg);
        const crmRes = await postResultToCRM(config, disputeErrorId, errMsg, false);
        await writeLog(jobId, "CRM_RESPONSE", `Posted file-not-found error → ${crmRes}`);
        await broadcastStatus(jobId, "failed", errMsg);
        return;
      }
      await writeLog(jobId, "PDF_DOWNLOADED", `Saved: Downloads/${pdfFilename} | Size: ${sizeMB.toFixed(2)} MB`);
    }

    if (sizeMB > config.COMPRESS_THRESHOLD_MB) {
      await writeLog(jobId, "PDF_SIZE_WARNING",
        `File is ${sizeMB.toFixed(2)} MB — exceeds ${config.COMPRESS_THRESHOLD_MB} MB threshold. ` +
        `Uploading original. To auto-compress, expose a /compress-pdf endpoint on your server.`
      );
    }

    await broadcastStatus(jobId, "pdf_ok", `PDF ready: ${pdfFilename} (${sizeMB.toFixed(2)} MB)`);

    // ── 4. Fetch PDF bytes in background (content scripts can't cross-origin fetch in MV3)
    //
    // In Manifest V3, content scripts are subject to the PAGE's CORS policy,
    // NOT the extension's host_permissions. Since we're on experian.com and
    // the PDF lives on http://207.244.236.188, the content script's fetch()
    // would be blocked by CORS + mixed-content (HTTPS→HTTP).
    // Solution: fetch here in the service worker (which HAS host_permissions)
    // and pass the base64 data to the content script via storage.

    await writeLog(jobId, "PDF_FETCHING_BYTES", "Fetching PDF bytes for content script…");
    let pdfBase64 = "";
    try {
      const pdfRes = await fetch(pdfUrl, { cache: "no-store" });
      if (!pdfRes.ok) throw new Error(`HTTP ${pdfRes.status}`);
      const arrayBuf = await pdfRes.arrayBuffer();
      // Convert to base64 for safe transfer via chrome.storage
      const bytes = new Uint8Array(arrayBuf);
      let binary = "";
      const chunkSize = 8192;
      for (let i = 0; i < bytes.length; i += chunkSize) {
        binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunkSize));
      }
      pdfBase64 = btoa(binary);
      await writeLog(jobId, "PDF_BYTES_OK", `Got ${bytes.length} bytes (${(bytes.length / 1024 / 1024).toFixed(2)} MB)`);
    } catch (fetchErr) {
      await writeLog(jobId, "PDF_BYTES_ERROR", `Could not fetch PDF bytes: ${fetchErr.message}`);
      // Will fall back to content script trying fetch (may fail due to CORS)
    }

    // ── 5. Prepare SSN parts ──────────────────────────────────────────────────
    const rawSSN = String(clientData.social_security_number || "").replace(/[-\s]/g, "");

    // ── 6. Store job payload for content script ───────────────────────────────
    //
    // pdfBase64   → PDF file data as base64 (fetched by background, not content script)
    // pdfFilename → .pdf extension guaranteed; used as File.name
    // downloadId  → background uses this after job to delete the file

    const jobPayload = {
      jobId,
      disputeErrorId,
      clientData: {
        firstname: clientData.firstname,
        lastname: clientData.lastname,
        address: clientData.address,
        city: clientData.city,
        state: clientData.state,
        postalcode: clientData.postalcode,
        email: clientData.email,
        ssn1: rawSSN.substring(0, 3),
        ssn2: rawSSN.substring(3, 5),
        ssn3: rawSSN.substring(5, 9),
        disputeErrorId,
      },
      pdf: {
        pdfUrl,
        pdfBase64,             // PDF bytes as base64 — avoids CORS in content script
        filename: pdfFilename, // always ends in .pdf
        sizeMB,
        downloadId: downloadItem?.id,
      },
      page: 1,
    };

    await chrome.storage.local.set({ experian_job: jobPayload });
    await writeLog(jobId, "JOB_STORED", "Job payload written — opening Experian tab…");

    // ── 6. Open Experian tab ──────────────────────────────────────────────────
    await broadcastStatus(jobId, "opening_experian", "Opening Experian upload page…");
    const tab = await chrome.tabs.create({ url: config.EXPERIAN_URL, active: true });
    await setJobState({ tabId: tab.id, jobId, status: "form_in_progress" });
    await writeLog(jobId, "TAB_OPENED",
      `Experian tab opened | tab_id=${tab.id} | url=${config.EXPERIAN_URL}`
    );

  } catch (err) {
    await writeLog(jobId, "JOB_FAILED", `FATAL: ${err.message}`, true);
    await updateSummary("error", err.message);
    await broadcastStatus(jobId, "failed", err.message);
  }
}


// ── Handle Result from Content Script ────────────────────────────────────────

async function handleFormResult({ jobId, isSuccess, resultMessage, disputeErrorId }) {
  const config = await getConfig();

  await writeLog(jobId, isSuccess ? "FORM_SUCCESS" : "FORM_ERROR",
    `Result: ${resultMessage.substring(0, 500)}`
  );

  if (isSuccess) await updateSummary("success");
  else await updateSummary("error", resultMessage);

  await writeLog(jobId, "CRM_POSTING",
    `Posting ${isSuccess ? "SUCCESS" : "ERROR"} to CRM | dispute_error_id=${disputeErrorId}`
  );
  const crmRes = await postResultToCRM(config, disputeErrorId, resultMessage, isSuccess);
  await writeLog(jobId, "CRM_RESPONSE", crmRes);

  // Delete the PDF from Downloads/
  const stored = await chrome.storage.local.get("experian_job");
  const downloadId = stored.experian_job?.pdf?.downloadId;
  await deleteDownloadedFile(downloadId, jobId);

  await chrome.storage.local.remove("experian_job");

  await broadcastStatus(jobId,
    isSuccess ? "completed" : "failed",
    resultMessage.substring(0, 200)
  );
  await writeLog(jobId, "JOB_DONE",
    `Finished | success=${isSuccess} | crm_response=${crmRes.substring(0, 100)}`,
    true
  );
}


// ── Message Listener ──────────────────────────────────────────────────────────

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  switch (message.type) {

    case "START_DISPUTE":
      runDisputePipeline().catch(console.error);
      sendResponse({ ok: true });
      return false;

    case "FORM_RESULT":
      handleFormResult(message)
        .then(() => sendResponse({ ok: true }))
        .catch(err => sendResponse({ ok: false, error: err.message }));
      return true;

    case "CONTENT_LOG":
      writeLog(message.jobId, message.step, message.detail).catch(console.error);
      return false;

    case "FETCH_PDF_DATA":
      // Fallback: content script requests PDF bytes via messaging
      // (in case pdfBase64 was empty in storage)
      (async () => {
        try {
          const res = await fetch(message.pdfUrl, { cache: "no-store" });
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          const buf = await res.arrayBuffer();
          const bytes = new Uint8Array(buf);
          let binary = "";
          const chunkSize = 8192;
          for (let i = 0; i < bytes.length; i += chunkSize) {
            binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunkSize));
          }
          sendResponse({ ok: true, pdfBase64: btoa(binary) });
        } catch (err) {
          sendResponse({ ok: false, error: err.message });
        }
      })();
      return true;

    case "GET_STATE":
      getJobState()
        .then(state => sendResponse({ state }))
        .catch(() => sendResponse({ state: {} }));
      return true;

    case "GET_SUMMARY":
      chrome.storage.local.get("log_summary")
        .then(r => sendResponse({ summary: r.log_summary || {} }));
      return true;

    case "GET_LOGS":
      chrome.storage.local.get(`log_${message.date}`)
        .then(r => sendResponse({ logs: r[`log_${message.date}`] || [] }));
      return true;
  }
});