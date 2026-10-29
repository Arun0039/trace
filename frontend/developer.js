const D = {
  auth: null,
  summary: null,
  health: null,
  errors: [],
  pagination: { page: 1, pages: 1, total: 0, page_size: 25 },
  filters: {
    status: "", source: "", severity: "", q: "",
    date_from: "", date_to: "", period: "24h",
  },
  detail: null,
};

const API_BASE_URL = String(window.QA_API_BASE_URL || "").replace(/\/+$/, "");
const root = document.getElementById("devRoot");
const drawer = document.getElementById("devDrawer");
const drawerBody = document.getElementById("devDetailBody");
const toastEl = document.getElementById("devToast");

function esc(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function fmtDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return esc(value);
  return date.toLocaleString(undefined, {
    year: "numeric", month: "short", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit",
  });
}

function fmtBytes(value) {
  const bytes = Number(value) || 0;
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let amount = bytes / 1024;
  let unit = 0;
  while (amount >= 1024 && unit < units.length - 1) {
    amount /= 1024;
    unit += 1;
  }
  return `${amount >= 10 ? amount.toFixed(0) : amount.toFixed(1)} ${units[unit]}`;
}

function toast(message) {
  toastEl.textContent = message;
  toastEl.classList.add("show");
  clearTimeout(toastEl._timer);
  toastEl._timer = setTimeout(() => toastEl.classList.remove("show"), 2800);
}

async function devApi(path, options = {}) {
  let response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      credentials: "include",
      ...options,
      headers: {
        "Content-Type": "application/json",
        ...(options.headers || {}),
      },
    });
  } catch (error) {
    throw new Error("Cannot reach the QA server.");
  }
  const type = response.headers.get("content-type") || "";
  const data = type.includes("application/json") ? await response.json() : null;
  if (!response.ok) {
    const error = new Error(data?.error || `Request failed (${response.status})`);
    error.status = response.status;
    throw error;
  }
  return data;
}

function can(permission) {
  const permissions = D.auth?.permissions || [];
  return permissions.includes("*") || permissions.includes(permission);
}

