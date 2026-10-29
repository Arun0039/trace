const PO = {
  page: "overview",
  tenant: null,
  pageNumber: 1,
  pageSize: 25,
  totalPages: 1,
  q: "",
  status: "",
  plan: "",
  sort: "created_at",
  direction: "desc",
  usageFrom: "",
  usageTo: "",
  usageTenant: "",
  storageQ: "",
  errorPage: 1,
  errorPages: 1,
  auditPage: 1,
  auditPages: 1,
  errorFilters: { severity: "", status: "", tenant_id: "", date_from: "", date_to: "" },
  tenants: [],
  plans: [],
};

const API_BASE_URL = String(window.QA_API_BASE_URL || "").replace(/\/+$/, "");
const poRoot = document.getElementById("poRoot");
const poNav = document.getElementById("poNav");
const poToast = document.getElementById("poToast");

function poEsc(value) {
  return String(value ?? "").replace(/[&<>"']/g, char => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[char]);
}

function poDate(value) {
  if (!value) return "—";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? poEsc(value) : parsed.toLocaleString();
}

function poBytes(value) {
  const bytes = Number(value) || 0;
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let amount = bytes / 1024;
  let unit = 0;
  while (amount >= 1024 && unit < units.length - 1) {
    amount /= 1024;
    unit += 1;
  }
  return `${amount.toFixed(amount >= 10 ? 0 : 1)} ${units[unit]}`;
}

function poToastMessage(message, isError = false) {
  poToast.textContent = message;
  poToast.classList.toggle("error", isError);
  poToast.classList.add("show");
  clearTimeout(poToast._timer);
  poToast._timer = setTimeout(() => poToast.classList.remove("show"), 3200);
}

async function poApi(path, options = {}) {
  let response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      credentials: "include",
      ...options,
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    });
  } catch {
    throw new Error("Cannot reach the TRACE server.");
  }
  const data = (response.headers.get("content-type") || "").includes("application/json")
    ? await response.json() : null;
  if (!response.ok) {
    const error = new Error(data?.error || `Request failed (${response.status}).`);
    error.status = response.status;
    throw error;
  }
  return data;
}

function poRequest(method, values = {}) {
  return poApi("/api/product-owner" + method, {
    method: "POST",
    body: JSON.stringify(values),
  });
}

function poMetric(label, value, note = "") {
  return `<div class="po-card"><span>${poEsc(label)}</span><strong>${poEsc(value)}</strong><small>${poEsc(note)}</small></div>`;
}

function poTable(headers, rows, empty = "No records found.") {
  return `<div class="dev-panel"><div class="dev-table-wrap"><table class="dev-table po-table">
    <thead><tr>${headers.map(header => `<th>${poEsc(header)}</th>`).join("")}</tr></thead>
    <tbody>${rows || `<tr><td colspan="${headers.length}" class="po-empty">${poEsc(empty)}</td></tr>`}</tbody>
  </table></div></div>`;
}

function poTitle(title, subtitle = "") {
  return `<div class="dev-title-row"><div><div class="dev-eye">TRACE · Product Owner</div>
    <h1>${poEsc(title)}</h1><p class="dev-sub">${poEsc(subtitle)}</p></div></div>`;
}

async function poLoadOverview() {
  const data = await poApi("/api/product-owner/dashboard");
  return `${poTitle("Operations overview", "System-level account, workspace, trial, storage, and error monitoring.")}
    <div class="po-cards">
      ${poMetric("Registered users", data.users)}
      ${poMetric("Tenants / workspaces", data.tenants, `${data.active_tenants} active · ${data.suspended_tenants} suspended`)}
      ${poMetric("Trials", data.trials, `${data.expired_trials} expired · ${data.trial_duration_days} days configured`)}
      ${poMetric("Active subscriptions", data.active_plans)}
      ${poMetric("Open errors", data.open_errors, `${data.error_occurrences} logged occurrences`)}
      ${poMetric("Measured storage", poBytes(data.storage.total_bytes), `Files ${poBytes(data.storage.filesystem_bytes)} · DB ${poBytes(data.storage.database_bytes)}`)}
    </div>
    <p class="po-notice">Storage values are cached measurements/estimates last calculated ${poDate(data.storage.computed_at)}. API request analytics are process-local and reset on restart.</p>`;
}

