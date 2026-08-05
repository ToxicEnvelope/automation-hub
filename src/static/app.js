// ─── Elements ───
const suiteFilter    = document.getElementById("suiteFilter");
const envFilter      = document.getElementById("envFilter");
const platformFilter = document.getElementById("platformFilter");
const qInput         = document.getElementById("qInput");
const rows           = document.getElementById("rows");
const refreshBtn     = document.getElementById("refreshBtn");
const pagination     = document.getElementById("pagination");
const countLbl       = document.getElementById("countLbl");
const lastUpdated    = document.getElementById("lastUpdated");
const themeToggle    = document.getElementById("themeToggle");
const tableCard      = document.getElementById("tableCard");
const clearFiltersBtn = document.getElementById("clearFiltersBtn");
const statsSection = document.getElementById("statsSection");
const statsDrawerToggle = document.getElementById("statsDrawerToggle");
const statsDrawerLabel = document.getElementById("statsDrawerLabel");

const statTotal = document.getElementById("statTotal");
const statTotalSub = document.getElementById("statTotalSub");
const statPassRate = document.getElementById("statPassRate");
const statPassRateSub = document.getElementById("statPassRateSub");
const statRunning = document.getElementById("statRunning");
const statAvgDuration = document.getElementById("statAvgDuration");
const testOutcomeMeta = document.getElementById("testOutcomeMeta");
const topFailedTestsMeta = document.getElementById("topFailedTestsMeta");
const testOutcomeCard = document.getElementById("testOutcomeCard");
const topFailedTestsCard = document.getElementById("topFailedTestsCard");

let isLoading = false;
let allRuns = [];
let currentPage = 1;
let sortCol = null;
let sortDir = -1;
let suiteFilterValue = "";
let envFilterValue = "";
let platformFilterValue = "";
const ITEMS_PER_PAGE = 25;
let chartStatus, chartSuite, chartEnv, chartPlatform, chartTestStatus, chartTopFailedTests;
let currentTestStatistics = null;

// ─── Floating AI failure-chat elements/state ───
const aiChatLauncher = document.getElementById("aiChatLauncher");
const aiChatUnread = document.getElementById("aiChatUnread");
const aiChatPanel = document.getElementById("aiChatPanel");
const aiChatClose = document.getElementById("aiChatClose");
const aiChatProviderState = document.getElementById("aiChatProviderState");
const aiChatRunSelect = document.getElementById("aiChatRunSelect");
const aiChatTestSelect = document.getElementById("aiChatTestSelect");
const aiChatScopeStatus = document.getElementById("aiChatScopeStatus");
const aiChatMessages = document.getElementById("aiChatMessages");
const aiChatQuickActions = document.getElementById("aiChatQuickActions");
const aiChatOpenReport = document.getElementById("aiChatOpenReport");
const aiChatForm = document.getElementById("aiChatForm");
const aiChatInput = document.getElementById("aiChatInput");
const aiChatSend = document.getElementById("aiChatSend");

const AI_CHAT_OPEN_KEY = "ah-ai-chat-open";
let aiChatFailedRuns = [];
let aiChatTests = [];
let aiChatConversationId = null;
let aiChatHistory = [];
let aiChatSending = false;

// ─── Stats drawer ───
const STATS_DRAWER_KEY = "ah-stats-collapsed";

function setStatsDrawerCollapsed(collapsed) {
  if (!statsSection || !statsDrawerToggle) return;
  statsSection.classList.toggle("is-collapsed", collapsed);
  statsDrawerToggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
  if (statsDrawerLabel) statsDrawerLabel.textContent = collapsed ? "Expand" : "Collapse";
  localStorage.setItem(STATS_DRAWER_KEY, collapsed ? "1" : "0");
}

function initStatsDrawer() {
  if (!statsDrawerToggle) return;
  const savedCollapsed = localStorage.getItem(STATS_DRAWER_KEY) === "1";
  setStatsDrawerCollapsed(savedCollapsed);
  statsDrawerToggle.addEventListener("click", () => {
    const collapsed = statsSection?.classList.contains("is-collapsed") || false;
    setStatsDrawerCollapsed(!collapsed);

    // When reopening, force Chart.js to recalculate dimensions after the drawer animation.
    if (collapsed) {
      setTimeout(() => {
        [
          chartStatus,
          chartSuite,
          chartEnv,
          chartPlatform,
          chartTestStatus,
          chartTopFailedTests,
        ].forEach(chart => chart?.resize?.());
      }, 320);
    }
  });
}

// ─── Loading States ───
function showStatsLoading(useOverlay = false) {
  document.querySelectorAll('.stat-card').forEach(card => {
    if (useOverlay) {
      card.querySelector('.loading-overlay')?.classList.add('active');
    } else {
      card.classList.add('loading');
      if (!card.querySelector('.skeleton-value')) {
        card.insertAdjacentHTML('beforeend', `
          <div class="skeleton skeleton-title"></div>
          <div class="skeleton skeleton-value"></div>
          <div class="skeleton skeleton-subtitle"></div>
        `);
      }
    }
  });
}

function hideStatsLoading() {
  document.querySelectorAll('.stat-card').forEach(card => {
    card.classList.remove('loading');
    card.querySelectorAll('.skeleton').forEach(s => s.remove());
    card.querySelector('.loading-overlay')?.classList.remove('active');
  });
}

function showChartsLoading(useOverlay = false) {
  document.querySelectorAll('.chart-card').forEach(card => {
    if (useOverlay) {
      card.querySelector('.loading-overlay')?.classList.add('active');
    } else {
      card.classList.add('loading');
      const wrap = card.querySelector('.chart-wrap');
      if (wrap && !wrap.querySelector('.skeleton-chart')) {
        wrap.insertAdjacentHTML('beforeend', '<div class="skeleton skeleton-chart"></div>');
      }
    }
  });
}

function hideChartsLoading() {
  document.querySelectorAll('.chart-card').forEach(card => {
    card.classList.remove('loading');
    card.querySelectorAll('.skeleton').forEach(s => s.remove());
    card.querySelector('.loading-overlay')?.classList.remove('active');
  });
}

function showTableInitialLoading() {
  rows.innerHTML = `
    <tr>
      <td colspan="10" style="text-align:center; padding:60px 20px;">
        <div class="spinner" style="margin:0 auto 16px;"></div>
        <div style="color:var(--text-secondary); font-size:14px;">Loading test runs...</div>
      </td>
    </tr>
  `;
}