function summaryView() {
  const summary = D.summary || {};
  const totals = summary.totals || {};
  const statuses = summary.statuses || {};
  const sources = summary.sources || {};
  const severities = summary.unresolved_severities || {};
  const requests = summary.api_requests || {};
  const frequency = summary.frequency || [];
  const maxDaily = Math.max(1, ...frequency.map(day => Number(day.occurrences) || 0));
  const sourceRows = Object.entries(sources).sort((a, b) => b[1] - a[1]).map(([source, count]) =>
    `<div class="dev-status-row"><span class="dev-dot"></span><span>${esc(source)}</span><strong style="margin-left:auto">${count}</strong></div>`
  ).join("") || '<div class="dev-empty">No errors recorded.</div>';
  const severityRows = Object.entries(severities).sort((a, b) => b[1] - a[1]).map(([severity, count]) =>
    `<div class="dev-status-row"><span class="dev-dot ${severity === "critical" || severity === "error" ? "error" : severity === "warning" ? "degraded" : ""}"></span><span>${esc(severity)}</span><strong style="margin-left:auto">${count}</strong></div>`
  ).join("") || '<div class="dev-empty">No unresolved errors.</div>';
  const trendRows = frequency.map(day => `
    <div class="dev-trend-row">
      <span>${esc(day.day)}</span>
      <div class="dev-trend-track"><i style="width:${Math.min(100, (Number(day.occurrences) || 0) * 100 / maxDaily)}%"></i></div>
      <strong>${Number(day.occurrences) || 0}</strong>
    </div>`).join("") || '<div class="dev-empty">No errors during this period.</div>';
  const failedEndpoints = requests.top_failed_endpoints?.length
    ? requests.top_failed_endpoints
    : summary.top_endpoints || [];
  const endpointRows = failedEndpoints.map(item =>
    `<div class="dev-status-row"><code>${esc(item.endpoint)}</code><strong style="margin-left:auto">${Number(item.failures ?? item.occurrences) || 0}</strong></div>`
  ).join("") || '<div class="dev-empty">No endpoint errors.</div>';
  const typeRows = (summary.top_error_types || []).map(item =>
    `<div class="dev-status-row"><span>${esc(item.error_type || "Unknown")}</span><strong style="margin-left:auto">${Number(item.occurrences) || 0}</strong></div>`
  ).join("") || '<div class="dev-empty">No exception types recorded.</div>';
  return `
    <div class="dev-grid">
      <div class="dev-metric"><span>Open errors</span><strong>${summary.open || 0}</strong><small>${statuses.acknowledged || 0} acknowledged</small></div>
      <div class="dev-metric"><span>Errors · ${esc(summary.period || "24h")}</span><strong>${totals.period_errors || 0}</strong><small>${totals.period_occurrences || 0} occurrences</small></div>
      <div class="dev-metric"><span>Recurring errors</span><strong>${totals.recurring_errors || 0}</strong><small>${totals.recurring_occurrences || 0} occurrences</small></div>
      <div class="dev-metric"><span>All occurrences</span><strong>${totals.occurrences || 0}</strong><small>${totals.errors || 0} grouped errors</small></div>
      <div class="dev-metric"><span>API failure rate</span><strong>${Number(requests.failure_rate_percent || 0)}%</strong><small>${requests.http_4xx || 0} HTTP 4xx · ${requests.http_5xx || 0} HTTP 5xx</small></div>
    </div>
    <div class="dev-columns">
      <section class="dev-panel">
        <div class="dev-panel-head"><h2>Recent errors</h2><span class="dev-pill">${esc(summary.generated_at ? `updated ${fmtDate(summary.generated_at)}` : "")}</span></div>
        <div class="dev-panel-body">
          ${(summary.recent || []).map(item => `
            <div class="dev-status-row" data-open-error="${item.id}" style="cursor:pointer">
              <span class="dev-dot ${item.severity === "critical" ? "error" : item.status === "resolved" ? "ok" : ""}"></span>
              <span style="min-width:0;overflow-wrap:anywhere">${esc(item.message)}<br><small class="dev-error-meta">${esc(item.source)} · ${esc(item.route || "no route")} · ${item.occurrence_count}×</small></span>
              <span style="margin-left:auto">${esc(item.status)}</span>
            </div>`).join("") || '<div class="dev-empty">No recent errors.</div>'}
        </div>
      </section>
      <section class="dev-panel">
        <div class="dev-panel-head"><h2>By source</h2></div>
        <div class="dev-panel-body">${sourceRows}</div>
      </section>
    </div>
    <section class="dev-panel" style="margin-bottom:16px">
      <div class="dev-panel-head"><h2>Unresolved severity</h2></div>
      <div class="dev-panel-body">${severityRows}</div>
    </section>
    <div class="dev-columns">
      <section class="dev-panel"><div class="dev-panel-head"><h2>Error frequency</h2></div><div class="dev-panel-body">${trendRows}</div></section>
      <section class="dev-panel"><div class="dev-panel-head"><h2>Top exception types</h2></div><div class="dev-panel-body">${typeRows}</div></section>
    </div>
    <div class="dev-columns">
      <section class="dev-panel"><div class="dev-panel-head"><h2>Top failing endpoints</h2></div><div class="dev-panel-body">${endpointRows}</div></section>
      <section class="dev-panel"><div class="dev-panel-head"><h2>API response counts</h2></div><div class="dev-panel-body">
        <div class="dev-status-row"><span>Requests observed</span><strong style="margin-left:auto">${requests.request_count || 0}</strong></div>
        <div class="dev-status-row"><span>Failed requests</span><strong style="margin-left:auto">${requests.failure_count || 0}</strong></div>
        <div class="dev-sub">${esc(requests.retention || "")}</div>
      </div></section>
    </div>`;
}

