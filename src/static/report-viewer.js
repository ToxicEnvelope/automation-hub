const viewerSub = document.getElementById("viewerSub");
const viewerError = document.getElementById("viewerError");
const reportFrame = document.getElementById("reportFrame");
const frameLoading = document.getElementById("frameLoading");
const openOriginalBtn = document.getElementById("openOriginalBtn");
const themeToggle = document.getElementById("themeToggle");

const metaRunId = document.getElementById("metaRunId");
const metaSuite = document.getElementById("metaSuite");
const metaEnv = document.getElementById("metaEnv");
const metaPlatform = document.getElementById("metaPlatform");
const metaStatus = document.getElementById("metaStatus");

const aiSub = document.getElementById("aiSub");
const cacheBadge = document.getElementById("cacheBadge");
const testSelectorWrap = document.getElementById("testSelectorWrap");
const testSelect = document.getElementById("testSelect");
const testSelectorHint = document.getElementById("testSelectorHint");
const aiLoading = document.getElementById("aiLoading");
const aiContent = document.getElementById("aiContent");
const aiError = document.getElementById("aiError");
const summaryTitle = document.getElementById("summaryTitle");
const summaryCategory = document.getElementById("summaryCategory");
const summaryConfidence = document.getElementById("summaryConfidence");
const shortSummary = document.getElementById("shortSummary");
const rootCause = document.getElementById("rootCause");
const suggestedFix = document.getElementById("suggestedFix");
const suggestedFixSteps = document.getElementById("suggestedFixSteps");
const historicalInsight = document.getElementById("historicalInsight");
const historyList = document.getElementById("historyList");
const evidenceList = document.getElementById("evidenceList");
const providerWarning = document.getElementById("providerWarning");
const regenerateBtn = document.getElementById("regenerateBtn");
const modelProbeBtn = document.getElementById("modelProbeBtn");
const modelProbeStatus = document.getElementById("modelProbeStatus");
const copyBtn = document.getElementById("copyBtn");
const feedbackPanel = document.getElementById("feedbackPanel");
const feedbackStateBadge = document.getElementById("feedbackStateBadge");
const feedbackCorrectionFields = document.getElementById("feedbackCorrectionFields");
const actualCauseInput = document.getElementById("actualCause");
const actualFixInput = document.getElementById("actualFix");
const feedbackNotesInput = document.getElementById("feedbackNotes");
const saveFeedbackBtn = document.getElementById("saveFeedbackBtn");
const feedbackSaveStatus = document.getElementById("feedbackSaveStatus");
const feedbackVerdictButtons = Array.from(document.querySelectorAll(".feedback-verdict"));

let currentReport = null;
let currentAgentPayload = null;
let availableTests = [];
let currentSelectedTest = null;
let selectedFeedbackVerdict = null;

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function paramsFromLocation() {
  const params = new URLSearchParams(window.location.search);
  return {
    suite: params.get("suite") || "",
    env: params.get("env") || "",
    platform: params.get("platform") || "",
    run_id: params.get("run_id") || "",
  };
}

function buildQuery(params) {
  const q = new URLSearchParams();
  q.set("suite", params.suite);
  q.set("env", params.env);
  q.set("platform", params.platform);
  q.set("run_id", params.run_id);
  return q.toString();
}

function setError(message) {
  viewerError.style.display = "block";
  viewerError.textContent = message;
}

function clearError() {
  viewerError.style.display = "none";
  viewerError.textContent = "";
}

function setAiLoading(value) {
  aiLoading.style.display = value ? "flex" : "none";
  regenerateBtn.disabled = value;
  modelProbeBtn.disabled = value;
  copyBtn.disabled = value;
  if (saveFeedbackBtn) saveFeedbackBtn.disabled = value || !selectedFeedbackVerdict || !currentAgentPayload?.memory?.memory_id;
}

function setAiError(message) {
  aiError.style.display = message ? "block" : "none";
  aiError.textContent = message || "";
}