function setTableRefreshing(v) {
  tableCard?.classList.toggle("is-loading", !!v);
}

// ─── Charts ───
function getChartColors() {
  const isDark = document.documentElement.classList.contains("dark");
  return {
    passed: isDark ? "#86efac" : "#16a34a",
    failed: isDark ? "#fca5a5" : "#dc2626",
    error: isDark ? "#fdba74" : "#ea580c",
    running: isDark ? "#93c5fd" : "#3b82f6",
    other: isDark ? "#fde047" : "#eab308",
    text: isDark ? "#e5e7eb" : "#4b5563"
  };
}

function initCharts() {
  if (typeof Chart === "undefined") return;
  const colors = getChartColors();
  const commonOptions = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: {
        position: 'bottom',
        labels: { color: colors.text, padding: 12, font: { size: 11 } },
      },
    },
  };

  const c1 = document.getElementById('chartStatus');
  const c2 = document.getElementById('chartSuite');
  const c3 = document.getElementById('chartEnv');
  const c4 = document.getElementById('chartPlatform');
  const c5 = document.getElementById('chartTestStatus');
  const c6 = document.getElementById('chartTopFailedTests');

  if (c1) {
    chartStatus = new Chart(c1, {
      type: 'doughnut',
      data: { labels: [], datasets: [{ data: [], backgroundColor: [], borderWidth: 0 }] },
      options: commonOptions,
    });
  }
  if (c2) {
    chartSuite = new Chart(c2, {
      type: 'doughnut',
      data: { labels: [], datasets: [{ data: [], backgroundColor: ['#4f46e5', '#06b6d4', '#10b981'], borderWidth: 0 }] },
      options: commonOptions,
    });
  }
  if (c3) {
    chartEnv = new Chart(c3, {
      type: 'doughnut',
      data: { labels: [], datasets: [{ data: [], backgroundColor: ['#10b981', '#f59e0b', '#ef4444'], borderWidth: 0 }] },
      options: commonOptions,
    });
  }
  if (c4) {
    chartPlatform = new Chart(c4, {
      type: 'doughnut',
      data: { labels: [], datasets: [{ data: [], backgroundColor: ['#3b82f6', '#ec4899', '#a855f7'], borderWidth: 0 }] },
      options: commonOptions,
    });
  }
  if (c5) {
    chartTestStatus = new Chart(c5, {
      type: 'bar',
      data: {
        labels: [],
        datasets: [
          { label: 'Passed', data: [], backgroundColor: colors.passed, borderWidth: 0, borderRadius: 5 },
          { label: 'Failed', data: [], backgroundColor: colors.failed, borderWidth: 0, borderRadius: 5 },
          { label: 'Error', data: [], backgroundColor: colors.error, borderWidth: 0, borderRadius: 5 },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        indexAxis: 'y',
        plugins: {
          legend: {
            position: 'bottom',
            labels: { color: colors.text, padding: 12, font: { size: 11 } },
          },
        },
        scales: {
          x: {
            beginAtZero: true,
            stacked: true,
            ticks: { color: colors.text, precision: 0 },
            grid: { color: 'rgba(148, 163, 184, 0.16)' },
            title: { display: true, text: 'Occurrences', color: colors.text },
          },
          y: {
            stacked: true,
            ticks: {
              color: colors.text,
              autoSkip: false,
              callback(value) {
                const label = this.getLabelForValue(value);
                return label.length > 52 ? `${label.slice(0, 49)}…` : label;
              },
            },
            grid: { display: false },
          },
        },
      },
    });
  }
  if (c6) {
    chartTopFailedTests = new Chart(c6, {
      type: 'bar',
      data: {
        labels: [],
        datasets: [
          { label: 'Failed', data: [], backgroundColor: colors.failed, borderWidth: 0, borderRadius: 5 },
          { label: 'Error', data: [], backgroundColor: colors.error, borderWidth: 0, borderRadius: 5 },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        indexAxis: 'y',
        plugins: {
          legend: {
            position: 'bottom',
            labels: { color: colors.text, padding: 12, font: { size: 11 } },
          },
          tooltip: {
            callbacks: {
              title(items) {
                return items?.[0]?.label || '';
              },
            },
          },
        },
        scales: {
          x: {
            beginAtZero: true,
            stacked: true,
            ticks: { color: colors.text, precision: 0 },
            grid: { color: 'rgba(148, 163, 184, 0.16)' },
            title: { display: true, text: 'Occurrences', color: colors.text },
          },
          y: {
            stacked: true,
            ticks: {
              color: colors.text,
              autoSkip: false,
              callback(value) {
                const label = this.getLabelForValue(value);
                return label.length > 52 ? `${label.slice(0, 49)}…` : label;
              },
            },
            grid: { display: false },
          },
        },
      },
    });
  }
}

function updateChart(chart, counts, colorMap) {
  if (!chart) return;
  const entries = Object.entries(counts).sort((a, b) => b[1] - a[1]);
  chart.data.labels = entries.map(([k, v]) => `${k} (${v})`);
  chart.data.datasets[0].data = entries.map(([, v]) => v);
  if (colorMap) chart.data.datasets[0].backgroundColor = entries.map(([k]) => colorMap[k] || colorMap.unknown);
  chart.update('none');
}


function setTestStatisticsLoading(value) {
  [testOutcomeCard, topFailedTestsCard].forEach(card => {
    if (!card) return;
    card.querySelector('.loading-overlay')?.classList.toggle('active', value);
  });
}

function updateTestStatisticsCharts(payload) {
  currentTestStatistics = payload || null;
  const counts = payload?.status_counts || {};
  const outcomes = Array.isArray(payload?.test_outcomes_by_name) ? payload.test_outcomes_by_name : [];
  const topFailed = Array.isArray(payload?.top_failed_tests) ? payload.top_failed_tests : [];
  const meta = payload?.meta || {};
  const colors = getChartColors();

  const passed = Number(counts.passed || 0);
  const failed = Number(counts.failed || 0);
  const error = Number(counts.error || 0);
  const totalVisible = passed + failed + error;

  testOutcomeCard?.classList.toggle('is-empty', outcomes.length === 0);
  topFailedTestsCard?.classList.toggle('is-empty', topFailed.length === 0);

  if (chartTestStatus) {
    chartTestStatus.data.labels = outcomes.map(item => item.test_name || 'Unknown test');
    chartTestStatus.data.datasets[0].data = outcomes.map(item => Number(item.passed || 0));
    chartTestStatus.data.datasets[0].backgroundColor = colors.passed;
    chartTestStatus.data.datasets[1].data = outcomes.map(item => Number(item.failed || 0));
    chartTestStatus.data.datasets[1].backgroundColor = colors.failed;
    chartTestStatus.data.datasets[2].data = outcomes.map(item => Number(item.error || 0));
    chartTestStatus.data.datasets[2].backgroundColor = colors.error;
    chartTestStatus.options.plugins.legend.labels.color = colors.text;
    chartTestStatus.options.scales.x.ticks.color = colors.text;
    chartTestStatus.options.scales.x.title.color = colors.text;
    chartTestStatus.options.scales.y.ticks.color = colors.text;
    chartTestStatus.update('none');
  }

  if (chartTopFailedTests) {
    chartTopFailedTests.data.labels = topFailed.map(item => item.test_name || 'Unknown test');
    chartTopFailedTests.data.datasets[0].data = topFailed.map(item => Number(item.failed || 0));
    chartTopFailedTests.data.datasets[0].backgroundColor = colors.failed;
    chartTopFailedTests.data.datasets[1].data = topFailed.map(item => Number(item.error || 0));
    chartTopFailedTests.data.datasets[1].backgroundColor = colors.error;
    chartTopFailedTests.options.plugins.legend.labels.color = colors.text;
    chartTopFailedTests.options.scales.x.ticks.color = colors.text;
    chartTopFailedTests.options.scales.x.title.color = colors.text;
    chartTopFailedTests.options.scales.y.ticks.color = colors.text;
    chartTopFailedTests.update('none');
  }

  const testCount = Number(meta.test_cases_scanned || 0);
  const runCount = Number(meta.runs_with_test_data || 0);
  const truncated = meta.truncated ? ' · bounded scan' : '';
  if (testOutcomeMeta) {
    testOutcomeMeta.textContent = testCount > 0
      ? `${testCount.toLocaleString()} cases · ${passed.toLocaleString()} passed · ${failed.toLocaleString()} failed · ${error.toLocaleString()} errors · ${runCount.toLocaleString()} run${runCount === 1 ? '' : 's'}${truncated}`
      : 'No test-level Allure data was found in the loaded runs';
  }
  if (topFailedTestsMeta) {
    topFailedTestsMeta.textContent = topFailed.length
      ? `Ranked by failed + error occurrences across ${runCount.toLocaleString()} run${runCount === 1 ? '' : 's'}`
      : 'No failed or error test cases were found';
  }
}

async function loadTestStatistics() {
  const completedRuns = allRuns
    .filter(run => String(run.status || '').toLowerCase() !== 'running')
    .slice(0, 100)
    .map(run => ({
      suite: run.suite,
      env: run.env,
      platform: run.platform,
      run_id: run.run_id,
    }));

  if (!completedRuns.length) {
    updateTestStatisticsCharts({ status_counts: {}, test_outcomes_by_name: [], top_failed_tests: [], meta: {} });
    return;
  }

  setTestStatisticsLoading(true);
  try {
    const response = await fetch('/api/test-statistics', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        runs: completedRuns,
        max_runs: 100,
        max_test_cases: 5000,
      }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || !payload.ok) {
      throw new Error(payload.error || `Failed loading test statistics: HTTP ${response.status}`);
    }
    updateTestStatisticsCharts(payload);
  } catch (error) {
    console.error('Test statistics error:', error);
    updateTestStatisticsCharts({ status_counts: {}, test_outcomes_by_name: [], top_failed_tests: [], meta: {} });
    if (testOutcomeMeta) testOutcomeMeta.textContent = 'Test-level statistics could not be loaded';
    if (topFailedTestsMeta) topFailedTestsMeta.textContent = 'Top failed tests could not be loaded';
  } finally {
    setTestStatisticsLoading(false);
  }
}

