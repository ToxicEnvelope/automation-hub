// ─── Elements ───
const envSel       = document.getElementById("envSel");
const platformSel  = document.getElementById("platformSel");
const suiteSel     = document.getElementById("suiteSel");
const qInput       = document.getElementById("qInput");
const rows         = document.getElementById("rows");
const refreshBtn   = document.getElementById("refreshBtn");
const pagination   = document.getElementById("pagination");
const countLbl     = document.getElementById("countLbl");
const lastUpdated  = document.getElementById("lastUpdated");
const themeToggle  = document.getElementById("themeToggle");

let nextCursor = null;
let isLoading  = false;
let allRuns    = [];
let currentPage = 1;
const ITEMS_PER_PAGE = 50;

// ─── Theme ───
const THEME_KEY = "ah_theme";

function getInitialTheme() {
  const saved = localStorage.getItem(THEME_KEY);
  if (saved === "dark" || saved === "light") return saved;
  return (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches) ? "dark" : "light";
}

function applyTheme(theme) {
  document.documentElement.classList.toggle("dark", theme === "dark");
}

let currentTheme = getInitialTheme();
applyTheme(currentTheme);

themeToggle.addEventListener("click", () => {
  currentTheme = currentTheme === "dark" ? "light" : "dark";
  localStorage.setItem(THEME_KEY, currentTheme);
  applyTheme(currentTheme);
});

// ─── Custom Dropdown Logic ───
// Each .dd root exposes a .value property and dispatches "change" on itself.
function initDropdown(ddEl) {
  const trigger = ddEl.querySelector(".dd-trigger");
  const panel   = ddEl.querySelector(".dd-panel");
  const label   = ddEl.querySelector(".dd-label");
  const options = ddEl.querySelectorAll(".dd-option");

  // expose .value on the element so buildParams() works unchanged
  Object.defineProperty(ddEl, "value", {
    get() { return ddEl.dataset.value; },
    set(v) {
      ddEl.dataset.value = v;
      options.forEach(o => o.classList.toggle("active", o.dataset.value === v));
      const active = ddEl.querySelector(".dd-option.active");
      label.textContent = active ? active.textContent : "All";
    }
  });

  function close() { ddEl.classList.remove("open"); }

  trigger.addEventListener("click", (e) => {
    e.stopPropagation();
    // close every OTHER open dropdown first
    document.querySelectorAll(".dd.open").forEach(d => { if (d !== ddEl) d.classList.remove("open"); });
    ddEl.classList.toggle("open");
  });

  options.forEach(opt => {
    opt.addEventListener("click", () => {
      ddEl.value = opt.dataset.value;
      close();
      ddEl.dispatchEvent(new Event("change", { bubbles: true }));
    });
  });
}

// close all dropdowns when clicking outside
document.addEventListener("click", () => {
  document.querySelectorAll(".dd.open").forEach(d => d.classList.remove("open"));
});

// init all three after DOM ready
initDropdown(envSel);
initDropdown(platformSel);
initDropdown(suiteSel);

// ─── Helpers ───
function escapeHtml(str) {
  return String(str)
    .replaceAll("&","&amp;")
    .replaceAll("<","&lt;")
    .replaceAll(">","&gt;")
    .replaceAll('"','&quot;')
    .replaceAll("'","&#039;");
}

function statusBadge(status) {
  const s = (status || "").toLowerCase();
  if (s === "passed")  return '<span class="badge badge-passed">Passed</span>';
  if (s === "failed")  return '<span class="badge badge-failed">Failed</span>';
  if (s === "running") return '<span class="badge badge-running blink">Running</span>';
  return '<span class="badge badge-other">' + escapeHtml(status ?? "") + '</span>';
}

function versionBadge(version) {
  const v = (version || "").toString().trim();
  if (!v || v.toLowerCase() === "unknown")
    return '<span class="badge badge-unknown">unknown</span>';
  return '<span class="badge badge-version">' + escapeHtml(v) + '</span>';
}