function setFeedbackStatus(message, kind = '') {
  if (!feedbackSaveStatus) return;
  feedbackSaveStatus.textContent = message || '';
  feedbackSaveStatus.className = `feedback-save-status${kind ? ` ${kind}` : ''}`;
}

function feedbackStatusLabel(status) {
  if (status === 'verified') return 'Verified';
  if (status === 'corrected') return 'Human corrected';
  if (status === 'rejected') return 'Rejected';
  if (status === 'not_stored') return 'Not stored';
  return 'Not reviewed';
}

function selectFeedbackVerdict(verdict, { preserveStatus = false } = {}) {
  selectedFeedbackVerdict = verdict || null;
  feedbackVerdictButtons.forEach(button => {
    button.classList.toggle('selected', button.dataset.feedback === selectedFeedbackVerdict);
  });

  const needsCorrection = ['partially_correct', 'incorrect'].includes(selectedFeedbackVerdict);
  if (feedbackCorrectionFields) feedbackCorrectionFields.style.display = needsCorrection ? 'grid' : 'none';
  if (saveFeedbackBtn) {
    saveFeedbackBtn.disabled = !selectedFeedbackVerdict || !currentAgentPayload?.memory?.memory_id;
  }
  if (!preserveStatus) setFeedbackStatus('');
}

function renderFeedbackState(payload) {
  const memory = payload?.memory || {};
  const feedback = payload?.human_feedback || memory.human_feedback || null;
  const memoryStatus = payload?.memory_status || memory.memory_status || 'unreviewed';
  const canReview = Boolean(memory.memory_id);

  if (feedbackStateBadge) {
    feedbackStateBadge.textContent = feedbackStatusLabel(memoryStatus);
    feedbackStateBadge.className = `feedback-state-badge ${memoryStatus}`;
  }

  selectFeedbackVerdict(feedback?.verdict || null, { preserveStatus: true });
  if (actualCauseInput) actualCauseInput.value = feedback?.actual_cause || '';
  if (actualFixInput) actualFixInput.value = feedback?.actual_fix || '';
  if (feedbackNotesInput) feedbackNotesInput.value = feedback?.notes || '';

  feedbackVerdictButtons.forEach(button => { button.disabled = !canReview; });
  if (actualCauseInput) actualCauseInput.disabled = !canReview;
  if (actualFixInput) actualFixInput.disabled = !canReview;
  if (feedbackNotesInput) feedbackNotesInput.disabled = !canReview;
  if (saveFeedbackBtn) saveFeedbackBtn.disabled = !canReview || !selectedFeedbackVerdict;

  if (!canReview) {
    setFeedbackStatus('Feedback is unavailable because this analysis was not stored in AI memory.');
  } else if (feedback) {
    setFeedbackStatus(`Last review saved as ${feedbackStatusLabel(memoryStatus).toLowerCase()}.`, 'success');
  } else {
    setFeedbackStatus('');
  }
}