function healthView() {
  const health = D.health || {};
  const database = health.database || {};
  const storage = health.storage || {};
  const jobs = health.jobs || {};
  const dot = status => `dev-dot ${status === "ok" ? "ok" : status === "error" ? "error" : "degraded"}`;
  return `
    <section class="dev-panel">
      <div class="dev-panel-head"><h2>Runtime health</h2><span class="dev-pill ${health.status === "ok" ? "resolved" : "warning"}">${esc(health.status || "unknown")}</span></div>
      <div class="dev-panel-body">
        <div class="dev-status-row"><span class="${dot(database.status)}"></span><span>Database</span><strong style="margin-left:auto">${esc(database.status || "unknown")} · ${database.latency_ms ?? "—"} ms</strong></div>
        <div class="dev-status-row"><span class="${dot(storage.status)}"></span><span>Storage</span><strong style="margin-left:auto">${esc(storage.status || "unknown")} · ${fmtBytes(storage.free_bytes)} free</strong></div>
        <div class="dev-status-row"><span class="${dot(jobs.status)}"></span><span>Analysis jobs</span><strong style="margin-left:auto">${jobs.running || 0} running · ${jobs.queued || 0} queued · ${jobs.stale || 0} stale</strong></div>
        <div class="dev-status-row"><span class="dev-dot"></span><span>Uptime</span><strong style="margin-left:auto">${Math.round((health.uptime_seconds || 0) / 60)} min</strong></div>
        <div class="dev-status-row"><span class="dev-dot ${jobs.failed_24h ? "error" : ""}"></span><span>Failed jobs (24h)</span><strong style="margin-left:auto">${jobs.failed_24h || 0}</strong></div>
      </div>
    </section>`;
}

function errorsView() {
  const filters = D.filters;
  const rows = D.errors.map(item => `
    <tr data-open-error="${item.id}">
      <td>${fmtDate(item.last_seen_at)}</td>
      <td><span class="dev-pill ${esc(item.severity)}">${esc(item.severity)}</span></td>
      <td><span class="dev-error-message">${esc(item.message)}</span><span class="dev-error-meta">${esc(item.error_type || "Error")}</span></td>
      <td>${esc(item.source)}</td>
      <td><code>${esc(item.request_id || "—")}</code></td>
      <td><code>${esc(item.route || "—")}</code></td>
      <td>${esc(item.status_code ?? "—")}</td>
      <td><span class="dev-pill ${item.status === "resolved" ? "resolved" : ""}">${esc(item.status)}</span></td>
      <td>${item.occurrence_count}</td>
    </tr>`).join("");
  const pagination = D.pagination || {};
  return `
    <div class="dev-grid" style="grid-template-columns:1fr">
      <section class="dev-panel">
        <div class="dev-panel-head">
          <h2>Errors</h2>
          <div class="dev-filters">
            <select id="devStatus"><option value="">All statuses</option>${["open", "acknowledged", "resolved", "ignored"].map(value => `<option value="${value}" ${filters.status === value ? "selected" : ""}>${value}</option>`).join("")}</select>
            <select id="devSource"><option value="">All sources</option>${["frontend", "backend", "api", "job", "database"].map(value => `<option value="${value}" ${filters.source === value ? "selected" : ""}>${value}</option>`).join("")}</select>
            <select id="devSeverity"><option value="">All severities</option>${["debug", "info", "warning", "error", "critical"].map(value => `<option value="${value}" ${filters.severity === value ? "selected" : ""}>${value}</option>`).join("")}</select>
            <input id="devSearch" type="search" placeholder="Search message or route…" value="${esc(filters.q)}">
            <label class="dev-date-filter">From <input id="devDateFrom" type="date" value="${esc(filters.date_from)}"></label>
            <label class="dev-date-filter">To <input id="devDateTo" type="date" value="${esc(filters.date_to)}"></label>
          </div>
        </div>
        <div class="dev-table-wrap">
          <table class="dev-table">
            <thead><tr><th>Timestamp</th><th>Severity</th><th>Error</th><th>Service</th><th>Request ID</th><th>Endpoint</th><th>HTTP</th><th>Status</th><th>Count</th></tr></thead>
            <tbody>${rows || '<tr><td colspan="9" class="dev-empty"><strong>No matching errors</strong><br>Errors reported by the frontend, API, jobs, and database will appear here.</td></tr>'}</tbody>
          </table>
        </div>
        <div class="dev-pagination">
          <span>${pagination.total || 0} error${pagination.total === 1 ? "" : "s"} · page ${pagination.page || 1} of ${pagination.pages || 1}</span>
          <span>
            <button type="button" data-dev-page="${Math.max(1, (pagination.page || 1) - 1)}" ${pagination.page <= 1 ? "disabled" : ""}>Previous</button>
            <button type="button" data-dev-page="${Math.min(pagination.pages || 1, (pagination.page || 1) + 1)}" ${pagination.page >= pagination.pages ? "disabled" : ""}>Next</button>
          </span>
        </div>
      </section>
    </div>`;
}