function buildNumberBadge(buildNum) {
  const b = (buildNum || "").toString().trim();
  if (!b || b.toLowerCase() === "unknown")
    return '<span class="badge badge-unknown">unknown</span>';
  return '<span class="badge badge-version">' + escapeHtml(b) + '</span>';
}

function safeDate(iso) {
  if (!iso) return null;
  const d = new Date(iso);
  return isNaN(d.getTime()) ? null : d;
}

function formatDuration(ms) {
  if (ms == null || ms < 0) return "\u2014";
  const tot = Math.floor(ms / 1000);
  const h = Math.floor(tot / 3600);
  const m = Math.floor((tot % 3600) / 60);
  const s = tot % 60;
  const mm = String(m).padStart(2,"0");
  const ss = String(s).padStart(2,"0");
  return h > 0 ? h + "h " + mm + ":" + ss : mm + ":" + ss;
}

function durationCell(run) {
  const status   = (run.status || "").toLowerCase();
  const started  = safeDate(run.started_at);
  const finished = safeDate(run.finished_at);
  if (status === "running" || !finished)
    return '<span class="dots"><span></span><span></span><span></span></span>';
  if (!started) return "\u2014";
  return formatDuration(finished.getTime() - started.getTime());
}

function linkCell(run) {
  const isRunning = (run.status || "").toLowerCase() === "running";
  if (!isRunning && run.report_url)
    return '<a class="link" href="' + run.report_url + '" target="_blank" rel="noopener noreferrer" onclick="event.stopPropagation()">Open Report \u2192</a>';
  return '<span class="link-disabled" title="Not available while running">Open Report</span>';
}

// ─── Render ───
function renderRows(items) {
  if (!items.length) {
    rows.innerHTML = '<tr class="empty-row"><td colspan="10">No runs match your filters.</td></tr>';
    return;
  }
  rows.innerHTML = "";
  items.forEach(r => {
    const isRunning = (r.status || "").toLowerCase() === "running";
    rows.insertAdjacentHTML("beforeend",
      '<tr data-report="' + (r.report_url || "") + '" data-running="' + isRunning + '">' +
      '<td class="mono">' + escapeHtml(r.run_id ?? "") + '</td>' +
      '<td>' + escapeHtml(r.suite ?? "") + '</td>' +
      '<td>' + escapeHtml(r.env ?? "") + '</td>' +
      '<td>' + versionBadge(r.version) + '</td>' +
      '<td>' + buildNumberBadge(r.build_number) + '</td>' +
      '<td>' + escapeHtml(r.platform ?? "") + '</td>' +
      '<td>' + statusBadge(r.status) + '</td>' +
      '<td class="small">' + escapeHtml(r.started_at.split("+")[0] ?? "") + '</td>' +
      '<td class="mono">' + durationCell(r) + '</td>' +
      '<td>' + linkCell(r) + '</td>' +
      '</tr>'
    );
  });
}

function updateCount(visibleCount) {
  const total = allRuns.length;
  const vis   = visibleCount ?? total;
  countLbl.textContent = !total ? "" :
    vis === total ? "Showing " + total + " runs" : "Showing " + vis + " of " + total + " loaded runs";
}

// ─── Filter / sort ───
function matchesQuery(run, q) {
  if (!q) return true;

  // Compute duration string for searching
  let durationStr = "";
  const status = (run.status || "").toLowerCase();
  if (status !== "running") {
    const started = safeDate(run.started_at);
    const finished = safeDate(run.finished_at);
    if (started && finished) {
      durationStr = formatDuration(finished.getTime() - started.getTime());
    }
  }

  const hay = [
    run.run_id,
    run.suite,
    run.env,
    run.platform,
    run.status,
    run.version,
    run.build_number,
    run.started_at,
    run.finished_at,
    durationStr
  ].filter(Boolean).join(" ").toLowerCase();

  return hay.includes(q.toLowerCase());
}

// ─── Column sort state ───
let sortCol  = null;           // no default sort - backend returns newest first
let sortDir  = -1;             // -1 = desc, 1 = asc

// weight map for status so "running" naturally sorts to the top when desc
const STATUS_WEIGHT = { running:0, passed:1, failed:2 };
function statusWeight(s) { return STATUS_WEIGHT[(s||"").toLowerCase()] ?? 3; }