async function saveFeedback() {
  if (!currentAgentPayload?.memory?.memory_id || !selectedFeedbackVerdict) return;

  const actualCause = String(actualCauseInput?.value || '').trim();
  const actualFix = String(actualFixInput?.value || '').trim();
  const notes = String(feedbackNotesInput?.value || '').trim();

  if (selectedFeedbackVerdict === 'partially_correct' && !actualCause && !actualFix) {
    setFeedbackStatus('Provide the actual cause or actual fix for partially correct feedback.', 'error');
    return;
  }

  saveFeedbackBtn.disabled = true;
  setFeedbackStatus('Saving feedback...');

  try {
    const locator = paramsFromLocation();
    const response = await fetch('/api/ai/test-agent-feedback', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        ...locator,
        ...selectedTestRequestFields(),
        memory_id: currentAgentPayload.memory.memory_id,
        feedback: selectedFeedbackVerdict,
        actual_cause: actualCause || null,
        actual_fix: actualFix || null,
        notes: notes || null,
        provider: currentAgentPayload.provider || null,
        inference_id: currentAgentPayload.inference?.inference_id || null,
        evidence_hash: currentAgentPayload.evidence_hash || null,
      }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || !payload.ok) {
      throw new Error(payload.error || `Failed saving feedback: HTTP ${response.status}`);
    }

    currentAgentPayload.memory = {
      ...currentAgentPayload.memory,
      memory_status: payload.memory_status,
      retrieval_enabled: payload.retrieval_enabled,
      human_feedback: payload.human_feedback,
      effective_failure_cause: payload.effective_failure_cause,
      effective_suggested_fix: payload.effective_suggested_fix,
    };
    currentAgentPayload.memory_status = payload.memory_status;
    currentAgentPayload.retrieval_enabled = payload.retrieval_enabled;
    currentAgentPayload.human_feedback = payload.human_feedback;
    renderFeedbackState(currentAgentPayload);
    setFeedbackStatus('Feedback saved and AI memory updated.', 'success');
  } catch (error) {
    console.error(error);
    setFeedbackStatus(error.message || 'Failed saving feedback.', 'error');
  } finally {
    saveFeedbackBtn.disabled = !selectedFeedbackVerdict || !currentAgentPayload?.memory?.memory_id;
  }
}

function normalizeLabel(value) {
  return String(value || "unknown").replaceAll("_", " ");
}

function testDisplayName(test) {
  return test?.name || test?.full_name || test?.test_id || "Unknown failed test";
}

function testMessagePreview(test) {
  const message = String(test?.message || "").replace(/\s+/g, " ").trim();
  if (!message) return "";
  return message.length > 140 ? `${message.slice(0, 140)}…` : message;
}

function selectedTestRequestFields() {
  if (!currentSelectedTest) return {};
  return {
    test_id: currentSelectedTest.test_id || "",
    test_blob: currentSelectedTest.test_blob || "",
    test_name: currentSelectedTest.name || currentSelectedTest.full_name || "",
  };
}

function renderTestSelector(tests) {
  availableTests = Array.isArray(tests) ? tests : [];

  if (!availableTests.length) {
    currentSelectedTest = null;
    testSelectorWrap.style.display = "none";
    return;
  }

  const params = new URLSearchParams(window.location.search);
  const requestedTestId = params.get("test_id") || "";
  let selectedIndex = 0;
  if (requestedTestId) {
    const found = availableTests.findIndex(t => String(t.test_id || "") === requestedTestId);
    if (found >= 0) selectedIndex = found;
  }

  currentSelectedTest = availableTests[selectedIndex];
  testSelect.innerHTML = availableTests.map((test, index) => {
    const label = testDisplayName(test);
    const status = test.status || "failed";
    return `<option value="${index}">${escapeHtml(status)} · ${escapeHtml(label)}</option>`;
  }).join("");
  testSelect.value = String(selectedIndex);
  testSelectorWrap.style.display = "block";
  testSelectorHint.textContent = `Analyzing 1 selected test out of ${availableTests.length} failed/broken test case${availableTests.length === 1 ? "" : "s"}.`;
}

function updateSelectedTest(index) {
  const next = availableTests[Number(index)];
  if (!next) return;
  currentSelectedTest = next;
  testSelectorHint.textContent = testMessagePreview(next) || "The AI Failure Agent analysis is scoped to this selected test case.";
}

function renderReportMeta(report) {
  currentReport = report;
  const runId = report.run_id || "unknown";
  metaRunId.textContent = runId;
  metaRunId.title = runId;
  metaSuite.textContent = report.suite || "unknown";
  metaEnv.textContent = report.env || "unknown";
  metaPlatform.textContent = report.platform || "unknown";
  metaStatus.textContent = report.status || "unknown";
  viewerSub.textContent = `${runId} · ${report.suite || "unknown"} · ${report.env || "unknown"} · ${report.platform || "unknown"}`;

  if (report.report_url) {
    reportFrame.src = report.report_url;
    openOriginalBtn.href = report.report_url;
    openOriginalBtn.removeAttribute("aria-disabled");
    frameLoading.style.display = "flex";
  }
}