function poTenantRows(items, pageName) {
  PO.tenants = items;
  return items.map(row => `<tr data-tenant="${poEsc(row.id)}">
    <td><strong>${poEsc(row.email || "—")}</strong><br><span class="dev-error-meta">User ${poEsc(row.public_user_id || "—")}</span></td>
    <td>${poEsc(row.name)}<br><span class="dev-error-meta">Tenant ${poEsc(row.public_tenant_id || "—")}<br>Workspace ${poEsc(row.public_workspace_id || "—")}</span></td>
    <td><span class="po-pill">${poEsc(row.status)}</span><br><small>${poEsc(row.subscription_status || "none")} · ${poEsc(row.plan || "unassigned")}</small></td>
    <td>${poDate(row.trial_ends_at)}</td>
    <td>${poBytes(row.storage_bytes)}<br><small>${poDate(row.storage_calculated_at)}</small></td>
    <td>${poDate(row.signup_at)}<br><small>Last login ${poDate(row.last_login_at)}<br>
      Activity ${poDate(row.recent_activity)} · ${Number(row.recent_error_count) || 0} errors</small></td>
    <td class="po-inline"><button class="po-button" data-open-tenant="${poEsc(row.id)}">Details</button>
      ${pageName === "plans" ? `<button class="po-button primary" data-assign-plan="${poEsc(row.id)}">Assign plan</button>` : ""}</td>
  </tr>`).join("");
}

async function poLoadTenants(pageName = "tenants") {
  const params = new URLSearchParams({
    page: PO.pageNumber, page_size: PO.pageSize, q: PO.q,
    status: PO.status, plan: PO.plan, sort: PO.sort, direction: PO.direction,
  });
  const data = await poApi(`/api/product-owner/tenants?${params}`);
  PO.totalPages = Math.max(1, data.pagination.pages);
  const title = pageName === "plans" ? "Trial and plan management" : "User and tenant directory";
  const form = `<form id="poTenantFilters" class="po-toolbar">
    <input type="search" name="q" value="${poEsc(PO.q)}" placeholder="Search email, user, tenant, workspace">
    <select name="status"><option value="">All workspace statuses</option>
      <option value="Active" ${PO.status === "Active" ? "selected" : ""}>Active</option>
      <option value="Inactive" ${PO.status === "Inactive" ? "selected" : ""}>Suspended</option></select>
    <select name="plan"><option value="">All plans</option>${PO.plans.map(plan =>
      `<option value="${poEsc(plan.code)}" ${PO.plan === plan.code ? "selected" : ""}>${poEsc(plan.name)}</option>`
    ).join("")}</select>
    <select name="sort"><option value="created_at" ${PO.sort === "created_at" ? "selected" : ""}>Signup date</option>
      <option value="last_login_at" ${PO.sort === "last_login_at" ? "selected" : ""}>Last login</option>
      <option value="email" ${PO.sort === "email" ? "selected" : ""}>Email</option>
      <option value="workspace" ${PO.sort === "workspace" ? "selected" : ""}>Workspace</option>
      <option value="status" ${PO.sort === "status" ? "selected" : ""}>Workspace status</option>
      <option value="trial_ends_at" ${PO.sort === "trial_ends_at" ? "selected" : ""}>Trial expiry</option>
      <option value="storage_bytes" ${PO.sort === "storage_bytes" ? "selected" : ""}>Storage use</option></select>
    <select name="direction"><option value="desc" ${PO.direction === "desc" ? "selected" : ""}>Descending</option>
      <option value="asc" ${PO.direction === "asc" ? "selected" : ""}>Ascending</option></select>
    <button class="po-button" type="submit">Search</button>
  </form>`;
  const body = poTenantRows(data.items, pageName);
  return `${poTitle(title, pageName === "plans" ? "Review trials, assign manual plans, and reactivate eligible workspaces." : "Searchable and paginated tenant directory; email is a display attribute, not an identifier.")}
    ${form}${poTable(["User", "Tenant / workspace", "Access / subscription", "Trial expiry", "Storage", "Signup / last login", "Actions"], body)}
    <div class="po-pagination"><span>${data.pagination.total} tenants · Page ${data.pagination.page} of ${PO.totalPages}</span>
    <div class="po-inline"><button class="po-button" data-page-back ${PO.pageNumber <= 1 ? "disabled" : ""}>Previous</button>
    <button class="po-button" data-page-next ${PO.pageNumber >= PO.totalPages ? "disabled" : ""}>Next</button></div></div>`;
}