function compareRows(a, b) {
  if (!sortCol) return 0; // no sort - preserve backend order (newest first)
  const th = document.querySelector('thead th[data-col="' + sortCol + '"]');
  const type = th ? th.dataset.type : "string";
  let cmp = 0;

  switch (type) {
    case "date":
      cmp = (safeDate(a[sortCol])?.getTime() || 0) - (safeDate(b[sortCol])?.getTime() || 0);
      break;
    case "duration": {
      // compute ms on-the-fly; running → Infinity so it floats
      function durMs(r) {
        if ((r.status||"").toLowerCase() === "running") return Infinity;
        const s = safeDate(r.started_at), f = safeDate(r.finished_at);
        return (s && f) ? f.getTime() - s.getTime() : 0;
      }
      cmp = durMs(a) - durMs(b);
      break;
    }
    case "status":
      cmp = statusWeight(a.status) - statusWeight(b.status);
      break;
    default: // string
      cmp = (a[sortCol] || "").localeCompare(b[sortCol] || "", undefined, { numeric:true, sensitivity:"base" });
  }
  return cmp * sortDir;
}

function updateSortUi() {
  document.querySelectorAll("thead th[data-col]").forEach(th => {
    const active = th.dataset.col === sortCol;
    th.classList.toggle("sort-active", active);
    const arrow = th.querySelector(".sort-arrow");
    if (arrow) {
      arrow.classList.toggle("asc",  active &&  sortDir === 1);
      arrow.classList.toggle("desc", active &&  sortDir === -1);
    }
  });
}

function renderPagination(totalPages) {
  if (totalPages <= 1) {
    pagination.innerHTML = "";
    return;
  }

  const buttons = [];

  // Previous button
  buttons.push(`<button class="page-btn" data-page="${currentPage - 1}" ${currentPage === 1 ? "disabled" : ""}>‹</button>`);

  // Page numbers with ellipsis logic
  if (totalPages <= 7) {
    // Show all pages
    for (let i = 1; i <= totalPages; i++) {
      buttons.push(`<button class="page-btn ${i === currentPage ? "active" : ""}" data-page="${i}">${i}</button>`);
    }
  } else {
    // Show first page
    buttons.push(`<button class="page-btn ${1 === currentPage ? "active" : ""}" data-page="1">1</button>`);

    if (currentPage > 3) {
      buttons.push(`<span class="page-ellipsis">…</span>`);
    }

    // Show pages around current
    const start = Math.max(2, currentPage - 1);
    const end = Math.min(totalPages - 1, currentPage + 1);
    for (let i = start; i <= end; i++) {
      buttons.push(`<button class="page-btn ${i === currentPage ? "active" : ""}" data-page="${i}">${i}</button>`);
    }

    if (currentPage < totalPages - 2) {
      buttons.push(`<span class="page-ellipsis">…</span>`);
    }

    // Show last page
    buttons.push(`<button class="page-btn ${totalPages === currentPage ? "active" : ""}" data-page="${totalPages}">${totalPages}</button>`);
  }

  // Next button
  buttons.push(`<button class="page-btn" data-page="${currentPage + 1}" ${currentPage === totalPages ? "disabled" : ""}>›</button>`);

  pagination.innerHTML = buttons.join("");
}

function applyClientFilter() {
  const q = qInput.value.trim();
  const filtered = allRuns
    .filter(r => matchesQuery(r, q))
    .sort(compareRows);

  // Paginate
  const totalPages = Math.ceil(filtered.length / ITEMS_PER_PAGE);
  const startIdx = (currentPage - 1) * ITEMS_PER_PAGE;
  const endIdx = startIdx + ITEMS_PER_PAGE;
  const pageItems = filtered.slice(startIdx, endIdx);

  renderRows(pageItems);
  updateCount(filtered.length);
  updateSortUi();
  renderPagination(totalPages);
}

// ─── API ───
function buildParams() {
  const p = new URLSearchParams();
  if (envSel.value)      p.set("env",      envSel.value);
  if (platformSel.value) p.set("platform", platformSel.value);
  if (suiteSel.value && suiteSel.value !== "all") p.set("suite", suiteSel.value);
  return p;
}