function evidenceFromPayload(payload) {
  const agentEvidence = payload?.agent?.evidence;
  if (Array.isArray(agentEvidence) && agentEvidence.length) return agentEvidence;
  const summaryEvidence = payload?.summary?.evidence;
  if (Array.isArray(summaryEvidence) && summaryEvidence.length) return summaryEvidence;
  const extractedFailures = payload?.evidence?.failures;
  if (Array.isArray(extractedFailures)) return extractedFailures;
  return [];
}

function renderEvidence(items) {
  if (!items.length) {
    evidenceList.innerHTML = `<div class="evidence-card"><div class="evidence-message">No failed or broken test-case evidence was found in the report artifacts.</div></div>`;
    return;
  }

  evidenceList.innerHTML = items.slice(0, 8).map((item, index) => {
    const test = item.test || item.name || item.full_name || item.type || `Evidence ${index + 1}`;
    const status = item.status || "evidence";
    const message = item.value || item.message || item.trace_excerpt || item.reason || "No message available.";
    const attachments = Array.isArray(item.attachments) ? item.attachments : [];
    const links = attachments
      .filter(a => a && a.url)
      .slice(0, 4)
      .map((a, i) => `<a href="${escapeHtml(a.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(a.name || `attachment ${i + 1}`)}</a>`)
      .join("");

    return `<div class="evidence-card">
      <div class="evidence-card-title">
        <div class="evidence-test-name">${escapeHtml(test)}</div>
        <span class="evidence-status">${escapeHtml(status)}</span>
      </div>
      <div class="evidence-message">${escapeHtml(message)}</div>
      ${links ? `<div class="evidence-links">${links}</div>` : ""}
    </div>`;
  }).join("");
}

function renderFixSteps(steps) {
  const cleanSteps = Array.isArray(steps) ? steps.filter(Boolean).slice(0, 8) : [];
  if (!cleanSteps.length) {
    suggestedFixSteps.innerHTML = "";
    return;
  }
  suggestedFixSteps.innerHTML = cleanSteps.map(step => `<li>${escapeHtml(step)}</li>`).join("");
}

function renderHistory(agent) {
  const historical = agent?.historical_insight || {};
  historicalInsight.textContent = historical.summary || "No similar previous failures were found.";
  const matches = Array.isArray(historical.matches) ? historical.matches.slice(0, 5) : [];
  if (!matches.length) {
    historyList.innerHTML = "";
    return;
  }
  historyList.innerHTML = matches.map(match => {
    const title = match.test_name || match.run_id || "Previous failure";
    const meta = [match.suite, match.env, match.platform, match.run_id].filter(Boolean).join(" · ");
    const cause = match.failure_cause || "No previous cause recorded.";
    const fix = match.suggested_fix || "No previous fix recorded.";
    const score = typeof match.score === "number" ? `${Math.round(match.score * 100)}% similar` : "similar";
    const reviewStatus = match.memory_status || "unreviewed";
    const reviewLabel = reviewStatus === "corrected" ? "Human corrected" : reviewStatus === "verified" ? "Human verified" : "AI generated";
    return `<div class="history-card">
      <div class="history-title-row">
        <div class="history-title">${escapeHtml(title)}</div>
        <span class="history-score">${escapeHtml(score)}</span>
      </div>
      <span class="history-review-status ${escapeHtml(reviewStatus)}">${escapeHtml(reviewLabel)}</span>
      <div class="history-meta">${escapeHtml(meta)}</div>
      <div class="history-body"><strong>Cause:</strong> ${escapeHtml(cause)}</div>
      <div class="history-body"><strong>Fix:</strong> ${escapeHtml(fix)}</div>
    </div>`;
  }).join("");
}

