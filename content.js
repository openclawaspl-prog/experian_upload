/**
 * content.js — Experian Dispute Automation Extension
 * ────────────────────────────────────────────────────
 * Injected into: https://www.experian.com/consumer/upload*
 *
 *   Page 1 — Personal Info (firstName, lastName, address, SSN, email…)
 *   Page 2 — Dispute Details (reason, description, PDF upload)
 *   Page 3 — Confirmation  (select Yes → Continue; read success/error)
 *
 * PDF upload strategy:
 *   - Background already downloaded the file to Downloads/<filename>.pdf
 *   - Content script fetches from the original pdfUrl (http://207.244.236.188
 *     is in host_permissions) to get the raw bytes
 *   - MIME type is ALWAYS forced to "application/pdf" regardless of what the
 *     server sends — Experian's file input rejects anything that isn't a PDF
 *   - Filename is ALWAYS <name>.pdf for the same reason
 *   - After job completes, background deletes the Downloads copy
 */

(async function ExperianDispute() {
  "use strict";

  // ── Basic helpers ────────────────────────────────────────────────────────────

  const sleep = ms => new Promise(r => setTimeout(r, ms));

  function rand(min, max) {
    return Math.floor(Math.random() * (max - min + 1)) + min;
  }

  function clog(step, detail) {
    console.log(`[Experian Extension] [${step}] ${detail}`);
    chrome.runtime.sendMessage({ type: "CONTENT_LOG", jobId, step, detail }).catch(() => { });
  }

  async function waitFor(selector, timeoutMs = 10000, intervalMs = 500) {
    const start = Date.now();
    while (Date.now() - start < timeoutMs) {
      const el = document.querySelector(selector);
      if (el) return el;
      await sleep(intervalMs);
    }
    return null;
  }

  async function waitVisible(selector, timeoutMs = 10000, intervalMs = 500) {
    const start = Date.now();
    while (Date.now() - start < timeoutMs) {
      const els = document.querySelectorAll(selector);
      for (const el of els) {
        if (el.offsetWidth > 0 || el.offsetHeight > 0) return el;
      }
      await sleep(intervalMs);
    }
    return null;
  }


  // ── Human-like typing engine ─────────────────────────────────────────────────

  async function humanType(el, text, opts = {}) {
    if (!el || !text) return;

    const minDelay = opts.minDelay ?? 65;
    const maxDelay = opts.maxDelay ?? 155;
    const pauseMin = opts.pauseMin ?? 200;
    const pauseMax = opts.pauseMax ?? 420;
    const pauseEvery = opts.pauseEvery ?? rand(4, 8);

    el.focus();
    el.click();
    await sleep(rand(80, 200));

    const isCE = el.isContentEditable;
    const nativeSetter = (!isCE && el instanceof HTMLTextAreaElement)
      ? Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, "value")?.set
      : (!isCE ? Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value")?.set : null);

    if (isCE) el.textContent = "";
    else if (nativeSetter) nativeSetter.call(el, "");
    else el.value = "";
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    await sleep(rand(30, 90));

    let charsSincePause = 0;

    for (let i = 0; i < text.length; i++) {
      const char = text[i];
      const keyCode = char.toUpperCase().charCodeAt(0);
      const keyInit = {
        key: char, code: `Key${char.toUpperCase()}`,
        keyCode, which: keyCode, charCode: keyCode,
        bubbles: true, cancelable: true,
      };

      el.dispatchEvent(new KeyboardEvent("keydown", keyInit));
      el.dispatchEvent(new KeyboardEvent("keypress", { ...keyInit, charCode: char.charCodeAt(0) }));

      let newVal = "";
      if (isCE) {
        newVal = (el.textContent || "") + char;
        el.textContent = newVal;
      } else {
        newVal = (el.value || "") + char;
        if (nativeSetter) nativeSetter.call(el, newVal);
        else el.value = newVal;
      }

      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.dispatchEvent(new KeyboardEvent("keyup", keyInit));

      charsSincePause++;
      if (charsSincePause >= pauseEvery) {
        await sleep(rand(pauseMin, pauseMax));
        charsSincePause = 0;
      } else if (Math.random() < 0.015) {
        await sleep(rand(600, 1400));
      } else {
        await sleep(rand(minDelay, maxDelay));
      }
    }

    el.dispatchEvent(new Event("change", { bubbles: true }));
    el.blur();
    await sleep(rand(60, 140));
  }

  async function typeField(selectors, value) {
    const list = Array.isArray(selectors) ? selectors : [selectors];
    for (const sel of list) {
      const elements = document.querySelectorAll(sel);
      for (const el of elements) {
        if (el.offsetWidth === 0 && el.offsetHeight === 0) continue;
        await humanType(el, String(value ?? ""));
        return true;
      }
    }
    return false;
  }

  async function typeTextarea(selectors, value) {
    const list = Array.isArray(selectors) ? selectors : [selectors];
    for (const sel of list) {
      const elements = document.querySelectorAll(sel);
      for (const el of elements) {
        if (el.offsetWidth === 0 && el.offsetHeight === 0) continue;
        await humanType(el, String(value ?? ""), {
          minDelay: 85, maxDelay: 210,
          pauseEvery: rand(3, 6), pauseMin: 300, pauseMax: 600,
        });
        return true;
      }
    }
    return false;
  }

  function setSelect(selectors, value) {
    const list = Array.isArray(selectors) ? selectors : [selectors];
    for (const sel of list) {
      const el = document.querySelector(sel);
      if (!el) continue;
      el.focus(); el.click();
      try {
        const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value")?.set;
        if (setter) setter.call(el, value); else el.value = value;
      } catch (_) { el.value = value; }
      el.dispatchEvent(new Event("change", { bubbles: true }));
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.blur();
      return true;
    }
    return false;
  }


  // ── PDF → File object ────────────────────────────────────────────────────────
  //
  // In Manifest V3, content scripts are subject to the PAGE's CORS policy,
  // NOT the extension's host_permissions. Since we're on experian.com and the
  // PDF lives on a different origin (http://207.244.236.188), we CANNOT fetch
  // it directly from the content script.
  //
  // Instead, the background service worker fetches the PDF bytes and stores
  // them as base64 in chrome.storage.local (in the job payload).
  // We decode that base64 here to build the File object.
  //
  // MIME type is ALWAYS forced to "application/pdf" — Experian's
  // <input accept=".pdf"> rejects anything else.
  // Filename is already guaranteed to end in ".pdf" by background.js.

  function base64ToArrayBuffer(base64) {
    const binary = atob(base64);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) {
      bytes[i] = binary.charCodeAt(i);
    }
    return bytes.buffer;
  }

  async function buildPdfFile(pdfBase64, pdfUrl, filename) {
    let arrayBuf;

    if (pdfBase64) {
      // Primary path: use base64 data from background service worker
      clog("PDF_BUILD", "Building File from base64 data (fetched by background)");
      arrayBuf = base64ToArrayBuffer(pdfBase64);
    } else {
      // Fallback: ask background to fetch via messaging
      clog("PDF_BUILD", "No base64 in storage — requesting via FETCH_PDF_DATA message");
      const response = await new Promise((resolve, reject) => {
        chrome.runtime.sendMessage(
          { type: "FETCH_PDF_DATA", pdfUrl },
          (res) => {
            if (chrome.runtime.lastError) reject(new Error(chrome.runtime.lastError.message));
            else if (!res?.ok) reject(new Error(res?.error || "FETCH_PDF_DATA failed"));
            else resolve(res);
          }
        );
      });
      arrayBuf = base64ToArrayBuffer(response.pdfBase64);
    }

    const pdfBlob = new Blob([arrayBuf], { type: "application/pdf" });
    return new File([pdfBlob], filename, { type: "application/pdf", lastModified: Date.now() });
  }

  async function attachPdfToInput(inputEl, pdfBase64, pdfUrl, filename) {
    const file = await buildPdfFile(pdfBase64, pdfUrl, filename);

    const dt = new DataTransfer();
    dt.items.add(file);
    inputEl.files = dt.files;
    inputEl.dispatchEvent(new Event("change", { bubbles: true }));
    inputEl.dispatchEvent(new Event("input", { bubbles: true }));
    return inputEl.files.length > 0;
  }


  // ── Misc helpers ─────────────────────────────────────────────────────────────

  function sendResult(isSuccess, resultMessage) {
    chrome.runtime.sendMessage({
      type: "FORM_RESULT", jobId, isSuccess, resultMessage,
      disputeErrorId: jobData.disputeErrorId,
    }).catch(() => { });
  }

  async function setPage(pageNumber) {
    const s = await chrome.storage.local.get("experian_job");
    if (s.experian_job) {
      s.experian_job.page = pageNumber;
      await chrome.storage.local.set({ experian_job: s.experian_job });
    }
  }

  const ERROR_PATTERNS = [
    "we're sorry. we were unable to honor your request",
    "your file couldn't be accessed",
    "err_empty_response",
    "an error has occurred",
    "unable to process your request",
  ];
  const hasErrorText = text => ERROR_PATTERNS.some(p => text.toLowerCase().includes(p));


  // ── Load job data ────────────────────────────────────────────────────────────

  await sleep(1500);

  const stored = await chrome.storage.local.get("experian_job");
  const jobData = stored.experian_job;

  if (!jobData) {
    console.log("[Experian Extension] No active job — standing by.");
    return;
  }

  const { jobId, clientData, pdf } = jobData;
  clog("PAGE_LOAD", `URL: ${window.location.href} | job_page: ${jobData.page}`);


  // ── Page detection ────────────────────────────────────────────────────────────

  async function detectAndHandlePage() {
    await sleep(2000);
    const hasP1 = document.querySelector("#firstName, [name='firstName']");
    const hasP2 = document.querySelector("#reason, #file0, #tellusmore, [name='tellusmore']");
    const storedPage = jobData.page || 1;

    if (hasP1 && storedPage <= 1) await handlePage1();
    else if (hasP2 || storedPage === 2) await handlePage2();
    else if (storedPage === 3 || (!hasP1 && !hasP2)) await handlePage3();
    else {
      await sleep(3000);
      const rP1 = document.querySelector("#firstName, [name='firstName']");
      const rP2 = document.querySelector("#reason, #file0");
      if (rP1) await handlePage1();
      else if (rP2) await handlePage2();
      else await handlePage3();
    }
  }

  await detectAndHandlePage();


  // ── PAGE 1 — Personal Information ────────────────────────────────────────────

  async function handlePage1() {
    clog("PAGE1_START", "Page 1 — typing personal info (human mode)…");

    const fnField = await waitFor("#firstName, [name='firstName']", 15000);
    if (!fnField) {
      clog("PAGE1_ERROR", "Page 1 fields never appeared — capturing error text");
      const finalText = document.body.innerText || document.body.textContent || "";
      let errorMsg = "ERROR: Page 1 form fields did not load";
      if (finalText) {
        errorMsg = `ERROR: ${extractErrorText(finalText).substring(0, 300)}`;
      }
      sendResult(false, errorMsg);
      return;
    }

    await sleep(rand(800, 1500));

    await typeField(["#firstName", "[name='firstName']"], clientData.firstname);
    clog("PAGE1_FIELD", `firstName → "${clientData.firstname}"`);
    await sleep(rand(300, 700));

    await typeField(["#lastName", "[name='lastName']"], clientData.lastname);
    clog("PAGE1_FIELD", `lastName → "${clientData.lastname}"`);
    await sleep(rand(300, 700));

    await typeField(["#address", "[name='address']"], clientData.address);
    clog("PAGE1_FIELD", `address → "${clientData.address}"`);
    await sleep(rand(400, 800));

    await typeField(["#city", "[name='city']"], clientData.city);
    clog("PAGE1_FIELD", `city → "${clientData.city}"`);
    await sleep(rand(300, 600));

    setSelect(["#state", "[name='state']"], clientData.state);
    clog("PAGE1_FIELD", `state → "${clientData.state}"`);
    await sleep(rand(400, 750));

    await typeField(
      ["#zip", "[name='zip']", "[name='postalcode']", "#postalcode"],
      clientData.postalcode
    );
    clog("PAGE1_FIELD", `zip → "${clientData.postalcode}"`);
    await sleep(rand(500, 1000));

    await typeField(["#ssn1", "[name='ssn1']"], clientData.ssn1);
    await sleep(rand(200, 500));
    await typeField(["#ssn2", "[name='ssn2']"], clientData.ssn2);
    await sleep(rand(200, 500));
    await typeField(["#ssn3", "[name='ssn3']"], clientData.ssn3);
    clog("PAGE1_FIELD", `ssn → ${clientData.ssn1}-${clientData.ssn2}-****`);
    await sleep(rand(400, 800));

    await typeField(["#emailAddress", "[name='emailAddress']"], clientData.email);
    clog("PAGE1_FIELD", `emailAddress → "${clientData.email}"`);
    await sleep(rand(300, 650));

    await typeField(["#emailAddressConfirm", "[name='emailAddressConfirm']"], clientData.email);
    clog("PAGE1_FIELD", `emailAddressConfirm → (same)`);
    await sleep(rand(300, 650));

    await typeField(["#alertReportId", "[name='alertReportId']"], clientData.disputeErrorId);
    clog("PAGE1_FIELD", `alertReportId → "${clientData.disputeErrorId}"`);
    await sleep(rand(400, 750));

    const checkbox = document.querySelector("#frmAcceptTC1, [name='frmAcceptTC1']");
    if (checkbox && !checkbox.checked) {
      await sleep(rand(400, 700));
      checkbox.click();
      clog("PAGE1_CHECKBOX", "Checked frmAcceptTC1");
      await sleep(rand(200, 450));
    } else if (!checkbox) {
      clog("PAGE1_CHECKBOX_MISSING", "#frmAcceptTC1 not found — continuing");
    }

    // Sanity-check: re-type SSN if framework wiped it
    await sleep(500);
    const ssn1El = document.querySelector("#ssn1, [name='ssn1']");
    if (ssn1El && !ssn1El.value) {
      clog("PAGE1_REFILL_SSN", "SSN empty after fill — re-typing");
      await typeField(["#ssn1", "[name='ssn1']"], clientData.ssn1);
      await sleep(rand(200, 450));
      await typeField(["#ssn2", "[name='ssn2']"], clientData.ssn2);
      await sleep(rand(200, 450));
      await typeField(["#ssn3", "[name='ssn3']"], clientData.ssn3);
      await sleep(rand(400, 700));
    }

    await setPage(2);
    await sleep(rand(700, 1400));

    const continueBtn = document.querySelector(
      "#continueButton, [name='continueButton'], button[type='submit']"
    );
    if (!continueBtn) {
      clog("PAGE1_ERROR", "Continue button not found");
      sendResult(false, "ERROR: Page 1 Continue button not found");
      return;
    }

    clog("PAGE1_SUBMIT", "Clicking Continue on Page 1…");
    continueBtn.click();
  }


  // ── PAGE 2 — Dispute Details + File Upload ────────────────────────────────────

  async function handlePage2() {
    clog("PAGE2_START", "Page 2 — filling dispute details…");

    const reasonEl = await waitFor("#reason", 15000);
    if (!reasonEl) {
      clog("PAGE2_ERROR", "#reason never appeared — capturing error text");
      const finalText = document.body.innerText || document.body.textContent || "";
      let errorMsg = "ERROR: Page 2 did not load — #reason not found";
      if (finalText) {
        errorMsg = `ERROR: ${extractErrorText(finalText).substring(0, 300)}`;
      }
      sendResult(false, errorMsg);
      return;
    }

    await sleep(rand(700, 1200));

    setSelect("#reason", "01");
    clog("PAGE2_REASON", "Selected reason: 01");

    // Give the page extra time to render the textarea after reason is selected
    await sleep(rand(1000, 1800));

    const textSelectors = [
      "#divTellusmore",
      "#tellusmore",
      "[name='tellusmore']",
      "#comments",
      "[name='comments']",
      "#description",
      "[name='description']",
      "textarea"
    ];

    await waitVisible(textSelectors.join(", "), 8000, 500);

    const textOk = await typeTextarea(textSelectors, "for dispute");
    clog("PAGE2_DETAILS", `Typed #tellusmore | ok: ${textOk}`);

    // Explicitly update the hidden input as well to guarantee submission
    const hiddenTell = document.querySelector("#tellusmore");
    if (hiddenTell) hiddenTell.value = "for dispute";

    await sleep(rand(500, 1000));

    // ── File upload ───────────────────────────────────────────────────────────
    const fileInput = await waitFor("#file0", 15000);
    if (!fileInput) {
      clog("PAGE2_FILE_MISSING", "#file0 not found");
      const finalText = document.body.innerText || document.body.textContent || "";
      let errorMsg = "ERROR: File upload input #file0 not found on Page 2";
      if (finalText) {
        errorMsg = `ERROR: ${extractErrorText(finalText).substring(0, 300)}`;
      }
      sendResult(false, errorMsg);
      return;
    }

    clog("PAGE2_FILE_ATTACH",
      `Building PDF File object: ${pdf.filename} (${pdf.sizeMB.toFixed(2)} MB) | ` +
      `base64 available: ${!!pdf.pdfBase64}`
    );

    // Build File from base64 data (fetched by background service worker).
    // Content scripts can't cross-origin fetch in MV3 — CORS blocks it.
    // If base64 is missing, attachPdfToInput will fall back to FETCH_PDF_DATA messaging.
    let attached = false;
    try {
      attached = await attachPdfToInput(fileInput, pdf.pdfBase64, pdf.pdfUrl, pdf.filename);
    } catch (fetchErr) {
      clog("PAGE2_FILE_FETCH_ERROR", `First attempt failed: ${fetchErr.message} — retrying…`);
      await sleep(2000);
      try {
        attached = await attachPdfToInput(fileInput, pdf.pdfBase64, pdf.pdfUrl, pdf.filename);
      } catch (retryErr) {
        clog("PAGE2_FILE_FAILED", `Retry also failed: ${retryErr.message}`);
        sendResult(false, `ERROR: Could not fetch PDF for upload — ${retryErr.message}`);
        return;
      }
    }

    if (!attached || !fileInput.files?.length) {
      clog("PAGE2_FILE_FAILED", "File not registered in input after attach");
      sendResult(false, "ERROR: File upload failed — file not registered in #file0");
      return;
    }

    clog("PAGE2_FILE_OK",
      `File attached: ${fileInput.files[0].name} | ` +
      `type: ${fileInput.files[0].type} | ` +
      `size: ${fileInput.files[0].size} bytes`
    );

    // Human wait after upload (watching progress bar)
    const uploadWaitMs = rand(10000, 15000);
    clog("PAGE2_UPLOAD_WAIT",
      `Waiting ${(uploadWaitMs / 1000).toFixed(1)}s after file attachment…`
    );
    await sleep(uploadWaitMs);

    await setPage(3);
    await sleep(rand(700, 1300));

    const continueBtn = document.querySelector("#continueButton, [name='continueButton']");
    if (!continueBtn) {
      clog("PAGE2_ERROR", "Continue button not found on Page 2");
      sendResult(false, "ERROR: Page 2 Continue button not found");
      return;
    }

    clog("PAGE2_SUBMIT", "Clicking Continue on Page 2…");
    continueBtn.click();
  }


  // ── PAGE 3 — Confirmation / Result ───────────────────────────────────────────

  async function handlePage3() {
    clog("PAGE3_START", "Page 3 — reading confirmation…");
    await sleep(4000);

    const yesSelectors = [
      "input[type='radio'][value='OK']",
      "input[type='radio'][value='Y']",
      "input[type='radio'][value='Yes']",
      "#yes",
      "[name='addMoreDocuments'][value='yes']",
      "[name='addmore'][value='yes']",
    ];

    let yesClicked = false;

    for (const sel of yesSelectors) {
      const el = document.querySelector(sel);
      if (el) {
        await sleep(rand(400, 900));
        el.click();
        clog("PAGE3_YES", `Selected Yes: ${sel}`);
        yesClicked = true;
        await sleep(rand(600, 1100));
        break;
      }
    }

    if (!yesClicked) {
      const allBtns = Array.from(document.querySelectorAll("button, input[type='button']"));
      const yesBtn = allBtns.find(
        b => (b.textContent || b.value || "").trim().toLowerCase() === "yes"
      );
      if (yesBtn) {
        await sleep(rand(700, 1200));
        yesBtn.click();
        clog("PAGE3_YES_BTN", "Clicked Yes button");
        yesClicked = true;
        await sleep(rand(600, 1100));
      }
    }

    if (!yesClicked) {
      clog("PAGE3_YES_SKIP", "No Yes option found — may not apply to this flow");
    }

    // ── Submit button logic ───────────────────────────────────────────────────
    // If the radio value='OK' was found and clicked, use #submitButton.
    // Otherwise fall back to the standard continueButton.

    if (yesClicked) {
      const submitBtn = document.querySelector("#submitButton");
      if (submitBtn) {
        await sleep(rand(600, 1200));
        clog("PAGE3_SUBMIT", "Clicking #submitButton after selecting OK radio…");

        // Re-enable in case it was previously disabled (e.g. previous attempt)
        submitBtn.disabled = false;

        submitBtn.click();
        await sleep(4000);
      } else {
        clog("PAGE3_SUBMIT_MISSING", "#submitButton not found — falling back to continueButton");
        const continueBtn = document.querySelector(
          "#continueButton, [name='continueButton'], button[type='submit']"
        );
        if (continueBtn) {
          await sleep(rand(600, 1200));
          clog("PAGE3_CONTINUE_FALLBACK", "Clicking continueButton as fallback…");
          continueBtn.click();
          await sleep(4000);
        }
      }
    } else {
      // No radio was clicked — try continueButton as before
      const continueBtn = document.querySelector(
        "#continueButton, [name='continueButton'], button[type='submit']"
      );
      if (continueBtn) {
        await sleep(rand(600, 1200));
        clog("PAGE3_CONTINUE", "Clicking Continue (no Yes radio found)…");
        continueBtn.click();
        await sleep(4000);
      }
    }

    // ── Read result ───────────────────────────────────────────────────────────
    const confirmUrl = "https://www.experian.com/consumer/upload/confirm";
    const urlCheckInterval = 500;
    const urlCheckTimeout = 15000;
    const urlStart = Date.now();

    clog("PAGE3_WAIT_URL", `Waiting for confirm URL: ${confirmUrl}`);

    while (Date.now() - urlStart < urlCheckTimeout) {
      if (window.location.href.includes("/consumer/upload/confirm")) break;
      await sleep(urlCheckInterval);
    }

    const isConfirmUrl = window.location.href.includes("/consumer/upload/confirm");
    clog("PAGE3_URL_CHECK", `Confirm URL reached: ${isConfirmUrl} | current: ${window.location.href}`);

    if (isConfirmUrl) {
      // Give the page a moment to render the confirmation span
      await sleep(1500);

      // Check for <span>Comfirmation</span> (Experian's typo — intentionally kept as-is)
      const allSpans = Array.from(document.querySelectorAll("span"));
      const confirmSpan = allSpans.find(
        s => (s.textContent || "").trim().toLowerCase() === "comfirmation"
      );

      if (confirmSpan) {
        clog("PAGE3_SUCCESS", "Confirm URL + Comfirmation span both found — dispute submitted successfully ✅");
        sendResult(true, "SUCCESS: Dispute submitted. Confirmation page reached.");
      } else {
        // URL is right but span not found — still likely a success, log the warning
        clog("PAGE3_SUCCESS_NO_SPAN", "Confirm URL reached but 'Comfirmation' span not found — treating as success");
        sendResult(true, "SUCCESS: Confirmation URL reached (span not found — possible page variation).");
      }
      return;
    }

    // Confirm URL never came — fall back to body text analysis
    clog("PAGE3_URL_TIMEOUT", "Confirm URL did not appear within timeout — reading body text");

    const finalText = (document.body ? (document.body.innerText || document.body.textContent) : "").trim();
    
    // Check for blank white page
    if (finalText.length < 10) {
      clog("PAGE3_ERROR_BLANK", "Page is completely blank (white page) after timeout");
      sendResult(false, "ERROR: Timeout — The page loaded as a blank white page without any content.");
      return;
    }

    const formMsg2 = document.querySelector("#FormMsg2");
    const isError = hasErrorText(finalText) || !!formMsg2;

    if (isError) {
      const errorBlock = formMsg2
        ? (formMsg2.innerText || formMsg2.textContent)
        : extractErrorText(finalText);
      clog("PAGE3_ERROR", `Error: ${errorBlock.substring(0, 300)}`);
      sendResult(false, `ERROR: ${errorBlock.substring(0, 500)}`);
    } else {
      const confirmText = extractConfirmText(finalText);
      clog("PAGE3_SUCCESS_FALLBACK", `Body text success: ${confirmText.substring(0, 300)}`);
      sendResult(true, `SUCCESS: ${confirmText.substring(0, 500)}`);
    }
  }


  // ── Text extraction helpers ──────────────────────────────────────────────────

  function extractErrorText(fullText) {
    const errEl = document.querySelector("#FormMsg2, .error, .alert, .errorMessage, .error-message");
    if (errEl && (errEl.innerText || errEl.textContent)) {
      const errText = (errEl.innerText || errEl.textContent).trim();
      if (errText.length > 5) return errText;
    }
    const lines = fullText.split("\n").map(l => l.trim()).filter(Boolean);
    const hit = lines.find(l => ERROR_PATTERNS.some(p => l.toLowerCase().includes(p)));
    return hit || lines.slice(0, 5).join(" ") || fullText.substring(0, 300);
  }

  function extractConfirmText(fullText) {
    const lines = fullText.split("\n").map(l => l.trim()).filter(Boolean);
    const hit = lines.find(l =>
      l.toLowerCase().includes("confirmation") ||
      l.toLowerCase().includes("reference number") ||
      l.toLowerCase().includes("received") ||
      l.toLowerCase().includes("submitted") ||
      l.toLowerCase().includes("thank you")
    );
    return hit || lines.slice(0, 5).join(" ") || fullText.substring(0, 300);
  }

})();