function setLoading(v) {
  isLoading = v;
  refreshBtn.disabled  = v;
  themeToggle.disabled = v;
}

function setLastUpdatedNow() {
  lastUpdated.style.display = "inline-block";
  lastUpdated.textContent   = "Updated " + new Date().toLocaleTimeString();
}

async function fetchPage({ reset }) {
  if (isLoading) return;
  setLoading(true);
  try {
    const params = buildParams();
    if (reset)                params.set("refresh","1");
    if (!reset && nextCursor) params.set("cursor", nextCursor);

    const res     = await fetch("/api/runs?" + params.toString());
    const payload = await res.json();

    nextCursor = payload.next_cursor || null;
    if (reset) allRuns = [];
    allRuns.push(...(payload.items || []));

    applyClientFilter();
    setLastUpdatedNow();
  } finally {
    setLoading(false);
  }
}

async function load() {
  nextCursor = null;
  await fetchPage({ reset: true });
}

// ─── Auto-poll for new/updated runs ───
let pollTimer = null;
const POLL_INTERVAL_MS = 600000; // poll every 10 minutes

async function pollForUpdates() {
  if (isLoading) return; // skip if already fetching

  try {
    // fetch just the first page with refresh=1 (fresh data)
    const params = buildParams();
    params.set("refresh", "1");

    const res = await fetch("/api/runs?" + params.toString());
    const payload = await res.json();
    const fresh = payload.items || [];

    // merge: update existing runs by run_id, prepend new ones
    const runMap = new Map(allRuns.map(r => [r.run_id, r]));
    let newCount = 0;

    fresh.forEach(f => {
      if (runMap.has(f.run_id)) {
        // update in place
        const idx = allRuns.findIndex(r => r.run_id === f.run_id);
        if (idx !== -1) allRuns[idx] = f;
      } else {
        // new run → prepend so it floats to top when sorted
        allRuns.unshift(f);
        newCount++;
      }
    });

    // re-render if anything changed
    if (newCount > 0 || fresh.length > 0) {
      applyClientFilter();
      setLastUpdatedNow();
    }
  } catch (err) {
    console.warn("Poll failed:", err);
  }
}

function startPolling() {
  stopPolling();
  pollTimer = setInterval(pollForUpdates, POLL_INTERVAL_MS);
}

function stopPolling() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = null;
}

// ─── Pagination click ───
pagination.addEventListener("click", e => {
  const btn = e.target.closest(".page-btn[data-page]");
  if (!btn || btn.disabled) return;
  currentPage = parseInt(btn.dataset.page, 10);
  applyClientFilter();
  window.scrollTo({ top: 0, behavior: "smooth" });
});

// ─── Thead sort click ───
document.querySelector("thead").addEventListener("click", e => {
  const th = e.target.closest("th[data-col]");
  if (!th) return;
  if (sortCol === th.dataset.col) {
    sortDir *= -1;                 // toggle direction
  } else {
    sortCol = th.dataset.col;      // new column → default desc
    sortDir = -1;
  }
  currentPage = 1;                 // reset to page 1 on sort
  applyClientFilter();             // re-sort + re-render + update arrows
});

// ─── Events ───
rows.addEventListener("click", e => {
  const tr = e.target.closest("tr[data-report]");
  if (!tr || tr.dataset.running === "true" || !tr.dataset.report) return;
  window.open(tr.dataset.report, "_blank", "noopener,noreferrer");
});

[envSel, platformSel, suiteSel].forEach(el => el.addEventListener("change", () => {
  currentPage = 1;
  load();
}));

qInput.addEventListener("input", () => {
  currentPage = 1;
  applyClientFilter();
});

refreshBtn.addEventListener("click", () => {
  currentPage = 1;
  load();
});

updateSortUi();
load().then(() => startPolling());

// stop polling when tab is hidden or user navigates away
document.addEventListener("visibilitychange", () => {
  document.hidden ? stopPolling() : startPolling();
});
window.addEventListener("beforeunload", stopPolling);