function poKeyValue(obj, pairs) {
  return `<dl class="dev-kv">${pairs.map(([label, key, formatter]) =>
    `<dt>${poEsc(label)}</dt><dd>${poEsc(formatter ? formatter(obj[key]) : obj[key] || "—")}</dd>`
  ).join("")}</dl>`;
}

function poInventoryRows(items) {
  return items.map(item => `<tr>
    <td><input type="checkbox" name="storage_item" value="${poEsc(item.id)}" ${item.deletable ? "" : "disabled"} aria-label="Select ${poEsc(item.name)}"></td>
    <td>${poEsc(item.name)}<br><small>${poEsc(item.path)}</small></td>
    <td>${poEsc(item.kind)} · ${poEsc(item.source)}</td>
    <td>${poBytes(item.size)} · ${Number(item.file_count) || 0} files</td>
    <td>${item.deletable ? "Eligible" : "Protected / dry-run only"}</td>
  </tr>`).join("");
}

async function poLoadTenant(id) {
  const data = await poApi(`/api/product-owner/tenants/${encodeURIComponent(id)}`);
  PO.tenant = data;
  const tenant = data.tenant;
  return `${poTitle(tenant.name, "Workspace details, lifecycle, scoped usage, and audited operations.")}
    <div class="po-detail-grid">
      <section class="po-panel"><h2>Identity and lifecycle</h2>${poKeyValue(tenant, [
        ["User ID", "public_user_id"], ["Email", "email"], ["Tenant ID", "public_tenant_id"],
        ["Workspace ID", "public_workspace_id"], ["Workspace status", "status"],
        ["Subscription status", "subscription_status"], ["Assigned plan", "plan"],
        ["Signup", "signup_at"], ["Last login", "last_login_at"],
        ["Trial started", "trial_started_at"], ["Trial expires", "trial_ends_at"],
        ["Suspension reason", "suspension_reason"],
      ])}</section>
      <section class="po-panel"><h2>Usage and storage</h2>
        <div class="po-cards">
          ${poMetric("Projects", data.counts.projects)}
          ${poMetric("Requirements", data.counts.requirements)}
          ${poMetric("Test cases", data.counts.test_cases)}
          ${poMetric("Test runs", data.counts.test_runs)}
          ${poMetric("Defects", data.counts.defects)}
          ${poMetric("Total", poBytes(data.storage.total_bytes), `DB ${poBytes(data.storage.database_bytes)} · Files ${poBytes(data.storage.filesystem_bytes)}`)}
        </div><p class="po-notice">${poEsc(data.storage.database_estimate_method || "")}</p></section>
    </div>
    <section class="po-panel"><h2>Authorized workspace operations</h2>
      <div class="po-toolbar"><button class="po-button primary" data-action="assign">Assign plan</button>
        <button class="po-button" data-action="reactivate">Reactivate</button>
        <button class="po-button danger" data-action="suspend">Suspend</button>
        <button class="po-button" data-page-storage="${poEsc(tenant.id)}">Manage storage</button></div>
      <p class="po-notice">Every operation requires an explicit confirmation and reason and is written to the administrative audit history.</p>
    </section>
    <section class="po-panel" style="margin-top:14px"><h2>Recent lifecycle activity</h2>
      ${poTable(["Actor", "Action", "Target", "Details", "Timestamp"],
        data.lifecycle_events.map(event => `<tr><td>${poEsc(event.actor_label || "System")}</td>
          <td>${poEsc(event.action)}</td><td>${poEsc(event.target_type)} ${poEsc(event.target_id)}</td>
          <td class="po-pre">${poEsc(event.details_json)}</td><td>${poDate(event.created_at)}</td></tr>`).join(""))}
    </section>
    <section class="po-panel" style="margin-top:14px"><h2>Recent application errors</h2>
      ${poTable(["Severity", "Status", "Error", "Endpoint", "Occurrences", "Last seen"],
        data.recent_errors.map(error => `<tr><td>${poEsc(error.severity)}</td>
          <td>${poEsc(error.status)}</td><td>${poEsc(error.error_type)}<br>${poEsc(error.message)}</td>
          <td>${poEsc(error.route)}</td><td>${poEsc(error.occurrence_count)}</td>
          <td>${poDate(error.last_seen_at)}</td></tr>`).join(""))}
    </section>
    <section class="po-panel" style="margin-top:14px"><h2>Storage usage history</h2>
      ${poTable(["Calculated at", "Total", "Filesystem", "Database"],
        data.storage_history.map(snapshot => `<tr><td>${poDate(snapshot.calculated_at)}</td>
          <td>${poBytes(snapshot.total_bytes)}</td><td>${poBytes(snapshot.filesystem_bytes)}</td>
          <td>${poBytes(snapshot.database_bytes)}</td></tr>`).join(""),
        "Storage history is recorded when the tenant inventory is recalculated.")}
    </section>`;
}