function renderAgent(payload) {
  currentAgentPayload = payload;
  const agent = payload.agent || {};
  const cause = agent.failure_cause || {};
  const fix = agent.suggested_fix || {};
  aiContent.style.display = "block";
  setAiError("");

  cacheBadge.style.display = "inline-flex";
  cacheBadge.textContent = payload.cached ? "cached" : "new";
  const selected = payload.selected_test || payload.evidence?.selected_test || currentSelectedTest;
  const provider = payload.provider || "unknown";
  const inference = payload.inference || {};
  const modelName = payload.model?.model_name || "unknown model";
  const source = payload.actual_model_response ? "verified model output" : (payload.fallback_used ? "heuristic fallback" : (inference.source || "unknown"));
  const latency = typeof inference.elapsed_ms === "number" ? ` · ${Math.round(inference.elapsed_ms)} ms` : "";
  const selectedPrefix = selected ? `Selected test: ${testDisplayName(selected)} · ` : "";
  aiSub.textContent = `${selectedPrefix}Provider: ${provider} · Source: ${source} · Model: ${modelName}${latency}`;

  summaryTitle.textContent = agent.title || "AI failure analysis";
  summaryCategory.textContent = normalizeLabel(cause.category);
  summaryConfidence.textContent = cause.confidence || "unknown";
  shortSummary.textContent = cause.summary || "No failure cause returned.";
  rootCause.textContent = cause.summary || "No evidence-based cause returned.";
  suggestedFix.textContent = fix.summary || "No suggested fix returned.";
  renderFixSteps(fix.steps || []);
  renderHistory(agent);
  renderEvidence(evidenceFromPayload(payload));
  renderFeedbackState(payload);

  if (payload.provider_warning || payload.cache_warning) {
    providerWarning.style.display = "block";
    providerWarning.textContent = payload.provider_warning || payload.cache_warning;
  } else {
    providerWarning.style.display = "none";
    providerWarning.textContent = "";
  }
}

async function loadContext() {
  clearError();
  const locator = paramsFromLocation();
  if (!locator.suite || !locator.env || !locator.platform || !locator.run_id) {
    throw new Error("Missing suite, env, platform, or run_id in the report viewer URL.");
  }

  const res = await fetch(`/api/report-context?${buildQuery(locator)}`);
  const payload = await res.json().catch(() => ({}));
  if (!res.ok || !payload.ok) {
    throw new Error(payload.error || `Failed loading report context: HTTP ${res.status}`);
  }
  renderReportMeta(payload.report);
  return payload.report;
}

async function loadTests() {
  const locator = paramsFromLocation();
  const res = await fetch(`/api/report-tests?${buildQuery(locator)}`);
  const payload = await res.json().catch(() => ({}));
  if (!res.ok || !payload.ok) {
    throw new Error(payload.error || `Failed loading failed tests: HTTP ${res.status}`);
  }
  renderTestSelector(payload.tests || []);
  return payload.tests || [];
}

async function loadAgentAnalysis(refresh = false) {
  const locator = paramsFromLocation();
  setAiLoading(true);
  setAiError("");
  try {
    const res = await fetch("/api/ai/test-agent-analysis", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ...locator,
        ...selectedTestRequestFields(),
        refresh,
      }),
    });
    const payload = await res.json().catch(() => ({}));
    if (!res.ok || !payload.ok) {
      throw new Error(payload.error || `Failed generating AI Failure Agent analysis: HTTP ${res.status}`);
    }
    renderAgent(payload);
  } catch (err) {
    console.error(err);
    aiContent.style.display = "none";
    setAiError(err.message || "Failed generating AI Failure Agent analysis.");
  } finally {
    setAiLoading(false);
  }
}