function render() {
  if (!D.auth) {
    root.innerHTML = '<div class="dev-loading">Checking access…</div>';
    return;
  }
  if (!can("developer.view")) {
    root.innerHTML = `<div class="dev-denied"><span class="dev-eye">Developer access</span><h1>Access denied</h1><p>Your account does not have developer monitoring permission. Ask a Global Admin to grant the Developer role or developer.view permission.</p><a href="/workspace">Return to workspace</a></div>`;
    return;
  }
  root.innerHTML = `
    <div class="dev-title-row">
      <div>
        <div class="dev-eye">SYSTEM · DEVELOPER</div>
        <h1>Error monitoring</h1>
        <p class="dev-sub">Tenant-scoped application errors, runtime health, and operational signals.</p>
      </div>
      <div class="dev-title-actions">
        <label for="devPeriod">Dashboard period</label>
        <select id="devPeriod">
          ${[["24h", "Last 24 hours"], ["7d", "Last 7 days"], ["30d", "Last 30 days"]].map(([value, label]) => `<option value="${value}" ${D.filters.period === value ? "selected" : ""}>${label}</option>`).join("")}
        </select>
        <span class="dev-pill">${esc(D.auth.user?.email || "Signed in")}</span>
      </div>
    </div>
    ${summaryView()}
    <div class="dev-columns">
      ${healthView()}
      <section class="dev-panel">
        <div class="dev-panel-head"><h2>Notes</h2></div>
        <div class="dev-panel-body">
          <p class="dev-sub">Error reports are redacted before storage. Resolve or ignore errors after investigation. Resolved and ignored errors older than the retention window are pruned automatically.</p>
        </div>
      </section>
    </div>
    ${errorsView()}`;
}

function queryString() {
  const params = new URLSearchParams({
    status: D.filters.status,
    source: D.filters.source,
    severity: D.filters.severity,
    q: D.filters.q,
    date_from: D.filters.date_from,
    date_to: D.filters.date_to,
    page: String(D.pagination.page || 1),
    page_size: String(D.pagination.page_size || 25),
  });
  return params.toString();
}

async function loadAll() {
  if (!D.auth || !can("developer.view")) {
    render();
    return;
  }
  try {
    const [summary, health, errors] = await Promise.all([
      devApi(`/api/developer/summary?period=${encodeURIComponent(D.filters.period)}`),
      devApi("/api/developer/health"),
      devApi(`/api/developer/errors?${queryString()}`),
    ]);
    D.summary = summary;
    D.health = health;
    D.errors = errors.errors || [];
    D.pagination = errors.pagination || D.pagination;
    render();
  } catch (error) {
    if (error.status === 401) {
      window.location.assign("/login");
      return;
    }
    if (error.status === 403) {
      D.auth.permissions = [];
      render();
      return;
    }
    toast(error.message);
    render();
  }
}

function detailView(record) {
  const canManage = can("developer.manage");
  const canDelete = can("developer.delete");
  return `
    <div class="dev-detail-section">
      <div class="dev-eye">${esc(record.source)} · ${esc(record.severity)} · ${esc(record.status)}</div>
      <h2 style="margin-top:7px;overflow-wrap:anywhere">${esc(record.message)}</h2>
    </div>
    <div class="dev-detail-section">
      <dl class="dev-kv">
        <dt>Type</dt><dd>${esc(record.error_type || "—")}</dd>
        <dt>Route</dt><dd>${esc(record.method || "")} ${esc(record.route || "—")}</dd>
        <dt>Status code</dt><dd>${esc(record.status_code ?? "—")}</dd>
        <dt>Occurrences</dt><dd>${record.occurrence_count}</dd>
        <dt>First seen</dt><dd>${fmtDate(record.first_seen_at)}</dd>
        <dt>Last seen</dt><dd>${fmtDate(record.last_seen_at)}</dd>
        <dt>Project</dt><dd>${esc(record.project_id ?? "—")}</dd>
        <dt>Tenant</dt><dd>${esc(record.tenant_id ?? "global")}</dd>
        <dt>Request ID</dt><dd>${esc(record.request_id || "—")}</dd>
        <dt>Correlation ID</dt><dd>${esc(record.correlation_id || "—")}</dd>
      </dl>
    </div>
    <div class="dev-detail-section">
      <h3>Stack trace</h3>
      <pre class="dev-code">${esc(record.stack_trace || "No stack trace provided.")}</pre>
    </div>
    <div class="dev-detail-section">
      <h3>Metadata</h3>
      <pre class="dev-code">${esc(JSON.stringify(record.metadata || {}, null, 2))}</pre>
    </div>
    <div class="dev-detail-section">
      <div class="dev-field">
        <label for="devDetailStatus">Status</label>
        <select id="devDetailStatus" ${canManage ? "" : "disabled"}>
          ${["open", "acknowledged", "resolved", "ignored"].map(value => `<option value="${value}" ${record.status === value ? "selected" : ""}>${value}</option>`).join("")}
        </select>
      </div>
      <div class="dev-field">
        <label for="devDetailNotes">Notes</label>
        <textarea id="devDetailNotes" maxlength="4000" ${canManage ? "" : "disabled"}>${esc(record.notes || "")}</textarea>
      </div>
      <div class="dev-actions">
        ${canManage ? '<button type="button" class="primary" id="devSaveError">Save changes</button>' : ""}
        ${canDelete ? '<button type="button" class="danger" id="devDeleteError">Delete error</button>' : ""}
        <button type="button" data-dev-close>Close</button>
      </div>
    </div>`;
}