async function poLoadStorage(id = "") {
  if (!id) {
    const params = new URLSearchParams({
      page: 1, page_size: 100, sort: "created_at", direction: "desc", q: PO.storageQ,
    });
    const data = await poApi(`/api/product-owner/tenants?${params}`);
    const options = data.items.map(item => `<option value="${poEsc(item.id)}">${poEsc(item.name)} · ${poEsc(item.public_tenant_id)}</option>`).join("");
    const selected = PO.tenant?.tenant?.id || data.items[0]?.id;
    if (!selected) return `${poTitle("Storage manager")}<div class="po-empty">No tenant storage is available.</div>`;
    return `${poTitle("Storage usage and cleanup", "Inventory is tenant-scoped; each storage measurement distinguishes exact bytes from database estimates.")}
      <form id="poStorageFilters" class="po-toolbar">
        <input type="search" name="q" value="${poEsc(PO.storageQ)}" placeholder="Find tenant by email, name, or ID">
        <button class="po-button" type="submit">Find tenant</button>
        <label class="po-field">Tenant<select id="poStorageTenant">${options}</select></label>
      </form>
      <div id="poStorageContent">${await poLoadStorage(String(selected))}</div>`;
  }
  const detail = await poApi(`/api/product-owner/tenants/${encodeURIComponent(id)}`);
  PO.tenant = detail;
  const items = detail.storage_items || [];
  return `<div class="po-cards">
      ${poMetric("Measured total", poBytes(detail.storage.total_bytes))}
      ${poMetric("Filesystem", poBytes(detail.storage.filesystem_bytes), "Exact file bytes where available")}
      ${poMetric("Database", poBytes(detail.storage.database_bytes), "Includes record-size estimates")}
      ${poMetric("Inventory items", detail.storage.item_count, `Calculated ${poDate(detail.storage.computed_at)}`)}
    </div><p class="po-notice">${poEsc(detail.storage.database_estimate_method || "")}</p>
    <form id="poStorageDelete" data-tenant-id="${poEsc(id)}">
      ${poTable(["Select", "Item", "Category", "Usage", "Cleanup eligibility"], poInventoryRows(items), "No managed storage items were found.")}
      <div class="po-toolbar"><button class="po-button danger" type="submit" ${items.some(item => item.deletable) ? "" : "disabled"}>Preview and delete selected</button>
      <span class="po-notice">System config, audit data, shared data, and protected record groups are not exposed.</span></div>
    </form>`;
}