function updateStatistics() {
  const total = allRuns.length;
  if (total === 0) {
    statTotal.textContent = "0";
    statTotalSub.textContent = "No data";
    statPassRate.textContent = "0%";
    statPassRateSub.textContent = "0 passed / 0 total";
    statRunning.textContent = "0";
    statAvgDuration.textContent = "--";
    return;
  }

  const statusCounts = {};
  const suiteCounts = {};
  const envCounts = {};
  const platformCounts = {};
  let passedCount = 0;
  let runningCount = 0;
  let totalDuration = 0;
  let completedCount = 0;

  allRuns.forEach(r => {
    const status = String(r.status || "unknown").toLowerCase();
    statusCounts[status] = (statusCounts[status] || 0) + 1;
    suiteCounts[r.suite || "unknown"] = (suiteCounts[r.suite || "unknown"] || 0) + 1;
    envCounts[r.env || "unknown"] = (envCounts[r.env || "unknown"] || 0) + 1;
    platformCounts[r.platform || "unknown"] = (platformCounts[r.platform || "unknown"] || 0) + 1;
    if (status === "passed") passedCount++;
    if (status === "running") runningCount++;
    if (status !== "running") {
      const ms = durMs(r);
      if (Number.isFinite(ms) && ms > 0) {
        totalDuration += ms;
        completedCount++;
      }
    }
  });

  statTotal.textContent = total.toLocaleString();
  statTotalSub.textContent = "Loaded from last 12 hours";
  const passRate = Math.round((passedCount / total) * 100);
  statPassRate.textContent = `${passRate}%`;
  statPassRateSub.textContent = `${passedCount} passed / ${total} total`;
  statRunning.textContent = runningCount.toLocaleString();
  statAvgDuration.textContent = completedCount > 0 ? formatDuration(totalDuration / completedCount) : "--";

  const colors = getChartColors();
  updateChart(chartStatus, statusCounts, { passed: colors.passed, failed: colors.failed, running: colors.running, unknown: colors.other });
  updateChart(chartSuite, suiteCounts, null);
  updateChart(chartEnv, envCounts, null);
  updateChart(chartPlatform, platformCounts, null);
}