async function openError(id) {
  try {
    const result = await devApi(`/api/developer/errors/${encodeURIComponent(id)}`);
    D.detail = result.record;
    drawerBody.innerHTML = detailView(D.detail);
    drawer.hidden = false;
  } catch (error) {
    toast(error.message);
  }
}

async function saveError() {
  if (!D.detail) return;
  const status = document.getElementById("devDetailStatus")?.value;
  const notes = document.getElementById("devDetailNotes")?.value || "";
  try {
    const result = await devApi(`/api/developer/errors/${encodeURIComponent(D.detail.id)}`, {
      method: "PATCH",
      body: JSON.stringify({ status, notes }),
    });
    D.detail = result.record;
    drawerBody.innerHTML = detailView(D.detail);
    toast("Error updated");
    await loadAll();
  } catch (error) {
    toast(error.message);
  }
}

async function deleteError() {
  if (!D.detail) return;
  if (!confirm("Delete this developer error record? This cannot be undone.")) return;
  try {
    await devApi(`/api/developer/errors/${encodeURIComponent(D.detail.id)}`, { method: "DELETE" });
    drawer.hidden = true;
    D.detail = null;
    toast("Error deleted");
    await loadAll();
  } catch (error) {
    toast(error.message);
  }
}

document.getElementById("devRefresh")?.addEventListener("click", () => loadAll());
document.getElementById("devSignOut")?.addEventListener("click", async () => {
  try {
    await devApi("/api/auth/logout", { method: "POST", body: "{}" });
  } catch (error) {
    console.warn(error);
  }
  window.location.assign("/login");
});

root.addEventListener("click", event => {
  const pageButton = event.target.closest("[data-dev-page]");
  if (pageButton) {
    D.pagination.page = Number(pageButton.dataset.devPage) || 1;
    loadAll();
    return;
  }
  const errorRow = event.target.closest("[data-open-error]");
  if (errorRow) openError(errorRow.dataset.openError);
});

root.addEventListener("change", event => {
  if (["devStatus", "devSource", "devSeverity"].includes(event.target.id)) {
    const key = event.target.id === "devStatus" ? "status"
      : event.target.id === "devSource" ? "source" : "severity";
    D.filters[key] = event.target.value;
    D.pagination.page = 1;
    loadAll();
  }
  if (["devDateFrom", "devDateTo"].includes(event.target.id)) {
    D.filters[event.target.id === "devDateFrom" ? "date_from" : "date_to"] = event.target.value;
    D.pagination.page = 1;
    loadAll();
  }
  if (event.target.id === "devPeriod") {
    D.filters.period = event.target.value;
    loadAll();
  }
  if (event.target.id === "devSearch") {
    D.filters.q = event.target.value.trim();
    D.pagination.page = 1;
    loadAll();
  }
});

drawer.addEventListener("click", event => {
  if (event.target.closest("[data-dev-close]")) {
    drawer.hidden = true;
    D.detail = null;
    return;
  }
  if (event.target.id === "devSaveError") saveError();
  if (event.target.id === "devDeleteError") deleteError();
});

(async function init() {
  try {
    D.auth = await devApi("/api/auth/status");
  } catch (error) {
    toast(error.message);
    return;
  }
  if (!D.auth.authenticated) {
    window.location.assign("/login");
    return;
  }
  await loadAll();
})();