async function poLoadUsage() {
  const date = new Date();
  const start = new Date(date.getTime() - 29 * 86400000).toISOString().slice(0, 10);
  const dateFrom = PO.usageFrom || start;
  const dateTo = PO.usageTo || date.toISOString().slice(0, 10);
  const params = new URLSearchParams({ date_from: dateFrom, date_to: dateTo });
  if (PO.usageTenant) params.set("tenant_id", PO.usageTenant);
  const data = await poApi(`/api/product-owner/usage?${params}`);
  const tenantResponse = await poApi("/api/product-owner/tenants?page=1&page_size=100&sort=created_at&direction=desc");
  return `${poTitle("Product usage analytics", "Tenant-level usage events and record counts; request counts are process-local.")}
    <form id="poUsageFilters" class="po-toolbar">
      <label class="po-field">From<input type="date" name="date_from" value="${poEsc(dateFrom)}"></label>
      <label class="po-field">To<input type="date" name="date_to" value="${poEsc(dateTo)}"></label>
      <label class="po-field">Tenant drill-down<select name="tenant_id"><option value="">All tenants</option>
        ${tenantResponse.items.map(tenant => `<option value="${poEsc(tenant.id)}" ${PO.usageTenant === String(tenant.id) ? "selected" : ""}>${poEsc(tenant.name)} · ${poEsc(tenant.public_tenant_id)}</option>`).join("")}</select></label>
      <button class="po-button" type="submit">Apply range</button>
    </form>
    <div class="po-cards">${poMetric("Users", data.registered_users)}${poMetric("Tenants", data.registered_tenants)}
      ${poMetric("Daily active workspaces", data.daily_active_workspaces)}${poMetric("Monthly active workspaces", data.monthly_active_workspaces)}
      ${poMetric("Test cases created", data.test_cases_generated)}${poMetric("Test runs", data.test_runs)}
      ${poMetric("API requests", data.api_requests, `${data.api_failures} failures · ${data.api_failure_rate_percent}%`)}
      ${poMetric("Trial conversions", data.trial_conversions)}${poMetric("Suspensions", data.suspensions)}</div>
    <p class="po-notice">${poEsc(data.api_requests_note)}</p>
    ${poTable(["Event", "Events", "Affected workspaces"], data.events.map(event =>
      `<tr><td>${poEsc(event.event_type)}</td><td>${poEsc(event.events)}</td><td>${poEsc(event.workspaces)}</td></tr>`
    ).join(""))}
    ${poTable(["Storage snapshot date", "Total", "Filesystem", "Database"], data.storage_growth.map(snapshot =>
      `<tr><td>${poEsc(snapshot.day)}</td><td>${poBytes(snapshot.total_bytes)}</td>
        <td>${poBytes(snapshot.filesystem_bytes)}</td><td>${poBytes(snapshot.database_bytes)}</td></tr>`
    ).join(""), "No storage snapshots were calculated in this date range.")}`;
}