// ─── Theme ───
(function initTheme() {
  const saved = localStorage.getItem("ah-theme");
  if (saved === "dark") document.documentElement.classList.add("dark");
})();

themeToggle.addEventListener("click", () => {
  document.documentElement.classList.toggle("dark");
  const dark = document.documentElement.classList.contains("dark");
  localStorage.setItem("ah-theme", dark ? "dark" : "light");
  if (chartStatus) updateStatistics();
  if (currentTestStatistics) updateTestStatisticsCharts(currentTestStatistics);
});

// ─── Filter chips ───
function bindChipGroup(groupEl, onChange) {
  if (!groupEl) return;
  groupEl.addEventListener("click", (e) => {
    const btn = e.target.closest(".filter-chip");
    if (!btn) return;
    groupEl.querySelectorAll(".filter-chip").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    onChange(btn.dataset.value || "");
  });
}

function resetChipGroup(groupEl) {
  if (!groupEl) return;
  groupEl.querySelectorAll(".filter-chip").forEach(b => {
    b.classList.toggle("active", (b.dataset.value || "") === "");
  });
}

bindChipGroup(suiteFilter, (value) => {
  suiteFilterValue = value;
  currentPage = 1;
  load({ preserveExisting: true, refresh: true });
});

bindChipGroup(envFilter, (value) => {
  envFilterValue = value;
  currentPage = 1;
  load({ preserveExisting: true, refresh: true });
});

bindChipGroup(platformFilter, (value) => {
  platformFilterValue = value;
  currentPage = 1;
  load({ preserveExisting: true, refresh: true });
});