async function runModelProbe() {
  modelProbeBtn.disabled = true;
  modelProbeStatus.style.display = "block";
  modelProbeStatus.className = "ai-probe-status";
  modelProbeStatus.textContent = "Invoking the bundled GGUF model with a verification token...";

  try {
    const res = await fetch("/api/ai/provider-probe", { method: "POST" });
    const payload = await res.json().catch(() => ({}));
    const inference = payload.inference || {};
    const hash = inference.response_sha256 ? inference.response_sha256.slice(0, 16) : "none";
    const elapsed = typeof inference.elapsed_ms === "number" ? `${Math.round(inference.elapsed_ms)} ms` : "unknown time";

    if (!res.ok || !payload.verified) {
      throw new Error(payload.error || `Model verification failed: HTTP ${res.status}`);
    }

    modelProbeStatus.classList.add("success");
    modelProbeStatus.textContent = `Verified model inference. The GGUF returned the exact nonce in ${elapsed}; response SHA-256 starts with ${hash}.`;
  } catch (err) {
    console.error(err);
    modelProbeStatus.classList.add("failure");
    modelProbeStatus.textContent = err.message || "Model verification failed.";
  } finally {
    modelProbeBtn.disabled = false;
  }
}

async function copyAnalysis() {
  if (!currentAgentPayload?.agent) return;
  const agent = currentAgentPayload.agent;
  const cause = agent.failure_cause || {};
  const fix = agent.suggested_fix || {};
  const historical = agent.historical_insight || {};
  const report = currentAgentPayload.report || currentReport || {};
  const steps = Array.isArray(fix.steps) ? fix.steps.map((s, i) => `${i + 1}. ${s}`) : [];
  const text = [
    `AutomationHub AI Failure Agent`,
    `Run: ${report.run_id || "unknown"}`,
    `Suite/Env/Platform: ${report.suite || "unknown"}/${report.env || "unknown"}/${report.platform || "unknown"}`,
    currentSelectedTest ? `Selected test: ${testDisplayName(currentSelectedTest)}` : null,
    `Provider: ${currentAgentPayload.provider || "unknown"}`,
    `Category: ${normalizeLabel(cause.category)}`,
    `Confidence: ${cause.confidence || "unknown"}`,
    ``,
    `Failure cause:`,
    cause.summary || "—",
    ``,
    `Suggested fix:`,
    fix.summary || "—",
    steps.length ? steps.join("\n") : null,
    ``,
    `Historical insight:`,
    historical.summary || "—",
  ].filter(Boolean).join("\n");

  try {
    await navigator.clipboard.writeText(text);
    copyBtn.textContent = "Copied";
    setTimeout(() => { copyBtn.textContent = "Copy analysis"; }, 1400);
  } catch {
    const ta = document.createElement("textarea");
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    document.body.removeChild(ta);
    copyBtn.textContent = "Copied";
    setTimeout(() => { copyBtn.textContent = "Copy analysis"; }, 1400);
  }
}

function initTheme() {
  const saved = localStorage.getItem("ah-theme");
  if (saved === "dark") document.documentElement.classList.add("dark");
  if (!themeToggle) return;
  themeToggle.addEventListener("click", () => {
    document.documentElement.classList.toggle("dark");
    const dark = document.documentElement.classList.contains("dark");
    localStorage.setItem("ah-theme", dark ? "dark" : "light");
  });
}

reportFrame.addEventListener("load", () => {
  frameLoading.style.display = "none";
});

regenerateBtn.addEventListener("click", () => loadAgentAnalysis(true));
modelProbeBtn.addEventListener("click", runModelProbe);
copyBtn.addEventListener("click", copyAnalysis);
saveFeedbackBtn?.addEventListener("click", saveFeedback);
feedbackVerdictButtons.forEach(button => {
  button.addEventListener("click", () => selectFeedbackVerdict(button.dataset.feedback || null));
});
testSelect.addEventListener("change", () => {
  updateSelectedTest(testSelect.value);
  currentAgentPayload = null;
  selectFeedbackVerdict(null);
  renderFeedbackState({ memory_status: "not_stored", memory: {} });
  loadAgentAnalysis(false);
});

initTheme();

loadContext()
  .then(() => loadTests())
  .then(() => loadAgentAnalysis(false))
  .catch((err) => {
    console.error(err);
    setError(err.message || "Failed loading report viewer.");
    setAiLoading(false);
    setAiError("AI Failure Agent unavailable because the report context failed to load.");
  });