async function poLoadErrors() {
  const params = new URLSearchParams({
    page: PO.errorPage, page_size: 50, ...PO.errorFilters,
  });
  const data = await poApi(`/api/product-owner/errors?${params}`);
  PO.errorPages = Math.max(1, data.pagination.pages);
  let health = "Health status unavailable.";
  try {
    const state = await poApi("/api/developer/health");
    health = JSON.stringify(state, null, 2);
  } catch (error) {
    health = error.message;
  }
  return `${poTitle("Application errors and health", "Redacted, tenant-aware error records; health probes do not expose customer content.")}
    <form id="poErrorFilters" class="po-toolbar">
      <label class="po-field">From<input type="date" name="date_from" value="${poEsc(PO.errorFilters.date_from)}"></label>
      <label class="po-field">To<input type="date" name="date_to" value="${poEsc(PO.errorFilters.date_to)}"></label>
      <label class="po-field">Severity<select name="severity"><option value="">All</option>
        ${["debug","info","warning","error","critical"].map(value => `<option ${PO.errorFilters.severity === value ? "selected" : ""}>${value}</option>`).join("")}</select></label>
      <label class="po-field">Resolution<select name="status"><option value="">All</option>
        ${["open","acknowledged","resolved","ignored"].map(value => `<option ${PO.errorFilters.status === value ? "selected" : ""}>${value}</option>`).join("")}</select></label>
      <label class="po-field">Tenant ID<input name="tenant_id" inputmode="numeric" value="${poEsc(PO.errorFilters.tenant_id)}" placeholder="All tenants"></label>
      <button class="po-button" type="submit">Filter</button>
    </form>
    <section class="po-panel"><h2>Service health</h2><pre class="po-pre">${poEsc(health)}</pre></section>
    <section class="po-panel" style="margin-top:14px"><h2>Application errors</h2>
      ${poTable(["Severity", "Status", "Tenant", "Error", "Endpoint", "Occurrences", "Last seen"],
        data.items.map(item => `<tr><td>${poEsc(item.severity)}</td><td>${poEsc(item.status)}</td>
          <td>${poEsc(item.tenant_id ?? "system")}</td>
          <td><strong>${poEsc(item.error_type)}</strong><br>${poEsc(item.message)}
            <details><summary>Redacted stack</summary><pre class="po-pre">${poEsc(item.stack_trace)}</pre></details></td>
          <td>${poEsc(item.method)} ${poEsc(item.route)} · ${poEsc(item.status_code || "")}<br><small>Request ${poEsc(item.request_id)}</small></td>
          <td>${poEsc(item.occurrence_count)}</td><td>${poDate(item.last_seen_at)}</td></tr>`).join(""))}
      <div class="po-pagination"><span>${data.pagination.total} records · Page ${data.pagination.page} of ${PO.errorPages} · retention QA_DEVELOPER_ERROR_RETENTION_DAYS</span>
        <div class="po-inline"><button class="po-button" data-error-back ${PO.errorPage <= 1 ? "disabled" : ""}>Previous</button>
        <button class="po-button" data-error-next ${PO.errorPage >= PO.errorPages ? "disabled" : ""}>Next</button></div></div>
    </section>`;
}

async function poLoadAudit() {
  const params = new URLSearchParams({ page: PO.auditPage, page_size: 50 });
  const data = await poApi(`/api/product-owner/audit?${params}`);
  PO.auditPages = Math.max(1, data.pagination.pages);
  return `${poTitle("Administrative audit history", "Append-only operational history across Product Owner and workspace lifecycle actions.")}
    ${poTable(["Actor", "Action", "Tenant", "Reason", "Outcome", "Details", "Timestamp"],
      data.items.map(item => `<tr><td>${poEsc(item.actor_label)}</td><td>${poEsc(item.action)}</td>
        <td>${poEsc(item.target_tenant_id ?? "—")}</td><td>${poEsc(item.reason || "—")}</td>
        <td>${poEsc(item.outcome)}</td><td class="po-pre">${poEsc(item.details_json)}</td>
        <td>${poDate(item.created_at)}</td></tr>`).join(""))}
    <div class="po-pagination"><span>${data.pagination.total} entries · Page ${data.pagination.page} of ${PO.auditPages}</span>
      <div class="po-inline"><button class="po-button" data-audit-back ${PO.auditPage <= 1 ? "disabled" : ""}>Previous</button>
      <button class="po-button" data-audit-next ${PO.auditPage >= PO.auditPages ? "disabled" : ""}>Next</button></div></div>`;
}

async function poLoadPlans() {
  PO.plans = await poApi("/api/product-owner/plans");
  return poLoadTenants("plans");
}