// ─── SVG icons for table cells ───
function iconSuite() {
  return `<svg class="cell-icon" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M8 2 2.5 5 8 8l5.5-3L8 2Z" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/><path d="M2.5 8 8 11l5.5-3" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/><path d="M2.5 11 8 14l5.5-3" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
}
function iconEnvironment() {
  return `<svg class="cell-icon" viewBox="0 0 16 16" fill="none" aria-hidden="true"><rect x="2.5" y="3" width="11" height="4" rx="1.2" stroke="currentColor" stroke-width="1.5"/><rect x="2.5" y="9" width="11" height="4" rx="1.2" stroke="currentColor" stroke-width="1.5"/><path d="M5 5h.01M5 11h.01M7.5 5h3.5M7.5 11h3.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>`;
}
function iconPlatform() {
  return `<svg class="cell-icon" viewBox="0 0 16 16" fill="none" aria-hidden="true"><rect x="2.5" y="3" width="11" height="8" rx="1.4" stroke="currentColor" stroke-width="1.5"/><path d="M6.5 13h3M8 11v2" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>`;
}
function iconVersion() {
  return `<svg class="cell-icon" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M3 3.5h5.5L13 8l-5 5-5-5V3.5Z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/><circle cx="6" cy="6" r="1" fill="currentColor"/></svg>`;
}
function iconBuild() {
  return `<svg class="cell-icon" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M6 2.5 4.8 13.5M11.2 2.5 10 13.5M3 6h10M2.5 10h10" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>`;
}
function iconReport() {
  return `<svg class="cell-icon" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M6.5 4H4.2A1.2 1.2 0 0 0 3 5.2v6.6A1.2 1.2 0 0 0 4.2 13h6.6a1.2 1.2 0 0 0 1.2-1.2V9.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/><path d="M9 3h4v4M8 8l5-5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
}

function buildReportViewerUrl(r) {
  const p = new URLSearchParams();
  p.set("suite", String(r?.suite || ""));
  p.set("env", String(r?.env || ""));
  p.set("platform", String(r?.platform || ""));
  p.set("run_id", String(r?.run_id || ""));
  return `/report-viewer.html?${p.toString()}`;
}

// ─── Utils ───
function escapeHtml(s) {
  return String(s ?? "")
    .replaceAll("&","&amp;")
    .replaceAll("<","&lt;")
    .replaceAll(">","&gt;")
    .replaceAll('"',"&quot;")
    .replaceAll("'","&#39;");
}

function safeDate(v) {
  if (!v) return null;
  const d = new Date(v);
  return Number.isNaN(d.getTime()) ? null : d;
}

function formatDuration(ms) {
  if (ms == null || !Number.isFinite(ms)) return "—";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  const totalSec = Math.round(ms / 1000);
  const h = Math.floor(totalSec / 3600);
  const m = Math.floor((totalSec % 3600) / 60);
  const s = totalSec % 60;
  if (h) return `${h}h ${m}m`;
  if (m) return `${m}m ${s}s`;
  return `${s}s`;
}

function durMs(r) {
  if (typeof r.duration === "number") return r.duration < 10000 ? r.duration * 1000 : r.duration;
  const start = safeDate(r.started_at);
  const end = safeDate(r.finished_at);
  if (!start || !end) return Number.POSITIVE_INFINITY;
  return Math.max(0, end.getTime() - start.getTime());
}

function statusWeight(status) {
  const s = String(status || "").toLowerCase();
  if (s === "running") return 0;
  if (s === "failed") return 1;
  if (s === "passed") return 2;
  return 3;
}

function normalizeFilterValue(v) {
  if (v == null) return "";
  const s = String(v).trim().toLowerCase();
  return (s === "" || s === "all") ? "" : s;
}

function matchesQuery(r, q) {
  if (!q) return true;
  const needle = q.toLowerCase();
  const bag = [r.run_id, r.suite, r.env, r.version, r.build_number, r.platform, r.status, r.started_at, r.finished_at]
    .map(x => String(x ?? "").toLowerCase()).join(" ");
  return bag.includes(needle);
}

function shortRunId(runId) {
  const value = String(runId || "");
  if (value.length <= 18) return value;
  return `${value.slice(0, 10)}...${value.slice(-6)}`;
}

function badgeClass(prefix, value) {
  return `${prefix}-${String(value || "unknown").trim().toLowerCase().replace(/\s+/g, "-") || "unknown"}`;
}

function tableBadge(type, value, iconHtml) {
  return `<span class="table-badge table-badge-${type} ${badgeClass(`table-badge-${type}`, value)}">${iconHtml}<span>${escapeHtml(value || "unknown")}</span></span>`;
}

function versionBadge(value) {
  return `<span class="table-badge table-badge-version">${iconVersion()}<span>${escapeHtml(value || "unknown")}</span></span>`;
}

function buildBadge(value) {
  return `<span class="table-badge table-badge-build">${iconBuild()}<span>${escapeHtml(value || "unknown")}</span></span>`;
}

function updateCount(n) {
  countLbl.textContent = `${n.toLocaleString()} run${n === 1 ? "" : "s"}`;
}

function compareRows(a, b) {
  const aRunning = String(a.status || "").toLowerCase() === "running";
  const bRunning = String(b.status || "").toLowerCase() === "running";
  if (aRunning !== bRunning) return aRunning ? -1 : 1;
  if (!sortCol) {
    const at = safeDate(a.started_at)?.getTime() || 0;
    const bt = safeDate(b.started_at)?.getTime() || 0;
    return bt - at;
  }

  const th = document.querySelector(`thead th[data-col="${sortCol}"]`);
  const type = th ? th.dataset.type : "string";
  let cmp = 0;
  switch (type) {
    case "date": cmp = (safeDate(a[sortCol])?.getTime() || 0) - (safeDate(b[sortCol])?.getTime() || 0); break;
    case "duration": cmp = durMs(a) - durMs(b); break;
    case "status": cmp = statusWeight(a.status) - statusWeight(b.status); break;
    default: cmp = String(a[sortCol] || "").localeCompare(String(b[sortCol] || ""), undefined, { numeric: true, sensitivity: "base" });
  }
  return cmp * sortDir;
}

function updateSortUi() {
  document.querySelectorAll("thead th[data-col]").forEach(th => {
    const active = th.dataset.col === sortCol;
    th.classList.toggle("sort-active", active);
    const arrow = th.querySelector(".sort-arrow");
    if (arrow) {
      arrow.classList.toggle("asc", active && sortDir === 1);
      arrow.classList.toggle("desc", active && sortDir === -1);
    }
  });
}

function renderRows(items) {
  if (!items.length) {
    rows.innerHTML = '<tr class="empty-row"><td colspan="10">No runs match your filters.</td></tr>';
    return;
  }

  rows.innerHTML = items.map(r => {
    const status = String(r.status || "unknown").toLowerCase();
    const isRunning = status === "running";
    const statusClass = status === "passed" ? "status-passed" : status === "failed" ? "status-failed" : status === "running" ? "status-running" : "status-unknown";
    const dur = isRunning ? "—" : formatDuration(durMs(r));
    const viewerUrl = !isRunning && r.run_id ? buildReportViewerUrl(r) : "";
    const viewLink = viewerUrl
      ? `<a href="${escapeHtml(viewerUrl)}" class="report-link" target="_blank" rel="noopener noreferrer" onclick="event.stopPropagation()">${iconReport()} View</a>`
      : '<span class="muted">—</span>';
    const chatLink = status === "failed"
      ? `<button type="button" class="ai-chat-row-link" data-chat-run="${escapeHtml(buildChatRunKey(r))}" onclick="event.stopPropagation()">Ask AI</button>`
      : "";
    const reportLink = `<div class="report-actions">${viewLink}${chatLink}</div>`;

    return `<tr class="${viewerUrl ? 'clickable' : ''}" data-viewer="${escapeHtml(viewerUrl)}">
      <td class="mono" title="${escapeHtml(r.run_id)}">${escapeHtml(shortRunId(r.run_id))}</td>
      <td>${tableBadge("suite", r.suite, iconSuite())}</td>
      <td>${tableBadge("env", r.env, iconEnvironment())}</td>
      <td>${versionBadge(r.version)}</td>
      <td>${buildBadge(r.build_number)}</td>
      <td>${tableBadge("platform", r.platform, iconPlatform())}</td>
      <td><span class="status-badge ${statusClass}"><span class="status-dot"></span>${escapeHtml(r.status || "unknown")}</span></td>
      <td class="muted">${escapeHtml(r.started_at || "—")}</td>
      <td class="mono">${dur}</td>
      <td>${reportLink}</td>
    </tr>`;
  }).join('');
}

function renderPagination(totalPages) {
  if (totalPages <= 1) {
    pagination.innerHTML = "";
    return;
  }
  const buttons = [];
  buttons.push(`<button class="page-btn" data-page="${currentPage - 1}" ${currentPage === 1 ? "disabled" : ""}>‹</button>`);
  if (totalPages <= 7) {
    for (let i = 1; i <= totalPages; i++) buttons.push(`<button class="page-btn ${i === currentPage ? "active" : ""}" data-page="${i}">${i}</button>`);
  } else {
    buttons.push(`<button class="page-btn ${currentPage === 1 ? "active" : ""}" data-page="1">1</button>`);
    if (currentPage > 3) buttons.push(`<span class="page-ellipsis">…</span>`);
    const start = Math.max(2, currentPage - 1);
    const end = Math.min(totalPages - 1, currentPage + 1);
    for (let i = start; i <= end; i++) buttons.push(`<button class="page-btn ${i === currentPage ? "active" : ""}" data-page="${i}">${i}</button>`);
    if (currentPage < totalPages - 2) buttons.push(`<span class="page-ellipsis">…</span>`);
    buttons.push(`<button class="page-btn ${currentPage === totalPages ? "active" : ""}" data-page="${totalPages}">${totalPages}</button>`);
  }
  buttons.push(`<button class="page-btn" data-page="${currentPage + 1}" ${currentPage === totalPages ? "disabled" : ""}>›</button>`);
  pagination.innerHTML = buttons.join("");
}

function applyClientFilter() {
  const q = qInput.value.trim();
  const suiteFilterLocal = normalizeFilterValue(suiteFilterValue);
  const envFilterLocal = normalizeFilterValue(envFilterValue);
  const platformFilterLocal = normalizeFilterValue(platformFilterValue);

  const filtered = allRuns
    .filter(r => !suiteFilterLocal || String(r?.suite ?? "").trim().toLowerCase() === suiteFilterLocal)
    .filter(r => !envFilterLocal || String(r?.env ?? "").trim().toLowerCase() === envFilterLocal)
    .filter(r => !platformFilterLocal || String(r?.platform ?? "").trim().toLowerCase() === platformFilterLocal)
    .filter(r => matchesQuery(r, q))
    .sort(compareRows);

  const totalPages = Math.ceil(filtered.length / ITEMS_PER_PAGE);
  if (currentPage > totalPages && totalPages > 0) currentPage = totalPages;
  const startIdx = (currentPage - 1) * ITEMS_PER_PAGE;
  const pageItems = filtered.slice(startIdx, startIdx + ITEMS_PER_PAGE);
  renderRows(pageItems);
  updateCount(filtered.length);
  updateSortUi();
  renderPagination(totalPages);
  updateStatistics();
}

function requestFilterValue(kind, value) {
  const normalized = normalizeFilterValue(value);
  if (!normalized) return "all";

  if (kind === "env") {
    if (normalized === "production") return "prod";
    if (normalized === "staging") return "stage";
  }

  if (kind === "platform") {
    if (["white-label", "white label", "white_label"].includes(normalized)) return "whitelabel";
  }

  return normalized;
}

function buildParams({ refresh = false } = {}) {
  const p = new URLSearchParams();

  // Always send all three filter dimensions.
  // The backend now interprets `all` safely inside one request by scanning narrow prefixes server-side.
  p.set("suite", requestFilterValue("suite", suiteFilterValue));
  p.set("env", requestFilterValue("env", envFilterValue));
  p.set("platform", requestFilterValue("platform", platformFilterValue));

  p.set("limit", "200");
  p.set("since_hours", "12");
  p.set("max_blobs", "200");
  if (refresh) p.set("refresh", "1");
  return p;
}

function setLoading(v, { preserveExisting = false } = {}) {
  isLoading = v;
  refreshBtn.disabled = v;
  themeToggle.disabled = v;
  if (v) {
    refreshBtn.classList.add('loading');
    const hasExistingData = allRuns.length > 0;
    if (preserveExisting && hasExistingData) {
      showStatsLoading(true);
      showChartsLoading(true);
      setTableRefreshing(true);
    } else {
      showStatsLoading(false);
      showChartsLoading(false);
      showTableInitialLoading();
      setTableRefreshing(false);
    }
  } else {
    refreshBtn.classList.remove('loading');
    hideStatsLoading();
    hideChartsLoading();
    setTableRefreshing(false);
  }
}

function setLastUpdatedNow() {
  lastUpdated.style.display = "inline-block";
  lastUpdated.textContent = "Updated " + new Date().toLocaleTimeString();
}

async function load({ preserveExisting = false, refresh = true } = {}) {
  if (isLoading) return;
  setLoading(true, { preserveExisting });
  try {
    const params = buildParams({ refresh });
    const res = await fetch("/api/runs?" + params.toString());
    if (!res.ok) throw new Error(`Failed loading runs: HTTP ${res.status}`);
    const payload = await res.json();
    allRuns = payload.items || [];
    syncAiChatRuns();
    currentPage = 1;
    applyClientFilter();
    setLastUpdatedNow();
    await loadTestStatistics();
  } catch (err) {
    console.error("Fetch error:", err);
    if (allRuns.length === 0) {
      rows.innerHTML = '<tr class="empty-row"><td colspan="10">Failed loading runs. Try refreshing again.</td></tr>';
      updateCount(0);
    }
  } finally {
    setLoading(false, { preserveExisting });
  }
}

// ─── Floating AutomationHub AI failure chat ───
function buildChatRunKey(run) {
  return [run?.suite, run?.env, run?.platform, run?.run_id]
    .map(value => encodeURIComponent(String(value || "")))
    .join("|");
}

function chatRunFromKey(key) {
  return aiChatFailedRuns.find(run => buildChatRunKey(run) === key) || null;
}

function currentAiChatRun() {
  return chatRunFromKey(aiChatRunSelect?.value || "");
}

function currentAiChatTest() {
  const rawValue = aiChatTestSelect?.value ?? "";
  if (rawValue === "") return null;
  const index = Number(rawValue);
  return Number.isInteger(index) && index >= 0 ? aiChatTests[index] || null : null;
}

function setAiChatScopeStatus(message, kind = "") {
  if (!aiChatScopeStatus) return;
  aiChatScopeStatus.textContent = message || "";
  aiChatScopeStatus.className = `ai-chat-scope-status${kind ? ` ${kind}` : ""}`;
}

function setAiChatEnabled() {
  const ready = Boolean(currentAiChatRun() && currentAiChatTest()) && !aiChatSending;
  if (aiChatInput) aiChatInput.disabled = !ready;
  if (aiChatSend) aiChatSend.disabled = !ready || !aiChatInput?.value.trim();
  aiChatQuickActions?.querySelectorAll("button").forEach(button => {
    button.disabled = !ready;
  });
}

function setAiChatOpen(open) {
  if (!aiChatPanel || !aiChatLauncher) return;
  aiChatPanel.classList.toggle("is-open", open);
  aiChatPanel.setAttribute("aria-hidden", open ? "false" : "true");
  aiChatLauncher.setAttribute("aria-expanded", open ? "true" : "false");
  localStorage.setItem(AI_CHAT_OPEN_KEY, open ? "1" : "0");
  if (open) {
    if (aiChatUnread) aiChatUnread.hidden = true;
    if (!aiChatRunSelect?.value && aiChatFailedRuns.length) {
      aiChatRunSelect.value = buildChatRunKey(aiChatFailedRuns[0]);
      loadAiChatTests({ autoSelectFirst: true });
    }
    setTimeout(() => aiChatInput?.focus(), 180);
  }
}

function resetAiChatConversation({ keepMessages = false } = {}) {
  aiChatConversationId = null;
  aiChatHistory = [];
  if (!keepMessages && aiChatMessages) {
    aiChatMessages.innerHTML = "";
    appendAiChatMessage(
      "assistant",
      "Ask why this test failed, whether it happened before, or what to check first."
    );
  }
  if (aiChatOpenReport) {
    aiChatOpenReport.hidden = true;
    aiChatOpenReport.href = "#";
  }
}

function appendAiChatMessage(role, content, options = {}) {
  if (!aiChatMessages) return null;
  const row = document.createElement("div");
  row.className = `ai-chat-message ai-chat-message-${role}`;

  const bubble = document.createElement("div");
  bubble.className = "ai-chat-message-bubble";
  bubble.textContent = String(content || "");

  if (options.evidence?.length) {
    const evidence = document.createElement("div");
    evidence.className = "ai-chat-message-evidence";
    const title = document.createElement("strong");
    title.textContent = "Evidence used: ";
    evidence.appendChild(title);
    evidence.appendChild(document.createTextNode(
      options.evidence.slice(0, 3).map(item => item.value || item.type).filter(Boolean).join(" · ")
    ));
    bubble.appendChild(evidence);
  }

  if (options.meta) {
    const meta = document.createElement("div");
    meta.className = "ai-chat-message-meta";
    meta.textContent = options.meta;
    bubble.appendChild(meta);
  }

  row.appendChild(bubble);
  aiChatMessages.appendChild(row);
  aiChatMessages.scrollTop = aiChatMessages.scrollHeight;
  return row;
}

function appendAiChatThinking() {
  if (!aiChatMessages) return null;
  const row = document.createElement("div");
  row.className = "ai-chat-message ai-chat-message-assistant ai-chat-thinking";
  row.innerHTML = '<div class="ai-chat-message-bubble"><span class="ai-chat-thinking-dot"></span><span class="ai-chat-thinking-dot"></span><span class="ai-chat-thinking-dot"></span></div>';
  aiChatMessages.appendChild(row);
  aiChatMessages.scrollTop = aiChatMessages.scrollHeight;
  return row;
}

function buildAiChatReportUrl(run, test) {
  if (!run) return "#";
  const params = new URLSearchParams({
    suite: String(run.suite || ""),
    env: String(run.env || ""),
    platform: String(run.platform || ""),
    run_id: String(run.run_id || ""),
  });
  if (test?.test_id) params.set("test_id", String(test.test_id));
  return `/report-viewer.html?${params.toString()}`;
}

function syncAiChatRuns(preferredKey = "") {
  if (!aiChatRunSelect) return;
  const existingKey = preferredKey || aiChatRunSelect.value;
  aiChatFailedRuns = allRuns
    .filter(run => String(run?.status || "").toLowerCase() === "failed")
    .sort((a, b) => (safeDate(b.started_at)?.getTime() || 0) - (safeDate(a.started_at)?.getTime() || 0));

  aiChatRunSelect.innerHTML = '<option value="">Select a failed run</option>';
  aiChatFailedRuns.forEach(run => {
    const option = document.createElement("option");
    option.value = buildChatRunKey(run);
    option.textContent = `${run.suite || "unknown"} · ${run.env || "unknown"} · ${shortRunId(run.run_id)} · ${run.version || "unknown"}`;
    option.title = String(run.run_id || "");
    aiChatRunSelect.appendChild(option);
  });

  if (existingKey && chatRunFromKey(existingKey)) {
    aiChatRunSelect.value = existingKey;
  }

  if (!aiChatFailedRuns.length) {
    setAiChatScopeStatus("No failed runs are available in the current dashboard window.");
    aiChatTestSelect.innerHTML = '<option value="">No failed tests available</option>';
    aiChatTestSelect.disabled = true;
  } else if (!aiChatRunSelect.value) {
    setAiChatScopeStatus(`${aiChatFailedRuns.length} failed run${aiChatFailedRuns.length === 1 ? "" : "s"} available.`);
  }
  setAiChatEnabled();
}

async function loadAiChatTests({ autoSelectFirst = false } = {}) {
  const run = currentAiChatRun();
  aiChatTests = [];
  resetAiChatConversation();
  if (!aiChatTestSelect) return;

  aiChatTestSelect.innerHTML = '<option value="">Select a failed test</option>';
  aiChatTestSelect.disabled = true;
  setAiChatEnabled();

  if (!run) {
    setAiChatScopeStatus("Select a failed run and test to begin.");
    return;
  }

  setAiChatScopeStatus("Loading failed tests...");
  try {
    const params = new URLSearchParams({
      suite: String(run.suite || ""),
      env: String(run.env || ""),
      platform: String(run.platform || ""),
      run_id: String(run.run_id || ""),
    });
    const response = await fetch(`/api/report-tests?${params.toString()}`);
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || !payload.ok) {
      throw new Error(payload.error || `Failed loading failed tests: HTTP ${response.status}`);
    }

    aiChatTests = Array.isArray(payload.tests) ? payload.tests : [];
    aiChatTestSelect.innerHTML = '<option value="">Select a failed test</option>';
    aiChatTests.forEach((test, index) => {
      const option = document.createElement("option");
      option.value = String(index);
      option.textContent = `${test.status || "failed"} · ${test.name || test.full_name || test.test_id || "Unknown test"}`;
      option.title = String(test.full_name || test.name || "");
      aiChatTestSelect.appendChild(option);
    });
    aiChatTestSelect.disabled = aiChatTests.length === 0;

    if (aiChatTests.length) {
      setAiChatScopeStatus(`${aiChatTests.length} failed or error test${aiChatTests.length === 1 ? "" : "s"} found.`);
      if (autoSelectFirst) {
        aiChatTestSelect.value = "0";
        handleAiChatTestChange();
      }
    } else {
      setAiChatScopeStatus("No failed Allure test-case artifacts were found for this run.");
    }
  } catch (error) {
    console.error(error);
    setAiChatScopeStatus(error.message || "Failed loading tests.", "error");
  } finally {
    setAiChatEnabled();
  }
}

function handleAiChatTestChange() {
  resetAiChatConversation();
  const run = currentAiChatRun();
  const test = currentAiChatTest();
  if (run && test) {
    setAiChatScopeStatus(`Selected: ${test.name || test.full_name || test.test_id}`);
    if (aiChatOpenReport) {
      aiChatOpenReport.href = buildAiChatReportUrl(run, test);
      aiChatOpenReport.hidden = false;
    }
  }
  setAiChatEnabled();
}

async function sendAiChatQuestion(rawQuestion) {
  const question = String(rawQuestion || "").trim();
  const run = currentAiChatRun();
  const test = currentAiChatTest();
  if (!question || !run || !test || aiChatSending) return;

  const priorHistory = aiChatHistory.slice(-8);
  appendAiChatMessage("user", question);
  if (aiChatInput) {
    aiChatInput.value = "";
    aiChatInput.style.height = "auto";
  }

  aiChatSending = true;
  setAiChatEnabled();
  if (aiChatProviderState) aiChatProviderState.textContent = "Analyzing selected failure...";
  const thinking = appendAiChatThinking();

  try {
    const response = await fetch("/api/ai/failure-chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        suite: run.suite,
        env: run.env,
        platform: run.platform,
        run_id: run.run_id,
        test_id: test.test_id || null,
        test_blob: test.test_blob || null,
        test_name: test.full_name || test.name || null,
        question,
        conversation_id: aiChatConversationId,
        history: priorHistory,
      }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || !payload.ok) {
      throw new Error(payload.error || `AI chat failed: HTTP ${response.status}`);
    }

    aiChatConversationId = payload.conversation_id || aiChatConversationId;
    aiChatHistory.push({ role: "user", content: question });
    aiChatHistory.push({ role: "assistant", content: payload.answer || "No answer returned." });
    aiChatHistory = aiChatHistory.slice(-10);

    const modelLabel = payload.actual_model_response
      ? "Local model"
      : payload.fallback_used ? "Deterministic fallback" : (payload.provider || "AI");
    const meta = `${modelLabel} · ${payload.confidence || "low"} confidence · ${payload.answer_type || "unknown"}`;
    appendAiChatMessage("assistant", payload.answer || "No answer returned.", {
      evidence: payload.evidence || [],
      meta,
    });

    if (aiChatProviderState) {
      aiChatProviderState.textContent = payload.actual_model_response
        ? "Grounded answer from local model"
        : "Grounded fallback answer";
    }
    if (payload.report_viewer_url && aiChatOpenReport) {
      aiChatOpenReport.href = payload.report_viewer_url;
      aiChatOpenReport.hidden = false;
    }
    if (!aiChatPanel?.classList.contains("is-open") && aiChatUnread) {
      aiChatUnread.hidden = false;
      aiChatUnread.textContent = "1";
    }
  } catch (error) {
    console.error(error);
    appendAiChatMessage("assistant", error.message || "The AI assistant could not answer this question.", {
      meta: "Request failed",
    });
    if (aiChatProviderState) aiChatProviderState.textContent = "Assistant unavailable";
  } finally {
    thinking?.remove();
    aiChatSending = false;
    setAiChatEnabled();
    aiChatInput?.focus();
  }
}

function openAiChatForRunKey(key) {
  const run = chatRunFromKey(key);
  if (!run || !aiChatRunSelect) return;
  aiChatRunSelect.value = key;
  setAiChatOpen(true);
  loadAiChatTests({ autoSelectFirst: true });
}

function initAiChat() {
  if (!aiChatLauncher || !aiChatPanel) return;
  aiChatLauncher.addEventListener("click", () => {
    setAiChatOpen(!aiChatPanel.classList.contains("is-open"));
  });
  aiChatClose?.addEventListener("click", () => setAiChatOpen(false));
  aiChatRunSelect?.addEventListener("change", () => loadAiChatTests({ autoSelectFirst: false }));
  aiChatTestSelect?.addEventListener("change", handleAiChatTestChange);
  aiChatQuickActions?.addEventListener("click", event => {
    const button = event.target.closest("button[data-question]");
    if (!button || button.disabled) return;
    sendAiChatQuestion(button.dataset.question || "");
  });
  aiChatForm?.addEventListener("submit", event => {
    event.preventDefault();
    sendAiChatQuestion(aiChatInput?.value || "");
  });
  aiChatInput?.addEventListener("input", () => {
    aiChatInput.style.height = "auto";
    aiChatInput.style.height = `${Math.min(aiChatInput.scrollHeight, 112)}px`;
    setAiChatEnabled();
  });
  aiChatInput?.addEventListener("keydown", event => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendAiChatQuestion(aiChatInput.value);
    }
  });
  setAiChatOpen(localStorage.getItem(AI_CHAT_OPEN_KEY) === "1");
  setAiChatEnabled();
}

// ─── Auto-poll: once per hour only ───
let pollTimer = null;
const POLL_INTERVAL_MS = 60 * 60 * 1000;

function startPolling() {
  stopPolling();
  pollTimer = setInterval(() => {
    if (isLoading) return;
    load({ preserveExisting: true, refresh: true });
  }, POLL_INTERVAL_MS);
}

function stopPolling() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = null;
}

// ─── Events ───
document.querySelectorAll("thead th[data-col]").forEach(th => {
  th.addEventListener("click", () => {
    const col = th.dataset.col;
    if (!col) return;
    if (sortCol === col) sortDir *= -1;
    else { sortCol = col; sortDir = -1; }
    currentPage = 1;
    applyClientFilter();
  });
});

pagination.addEventListener("click", (e) => {
  const btn = e.target.closest("button.page-btn");
  if (!btn || btn.hasAttribute("disabled")) return;
  const page = Number(btn.dataset.page);
  if (!page || page === currentPage) return;
  currentPage = page;
  applyClientFilter();
  window.scrollTo({ top: 0, behavior: "smooth" });
});

rows.addEventListener("click", (e) => {
  const chatButton = e.target.closest(".ai-chat-row-link");
  if (chatButton?.dataset.chatRun) {
    openAiChatForRunKey(chatButton.dataset.chatRun);
    return;
  }
  const tr = e.target.closest("tr.clickable");
  if (!tr || !tr.dataset.viewer) return;
  window.open(tr.dataset.viewer, "_blank", "noopener,noreferrer");
});

qInput.addEventListener("input", () => {
  currentPage = 1;
  applyClientFilter();
});

clearFiltersBtn.addEventListener("click", () => {
  qInput.value = "";
  suiteFilterValue = "";
  envFilterValue = "";
  platformFilterValue = "";
  resetChipGroup(suiteFilter);
  resetChipGroup(envFilter);
  resetChipGroup(platformFilter);
  currentPage = 1;
  load({ preserveExisting: true, refresh: true });
});

refreshBtn.addEventListener("click", () => {
  currentPage = 1;
  load({ preserveExisting: true, refresh: true });
});

document.addEventListener("visibilitychange", () => {
  if (document.hidden) stopPolling();
  else startPolling();
});

window.addEventListener("beforeunload", stopPolling);

// ─── Initialize ───
showStatsLoading(false);
showChartsLoading(false);
showTableInitialLoading();
initAiChat();
setTimeout(() => {
  initCharts();
  initStatsDrawer();
  load({ preserveExisting: false, refresh: true }).then(startPolling);
}, 100);
