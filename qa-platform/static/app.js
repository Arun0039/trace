/* ── FrameTest AI · app.js ──────────────────────────────────────────────
   Single-file vanilla JS frontend.
   Every view renders into #page. Navigation is handled by state.view.
   All API calls go to the Python backend at the same origin.
────────────────────────────────────────────────────────────────────── */

/* ── Utilities ────────────────────────────────────────────────────── */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = v => String(v ?? "").replace(/[&<>"']/g, c =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pretty = v => esc(v).replace(/\n/g, "<br>");
const fmtDate = v => { if (!v) return "—"; const d = new Date(v); return isNaN(d) ? v : d.toLocaleDateString(); };

/* ── State ────────────────────────────────────────────────────────── */
const S = {
  view: "projects",
  viewHistory: [],
  settingsTab: "ai",        // which settings sub-pane is active
  project: null,
  projects: [],
  requirements: [],
  requirementDocs: [],
  cases: [],
  bugs: [],
  apiDocs: [],
  applications: [],
  analyses: [],
  reaRuntime: null,
  aiSettings: { ollama_base_url: "http://127.0.0.1:11434", ollama_model: "" },
  ollamaModels: [],
  ollamaStatus: "",
  caseGenerator: "deterministic",
  inventoryVisible: {},
};

const modal = $("#modal");
const modalForm = $("#modalForm");
let pollTimer = null;

/* ── API helper ───────────────────────────────────────────────────── */
async function api(path, opts = {}) {
  let res;
  try {
    res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  } catch (e) {
    if (e instanceof TypeError) throw new Error("Cannot reach the QA server. Make sure app.py is running.");
    throw e;
  }
  const ct = res.headers.get("content-type") || "";
  const data = ct.includes("application/json") ? await res.json() : null;
  if (!res.ok) throw new Error(data?.error || `Request failed (${res.status})`);
  return data;
}

/* ── Toast ────────────────────────────────────────────────────────── */
function toast(msg) {
  const el = $("#toast");
  el.textContent = msg;
  el.classList.add("show");
  setTimeout(() => el.classList.remove("show"), 2800);
}

/* ── Navigation ───────────────────────────────────────────────────── */
function navigate(view) {
  if (S.view !== view) S.viewHistory.push(S.view);
  S.view = view;
}
function goBack() {
  const prev = S.viewHistory.pop();
  if (prev) { S.view = prev; render(); }
}

/* ── Project API path shortcut ────────────────────────────────────── */
function pp(suffix) { return `/api/projects/${S.project.id}/${suffix}`; }

/* ── Load everything from the backend ────────────────────────────── */
async function loadProjects(preferred) {
  [S.projects, S.aiSettings] = await Promise.all([
    api("/api/projects"),
    api("/api/ai/settings"),
  ]);
  const sel = $("#projectSelect");
  if (sel) {
    sel.innerHTML = '<option value="">— choose project —</option>' +
      S.projects.map(p => `<option value="${p.id}">${esc(p.name)} · ${esc(p.product_type || p.project_key)}</option>`).join("");
    const id = preferred ?? S.project?.id;
    S.project = S.projects.find(p => String(p.id) === String(id)) || null;
    sel.value = S.project?.id || "";
  }
  if (S.project) await refresh();
  else await render();
}

async function refresh() {
  if (!S.project) return render();
  [
    S.requirements, S.requirementDocs, S.cases, S.bugs,
    S.apiDocs, S.applications, S.analyses,
  ] = await Promise.all([
    api(pp("requirements")),
    api(pp("requirements/documents")),
    api(pp("test-cases")),
    api(pp("bugs")),
    api(pp("api-docs")),
    api(pp("applications")),
    api(pp("analyses")),
  ]);
  if (!S.reaRuntime) S.reaRuntime = await api("/api/rea/runtime");
  // Update sidebar counts
  const rc = $("#reqCount"); if (rc) rc.textContent = S.requirements.length;
  const cc = $("#caseCount"); if (cc) cc.textContent = S.cases.length;
  const dc = $("#defectCount"); if (dc) dc.textContent = S.bugs.length;
  await render();
  pollAnalyses();
}

/* ── Sidebar state sync ───────────────────────────────────────────── */
function syncNav() {
  const settingsArea = S.view.startsWith("settings") || S.view === "archive";
  const projectArea = [
    "overview", "brd", "requirements", "cases",
    "defects", "reports", "project-settings",
    "api-docs", "applications", "application-analysis", "files",
  ].includes(S.view);

  // Top nav
  $$("#topNav .nav-item").forEach(btn => {
    const v = btn.dataset.view;
    const active = v === "projects" ? (S.view === "projects" || projectArea)
      : v === "dashboard" ? S.view === "dashboard"
      : false;
    btn.classList.toggle("active", active);
  });
  $$("#bottomNav .nav-item").forEach(btn =>
    btn.classList.toggle("active", settingsArea));

  // Project section
  const ps = $("#projectSection");
  if (ps) ps.hidden = !S.project || !projectArea;
  if (S.project) {
    const lbl = $("#projectLabel");
    if (lbl) lbl.textContent = S.project.project_key || "PROJECT";
  }
  $$("#projectNav .nav-item").forEach(btn =>
    btn.classList.toggle("active", btn.dataset.view === S.view));

  // Breadcrumb
  const crumbs = {
    projects: "Projects", dashboard: "Dashboard",
    overview: "Overview", brd: "BRD Documents",
    requirements: "Requirements", cases: "Test Cases",
    defects: "Defects", reports: "Reports & Analytics",
    "project-settings": "Project Settings",
    "api-docs": "API Docs", applications: "Applications",
    "application-analysis": "App Analysis", files: "Project Files",
    settings: "Settings", archive: "Archive",
    "settings-ai": "AI Module",
  };
  const crumb = $("#crumb");
  if (crumb) crumb.textContent = crumbs[S.view] || S.view;
}

/* ── Back button ──────────────────────────────────────────────────── */
function renderBack() {
  if (!S.viewHistory.length) return;
  if ($("#backBtn")) return;
  const btn = document.createElement("button");
  btn.id = "backBtn";
  btn.className = "btn btn-ghost back-btn";
  btn.textContent = "← Back";
  btn.onclick = goBack;
  const pg = $("#page");
  if (pg) pg.insertAdjacentElement("afterbegin", btn);
}

/* ════════════════════════════════════════════════════════════════════
   MAIN RENDER
════════════════════════════════════════════════════════════════════ */
async function render() {
  syncNav();
  const pg = $("#page");
  if (!pg) return;

  /* ── Global Dashboard ── */
  if (S.view === "dashboard") {
    pg.innerHTML = globalDashboardView();
    bindGlobalDash();
    return;
  }

  /* ── Settings ── */
  if (S.view === "settings" || S.view === "archive" || S.view === "settings-ai") {
    pg.innerHTML = settingsView();
    bindSettingsActions();
    renderBack();
    return;
  }

  /* ── Projects list ── */
  if (S.view === "projects" || !S.project) {
    pg.innerHTML = projectsView();
    bindProjectsView();
    return;
  }

  /* ── Project workspace ── */
  const p = S.project;
  const genReady = S.requirements.length > 0;
  const genOk = S.caseGenerator !== "ollama" || Boolean(S.aiSettings.ollama_model);

  const extraActions =
    S.view === "requirements"
      ? `<button class="btn btn-ghost" id="importBtn">↑ Upload BRD</button>
         <button class="btn btn-ghost" id="addBtn">＋ Add requirement</button>`
      : S.view === "cases"
      ? `<button class="btn btn-primary" id="genCasesBtn" ${genReady && genOk ? "" : "disabled"}>✦ Generate test cases</button>
         <button class="btn btn-ghost" id="addBtn">＋ Add test case</button>`
      : S.view === "defects"
      ? `<button class="btn btn-primary" id="addBtn">＋ Log defect</button>`
      : S.view === "api-docs"
      ? `<button class="btn btn-primary" id="apiImportBtn">↑ Import OpenAPI JSON</button>`
      : S.view === "applications"
      ? `<button class="btn btn-primary" id="appUploadBtn">↑ Upload application ZIP</button>`
      : "";

  const heading = `
    <div class="page-head">
      <div class="page-head-text">
        <div class="eyebrow">${esc(p.project_key)} · PROJECT WORKSPACE</div>
        <h1>${esc(p.name)}</h1>
        <p>${esc(p.description || "")}</p>
        <div class="project-tags">
          ${p.product_type ? `<span class="tag">PRODUCT · ${esc(p.product_type)}</span>` : ""}
          ${p.version ? `<span class="tag">BUILD ${esc(p.version)}</span>` : ""}
          ${p.environment ? `<span class="tag">${esc(p.environment)}</span>` : ""}
          <span class="tag">QA · ${esc(p.qa_owner)}</span>
        </div>
      </div>
      <div class="page-head-actions">
        ${extraActions}
        <button class="btn btn-ghost" id="exportBtn">↓ Export report</button>
        <button class="btn btn-danger" id="delProjectBtn">Delete project</button>
      </div>
    </div>`;

  switch (S.view) {
    case "overview":        pg.innerHTML = heading + await overviewView(); break;
    case "brd":             pg.innerHTML = heading + brdView(); break;
    case "requirements":    pg.innerHTML = heading + requirementsView(); break;
    case "cases":           pg.innerHTML = heading + casesView(); break;
    case "defects":         pg.innerHTML = heading + defectsView(); break;
    case "reports":         pg.innerHTML = heading + reportsView(); break;
    case "project-settings":pg.innerHTML = heading + projSettingsView(); break;
    case "api-docs":        pg.innerHTML = heading + apiDocsView(); break;
    case "applications":    pg.innerHTML = heading + applicationsView(); break;
    case "application-analysis": pg.innerHTML = heading + appAnalysisView(); break;
    case "files": {
      const r = await api(pp("files"));
      pg.innerHTML = heading + filesView(r);
      break;
    }
    default: pg.innerHTML = heading + await overviewView();
  }

  renderBack();
  bindWorkspaceActions();
}

/* ════════════════════════════════════════════════════════════════════
   VIEWS
════════════════════════════════════════════════════════════════════ */

/* ── Global dashboard ─────────────────────────────────────────────── */
function globalDashboardView() {
  const total = S.projects.length;
  const active = S.projects.filter(p => (p.status || "Active") === "Active").length;
  const cards = S.projects.slice(0, 6).map(p => `
    <div class="module-card" data-id="${p.id}" style="cursor:pointer" onclick="openProject(${p.id},'overview')">
      <span class="module-count">PRJ-${String(p.id).padStart(4, "0")}</span>
      <strong>${esc(p.name)}</strong>
      <span>${esc(p.product_type || p.project_key)}</span>
      <b>Open workspace →</b>
    </div>`).join("");
  return `
    <div class="page-head">
      <div class="page-head-text">
        <div class="eyebrow">TRACE · QA PLATFORM</div>
        <h1>Dashboard</h1>
        <p>All projects at a glance.</p>
      </div>
      <div class="page-head-actions">
        <button class="btn btn-primary" id="dashCreateBtn">＋ Create project</button>
      </div>
    </div>
    <div class="metric-grid">
      <div class="metric"><div class="metric-label">Total projects</div><div class="metric-icon">▦</div><div class="metric-value">${total}</div><div class="metric-note">${active} active</div></div>
      <div class="metric m-green"><div class="metric-label">Active</div><div class="metric-icon">◈</div><div class="metric-value">${active}</div><div class="metric-note">in progress</div></div>
      <div class="metric m-amber"><div class="metric-label">Archived</div><div class="metric-icon">▣</div><div class="metric-value">${total - active}</div><div class="metric-note">completed / paused</div></div>
      <div class="metric m-blue"><div class="metric-label">Workspace</div><div class="metric-icon">⊞</div><div class="metric-value m-blue" style="font-size:14px;margin-top:14px">LOCAL</div><div class="metric-note">SQLite · private</div></div>
    </div>
    <div class="panel">
      <div class="panel-head"><div class="panel-title">Recent projects</div></div>
      ${cards || '<div class="empty-row" style="padding:30px;text-align:center;color:var(--muted)"><strong style="color:var(--text2)">No projects yet</strong>Create a project to get started.</div>'}
      ${S.projects.length > 6 ? `<div style="margin-top:10px;font-size:11px;color:var(--muted)">Showing 6 of ${S.projects.length}. <button class="act-btn" onclick="navigate('projects');render()">View all →</button></div>` : ""}
    </div>`;
}
function bindGlobalDash() {
  $("#dashCreateBtn")?.addEventListener("click", () => openModal("project"));
}

/* ── Projects list ────────────────────────────────────────────────── */
function projectsView() {
  const rows = S.projects.map(p => `
    <tr data-search="${esc(`${p.name} ${p.product_type || ""} ${p.project_key} prj-${p.id}`.toLowerCase())}" data-status="${esc(p.status || "Active")}">
      <td><strong class="cell-main">${esc(p.name)}</strong><span class="cell-sub">${esc(p.product_type || "—")}</span></td>
      <td class="mono">PRJ-${String(p.id).padStart(4, "0")}</td>
      <td class="mono">${esc(p.project_key)}</td>
      <td><span class="badge ${p.status === "Inactive" ? "badge-muted" : "badge-green"}">${esc(p.status || "Active")}</span></td>
      <td>${fmtDate(p.created_at)}</td>
      <td>${fmtDate(p.updated_at)}</td>
      <td><div class="row-actions">
        <button class="act-btn" onclick="openProject(${p.id},'overview')">Open</button>
        <button class="act-btn" onclick="openModal('project','${p.id}')">Edit</button>
        <button class="act-btn act-btn-danger" onclick="deleteProject(${p.id})">Delete</button>
      </div></td>
    </tr>`).join("");
  return `
    <div class="page-head">
      <div class="page-head-text">
        <div class="eyebrow">WORKSPACE</div>
        <h1>Projects</h1>
        <p>Create and manage your QA projects.</p>
      </div>
      <div class="page-head-actions">
        <button class="btn btn-primary" id="createProjectBtn">＋ Create project</button>
      </div>
    </div>
    <div class="proj-tools">
      <input class="search-input" id="projSearch" placeholder="⌕  Search projects…">
      <select class="status-filter" id="projStatus">
        <option value="">All statuses</option>
        <option>Active</option><option>Inactive</option>
      </select>
    </div>
    <div class="table-wrap">
      <table class="data-table proj-table">
        <thead><tr><th>Name</th><th>ID</th><th>Key</th><th>Status</th><th>Created</th><th>Updated</th><th>Actions</th></tr></thead>
        <tbody>${rows || '<tr><td colspan="7" class="empty-row"><strong>No projects yet</strong>Click "Create project" to get started.</td></tr>'}</tbody>
      </table>
    </div>`;
}
function bindProjectsView() {
  $("#createProjectBtn")?.addEventListener("click", () => openModal("project"));
  $("#projSearch")?.addEventListener("input", filterProjects);
  $("#projStatus")?.addEventListener("change", filterProjects);
}
function filterProjects() {
  const q = ($("#projSearch")?.value || "").toLowerCase().trim();
  const s = $("#projStatus")?.value || "";
  $$("[data-search]").forEach(r => {
    r.hidden = !r.dataset.search.includes(q) || (s && r.dataset.status !== s);
  });
}

/* ── Overview ─────────────────────────────────────────────────────── */
async function overviewView() {
  const d = await api(pp("dashboard"));
  const statuses = ["Not Executed", "In Progress", "Pass", "Fail", "Blocked", "Skipped"];
  const missing = S.requirements.filter(r => !r.test_case_count);
  const reqRows = S.requirements.slice(0, 5).map(r => `
    <tr>
      <td class="mono">${esc(r.requirement_key)}</td>
      <td>${esc(r.title)}</td>
      <td>${r.test_case_count || 0}</td>
      <td><span class="badge ${r.test_case_count ? "badge-green" : "badge-muted"}">${r.test_case_count ? "Covered" : "Not covered"}</span></td>
    </tr>`).join("");
  return `
    <div class="metric-grid">
      <div class="metric m-green"><div class="metric-label">Requirements</div><div class="metric-icon">⌑</div><div class="metric-value">${d.requirements}</div><div class="metric-note">${d.covered_requirements} covered</div></div>
      <div class="metric"><div class="metric-label">Test cases</div><div class="metric-icon">☷</div><div class="metric-value">${d.test_cases}</div><div class="metric-note">${d.executed} executed</div></div>
      <div class="metric m-green"><div class="metric-label">Pass rate</div><div class="metric-icon">✓</div><div class="metric-value">${d.pass_percent}%</div><div class="metric-note">of executed</div></div>
      <div class="metric m-red"><div class="metric-label">Open defects</div><div class="metric-icon">⌁</div><div class="metric-value">${d.open_bugs}</div><div class="metric-note">${d.total_bugs} total</div></div>
    </div>
    <div class="dash-grid">
      <section>
        <div class="panel">
          <div class="panel-head">
            <div><div class="panel-title">Requirement coverage</div><div class="panel-sub">Test cases linked to requirements</div></div>
            <div class="cov-pct">${d.coverage_percent}%</div>
          </div>
          <div class="progress-bar"><span style="width:${d.coverage_percent}%"></span></div>
          <div class="cov-foot"><span>${d.covered_requirements} of ${d.requirements} covered</span><span>${missing.length} uncovered</span></div>
        </div>
        <div class="panel">
          <div class="panel-head">
            <div><div class="panel-title">Coverage matrix</div><div class="panel-sub">Traceability snapshot</div></div>
            <button class="act-btn" id="goRequirements">View all →</button>
          </div>
          <table class="cov-table">
            <thead><tr><th>REQ</th><th>Title</th><th>Cases</th><th>Status</th></tr></thead>
            <tbody>${reqRows || '<tr><td colspan="4" class="empty-row"><strong>No requirements</strong>Upload a BRD or add requirements manually.</td></tr>'}</tbody>
          </table>
        </div>
      </section>
      <section>
        <div class="panel">
          <div class="panel-head"><div><div class="panel-title">Execution status</div><div class="panel-sub">${d.executed} of ${d.test_cases} executed</div></div></div>
          <div class="status-list">
            ${statuses.map(s => `
              <div class="status-row">
                <i class="s-dot ${s.replace(" ", "-")}"></i>
                <span>${s}</span>
                <b>${d.statuses[s] || 0}</b>
              </div>`).join("")}
          </div>
        </div>
        <div class="panel">
          <div class="panel-head">
            <div><div class="panel-title">BRD → Test workflow</div></div>
            <span class="badge badge-green">ACTIVE</span>
          </div>
          <div class="info-box">
            <strong>FrameTest AI workflow</strong><br>
            1. Upload BRD → 2. Review requirements → 3. Generate test cases → 4. Execute &amp; track
            <br><br>
            <button class="act-btn" id="goBrd">Upload BRD →</button>
          </div>
        </div>
      </section>
    </div>`;
}

/* ── BRD Documents ────────────────────────────────────────────────── */
function brdView() {
  const docs = S.requirementDocs.map(d => `
    <tr>
      <td>
        <strong class="cell-main">${esc(d.filename)}</strong>
        <span class="cell-sub">v${d.version || 1} · ${d.requirement_count} requirements extracted</span>
      </td>
      <td><span class="badge badge-blue">${esc(d.content_type.includes("docx") ? "DOCX" : d.content_type.includes("pdf") ? "PDF" : "TXT/MD")}</span></td>
      <td class="mono">${(d.file_size_bytes / 1024).toFixed(1)} KB</td>
      <td>${fmtDate(d.uploaded_at)}</td>
      <td><div class="row-actions">
        <a class="act-btn" href="/api/projects/${S.project.id}/requirements/documents/${d.id}">Download</a>
      </div></td>
    </tr>`).join("");
  return `
    <div class="workflow-box panel">
      <div class="panel-title">Upload Business Requirements Document</div>
      <div class="panel-sub" style="margin-bottom:12px">
        Supported formats: DOCX, TXT, Markdown. Ollama will extract and structure requirements automatically.
        Falls back to rule-based extraction if Ollama is unavailable.
      </div>
      <div style="display:flex;gap:8px;flex-wrap:wrap">
        <button class="btn btn-primary" id="uploadBrdBtn">↑ Upload BRD file</button>
        <button class="btn btn-ghost" id="configAiBtn">Configure AI model</button>
      </div>
      <details style="margin-top:14px">
        <summary>Or paste raw requirement text</summary>
        <textarea id="pasteText" maxlength="1000000" placeholder="Paste BRD content here…"></textarea>
        <button class="btn btn-primary" id="pasteBtn">Analyze with AI</button>
      </details>
    </div>
    <div class="panel">
      <div class="panel-head">
        <div><div class="panel-title">Uploaded documents</div><div class="panel-sub">Original files are stored in project storage with version tracking.</div></div>
        <span class="badge">${S.requirementDocs.length} ${S.requirementDocs.length === 1 ? "document" : "documents"}</span>
      </div>
      <div class="table-wrap">
        <table class="data-table">
          <thead><tr><th>File</th><th>Type</th><th>Size</th><th>Uploaded</th><th></th></tr></thead>
          <tbody>${docs || '<tr><td colspan="5" class="empty-row"><strong>No documents uploaded</strong>Upload a BRD to start extracting requirements.</td></tr>'}</tbody>
        </table>
      </div>
    </div>`;
}

/* ── Requirements ─────────────────────────────────────────────────── */
function requirementsView() {
  const rows = S.requirements.map(r => `
    <tr data-search="${esc(`${r.requirement_key} ${r.title} ${r.description} ${r.module_type || ""}`.toLowerCase())}">
      <td><span class="mono cell-main">${esc(r.requirement_key)}</span><span class="cell-sub">${esc(r.title)}</span></td>
      <td><span class="badge ${r.module_type === "Non-Functional" ? "badge-purple" : "badge-blue"}">${esc(r.module_type || "Functional")}</span></td>
      <td>${esc(r.module_category || "Functional")}</td>
      <td><span class="badge ${r.priority === "High" ? "badge-red" : r.priority === "Low" ? "badge-muted" : "badge-amber"}">${esc(r.priority)}</span></td>
      <td><span class="badge ${r.test_case_count ? "badge-green" : "badge-muted"}">${r.test_case_count ? `${r.test_case_count} linked` : "Not covered"}</span></td>
      <td><span class="badge ${r.review_status === "Reviewed" ? "badge-green" : "badge-amber"}">${esc(r.review_status)}</span></td>
      <td class="mono" style="color:var(--muted)">${esc(r.source_document || "Manual")}</td>
      <td><div class="row-actions">
        <button class="act-btn edit-req" data-id="${r.id}">Edit</button>
        <button class="act-btn act-btn-danger del-req" data-id="${r.id}">Delete</button>
      </div></td>
    </tr>`).join("");
  return `
    <div class="workflow-box panel info-box" style="margin-bottom:14px">
      <strong>Step 1 — Provide requirement source</strong><br>
      Upload a BRD from the <button class="act-btn" id="goBrdFromReq">BRD Documents</button> section or add requirements manually below.
      Requirements extracted by AI need QA review before test-case generation.
    </div>
    <div class="filter-row">
      <input class="search-input" id="searchTable" placeholder="⌕  Search requirements…">
    </div>
    <div class="table-wrap">
      <table class="data-table req-table">
        <thead><tr><th>Requirement</th><th>Type</th><th>Category</th><th>Priority</th><th>Coverage</th><th>Review</th><th>Source</th><th></th></tr></thead>
        <tbody>${rows || '<tr><td colspan="8" class="empty-row"><strong>No requirements yet</strong>Upload a BRD or add one manually.</td></tr>'}</tbody>
      </table>
    </div>`;
}

/* ── Test Cases ───────────────────────────────────────────────────── */
function casesView() {
  const genOk = S.caseGenerator !== "ollama" || Boolean(S.aiSettings.ollama_model);
  const aiMsg = S.caseGenerator === "ollama" && !S.aiSettings.ollama_model
    ? '<div class="info-box warn-box" style="margin-top:8px">Configure an Ollama model in <button class="act-btn" id="goAiSettings">Settings → AI Module</button> to enable AI generation.</div>' : "";
  const rows = S.cases.map(c => `
    <tr data-search="${esc(`${c.case_key} ${c.scenario} ${c.requirement_key || ""} ${c.test_type}`.toLowerCase())}">
      <td class="mono">${esc(c.case_key)}<br>${c.generation_key ? '<span class="gen-label">AI DRAFT</span>' : ""}</td>
      <td class="mono">${esc(c.requirement_key || "—")}</td>
      <td><span class="badge">${esc(c.test_type || "Functional")}</span></td>
      <td>${esc(c.scenario)}</td>
      <td class="case-long">${pretty(c.steps || "—")}</td>
      <td class="case-long">${pretty(c.expected_result || "—")}</td>
      <td>
        <select class="exec-select" data-id="${c.id}" aria-label="Status for ${esc(c.case_key)}">
          ${["Not Executed","In Progress","Pass","Fail","Blocked","Skipped"].map(s =>
            `<option ${s === c.status ? "selected" : ""}>${s}</option>`).join("")}
        </select>
      </td>
      <td class="case-long">${pretty(c.actual_result || "—")}</td>
      <td><div class="row-actions">
        <button class="act-btn edit-case" data-id="${c.id}">Edit</button>
        ${c.execution_id ? `<button class="act-btn ev-case" data-case="${c.id}">History</button>` : ""}
        ${c.status === "Fail" ? `<button class="act-btn bug-from-case" data-case="${c.id}">＋ Defect</button>` : ""}
        <button class="act-btn act-btn-danger del-case" data-id="${c.id}">Delete</button>
      </div></td>
    </tr>`).join("");
  return `
    <div class="gen-note info-box">
      <strong>Step 2 — Generate test cases from approved requirements.</strong>
      Every generated case is linked to its source requirement.
      ${!S.requirements.length ? "<br>Add requirements first." : ""}
    </div>
    <div class="panel">
      <div class="panel-head">
        <div><div class="panel-title">Test-case generator</div><div class="panel-sub">Local deterministic or Ollama AI generation.</div></div>
        <span class="badge">OPTIONAL AI</span>
      </div>
      <div class="gen-select">
        <label>Generation method
          <select id="caseGenerator">
            <option value="deterministic" ${S.caseGenerator === "deterministic" ? "selected" : ""}>Local deterministic (no model needed)</option>
            <option value="ollama" ${S.caseGenerator === "ollama" ? "selected" : ""}>Ollama AI model</option>
          </select>
        </label>
        <label>&nbsp;</label>
      </div>
      ${aiMsg}
    </div>
    <div class="filter-row">
      <input class="search-input" id="searchTable" placeholder="⌕  Search test cases…">
    </div>
    <div class="table-wrap">
      <table class="data-table tc-table">
        <thead><tr><th>Case ID</th><th>Req ID</th><th>Type</th><th>Scenario</th><th>Steps</th><th>Expected result</th><th>Status</th><th>Actual result</th><th>Actions</th></tr></thead>
        <tbody>${rows || '<tr><td colspan="9" class="empty-row"><strong>No test cases yet</strong>Generate cases from reviewed requirements.</td></tr>'}</tbody>
      </table>
    </div>`;
}

/* ── Defects ──────────────────────────────────────────────────────── */
function defectsView() {
  const rows = S.bugs.map(b => `
    <tr data-search="${esc(`${b.bug_key} ${b.title} ${b.case_key || ""}`.toLowerCase())}">
      <td><span class="mono cell-main">${esc(b.bug_key)}</span><span class="cell-sub">${esc(b.title)}</span></td>
      <td class="mono">${esc(b.case_key || "—")}</td>
      <td><span class="badge ${b.severity === "Critical" || b.severity === "High" ? "badge-red" : "badge-amber"}">${esc(b.severity)} · ${esc(b.priority)}</span></td>
      <td>${esc(b.assigned_to || "—")}</td>
      <td><span class="badge ${b.status === "Open" ? "badge-red" : b.status === "Resolved" || b.status === "Closed" ? "badge-green" : "badge-amber"}">${esc(b.status)}</span></td>
      <td><button class="act-btn edit-bug" data-id="${b.id}">Update</button></td>
    </tr>`).join("");
  return `
    <div class="filter-row">
      <input class="search-input" id="searchTable" placeholder="⌕  Search defects…">
    </div>
    <div class="table-wrap">
      <table class="data-table">
        <thead><tr><th>Defect</th><th>Linked case</th><th>Severity · Priority</th><th>Assigned to</th><th>Status</th><th></th></tr></thead>
        <tbody>${rows || '<tr><td colspan="6" class="empty-row"><strong>No defects logged</strong>Log defects from failed test cases.</td></tr>'}</tbody>
      </table>
    </div>`;
}

/* ── Reports ──────────────────────────────────────────────────────── */
function reportsView() {
  const total = S.cases.length;
  const passed = S.cases.filter(c => c.status === "Pass").length;
  const failed = S.cases.filter(c => c.status === "Fail").length;
  const blocked = S.cases.filter(c => c.status === "Blocked").length;
  const notRun = S.cases.filter(c => c.status === "Not Executed").length;
  const inProg = S.cases.filter(c => c.status === "In Progress").length;
  const skipped = S.cases.filter(c => c.status === "Skipped").length;
  const covered = S.requirements.filter(r => r.test_case_count > 0).length;
  const passRate = (total - notRun) > 0 ? Math.round(passed * 100 / (total - notRun)) : 0;
  const openBugs = S.bugs.filter(b => b.status === "Open" || b.status === "In Progress").length;
  const bars = [
    ["Pass", passed, "var(--lime)"],
    ["Fail", failed, "var(--red)"],
    ["Blocked", blocked, "var(--amber)"],
    ["Not Executed", notRun, "var(--muted)"],
    ["In Progress", inProg, "var(--blue)"],
    ["Skipped", skipped, "#4e5870"],
  ].map(([label, count, color]) => `
    <div class="bar-row">
      <span class="bar-label">${label}</span>
      <div class="bar-track"><div class="bar-fill" style="width:${total ? Math.round(count * 100 / total) : 0}%;background:${color}"></div></div>
      <span class="bar-count">${count}</span>
    </div>`).join("");
  return `
    <div class="panel">
      <div class="panel-head"><div class="panel-title">Project snapshot</div><div class="panel-sub">Live totals</div></div>
      <div class="report-stat-grid">
        <div class="report-stat"><span>Requirements</span><strong>${S.requirements.length}</strong><small>${covered} covered</small></div>
        <div class="report-stat"><span>Test Cases</span><strong>${total}</strong><small>${total - notRun} executed</small></div>
        <div class="report-stat"><span>Pass Rate</span><strong class="stat-lime">${passRate}%</strong><small>${passed} passed</small></div>
        <div class="report-stat"><span>Failed</span><strong class="stat-red">${failed}</strong><small>${blocked} blocked</small></div>
        <div class="report-stat"><span>Open Defects</span><strong class="stat-amber">${openBugs}</strong><small>${S.bugs.length} total</small></div>
        <div class="report-stat"><span>Coverage</span><strong>${S.requirements.length ? Math.round(covered * 100 / S.requirements.length) : 0}%</strong><small>of requirements</small></div>
      </div>
    </div>
    <div class="panel">
      <div class="panel-head">
        <div><div class="panel-title">Export report</div><div class="panel-sub">Full project CSV — test cases, coverage, defects</div></div>
        <button class="btn btn-primary export-csv">↓ Download CSV</button>
      </div>
      <div class="info-box">Saved to <code>storage/projects/${S.project.id}/exports/</code></div>
    </div>
    <div class="panel">
      <div class="panel-head"><div class="panel-title">Execution breakdown</div></div>
      <div class="bar-rows">${bars}</div>
    </div>`;
}

/* ── Project Settings ─────────────────────────────────────────────── */
function projSettingsView() {
  const p = S.project;
  return `
    <div class="panel">
      <div class="panel-head">
        <div><div class="panel-title">${esc(p.name)}</div><div class="panel-sub">PRJ-${String(p.id).padStart(4,"0")} · ${esc(p.project_key)}</div></div>
        <button class="btn btn-ghost" id="editProjBtn">Edit project</button>
      </div>
      <div class="proj-detail-grid">
        <div><span>Product type</span><strong>${esc(p.product_type||"—")}</strong></div>
        <div><span>Status</span><strong>${esc(p.status||"Active")}</strong></div>
        <div><span>QA owner</span><strong>${esc(p.qa_owner)}</strong></div>
        <div><span>Version</span><strong>${esc(p.version||"—")}</strong></div>
        <div><span>Environment</span><strong>${esc(p.environment||"—")}</strong></div>
        <div><span>Description</span><strong>${esc(p.description||"—")}</strong></div>
        <div><span>Created</span><strong>${fmtDate(p.created_at)}</strong></div>
        <div><span>Updated</span><strong>${fmtDate(p.updated_at)}</strong></div>
      </div>
    </div>`;
}

/* ── API Docs ─────────────────────────────────────────────────────── */
function apiDocsView() {
  const eps = S.apiDocs.flatMap(doc => doc.endpoints.map(e => ({ ...e, doc })));
  const rows = eps.map(e => `
    <tr data-search="${esc(`${e.method} ${e.path} ${e.summary}`.toLowerCase())}">
      <td><span class="method ${e.method.toLowerCase()}">${esc(e.method)}</span></td>
      <td class="mono" style="white-space:nowrap">${esc(e.path)}</td>
      <td><strong class="cell-main">${esc(e.summary||"—")}</strong><span class="cell-sub">${esc(e.operation_id||"")}</span></td>
      <td>${(Array.isArray(e.tags)?e.tags:[]).map(t=>`<span class="badge">${esc(t)}</span>`).join(" ")||"—"}</td>
      <td class="mono" style="color:var(--muted)">${esc(e.doc.title)}</td>
    </tr>`).join("");
  return `
    <div class="filter-row">
      <input class="search-input" id="searchTable" placeholder="⌕  Search endpoints…">
    </div>
    <div class="table-wrap">
      <table class="data-table ep-table">
        <thead><tr><th>Method</th><th>Path</th><th>Summary</th><th>Tags</th><th>Spec</th></tr></thead>
        <tbody>${rows || '<tr><td colspan="5" class="empty-row"><strong>No API docs imported</strong>Import an OpenAPI or Swagger JSON file.</td></tr>'}</tbody>
      </table>
    </div>`;
}

/* ── Applications ─────────────────────────────────────────────────── */
function applicationsView() {
  const cards = S.applications.map(app => {
    const an = app.analysis || {};
    const inv = app.inventory || [];
    const vis = S.inventoryVisible[app.id] || 100;
    const fw = (an.frameworks || []).map(f => `<span class="tag">${esc(f)}</span>`).join("") || '<span style="color:var(--muted)">Not identified</span>';
    const rows = inv.slice(0, vis).map(f => `
      <tr><td class="mono">${esc(f.path)}</td><td>${esc(f.type)}</td><td class="mono">${Number(f.size_bytes).toLocaleString()} B</td></tr>`).join("");
    return `
      <article class="app-card">
        <div class="panel-head">
          <div><div class="panel-title">${esc(app.filename)}</div><div class="panel-sub">Uploaded ${fmtDate(app.uploaded_at)} · ${Number(app.archive_size_bytes).toLocaleString()} bytes</div></div>
          <span class="badge badge-green">${Number(an.total_files||inv.length).toLocaleString()} files</span>
        </div>
        <div class="fw-list">${fw}</div>
        <div class="app-stats">
          ${[["Files",an.total_files||inv.length],["Scanned",an.scanned_source_files||0],["Routes",an.route_count||0],["Components",an.component_count||0]].map(([l,v])=>`<div class="app-stat"><span>${l}</span><strong>${v}</strong></div>`).join("")}
        </div>
        <div class="info-box" style="margin:12px 0">
          <strong>Validate requirements against source</strong> — Ollama will review source excerpts for coverage evidence.
          <button class="btn btn-ghost btn-sm validate-cov" data-app="${app.id}" style="margin-top:8px">Validate requirement coverage</button>
        </div>
        <div class="table-wrap">
          <table class="data-table">
            <thead><tr><th>Path</th><th>Type</th><th>Size</th></tr></thead>
            <tbody>${rows||'<tr><td colspan="3" class="empty-row">No files.</td></tr>'}</tbody>
          </table>
        </div>
        ${vis < inv.length ? `<div class="inv-more"><span style="font-size:11px;color:var(--muted)">Showing ${vis} of ${inv.length}</span><button class="btn btn-ghost btn-sm load-inv" data-app="${app.id}">Load more</button></div>` : ""}
      </article>`;
  }).join("");
  return `
    <div class="panel">
      <div class="panel-head">
        <div><div class="panel-title">Application ZIP analysis</div><div class="panel-sub">Upload a build ZIP. Code is never executed.</div></div>
        <span class="badge badge-amber">CODE NOT EXECUTED</span>
      </div>
      <div class="info-box">Upload a developer build ZIP to inspect its structure, detect frameworks, routes, and components.</div>
      <div style="margin-top:12px"><button class="btn btn-primary" id="appUploadInlineBtn">↑ Choose application ZIP</button></div>
    </div>
    <div class="app-history">${cards || '<div class="panel" style="text-align:center;padding:30px;color:var(--muted)"><strong style="color:var(--text2)">No application uploaded</strong></div>'}</div>`;
}

/* ── App Analysis ─────────────────────────────────────────────────── */
function appAnalysisView() {
  const rt = S.reaRuntime || {};
  const hist = S.analyses.map(item => {
    const badge = item.status === "completed" ? "badge-green" : item.status === "failed" ? "badge-red" : "badge-muted";
    return `
      <article class="analysis-card">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:12px">
          <span class="badge ${badge}">${esc(item.status)}</span>
          <span class="mono" style="color:var(--muted)">#${item.id} · ${fmtDate(item.created_at)}</span>
        </div>
        <div class="analysis-path">${esc(item.input_path)}</div>
        ${item.error ? `<div class="error-box">${esc(item.error)}</div>` : ""}
        ${item.summary ? analysisResultMarkup(item.summary) : ""}
      </article>`;
  }).join("");
  return `
    <div class="panel">
      <div class="panel-head">
        <div><div class="panel-title">Analyze a local JavaScript application</div><div class="panel-sub">REA statically inspects a directory or .asar. Does not run code.</div></div>
        <span class="badge ${rt.analysis_enabled ? "badge-green" : "badge-muted"}">${rt.analysis_enabled ? "REA READY" : "REA UNAVAILABLE"}</span>
      </div>
      <div id="reaStatus" class="info-box">${rt.analysis_enabled ? "Ready · REA static analysis via CLI" : esc(rt.reason || "Checking REA runtime…")}</div>
      <div class="analysis-form">
        <label for="appPath">Absolute path on this server</label>
        <input id="appPath" type="text" placeholder="C:\\path\\to\\app or /path/to/app" autocomplete="off" spellcheck="false">
        <div class="analysis-actions">
          <button class="btn btn-primary" id="startAnalysis" ${rt.analysis_enabled ? "" : "disabled"}>Start analysis</button>
          <button class="btn btn-ghost" id="checkRea">Check availability</button>
        </div>
      </div>
    </div>
    <div class="panel">
      <div class="panel-head"><div class="panel-title">Analysis history</div></div>
      <div class="analysis-history">${hist || '<div style="text-align:center;padding:24px;color:var(--muted)">No analyses yet.</div>'}</div>
    </div>`;
}
function analysisResultMarkup(summary) {
  if (!summary) return "";
  const stats = summary.statistics || {};
  const metrics = [["Modules","modules"],["Files","relevant_files"],["Parsed JS","parsed_javascript_files"],["Findings","findings"]].map(([l,k]) =>
    Number.isInteger(stats[k]) ? `<div class="a-stat"><span>${l}</span><strong>${stats[k].toLocaleString()}</strong></div>` : "").join("");
  return `<div class="analysis-result">${metrics ? `<div class="a-stats">${metrics}</div>` : ""}</div>`;
}

/* ── Project files ────────────────────────────────────────────────── */
function filesView(result) {
  const rows = result.files.map(f => `
    <tr>
      <td class="mono">${esc(f.path)}</td>
      <td class="mono">${f.size.toLocaleString()} B</td>
      <td><button class="act-btn dl-file" data-path="${esc(f.path)}">Download</button></td>
    </tr>`).join("");
  return `
    <div class="panel">
      <div class="panel-head"><div class="panel-title">Project files</div></div>
      ${result.truncated ? '<div class="info-box">Showing first 2,000 files.</div>' : ""}
      <div class="table-wrap">
        <table class="data-table">
          <thead><tr><th>Path</th><th>Size</th><th></th></tr></thead>
          <tbody>${rows || '<tr><td colspan="3" class="empty-row"><strong>No files</strong></td></tr>'}</tbody>
        </table>
      </div>
    </div>`;
}

/* ── Settings ─────────────────────────────────────────────────────── */
function settingsView() {
  const tab = S.settingsTab;
  const navItems = [
    ["ai", "✦", "AI Module"],
    ["profile", "◉", "User Profile"],
    ["team", "◎", "Team & Members"],
    ["roles", "◈", "Roles & Permissions"],
    ["integrations", "⇄", "Integrations"],
    ["notifications", "◻", "Notifications"],
    ["preferences", "⚙", "App Preferences"],
    ["archive", "▣", "Archive"],
  ];
  const navHtml = navItems.map(([id, icon, label]) => `
    <button class="settings-nav-item ${tab === id ? "active" : ""}" data-stab="${id}">${icon} ${label}</button>`).join("");

  let pane = "";
  if (tab === "ai") {
    const models = S.ollamaModels.length ? S.ollamaModels : S.aiSettings.ollama_model ? [S.aiSettings.ollama_model] : [];
    const opts = models.map(m => `<option value="${esc(m)}" ${m === S.aiSettings.ollama_model ? "selected" : ""}>${esc(m)}</option>`).join("");
    pane = `
      <h2>AI Module</h2>
      <div class="pane-sub">Connect a local Ollama model for AI-powered requirement extraction and test-case generation.</div>
      <div class="ai-fields">
        <label>Ollama local URL<input id="ollamaUrl" value="${esc(S.aiSettings.ollama_base_url)}" placeholder="http://127.0.0.1:11434"></label>
        <label for="ollamaModel">Installed model
          <select id="ollamaModel">
            <option value="">${S.ollamaModels.length ? "Select a model" : "Connect to load models"}</option>
            ${opts}
          </select>
        </label>
      </div>
      <div class="ai-actions">
        <button class="btn btn-ghost" id="saveAi">Save settings</button>
        <button class="btn btn-ghost" id="testAi">Connect &amp; refresh model list</button>
      </div>
      <div class="ai-status" id="aiStatus">${esc(S.ollamaStatus)}</div>
      <div class="info-box" style="margin-top:14px">
        <strong>Install a model:</strong> <code>ollama pull llama3.1:8b</code>
        then click "Connect". Only loopback URLs are accepted.
        Requirement text and source excerpts are sent only to your local model — never to the cloud.
      </div>`;
  } else if (tab === "archive") {
    pane = `<h2>Archive</h2><div class="pane-sub">Files retained from deleted projects.</div><div id="archiveContent"><div style="color:var(--muted);font-size:12px">Loading…</div></div>`;
  } else {
    const labels = { profile:"User Profile",team:"Team & Members",roles:"Roles & Permissions",integrations:"Integrations",notifications:"Notifications",preferences:"App Preferences" };
    pane = `<h2>${labels[tab]||tab}</h2><div class="pane-sub">Configuration for ${labels[tab]||tab}.</div><div class="coming-soon">This section is coming in a future release.</div>`;
  }

  return `
    <div class="page-head">
      <div class="page-head-text">
        <div class="eyebrow">WORKSPACE</div>
        <h1>Settings</h1>
      </div>
    </div>
    <div class="settings-grid">
      <div class="settings-nav">${navHtml}</div>
      <div class="settings-pane">${pane}</div>
    </div>`;
}

/* ════════════════════════════════════════════════════════════════════
   BIND WORKSPACE ACTIONS — called after every workspace render
════════════════════════════════════════════════════════════════════ */
function bindWorkspaceActions() {
  /* Heading actions */
  $("#addBtn")?.addEventListener("click", () => {
    const kinds = { requirements: "requirement", cases: "case", defects: "bug" };
    openModal(kinds[S.view] || "requirement");
  });
  $("#importBtn")?.addEventListener("click", importBrd);
  $("#uploadBrdBtn")?.addEventListener("click", importBrd);
  $("#configAiBtn")?.addEventListener("click", () => { S.settingsTab = "ai"; navigate("settings"); render(); });
  $("#pasteBtn")?.addEventListener("click", pasteRequirement);
  $("#goBrdFromReq")?.addEventListener("click", () => { navigate("brd"); render(); });
  $("#goBrd")?.addEventListener("click", () => { navigate("brd"); render(); });
  $("#goRequirements")?.addEventListener("click", () => { navigate("requirements"); render(); });
  $("#goAiSettings")?.addEventListener("click", () => { S.settingsTab = "ai"; navigate("settings"); render(); });

  $("#genCasesBtn")?.addEventListener("click", generateCases);
  $("#caseGenerator")?.addEventListener("change", e => { S.caseGenerator = e.target.value; render(); });

  $("#apiImportBtn")?.addEventListener("click", importApiDoc);
  $("#appUploadBtn")?.addEventListener("click", importAppZip);
  $("#appUploadInlineBtn")?.addEventListener("click", importAppZip);
  $$(".validate-cov").forEach(b => b.addEventListener("click", () => validateCoverage(b.dataset.app)));
  $$(".load-inv").forEach(b => b.addEventListener("click", () => {
    S.inventoryVisible[b.dataset.app] = (S.inventoryVisible[b.dataset.app] || 100) + 100;
    render();
  }));

  $("#startAnalysis")?.addEventListener("click", startAnalysis);
  $("#checkRea")?.addEventListener("click", checkRea);

  $("#exportBtn")?.addEventListener("click", exportReport);
  $$(".export-csv").forEach(b => b.addEventListener("click", exportReport));

  $("#editProjBtn")?.addEventListener("click", () => openModal("project", S.project.id));
  $("#delProjectBtn")?.addEventListener("click", () => deleteProject(S.project.id));

  /* Table search */
  $("#searchTable")?.addEventListener("input", e => {
    const q = e.target.value.toLowerCase();
    $$("[data-search]").forEach(r => r.hidden = !r.dataset.search.includes(q));
  });

  /* Requirement actions */
  $$(".edit-req").forEach(b => b.addEventListener("click", () => openModal("requirement", b.dataset.id)));
  $$(".del-req").forEach(b => b.addEventListener("click", () => deleteRequirement(b.dataset.id)));

  /* Case actions */
  $$(".edit-case").forEach(b => b.addEventListener("click", () => openModal("case", b.dataset.id)));
  $$(".del-case").forEach(b => b.addEventListener("click", () => deleteCase(b.dataset.id)));
  $$(".ev-case").forEach(b => b.addEventListener("click", () => showEvidence(b.dataset.case)));
  $$(".bug-from-case").forEach(b => b.addEventListener("click", () => openModal("bug", null, b.dataset.case)));
  $$(".exec-select").forEach(sel => sel.addEventListener("change", async () => {
    const tc = S.cases.find(c => String(c.id) === sel.dataset.id);
    if (!tc) return;
    sel.disabled = true;
    try {
      await api(pp(`executions/${tc.id}`), {
        method: "POST",
        body: JSON.stringify({ status: sel.value, actual_result: tc.actual_result || "", executed_by: S.project.qa_owner }),
      });
      await refresh(); toast("Status updated");
    } catch (e) { sel.value = tc.status; sel.disabled = false; toast(e.message); }
  }));

  /* Bug actions */
  $$(".edit-bug").forEach(b => b.addEventListener("click", () => openModal("bug", b.dataset.id)));

  /* File download */
  $$(".dl-file").forEach(b => b.addEventListener("click", () => {
    location.href = pp(`files/download?path=${encodeURIComponent(b.dataset.path)}`);
  }));
}

/* ── Settings actions ─────────────────────────────────────────────── */
function bindSettingsActions() {
  /* Tab switcher */
  $$(".settings-nav-item").forEach(btn => btn.addEventListener("click", async () => {
    S.settingsTab = btn.dataset.stab;
    render();
    if (S.settingsTab === "archive") loadArchive();
  }));
  /* AI */
  $("#ollamaModel")?.addEventListener("change", async e => {
    S.aiSettings.ollama_model = e.target.value;
    await saveAiSettings();
  });
  $("#saveAi")?.addEventListener("click", saveAiSettings);
  $("#testAi")?.addEventListener("click", testOllama);
}

async function loadArchive() {
  const el = $("#archiveContent");
  if (!el) return;
  try {
    const { archives } = await api("/api/archive");
    const rows = archives.map(a => `
      <tr>
        <td><strong class="cell-main">${esc(a.archive_id)}</strong></td>
        <td>${fmtDate(a.archived_at)}</td>
        <td><span class="badge">Retained</span></td>
      </tr>`).join("");
    el.innerHTML = `
      <div class="info-box archive-note">Archived folders are kept in <code>storage/archived-projects</code>. They cannot be restored as active projects.</div>
      <div class="table-wrap">
        <table class="data-table archive-table">
          <thead><tr><th>Archive folder</th><th>Archived date</th><th>Status</th></tr></thead>
          <tbody>${rows || '<tr><td colspan="3" class="empty-row"><strong>Archive is empty</strong></td></tr>'}</tbody>
        </table>
      </div>`;
  } catch (e) {
    el.innerHTML = `<div class="error-box">Could not load archive: ${esc(e.message)}</div>`;
  }
}

async function saveAiSettings() {
  try {
    S.aiSettings = await api("/api/ai/settings", {
      method: "POST",
      body: JSON.stringify({ ollama_base_url: $("#ollamaUrl")?.value, ollama_model: $("#ollamaModel")?.value }),
    });
    S.ollamaStatus = "Settings saved.";
    toast("AI settings saved");
    render();
  } catch (e) { toast(e.message); }
}
async function testOllama() {
  const status = $("#aiStatus");
  const save = $("#saveAi"), test = $("#testAi");
  if (save) save.disabled = true;
  if (test) test.disabled = true;
  if (status) status.textContent = "Connecting to Ollama…";
  try {
    S.aiSettings = await api("/api/ai/settings", {
      method: "POST",
      body: JSON.stringify({ ollama_base_url: $("#ollamaUrl")?.value, ollama_model: $("#ollamaModel")?.value }),
    });
    const r = await api("/api/ai/ollama/test", { method: "POST", body: JSON.stringify({}) });
    S.ollamaModels = r.models;
    S.ollamaStatus = r.models.length
      ? `Connected · ${r.models.length} model${r.models.length > 1 ? "s" : ""} available: ${r.models.join(", ")}`
      : "Connected but no models installed. Run: ollama pull llama3.1:8b";
    render();
  } catch (e) {
    S.ollamaStatus = e.message;
    if (status) status.textContent = e.message;
  } finally {
    if ($("#saveAi")) $("#saveAi").disabled = false;
    if ($("#testAi")) $("#testAi").disabled = false;
  }
}

/* ════════════════════════════════════════════════════════════════════
   CRUD OPERATIONS
════════════════════════════════════════════════════════════════════ */

/* ── Open project ─────────────────────────────────────────────────── */
async function openProject(id, view = "overview") {
  S.project = S.projects.find(p => String(p.id) === String(id)) || null;
  navigate(view);
  const sel = $("#projectSelect");
  if (sel) sel.value = S.project?.id || "";
  if (S.project) await refresh();
}

/* ── Delete project ───────────────────────────────────────────────── */
async function deleteProject(id) {
  const p = S.projects.find(x => String(x.id) === String(id));
  if (!p || !confirm(`Delete "${p.name}"? Records will be removed. Files will be archived.`)) return;
  try {
    const r = await api(`/api/projects/${p.id}`, { method: "DELETE" });
    if (String(S.project?.id) === String(p.id)) S.project = null;
    S.view = "projects"; S.viewHistory = [];
    await loadProjects();
    toast(r.files_archived ? "Project deleted — files archived." : "Project deleted.");
  } catch (e) { toast(e.message); }
}

/* ── Delete requirement ───────────────────────────────────────────── */
async function deleteRequirement(id) {
  const r = S.requirements.find(x => String(x.id) === id);
  if (!r) return;
  const linked = Number(r.test_case_count || 0);
  if (!confirm(`Delete ${r.requirement_key} — ${r.title}?${linked ? ` ${linked} linked test case(s) will be unlinked.` : ""}`)) return;
  try {
    const res = await api(pp(`requirements/${id}`), { method: "DELETE" });
    await refresh();
    toast(`Deleted${res.unlinked_test_cases ? `; ${res.unlinked_test_cases} test case(s) unlinked` : ""}`);
  } catch (e) { toast(e.message); }
}

/* ── Delete test case ─────────────────────────────────────────────── */
async function deleteCase(id) {
  const c = S.cases.find(x => String(x.id) === id);
  if (!c || !confirm(`Delete ${c.case_key} — ${c.scenario}?`)) return;
  try {
    await api(pp(`test-cases/${id}`), { method: "DELETE" });
    await refresh(); toast("Test case deleted");
  } catch (e) { toast(e.message); }
}

/* ── Import BRD ───────────────────────────────────────────────────── */
function importBrd(generator = "auto") {
  const picker = document.createElement("input");
  picker.type = "file";
  picker.accept = ".docx,.txt,.md";
  picker.onchange = async () => {
    const file = picker.files[0]; if (!file) return;
    if (!/\.(docx|txt|md)$/i.test(file.name)) { toast("Choose DOCX, TXT, or Markdown"); return; }
    const isDocx = /\.docx$/i.test(file.name);
    if (file.size > (isDocx ? 5 : 1) * 1024 * 1024) { toast(`File too large (max ${isDocx ? "5 MB" : "1 MB"})`); return; }
    const btn = $("#uploadBrdBtn") || $("#importBtn");
    if (btn) { btn.disabled = true; btn.textContent = "Analyzing…"; }
    toast("Analyzing document with AI…");
    try {
      const payload = { filename: file.name, generator };
      if (isDocx) {
        const dataUrl = await new Promise((res, rej) => {
          const fr = new FileReader(); fr.onload = () => res(fr.result); fr.onerror = () => rej(new Error("Read failed")); fr.readAsDataURL(file);
        });
        payload.document_data = String(dataUrl).split(",", 2)[1];
      } else {
        payload.content = await file.text();
      }
      const r = await api(pp("requirements/import"), { method: "POST", body: JSON.stringify(payload) });
      await refresh();
      const label = r.generator === "ollama" ? "AI-extracted" : "rule-based";
      toast(`${r.count} ${label} requirement(s) added (${r.functional_count} Functional, ${r.non_functional_count} Non-Functional)${r.skipped_duplicates ? `; ${r.skipped_duplicates} duplicate(s) skipped` : ""}${r.analysis_warning ? " · " + r.analysis_warning : ""}`);
      navigate("requirements"); render();
    } catch (e) { toast(e.message); }
    finally { if (btn) { btn.disabled = false; btn.textContent = "↑ Upload BRD file"; } }
  };
  picker.click();
}

async function pasteRequirement() {
  const ta = $("#pasteText");
  const content = ta?.value || "";
  if (!content.trim()) { toast("Enter requirement text first"); return; }
  const btn = $("#pasteBtn");
  if (btn) { btn.disabled = true; btn.textContent = "Analyzing…"; }
  toast("Analyzing with AI…");
  try {
    const r = await api(pp("requirements/import"), {
      method: "POST",
      body: JSON.stringify({ filename: "pasted-requirements.txt", content, generator: "ollama" }),
    });
    await refresh();
    toast(`${r.count} requirement(s) extracted${r.analysis_warning ? " · " + r.analysis_warning : ""}`);
    navigate("requirements"); render();
  } catch (e) { toast(e.message); }
  finally { if (btn) { btn.disabled = false; btn.textContent = "Analyze with AI"; } }
}

/* ── Generate test cases ──────────────────────────────────────────── */
async function generateCases() {
  const btn = $("#genCasesBtn");
  if (btn) { btn.disabled = true; btn.textContent = S.caseGenerator === "ollama" ? "Generating with AI…" : "Generating…"; }
  try {
    const r = await api(pp("test-cases/generate"), { method: "POST", body: JSON.stringify({ generator: S.caseGenerator }) });
    await refresh();
    if (r.count) {
      toast(`${r.count} case(s) generated; ${r.skipped_existing} skipped.${r.truncated ? " 300-case limit reached." : ""}`);
      navigate("cases"); render();
    } else {
      toast(`No new cases; ${r.skipped_existing} existing case(s) already cover the requirements.`);
    }
  } catch (e) { toast(e.message); }
  finally { if ($("#genCasesBtn")) { $("#genCasesBtn").disabled = false; $("#genCasesBtn").textContent = "✦ Generate test cases"; } }
}

/* ── Import API doc ───────────────────────────────────────────────── */
function importApiDoc() {
  const picker = document.createElement("input");
  picker.type = "file"; picker.accept = ".json";
  picker.onchange = async () => {
    const file = picker.files[0]; if (!file) return;
    if (!/\.json$/i.test(file.name)) { toast("Choose a .json file"); return; }
    if (file.size > 1024 * 1024) { toast("File too large (max 1 MB)"); return; }
    try {
      const r = await api(pp("api-docs/import"), { method: "POST", body: JSON.stringify({ filename: file.name, content: await file.text() }) });
      await refresh(); toast(`${r.title}: API doc imported`);
    } catch (e) { toast(e.message); }
  };
  picker.click();
}

/* ── Import application ZIP ───────────────────────────────────────── */
function importAppZip() {
  const picker = document.createElement("input");
  picker.type = "file"; picker.accept = ".zip";
  picker.onchange = async () => {
    const file = picker.files[0]; if (!file) return;
    if (!/\.zip$/i.test(file.name)) { toast("Choose a .zip file"); return; }
    if (file.size > 25 * 1024 * 1024) { toast("ZIP too large (max 25 MB)"); return; }
    try {
      const r = await api(`${pp("applications/import")}?filename=${encodeURIComponent(file.name)}`, {
        method: "POST", headers: { "Content-Type": "application/zip" }, body: await file.arrayBuffer(),
      });
      await refresh(); toast(`${r.filename}: ZIP analyzed`);
    } catch (e) { toast(e.message); }
  };
  picker.click();
}

/* ── Validate coverage ────────────────────────────────────────────── */
async function validateCoverage(appId) {
  const btn = $(`.validate-cov[data-app="${appId}"]`);
  if (btn) { btn.disabled = true; btn.textContent = "Checking…"; }
  try {
    const r = await api(pp(`applications/${appId}/validate-requirements`), { method: "POST", body: JSON.stringify({}) });
    await refresh(); toast(`Coverage estimate saved for ${r.coverage.length} requirement(s).`);
  } catch (e) { toast(e.message); }
  finally {
    const cur = $(`.validate-cov[data-app="${appId}"]`);
    if (cur) { cur.disabled = false; cur.textContent = "Validate requirement coverage"; }
  }
}

/* ── REA Analysis ─────────────────────────────────────────────────── */
async function startAnalysis() {
  const path = $("#appPath")?.value;
  const btn = $("#startAnalysis");
  if (!path) { const s = $("#reaStatus"); if (s) s.textContent = "Enter an absolute path."; return; }
  if (btn) btn.disabled = true;
  try {
    await api(pp("analyses"), { method: "POST", body: JSON.stringify({ input_path: path }) });
    toast("REA analysis queued."); await refresh();
  } catch (e) {
    const s = $("#reaStatus"); if (s) s.textContent = e.message;
  } finally {
    if ($("#startAnalysis")) $("#startAnalysis").disabled = !S.reaRuntime?.analysis_enabled;
  }
}
async function checkRea() {
  const btn = $("#checkRea"); if (btn) btn.disabled = true;
  try { S.reaRuntime = await api("/api/rea/runtime"); render(); }
  catch (e) { toast(e.message); }
  finally { if ($("#checkRea")) $("#checkRea").disabled = false; }
}
function pollAnalyses() {
  const active = S.analyses.some(a => a.status === "queued" || a.status === "running");
  if (active && !pollTimer) pollTimer = setTimeout(async () => { pollTimer = null; await refresh(); }, 1500);
  if (!active && pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
}

/* ── Export report ────────────────────────────────────────────────── */
function exportReport() {
  if (!S.project) return;
  location.href = pp("report/csv");
}

/* ── Show evidence ────────────────────────────────────────────────── */
async function showEvidence(caseId) {
  try {
    const hist = await api(pp(`test-cases/${caseId}/executions`));
    const c = S.cases.find(x => x.id == caseId);
    $("#modalEyebrow").textContent = "SCREENSHOTS";
    $("#modalTitle").textContent = `Evidence · ${c?.case_key || ""}`;
    $("#formError").textContent = "";
    $("#saveModal").hidden = true;
    $("#modalFields").innerHTML = hist.map(ex => `
      <section class="ev-hist">
        <div class="panel-sub">${esc(ex.status)} · ${fmtDate(ex.executed_at)}${ex.executed_by ? " · " + esc(ex.executed_by) : ""}</div>
        ${ex.evidence?.length
          ? ex.evidence.map(item => {
              const base = pp(`evidence/${item.id}`);
              return `<div class="ev-card">
                <a href="${base}" target="_blank" rel="noopener"><img src="${base}" alt="Evidence ${esc(item.filename)}"></a>
                <div class="ev-meta"><strong>${esc(item.filename)}</strong><span>${(item.size/1024).toFixed(1)} KB · ${fmtDate(item.created_at)}</span><a class="act-btn" href="${base}/download">Download</a></div>
              </div>`;
            }).join("")
          : '<div style="color:var(--muted);font-size:11px;padding:8px 0">No screenshot attached.</div>'}
      </section>`).join("") || '<div style="color:var(--muted);padding:16px">No executions yet.</div>';
    modal.showModal();
  } catch (e) { toast(e.message); }
}

/* ════════════════════════════════════════════════════════════════════
   MODAL — create / edit records
════════════════════════════════════════════════════════════════════ */
function field(name, label, value = "", type = "text", full = false, placeholder = "", required = false) {
  return `<div class="field${full?" full":""}">
    <label for="f_${name}">${label}${required?" *":""}</label>
    ${type === "textarea"
      ? `<textarea id="f_${name}" name="${name}" placeholder="${esc(placeholder)}" ${required?"required":""}>${esc(value)}</textarea>`
      : `<input id="f_${name}" name="${name}" type="${type}" value="${esc(value)}" placeholder="${esc(placeholder)}" ${required?"required":""}>`}
  </div>`;
}
function selectField(name, label, opts, value, full = false) {
  return `<div class="field${full?" full":""}"><label for="f_${name}">${label}</label>
    <select id="f_${name}" name="${name}">
      ${opts.map(o => `<option value="${esc(o.value??o)}" ${String(o.value??o)===String(value)?"selected":""}>${esc(o.label??o)}</option>`).join("")}
    </select></div>`;
}

function openModal(kind, id = null, linkCase = null) {
  const isEdit = Boolean(id);
  const titles = {
    project: isEdit ? "Edit project" : "Create project",
    requirement: isEdit ? "Edit requirement" : "Add requirement",
    case: isEdit ? "Edit test case" : "Add test case",
    execution: "Record execution",
    bug: isEdit ? "Update defect" : "Log defect",
  };
  const eyebrows = { project: "PROJECT SETUP", requirement: "REQUIREMENT", case: "TEST CASE", execution: "EXECUTION", bug: "DEFECT" };
  const data =
    kind === "project" ? S.projects.find(x => String(x.id) === String(id))
    : kind === "requirement" ? S.requirements.find(x => x.id == id)
    : kind === "case" ? S.cases.find(x => x.id == id)
    : kind === "bug" ? S.bugs.find(x => x.id == id)
    : null;
  const linkedCase = kind === "bug" && linkCase ? S.cases.find(x => x.id == linkCase) : null;

  $("#saveModal").hidden = false;
  $("#modalEyebrow").textContent = eyebrows[kind] || kind.toUpperCase();
  $("#modalTitle").textContent = titles[kind] || "Edit";
  $("#formError").textContent = "";

  const reqOpts = [{ value: "", label: "Not linked" }, ...S.requirements.map(r => ({ value: r.id, label: `${r.requirement_key} · ${r.title}` }))];
  let fields = "";

  if (kind === "project") {
    fields =
      field("name", "Project name", data?.name || "", "text", true, "e.g. Customer Portal", true) +
      field("product_type", "Type of product", data?.product_type || "", "text", false, "e.g. Web application") +
      field("project_key", "Project key", data?.project_key || "", "text", false, "e.g. CP001", true) +
      field("qa_owner", "QA owner", data?.qa_owner || "", "text", false, "Your name", true) +
      field("version", "Version", data?.version || "", "text", false, "1.0.0") +
      field("environment", "Environment", data?.environment || "", "text", false, "QA") +
      selectField("status", "Status", ["Active", "Inactive"], data?.status || "Active") +
      field("description", "Description", data?.description || "", "textarea", true, "Project context");
  }
  if (kind === "requirement") {
    fields =
      field("title", "Title", data?.title || "", "text", true, "Describe the requirement", true) +
      field("description", "Description", data?.description || "", "textarea", true, "Requirement details") +
      field("acceptance_criteria", "Acceptance criteria", data?.acceptance_criteria || "", "textarea", false, "Expected behavior") +
      field("business_rules", "Business rules", data?.business_rules || "", "textarea", false, "Business rules") +
      field("validation_rules", "Validation rules", data?.validation_rules || "", "textarea", false, "Input constraints") +
      field("dependencies", "Dependencies", data?.dependencies || "", "textarea", false, "Related requirements") +
      selectField("priority", "Priority", ["High", "Medium", "Low"], data?.priority || "Medium") +
      selectField("module_type", "Type", ["Functional", "Non-Functional"], data?.module_type || "Functional") +
      selectField("module_category", "Category", ["Functional", "Performance", "Security", "Usability", "Compatibility", "Reliability", "Other"], data?.module_category || "Functional") +
      selectField("review_status", "Review status", ["Needs review", "Reviewed"], data?.review_status || "Needs review");
  }
  if (kind === "case") {
    fields =
      field("scenario", "Scenario", data?.scenario || "", "text", true, "e.g. Login with valid credentials", true) +
      selectField("requirement_id", "Requirement", reqOpts, data?.requirement_id || "") +
      selectField("test_type", "Type", ["Functional","Negative","Boundary","Validation","Permission","API","Integration","Performance","Security","Usability","Compatibility","Reliability","Non-Functional"], data?.test_type || "Functional") +
      field("description", "Description", data?.description || "", "textarea", true, "What this test verifies") +
      field("steps", "Steps", data?.steps || "", "textarea", true, "Steps, one per line") +
      field("expected_result", "Expected result", data?.expected_result || "", "textarea", true, "Observable outcome") +
      selectField("status", "Status", ["Not Executed","In Progress","Pass","Fail","Blocked","Skipped"], data?.status || "Not Executed") +
      field("actual_result", "Actual result", data?.actual_result || "", "textarea", false, "What happened");
  }
  if (kind === "execution") {
    const c = S.cases.find(x => x.id == id);
    fields =
      `<div class="field full"><div class="info-box"><strong>${esc(c?.case_key)} · ${esc(c?.scenario)}</strong><br>${pretty(c?.steps||"No steps")}<br><br><span style="color:var(--muted)">Expected: ${esc(c?.expected_result||"—")}</span></div></div>` +
      selectField("status", "Status", ["Not Executed","In Progress","Pass","Fail","Blocked","Skipped"], c?.status || "Not Executed") +
      field("executed_by", "Executed by", S.project?.qa_owner || "") +
      field("actual_result", "Actual result", c?.actual_result || "", "textarea", true, "What happened?") +
      field("comments", "Comments", "", "textarea", false, "Optional notes") +
      `<div class="field full"><label for="f_evidence">Screenshot (PNG/JPEG/GIF/WebP, max 5 MB)</label><input id="f_evidence" name="evidence" type="file" accept="image/png,image/jpeg,image/gif,image/webp"></div>`;
  }
  if (kind === "bug") {
    const copied = linkedCase ? `Test case: ${linkedCase.case_key} — ${linkedCase.scenario}\nSteps: ${linkedCase.steps||""}\nExpected: ${linkedCase.expected_result||""}\nActual: ${linkedCase.actual_result||""}` : "";
    fields =
      field("title", "Title", data?.title || (linkedCase ? `${linkedCase.case_key}: ${linkedCase.scenario} failed` : ""), "text", true, "Short defect summary", true) +
      selectField("test_case_id", "Linked test case", [{ value: "", label: "No linked case" }, ...S.cases.map(c => ({ value: c.id, label: `${c.case_key} · ${c.scenario}` }))], data?.test_case_id || linkCase || "") +
      field("description", "Description", data?.description || copied, "textarea", true, "Steps, expected vs actual") +
      selectField("severity", "Severity", ["Critical","High","Medium","Low"], data?.severity || "Medium") +
      selectField("priority", "Priority", ["P1","P2","P3","P4"], data?.priority || "P2") +
      selectField("status", "Status", ["Open","In Progress","Resolved","Closed"], data?.status || "Open") +
      field("assigned_to", "Assigned to", data?.assigned_to || "", "text", false, "Developer name");
  }

  $("#modalFields").innerHTML = fields;
  $("#saveModal").textContent =
    kind === "execution" ? "Save execution"
    : kind === "project" ? (isEdit ? "Save project" : "Create project")
    : isEdit ? "Save changes"
    : kind === "bug" ? "Log defect"
    : `Add ${kind}`;

  modalForm.onsubmit = async e => {
    e.preventDefault();
    $("#formError").textContent = "";
    const payload = Object.fromEntries(new FormData(modalForm).entries());
    try {
      if (kind === "project") {
        const proj = isEdit
          ? await api(`/api/projects/${id}`, { method: "PATCH", body: JSON.stringify(payload) })
          : await api("/api/projects", { method: "POST", body: JSON.stringify(payload) });
        modal.close();
        if (!isEdit) S.view = "overview";
        await loadProjects(isEdit ? S.project?.id : proj.id);
        toast(isEdit ? "Project updated" : "Project created");
        return;
      }
      const suffix = { requirement: "requirements", case: "test-cases", bug: "bugs" }[kind];
      if (kind === "case") {
        const { status, actual_result } = payload;
        delete payload.status; delete payload.actual_result;
        if (isEdit) {
          await api(pp(`${suffix}/${id}`), { method: "PATCH", body: JSON.stringify(payload) });
          const ex = S.cases.find(c => String(c.id) === String(id));
          if (ex && (ex.status !== status || ex.actual_result !== actual_result)) {
            await api(pp(`executions/${id}`), { method: "POST", body: JSON.stringify({ status, actual_result, executed_by: S.project.qa_owner }) });
          }
        } else {
          const created = await api(pp(suffix), { method: "POST", body: JSON.stringify(payload) });
          if (status !== "Not Executed" || actual_result) {
            await api(pp(`executions/${created.id}`), { method: "POST", body: JSON.stringify({ status, actual_result, executed_by: S.project.qa_owner }) });
          }
        }
        modal.close(); await refresh(); toast(isEdit ? "Test case updated" : "Test case added"); return;
      }
      if (kind === "execution") {
        const evidFile = modalForm.elements.evidence?.files[0];
        delete payload.evidence;
        const exec = await api(pp(`executions/${id}`), { method: "POST", body: JSON.stringify(payload) });
        if (evidFile) {
          try { await uploadEvidence(exec.id, evidFile); }
          catch (ev) { modal.close(); await refresh(); toast(`Execution saved; evidence failed: ${ev.message}`); return; }
        }
        modal.close(); await refresh(); toast("Execution saved"); return;
      }
      if (isEdit) await api(pp(`${suffix}/${id}`), { method: "PATCH", body: JSON.stringify(payload) });
      else         await api(pp(suffix), { method: "POST", body: JSON.stringify(payload) });
      modal.close(); await refresh();
      toast(kind === "bug" && !isEdit ? "Defect logged" : "Saved");
    } catch (e) { $("#formError").textContent = e.message; }
  };
  modal.showModal();
}

async function uploadEvidence(execId, file) {
  if (file.size > 5 * 1024 * 1024) throw new Error("Image too large (max 5 MB)");
  if (!["image/png","image/jpeg","image/gif","image/webp"].includes(file.type)) throw new Error("Choose PNG, JPEG, GIF, or WebP");
  const dataUrl = await new Promise((res, rej) => {
    const fr = new FileReader(); fr.onload = () => res(fr.result); fr.onerror = () => rej(new Error("Read failed")); fr.readAsDataURL(file);
  });
  await api(pp(`executions/${execId}/evidence`), {
    method: "POST",
    body: JSON.stringify({ filename: file.name, media_type: file.type, image_data: String(dataUrl).split(",", 2)[1] }),
  });
}

/* ════════════════════════════════════════════════════════════════════
   BOOTSTRAP
════════════════════════════════════════════════════════════════════ */
$("#projectSelect").addEventListener("change", async e => {
  S.project = S.projects.find(p => String(p.id) === e.target.value) || null;
  S.view = "overview"; S.viewHistory = [];
  if (S.project) await refresh();
  else { S.requirements=[]; S.requirementDocs=[]; S.cases=[]; S.bugs=[]; S.apiDocs=[]; S.applications=[]; S.analyses=[]; render(); }
});

/* Top nav */
$("#topNav").addEventListener("click", e => {
  const btn = e.target.closest(".nav-item");
  if (!btn) return;
  navigate(btn.dataset.view);
  render();
});

/* Project nav */
$("#projectNav").addEventListener("click", e => {
  const btn = e.target.closest(".nav-item");
  if (!btn) return;
  navigate(btn.dataset.view);
  render();
});

/* Bottom nav (Settings) */
$("#bottomNav").addEventListener("click", e => {
  const btn = e.target.closest(".nav-item");
  if (!btn) return;
  S.settingsTab = "ai";
  navigate("settings");
  render();
});

/* Modal close */
$("#closeModal").onclick = () => modal.close();
$("#cancelModal").onclick = () => modal.close();
modal.addEventListener("close", () => { $("#saveModal").hidden = false; });

/* Splash create */
$("#splashCreateBtn")?.addEventListener("click", () => openModal("project"));

/* Load on start */
loadProjects().catch(e => toast(e.message));