async function poRender() {
  poNav.hidden = true;
  poRoot.innerHTML = '<div class="dev-loading">Loading Product Owner operations…</div>';
  try {
    await poApi("/api/product-owner/session");
    poNav.hidden = false;
    document.getElementById("poIdentity").textContent = "Authenticated Product Owner";
    poNav.querySelectorAll("[data-page]").forEach(button => {
      if (button.dataset.page === PO.page) button.setAttribute("aria-current", "page");
      else button.removeAttribute("aria-current");
    });
    const view = {
      overview: poLoadOverview,
      tenants: () => poLoadTenants("tenants"),
      plans: poLoadPlans,
      storage: () => poLoadStorage(),
      usage: poLoadUsage,
      errors: poLoadErrors,
      audit: poLoadAudit,
    }[PO.page];
    poRoot.innerHTML = await view();
  } catch (error) {
    if (error.status === 401 || error.status === 403) {
      poNav.hidden = true;
      document.getElementById("poIdentity").textContent = "";
      poRoot.innerHTML = `<section class="po-login"><div class="dev-eye">Restricted administration</div>
        <h1>Product Owner sign in</h1>
        <p class="dev-sub">Use the configured bootstrap administrator password. Workspace accounts are not accepted.</p>
        <form id="poLogin"><label class="po-field">Administrator password
          <input name="password" type="password" autocomplete="current-password" required></label>
          <button class="po-button primary" type="submit">Sign in</button>
          <div class="po-error" id="poLoginError" role="alert"></div></form>
        <p class="po-notice">Requires QA_ADMIN_PASSWORD configured on the backend. This route is not linked from customer navigation.</p>
      </section>`;
    } else {
      poRoot.innerHTML = `<section class="po-panel po-error">${poEsc(error.message)}</section>`;
    }
  }
}

async function poOperationalAction(id, action) {
  const promptText = action === "suspend"
    ? "Suspend this workspace? Existing customer data will be preserved."
    : action === "reactivate" ? "Reactivate this workspace?" : "Assign a plan to this workspace?";
  if (!window.confirm(promptText)) return;
  let plan;
  if (action === "assign") {
    if (!PO.plans.length) PO.plans = await poApi("/api/product-owner/plans");
    plan = window.prompt(`Plan code (${PO.plans.map(item => item.code).join(", ")})`);
    if (!PO.plans.some(item => item.code === plan)) return;
    action = "plan";
  }
  const reason = window.prompt("Enter the operational reason (at least five characters):");
  if (!reason || reason.trim().length < 5) return;
  await poRequest(`/tenants/${encodeURIComponent(id)}/${action}`, {
    confirmed: true, reason: reason.trim(), ...(plan ? { plan } : {}),
  });
  poToastMessage("Operation completed and audited.");
  PO.page = "tenant";
  await poRenderTenant(id);
}

async function poRenderTenant(id) {
  poRoot.innerHTML = '<div class="dev-loading">Loading tenant…</div>';
  try {
    const html = await poLoadTenant(id);
    poRoot.innerHTML = html;
    PO.page = "tenant";
    poNav.querySelectorAll("[data-page]").forEach(button => button.removeAttribute("aria-current"));
  } catch (error) {
    poRoot.innerHTML = `<section class="po-panel po-error">${poEsc(error.message)}</section>`;
  }
}

poNav.addEventListener("click", async event => {
  const button = event.target.closest("[data-page]");
  if (!button) return;
  PO.page = button.dataset.page;
  PO.pageNumber = 1;
  await poRender();
});

poRoot.addEventListener("submit", async event => {
  event.preventDefault();
  if (event.target.id === "poLogin") {
    const password = new FormData(event.target).get("password");
    try {
      await poApi("/api/auth/login", { method: "POST", body: JSON.stringify({ password }) });
      await poRender();
    } catch (error) {
      const message = document.getElementById("poLoginError");
      if (message) message.textContent = error.message;
    }
    return;
  }
  if (event.target.id === "poTenantFilters") {
    const data = new FormData(event.target);
    PO.q = String(data.get("q") || "").trim();
    PO.status = String(data.get("status") || "");
    PO.plan = String(data.get("plan") || "");
    PO.sort = String(data.get("sort") || "created_at");
    PO.direction = String(data.get("direction") || "desc");
    PO.pageNumber = 1;
    await poRender();
    return;
  }
  if (event.target.id === "poUsageFilters") {
    const data = new FormData(event.target);
    PO.usageFrom = String(data.get("date_from") || "");
    PO.usageTo = String(data.get("date_to") || "");
    PO.usageTenant = String(data.get("tenant_id") || "");
    await poRender();
    return;
  }
  if (event.target.id === "poErrorFilters") {
    const data = new FormData(event.target);
    PO.errorFilters = Object.fromEntries(
      Object.keys(PO.errorFilters).map(key => [key, String(data.get(key) || "")])
    );
    await poRender();
    return;
  }
  if (event.target.id === "poStorageFilters") {
    const data = new FormData(event.target);
    PO.storageQ = String(data.get("q") || "").trim();
    PO.tenant = null;
    await poRender();
    return;
  }
  if (event.target.id === "poStorageDelete") {
    const form = event.target;
    const tenantId = form.dataset.tenantId;
    const selected = [...form.querySelectorAll('input[name="storage_item"]:checked')].map(item => item.value);
    if (!selected.length) return;
    const tenant = PO.tenant?.tenant;
    if (!tenant || !window.confirm(`Review ${selected.length} tenant-scoped storage item(s) for deletion?`)) return;
    const confirmation = window.prompt(`Type exactly: DELETE ${tenant.public_tenant_id}`);
    if (confirmation !== `DELETE ${tenant.public_tenant_id}`) return;
    const reason = window.prompt("Enter the reason for this cleanup:");
    if (!reason || reason.trim().length < 5) return;
    try {
      const result = await poRequest(`/tenants/${encodeURIComponent(tenantId)}/storage/delete`, {
        item_ids: selected, confirmation, reason: reason.trim(),
      });
      poToastMessage(`${result.deleted_count} item(s) deleted; ${result.skipped.length} skipped.`);
      await poRender();
    } catch (error) {
      poToastMessage(error.message, true);
    }
  }
});

poRoot.addEventListener("click", async event => {
  const openButton = event.target.closest("[data-open-tenant]");
  if (openButton) return poRenderTenant(openButton.dataset.openTenant);
  const row = event.target.closest("tr[data-tenant]");
  if (row && !event.target.closest("button,input,select")) {
    return poRenderTenant(row.dataset.tenant);
  }
  if (event.target.closest("[data-page-back]")) {
    PO.pageNumber = Math.max(1, PO.pageNumber - 1);
    return poRender();
  }
  if (event.target.closest("[data-page-next]")) {
    PO.pageNumber += 1;
    return poRender();
  }
  if (event.target.closest("[data-error-back]")) {
    PO.errorPage = Math.max(1, PO.errorPage - 1);
    return poRender();
  }
  if (event.target.closest("[data-error-next]")) {
    PO.errorPage = Math.min(PO.errorPages, PO.errorPage + 1);
    return poRender();
  }
  if (event.target.closest("[data-audit-back]")) {
    PO.auditPage = Math.max(1, PO.auditPage - 1);
    return poRender();
  }
  if (event.target.closest("[data-audit-next]")) {
    PO.auditPage = Math.min(PO.auditPages, PO.auditPage + 1);
    return poRender();
  }
  const assign = event.target.closest("[data-assign-plan]");
  if (assign) {
    try { await poOperationalAction(assign.dataset.assignPlan, "assign"); }
    catch (error) { poToastMessage(error.message, true); }
    return;
  }
  const storageButton = event.target.closest("[data-page-storage]");
  if (storageButton) {
    PO.page = "storage";
    await poRender();
    const select = document.getElementById("poStorageTenant");
    if (select) {
      select.value = storageButton.dataset.pageStorage;
      document.getElementById("poStorageContent").innerHTML =
        await poLoadStorage(select.value);
    }
    return;
  }
  const action = event.target.closest("[data-action]");
  if (action && PO.tenant) {
    try { await poOperationalAction(PO.tenant.tenant.id, action.dataset.action); }
    catch (error) { poToastMessage(error.message, true); }
  }
});

poRoot.addEventListener("change", async event => {
  if (event.target.id === "poStorageTenant") {
    document.getElementById("poStorageContent").innerHTML = await poLoadStorage(event.target.value);
  }
});

document.getElementById("poRefresh").addEventListener("click", () => poRender());
document.getElementById("poSignOut").addEventListener("click", async () => {
  try {
    await poApi("/api/auth/logout", { method: "POST", body: "{}" });
  } catch (error) {
    poToastMessage(error.message, true);
  }
  await poRender();
});
poRender();
