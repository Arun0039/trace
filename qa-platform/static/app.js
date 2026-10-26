const $ = (s, root=document) => root.querySelector(s);
const $$ = (s, root=document) => [...root.querySelectorAll(s)];
const state = { project: null, projects: [], view: "projects", requirements: [], requirementDocuments: [], cases: [], bugs: [], apiDocs: [], applications: [], analyses: [], projectFiles: [], inventoryVisible: {}, reaRuntime: null, aiSettings: {ollama_base_url:"http://127.0.0.1:11434",ollama_model:""}, ollamaModels:[], caseGenerator:"deterministic",ollamaStatus:"" };
const modal = $("#modal"), form = $("#modalForm");
let analysisPoll = null;
const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const pretty = value => esc(value).replace(/\n/g, "<br>");
async function api(path, options={}) {
  let response;
  try {
    response = await fetch(path, {headers:{"Content-Type":"application/json"}, ...options});
  } catch(error) {
    if(error instanceof TypeError) {
      throw new Error("The page loaded, but it could not reach the QA server API. Keep the start-qa-platform.bat console open, check the error printed there, and restart the launcher if the server has stopped.");
    }
    throw error;
  }
  const data = response.headers.get("content-type")?.includes("application/json") ? await response.json() : null;
  if (!response.ok) throw new Error(data?.error || `Request failed (${response.status})`);
  return data;
}
function toast(message) { const el=$("#toast"); el.textContent=message; el.classList.add("show"); setTimeout(()=>el.classList.remove("show"),2700); }
function projectPath(suffix) { return `/api/projects/${state.project.id}/${suffix}`; }
async function loadProjects(preferred) {
  state.projects=await api("/api/projects");
  state.aiSettings=await api("/api/ai/settings");
  const select=$("#projectSelect");
  select.innerHTML='<option value="">Choose a project</option>'+state.projects.map(p=>`<option value="${p.id}">${esc(p.name)} · ${esc(p.product_type||p.project_key)}</option>`).join("");
  const id=preferred || state.project?.id;
  state.project=state.projects.find(p=>String(p.id)===String(id))||null;
  select.value=state.project?.id||"";
  if(state.project) await refresh();
  else await render();
}
async function refresh() {
  if(!state.project) return render();
  [state.requirements,state.requirementDocuments,state.cases,state.bugs,state.apiDocs,state.applications,state.analyses]=await Promise.all([
    api(projectPath("requirements")),api(projectPath("requirements/documents")),api(projectPath("test-cases")),api(projectPath("bugs")),api(projectPath("api-docs")),api(projectPath("applications")),api(projectPath("analyses"))
  ]);
  if(!state.reaRuntime) state.reaRuntime=await api("/api/rea/runtime");
  $("#reqNavCount").textContent=state.requirements.length;
  $("#caseNavCount").textContent=state.cases.length;
  $("#bugNavCount").textContent=state.bugs.length;
  $("#apiDocNavCount").textContent=state.apiDocs.length;
  $("#applicationNavCount").textContent=state.applications.length;
  $("#analysisNavCount").textContent=state.analyses.length;
  await render();
  updateAnalysisPolling();
}
async function render() {
  const settingsArea=["settings","settings-ai","archive"].includes(state.view);
  const projectArea=["project-details","requirements","cases","files","api-docs","applications","application-analysis","bugs"].includes(state.view);
  $$("#workspaceNavigation .nav-item").forEach(button=>{
    button.disabled=false;
    const active=button.dataset.view==="projects"
      ?state.view==="projects"||projectArea
      :button.dataset.view==="settings"?settingsArea:button.dataset.view===state.view;
    button.classList.toggle("active",active);
  });
  $$("#navigation .nav-item").forEach(button=>{
    button.disabled=!state.project;
    button.classList.toggle("active",button.dataset.view===state.view);
  });
  $("#projectNavigationLabel").hidden=!state.project||!projectArea;
  $("#navigation").hidden=!state.project||!projectArea;
  $("#settingsNavigationLabel").hidden=!settingsArea;
  $("#settingsNavigation").hidden=!settingsArea;
  $$("#settingsNavigation .nav-item").forEach(button=>button.classList.toggle("active",button.dataset.view===state.view));
  $("#crumb").textContent=({
    projects:"Project",dashboard:"Dashboard (Beta)",settings:"Settings (Beta)",
    "settings-ai":"AI module",archive:"Archive",
    "project-details":"Project details",requirements:"Requirements",cases:"Test cases",
    files:"Project files","api-docs":"API docs",applications:"Applications",
    "application-analysis":"App analysis",bugs:"Bug tracker"
  })[state.view]||"Project";
  if(state.view==="settings") {
    $("#exportBtn").disabled=true;
    $("#page").innerHTML=settingsHomeView();
    $$(".settings-feature-card").forEach(button=>button.addEventListener("click",()=>{state.view=button.dataset.settingsFeature;render()}));
    return;
  }
  if(state.view==="settings-ai") {
    $("#exportBtn").disabled=true;
    $("#page").innerHTML=`<div class="page-heading"><div><div class="eyebrow">SETTINGS · CHILD FEATURE</div><h1>AI module</h1><p>Configure optional local AI features for requirement and test-case workflows.</p></div></div>${settingsView()}`;
    bindSettingsActions();
    return;
  }
  if(state.view==="archive") {
    $("#exportBtn").disabled=true;
    $("#page").innerHTML=await archiveView();
    return;
  }
  if(!state.project) {
    $("#exportBtn").disabled=true;
    $("#reqNavCount").textContent=$("#caseNavCount").textContent=$("#bugNavCount").textContent=$("#apiDocNavCount").textContent=$("#applicationNavCount").textContent=$("#analysisNavCount").textContent="0";
    if(state.view==="projects") {
      $("#page").innerHTML=projectListView();
      bindProjectListActions();
    } else {
      $("#page").innerHTML='<div class="welcome-empty"><div class="empty-orbit">T</div><div class="eyebrow">SELECT A PROJECT</div><h1>Choose a project<br><span>to continue.</span></h1><p>Open Project to create a project or select an existing workspace.</p><button class="button button-primary" id="emptyProjectListBtn">Open Project</button></div>';
      $("#emptyProjectListBtn").onclick=()=>{state.view="projects";render()};
    }
    return;
  }
  $("#exportBtn").disabled=state.view==="settings"||state.view==="projects";
  if(state.view==="projects") {
    $("#page").innerHTML=projectListView();
    bindProjectListActions();
    return;
  }
  const p=state.project;
  const generationReady=state.requirements.length>0;
  const generationConfigured=state.caseGenerator!=="ollama"||Boolean(state.aiSettings.ollama_model);
  const heading=`<div class="page-heading"><div><div class="eyebrow">${esc(p.project_key)} · PROJECT WORKSPACE</div><h1>${esc(p.name)}</h1><p>${esc(p.description||"A focused view of your quality progress.")}</p><div class="project-meta">${p.product_type?`<span class="tag">PRODUCT · ${esc(p.product_type)}</span>`:""}${p.version?`<span class="tag">BUILD ${esc(p.version)}</span>`:""}${p.environment?`<span class="tag">⌖ ${esc(p.environment)}</span>`:""}<span class="tag">QA OWNER · ${esc(p.qa_owner)}</span></div></div><div class="heading-actions">${state.view==="requirements"?'<button class="button button-quiet" id="importBtn">↑ Import / provide requirements</button><button class="button button-primary" id="aiImportBtn">✦ Import with AI</button><button class="button button-quiet" id="addBtn">＋ Add requirement</button>':state.view==="api-docs"?'<button class="button button-primary" id="apiImportBtn">↑ Import OpenAPI JSON</button>':state.view==="applications"?'<button class="button button-primary" id="applicationUploadBtn">↑ Upload application ZIP</button>':state.view==="cases"?`<button class="button button-primary" id="generateCasesBtn" ${generationReady&&generationConfigured?"":"disabled"}>✦ Generate mapped test cases</button><button class="button button-quiet" id="addBtn">＋ Add test case</button>`:state.view==="bugs"?'<button class="button button-primary" id="addBtn">＋ Log bug</button>':""}<button class="button button-danger" id="deleteProjectBtn">Delete project</button></div></div>`;
  if(state.view==="dashboard") await renderDashboard(heading);
  else if(state.view==="project-details") $("#page").innerHTML=heading+projectDetailsView();
  else if(state.view==="requirements") $("#page").innerHTML=heading+requirementsTable();
  else if(state.view==="cases") $("#page").innerHTML=heading+casesTable();
  else if(state.view==="files") {
    const result=await api(projectPath("files"));
    state.projectFiles=result.files;
    $("#page").innerHTML=heading+projectFilesTable(result);
  }
  else if(state.view==="api-docs") $("#page").innerHTML=heading+apiDocsView();
  else if(state.view==="applications") $("#page").innerHTML=heading+applicationsView();
  else if(state.view==="application-analysis") $("#page").innerHTML=heading+applicationAnalysisView();
  else $("#page").innerHTML=heading+bugsTable();
  $("#addBtn")?.addEventListener("click",()=>openModal(({requirements:"requirement",cases:"case",bugs:"bug"})[state.view]));
  $("#editProjectDetailsBtn")?.addEventListener("click",()=>openModal("project",state.project.id));
  $$(".project-module-card").forEach(button=>button.addEventListener("click",()=>{state.view=button.dataset.projectModule;render()}));
  $("#importBtn")?.addEventListener("click",()=>importRequirements());
  $("#aiImportBtn")?.addEventListener("click",()=>importRequirements("ollama"));
  $("#pasteRequirementBtn")?.addEventListener("click",pasteRequirement);
  $("#configureModelBtn")?.addEventListener("click",()=>{state.view="settings-ai";render()});
  $("#openSettingsBtn")?.addEventListener("click",()=>{state.view="settings-ai";render()});
  $("#apiImportBtn")?.addEventListener("click",importApiDoc);
  $("#applicationUploadBtn")?.addEventListener("click",importApplicationZip);
  $("#applicationUploadInlineBtn")?.addEventListener("click",importApplicationZip);
  $$(".validate-coverage").forEach(button=>button.addEventListener("click",()=>validateApplicationCoverage(button.dataset.application)));
  $("#generateCasesBtn")?.addEventListener("click",generateTestCases);
  $("#caseGenerator")?.addEventListener("change",event=>{
    state.caseGenerator=event.target.value;
    render();
  });
  $("#startAnalysisBtn")?.addEventListener("click",startApplicationAnalysis);
  $("#checkReaRuntimeBtn")?.addEventListener("click",checkReaRuntime);
  $("#exportBtn").onclick=()=>{location.href=projectPath("report/csv")};
  $("#deleteProjectBtn")?.addEventListener("click",()=>deleteProject(state.project.id));
  $$(".case-status-select").forEach(select=>select.addEventListener("change",async()=>{
    const testCase=state.cases.find(item=>String(item.id)===select.dataset.id);
    if(!testCase) return;
    select.disabled=true;
    try {
      await api(projectPath(`executions/${testCase.id}`),{method:"POST",body:JSON.stringify({
        status:select.value,actual_result:testCase.actual_result||"",executed_by:state.project.qa_owner
      })});
      await refresh();
      toast("Test case status updated");
    } catch(error) {
      select.value=testCase.status;
      select.disabled=false;
      toast(error.message);
    }
  }));
  $$(".delete-case").forEach(button=>button.addEventListener("click",async()=>{
    const testCase=state.cases.find(item=>String(item.id)===button.dataset.id);
    if(!testCase || !window.confirm(`Delete ${testCase.case_key} — ${testCase.scenario}? Its execution history will also be deleted.`)) return;
    try {
      await api(projectPath(`test-cases/${testCase.id}`),{method:"DELETE"});
      await refresh();
      toast("Test case deleted");
    } catch(error) {toast(error.message);}
  }));
  $$(".download-project-file").forEach(button=>button.addEventListener("click",()=>{
    location.href=projectPath(`files/download?path=${encodeURIComponent(button.dataset.path)}`);
  }));
  $("#searchTable")?.addEventListener("input",filterTable);
  $$(".edit-record").forEach(b=>b.addEventListener("click",()=>openModal(b.dataset.kind,b.dataset.id)));
  $$(".delete-requirement").forEach(b=>b.addEventListener("click",async()=>{
    const requirement=state.requirements.find(item=>String(item.id)===b.dataset.id);
    if(!requirement) return;
    const linked=Number(requirement.test_case_count||0);
    const warning=linked
      ? ` ${linked} linked test case${linked===1?"":"s"} will remain, but will no longer be linked to a requirement.`
      : "";
    if(!window.confirm(`Delete ${requirement.requirement_key} — ${requirement.title}?${warning}`)) return;
    try {
      const result=await api(projectPath(`requirements/${requirement.id}`),{method:"DELETE"});
      await refresh();
      toast(`Requirement deleted${result.unlinked_test_cases?`; ${result.unlinked_test_cases} test case${result.unlinked_test_cases===1?"":"s"} unlinked`:""}`);
    } catch(error) {toast(error.message);}
  }));
  $$(".execute-record").forEach(b=>b.addEventListener("click",()=>openModal("execution",b.dataset.id)));
  $$(".bug-from-case").forEach(b=>b.addEventListener("click",()=>openModal("bug",null,b.dataset.case)));
  $$(".evidence-record").forEach(b=>b.addEventListener("click",()=>showEvidence(b.dataset.case)));
  $$(".load-inventory").forEach(b=>b.addEventListener("click",()=>{
    const id=b.dataset.application;
    state.inventoryVisible[id]=(state.inventoryVisible[id]||100)+100;
    render();
  }));
  bindSettingsActions();
}
function projectListView() {
  const cards=state.projects.map(project=>`<tr data-project-search="${esc(`${project.name} ${project.product_type||""} ${project.project_key} PRJ-${String(project.id).padStart(4,"0")} ${project.id}`.toLowerCase())}" data-project-status="${esc(project.status||"Active")}">
    <td><strong class="cell-main">${esc(project.name)}</strong><span class="cell-sub">${esc(project.product_type||"Product type not set")}</span></td>
    <td class="mono">PRJ-${String(project.id).padStart(4,"0")}</td>
    <td>${esc(project.project_key)}</td>
    <td><span class="badge ${project.status==="Inactive"?"Not-Executed":"covered"}">${esc(project.status||"Active")}</span></td>
    <td>${esc(formatProjectDate(project.created_at))}</td>
    <td>${esc(formatProjectDate(project.updated_at))}</td>
    <td><div class="row-actions project-row-actions">
      <button class="link-button open-project" data-id="${project.id}">View</button>
      <button class="link-button project-details-action" data-id="${project.id}">Details</button>
      <button class="link-button edit-project" data-id="${project.id}">Edit</button>
      <button class="link-button delete-project" data-id="${project.id}">Delete</button>
    </div></td></tr>`).join("");
  return `<div class="page-heading"><div><div class="eyebrow">PROJECT MODULE</div><h1>Project</h1><p>Manage projects and open a project workspace to work with its child modules.</p></div><div class="heading-actions"><button class="button button-primary" id="createProjectBtn">＋ Create Project</button></div></div>
    <section class="panel project-list-tools"><label class="sr-only" for="projectSearch">Search projects</label><input class="search-input" id="projectSearch" placeholder="⌕  Search by project name or ID"><label class="sr-only" for="projectStatusFilter">Filter by status</label><select class="project-status-filter" id="projectStatusFilter"><option value="">All statuses</option><option>Active</option><option>Inactive</option></select></section>
    <div class="table-wrap"><table class="data-table project-list-table"><thead><tr><th>Project name</th><th>Project ID</th><th>Project key</th><th>Status</th><th>Created date</th><th>Updated date</th><th>Actions</th></tr></thead><tbody>${cards||'<tr><td colspan="7" class="empty-row"><strong>No projects yet</strong>Create a project to get started, then manage requirements, test cases, applications, API docs, analysis, and bugs inside its workspace.</td></tr>'}<tr id="projectNoResults" hidden><td colspan="7" class="empty-row">No projects match the current search or status filter.</td></tr></tbody></table></div>`;
}
function formatProjectDate(value) {
  if(!value) return "—";
  const date=new Date(value);
  return Number.isNaN(date.valueOf())?value:date.toLocaleDateString();
}
function bindProjectListActions() {
  $("#createProjectBtn")?.addEventListener("click",()=>openModal("project"));
  $("#projectSearch")?.addEventListener("input",filterProjects);
  $("#projectStatusFilter")?.addEventListener("change",filterProjects);
  $$(".open-project").forEach(button=>button.addEventListener("click",()=>openProject(button.dataset.id,"dashboard")));
  $$(".project-details-action").forEach(button=>button.addEventListener("click",()=>openProject(button.dataset.id,"project-details")));
  $$(".edit-project").forEach(button=>button.addEventListener("click",()=>openModal("project",button.dataset.id)));
  $$(".delete-project").forEach(button=>button.addEventListener("click",()=>deleteProject(button.dataset.id)));
}
function filterProjects() {
  const query=($("#projectSearch")?.value||"").trim().toLowerCase();
  const status=$("#projectStatusFilter")?.value||"";
  $$("[data-project-search]").forEach(row=>{
    row.hidden=!row.dataset.projectSearch.includes(query)||(status&&row.dataset.projectStatus!==status);
  });
  const rows=$$("[data-project-search]");
  const noResults=$("#projectNoResults");
  if(noResults) noResults.hidden=rows.length===0||rows.some(row=>!row.hidden);
}
async function openProject(id, view="dashboard") {
  state.project=state.projects.find(project=>String(project.id)===String(id))||null;
  state.view=view;
  if($("#projectSelect")) $("#projectSelect").value=state.project?.id||"";
  if(state.project) await refresh();
}
async function deleteProject(id) {
  const project=state.projects.find(item=>String(item.id)===String(id));
  if(!project || !window.confirm(
    `Delete project ${project.name} (${project.project_key})? Its records will be removed. Project files will be archived.`
  )) return;
  try {
    const result=await api(`/api/projects/${project.id}`,{method:"DELETE"});
    if(String(state.project?.id)===String(project.id)) state.project=null;
    state.view="projects";
    await loadProjects();
    toast(result.files_archived?"Project deleted; project files archived.":"Project deleted.");
  } catch(error) {
    toast(`Could not delete project: ${error.message}`);
  }
}
function projectDetailsView() {
  const modules=[
    ["requirements","Requirement","Requirements and acceptance criteria",state.requirements.length],
    ["cases","Test Case","Mapped test scenarios and execution results",state.cases.length],
    ["applications","Application","Uploaded application builds and coverage",state.applications.length],
    ["api-docs","API Documentation","Imported API specifications",state.apiDocs.length],
    ["application-analysis","App Analysis","Static application analysis history",state.analyses.length],
    ["bugs","Bug Tracker","Project issue tracking",state.bugs.length],
  ];
  return `<section class="panel"><div class="panel-head"><div><div class="panel-title">${esc(state.project.name)}</div><div class="panel-caption">Project ID PRJ-${String(state.project.id).padStart(4,"0")} · ${esc(state.project.project_key)}</div></div><button class="button button-quiet" id="editProjectDetailsBtn">Edit project</button></div>
    <div class="project-details-grid"><div><span>Type of product</span><strong>${esc(state.project.product_type||"—")}</strong></div><div><span>Status</span><strong>${esc(state.project.status||"Active")}</strong></div><div><span>QA owner</span><strong>${esc(state.project.qa_owner)}</strong></div><div><span>Version</span><strong>${esc(state.project.version||"—")}</strong></div><div><span>Environment</span><strong>${esc(state.project.environment||"—")}</strong></div><div><span>Description</span><strong>${esc(state.project.description||"—")}</strong></div><div><span>Created</span><strong>${esc(formatProjectDate(state.project.created_at))}</strong></div><div><span>Last updated</span><strong>${esc(formatProjectDate(state.project.updated_at))}</strong></div></div>
  </section><div class="project-module-grid">${modules.map(([view,title,description,count])=>`<button class="panel project-module-card" data-project-module="${view}"><span class="module-card-count">${count}</span><strong>${title}</strong><span>${description}</span><b>Open →</b></button>`).join("")}</div>`;
}
function settingsHomeView() {
  return `<div class="page-heading"><div><div class="eyebrow">WORKSPACE MODULE</div><h1>Settings <span class="badge beta-badge">BETA</span></h1><p>Workspace features and local application configuration.</p></div></div>
    <div class="settings-feature-grid">
      <button class="panel settings-feature-card" data-settings-feature="settings-ai"><span class="settings-feature-icon">✦</span><strong>AI module</strong><span>Connect to Ollama and select an installed local model.</span><b>Open AI settings →</b></button>
      <button class="panel settings-feature-card" data-settings-feature="archive"><span class="settings-feature-icon">▣</span><strong>Archive</strong><span>Browse retained files from deleted projects.</span><b>View archive →</b></button>
    </div>`;
}
async function archiveView() {
  try {
    const {archives}=await api("/api/archive");
    const rows=archives.map(item=>`<tr><td><strong class="cell-main">${esc(item.archive_id)}</strong><span class="cell-sub">Archived project files</span></td><td>${esc(formatProjectDate(item.archived_at))}</td><td><span class="badge">Retained</span></td></tr>`).join("");
    return `<div class="page-heading"><div><div class="eyebrow">SETTINGS · CHILD FEATURE</div><h1>Archive</h1><p>Project files retained when a project was deleted.</p></div></div>
      <section class="panel archive-note"><div class="panel-title">Archive retention</div><p class="panel-caption">Project folders are retained locally in <code>storage/archived-projects</code>. Deleting a project removes its database records; archived files are not restored as active projects.</p></section>
      <div class="table-wrap"><table class="data-table archive-table"><thead><tr><th>Archive folder</th><th>Archived date</th><th>Retention</th></tr></thead><tbody>${rows||'<tr><td colspan="3" class="empty-row"><strong>Archive is empty</strong>Deleted project folders will appear here if their files were retained.</td></tr>'}</tbody></table></div>`;
  } catch(error) {
    return `<div class="page-heading"><div><div class="eyebrow">SETTINGS · CHILD FEATURE</div><h1>Archive</h1><p>Project files retained when a project was deleted.</p></div></div><div class="analysis-error" role="alert">Could not load archive: ${esc(error.message)}</div>`;
  }
}
function bindSettingsActions() {
  $("#ollamaModel")?.addEventListener("change",async event=>{
    state.aiSettings.ollama_model=event.target.value;
    await saveOllamaSettings();
  });
  $("#saveOllamaSettingsBtn")?.addEventListener("click",saveOllamaSettings);
  $("#testOllamaConnectionBtn")?.addEventListener("click",testOllamaConnection);
}
async function renderDashboard(heading) {
  const d=await api(projectPath("dashboard"));
  const metric=(label,value,note,icon,accent="")=>`<div class="metric ${accent}"><div class="metric-label">${label}</div><div class="metric-accent">${icon}</div><div class="metric-value">${value}</div><div class="metric-note">${note}</div></div>`;
  const statuses=["Not Executed","In Progress","Pass","Fail","Blocked","Skipped"];
  const missing=state.requirements.filter(r=>!r.test_case_count);
  const rows=state.requirements.slice(0,6).map(r=>`<tr><td class="mono">${esc(r.requirement_key)}</td><td>${esc(r.title)}</td><td>${r.test_case_count||0} ${(r.test_case_count||0)===1?"case":"cases"}</td><td><span class="badge ${r.test_case_count?"covered":"Not-Covered"}">${r.test_case_count?"Covered":"Not Covered"}</span></td></tr>`).join("");
  $("#page").innerHTML=heading+`<div class="cards">${metric("Requirements",d.requirements,`${d.covered_requirements} covered`,"⌑")}${metric("Test cases",d.test_cases,`${d.executed} executed`,"☷")}${metric("Pass rate",`${d.pass_percent}%`,"of executed cases","✓","lime")}${metric("Open bugs",d.open_bugs,`${d.total_bugs} total logged`,"⌁")}</div>
  <div class="dashboard-grid"><section><div class="panel"><div class="panel-head"><div><div class="panel-title">Requirement coverage</div><div class="panel-caption">Requirements linked to at least one test case</div></div><div class="coverage-big">${d.coverage_percent}%</div></div><div class="progress"><span style="width:${d.coverage_percent}%"></span></div><div class="coverage-foot"><span>${d.covered_requirements} of ${d.requirements} requirements covered</span><span>${missing.length} uncovered</span></div></div>
  <div class="panel"><div class="panel-head"><div><div class="panel-title">Coverage matrix</div><div class="panel-caption">Traceability from requirement to test case</div></div><button class="link-button" id="goRequirements">View all →</button></div><table class="coverage-table"><thead><tr><th>Requirement</th><th>Title</th><th>Cases</th><th>Coverage</th></tr></thead><tbody>${rows||'<tr><td colspan="4" class="empty-row"><strong>No requirements yet</strong>Import a TXT/MD spec or add one manually.</td></tr>'}</tbody></table></div></section>
  <section><div class="panel"><div class="panel-head"><div><div class="panel-title">Execution status</div><div class="panel-caption">${d.executed} of ${d.test_cases} cases executed</div></div></div><div class="status-list">${statuses.map(s=>`<div class="status-line"><i class="status-dot ${s}"></i><span>${s}</span><b>${d.statuses[s]||0}</b></div>`).join("")}</div></div>
  <div class="panel"><div class="panel-head"><div><div class="panel-title">Static application analysis</div><div class="panel-caption">REA JavaScript and Electron source reconstruction</div></div><span class="badge">${state.reaRuntime?.analysis_enabled?"REA CLI READY":"REA CLI UNAVAILABLE"}</span></div><div class="integration-note"><strong>Analyze a local directory or ASAR without executing its code.</strong> Results and history are stored in this project. <button class="link-button" id="goAnalysis">Open application analysis →</button></div></div></section></div>`;
  $("#goRequirements").onclick=()=>{state.view="requirements";render()};
  $("#goAnalysis").onclick=()=>{state.view="application-analysis";render()};
}
function analysisMetric(label,value) {
  return `<div class="analysis-stat"><span>${esc(label)}</span><strong>${esc(value)}</strong></div>`;
}
function analysisSummaryMarkup(summary) {
  if(!summary) return "";
  const stats=summary.statistics||{}, metrics=[];
  [["modules","Modules"],["relevant_files","Relevant files"],["parsed_javascript_files","Parsed JavaScript"],["visited_ast_nodes","Visited AST nodes"],["findings","Findings"],["text_bytes_read","Source bytes read"]].forEach(([key,label])=>{
    if(Number.isInteger(stats[key])) metrics.push(analysisMetric(label,stats[key].toLocaleString()));
  });
  const electron=summary.electron||{}, electronLabels={
    browser_windows:"Browser windows",preload_entrypoints:"Preload entrypoints",
    context_bridge_apis:"Context bridge APIs",exposed_api_members:"Exposed API members",
    native_addon_bindings:"Native add-on bindings",utility_processes:"Utility processes"
  };
  Object.entries(electronLabels).forEach(([key,label])=>{
    if(Number.isInteger(electron[key])) metrics.push(analysisMetric(label,electron[key].toLocaleString()));
  });
  if(electron.ipc&&Number.isInteger(electron.ipc.operations)) metrics.push(analysisMetric("IPC operations",electron.ipc.operations.toLocaleString()));
  const artifact=summary.artifact||{};
  const limits=(summary.limitations||[]).map(item=>`<li>${esc(item)}</li>`).join("");
  const routes=summary.routes&&Object.keys(summary.routes).length
    ? `<div class="analysis-detail"><strong>Route inventory</strong><span>${esc(JSON.stringify(summary.routes))}</span></div>`:"";
  const coverage=summary.coverage||{};
  const appCoverage=coverage.application;
  const coverageText=appCoverage?.status?`Application graph coverage: ${appCoverage.status}`:"";
  return `<div class="analysis-result">
    <div class="analysis-result-head"><span class="badge covered">Completed · ${esc(artifact.format||"format not reported")}</span><span class="mono">${esc(artifact.sha256||"Artifact hash not reported")}</span></div>
    ${artifact.sha256?`<div class="analysis-detail"><strong>Artifact SHA-256</strong><code>${esc(artifact.sha256)}</code></div>`:""}
    ${metrics.length?`<div class="analysis-stats">${metrics.join("")}</div>`:""}
    ${coverageText?`<div class="analysis-detail">${esc(coverageText)}</div>`:""}
    ${routes}
    ${limits?`<details class="analysis-limitations"><summary>Limitations (${summary.limitations.length})</summary><ul>${limits}</ul></details>`:""}
  </div>`;
}
function applicationAnalysisView() {
  const runtime=state.reaRuntime||{};
  const history=state.analyses.map(item=>{
    const badge=item.status==="completed"?"covered":item.status==="failed"?"Fail":item.status;
    return `<article class="analysis-card">
      <div class="analysis-card-head"><div><span class="badge ${esc(badge)}">${esc(item.status)}</span><span class="cell-sub">${esc(item.created_at)}</span></div><span class="mono">#${item.id}</span></div>
      <div class="analysis-path">${esc(item.input_path)}</div>
      ${item.error?`<div class="analysis-error" role="alert">${esc(item.error)}</div>`:""}
      ${analysisSummaryMarkup(item.summary)}
    </article>`;
  }).join("");
  return `<section class="panel analysis-launch">
    <div class="panel-head"><div><div class="panel-title">Analyze a local JavaScript application</div><div class="panel-caption">REA statically inspects a directory or ASAR on this server. It does not run application code.</div></div><span class="badge">${runtime.analysis_enabled?"REA CLI READY":"REA CLI UNAVAILABLE"}</span></div>
    <div id="reaRuntimeStatus" class="integration-note" role="status" aria-live="polite">${runtime.analysis_enabled?"Ready · REA static analysis via CLI. No MCP integration is used.":esc(runtime.reason||"Checking REA runtime availability.")}</div>
    <div class="analysis-form">
      <label for="applicationPath">Absolute path on this computer/server</label>
      <input id="applicationPath" type="text" autocomplete="off" spellcheck="false" placeholder="C:\\path\\to\\application or /path/to/application" aria-describedby="applicationPathHelp">
      <p id="applicationPathHelp" class="panel-caption">Enter the exact absolute path of an extracted application directory or a .asar file. Archives are not uploaded or extracted; other file types are rejected.</p>
      <div class="analysis-actions"><button class="button button-primary" id="startAnalysisBtn" ${runtime.analysis_enabled?"":"disabled"}>Start static analysis</button><button class="button button-quiet" id="checkReaRuntimeBtn">Check REA availability</button></div>
      <p class="panel-caption">One analysis runs at a time. Jobs time out after ${runtime.timeout_seconds||600} seconds. Output is size-limited and temporary full Evidence is deleted after its summary is saved.</p>
    </div>
  </section>
  <section class="panel"><div class="panel-head"><div><div class="panel-title">Project analysis history</div><div class="panel-caption">Results are isolated to ${esc(state.project.project_key)} and retained in this local SQLite workspace.</div></div><span class="badge">${state.analyses.length} ${state.analyses.length===1?"analysis":"analyses"}</span></div>
    <div class="analysis-history">${history||'<div class="empty-row"><strong>No application analyses yet</strong>Enter a server-local directory or ASAR path above to begin.</div>'}</div>
  </section>`;
}
async function checkReaRuntime() {
  const button=$("#checkReaRuntimeBtn");
  if(button) button.disabled=true;
  try {
    state.reaRuntime=await api("/api/rea/runtime");
    if(state.view==="application-analysis") await render();
  } catch(error) { toast(error.message); }
  finally { if($("#checkReaRuntimeBtn")) $("#checkReaRuntimeBtn").disabled=false; }
}
async function startApplicationAnalysis() {
  const path=$("#applicationPath")?.value;
  const button=$("#startAnalysisBtn");
  if(!path) { $("#reaRuntimeStatus").textContent="Enter an absolute directory or .asar path."; return; }
  if(button) button.disabled=true;
  try {
    await api(projectPath("analyses"),{method:"POST",body:JSON.stringify({input_path:path})});
    toast("REA static analysis queued.");
    await refresh();
  } catch(error) {
    $("#reaRuntimeStatus").textContent=error.message;
  } finally {
    if($("#startAnalysisBtn")) $("#startAnalysisBtn").disabled=!state.reaRuntime?.analysis_enabled;
  }
}
async function validateApplicationCoverage(applicationId) {
  const button=$(`.validate-coverage[data-application="${applicationId}"]`);
  if(button) {button.disabled=true;button.textContent="Checking source evidence…";}
  try {
    const result=await api(projectPath(`applications/${applicationId}/validate-requirements`),{
      method:"POST",
      body:JSON.stringify({}),
    });
    await refresh();
    toast(`Coverage estimate saved for ${result.coverage.length} requirements. Review cited code evidence.`);
  } catch(error) {
    toast(error.message);
  } finally {
    const current=$(`.validate-coverage[data-application="${applicationId}"]`);
    if(current) {current.disabled=false;current.textContent="Validate requirement coverage";}
  }
}
function updateAnalysisPolling() {
  const active=state.analyses.some(item=>item.status==="queued"||item.status==="running");
  if(active&&!analysisPoll) analysisPoll=setTimeout(async()=>{analysisPoll=null;await refresh()},1500);
  if(!active&&analysisPoll){clearTimeout(analysisPoll);analysisPoll=null;}
}
function requirementsTable() {
  const documents=state.requirementDocuments.map(document=>`<a class="tag" href="${projectPath(`requirements/documents/${document.id}`)}">↓ ${esc(document.filename)} · ${Number(document.requirement_count)} requirements · ${esc(document.uploaded_at)}</a>`).join(" ");
  return `<section class="panel workflow-note"><div class="panel-title">Step 1 · Provide requirement source</div><p class="panel-caption">Upload a DOCX, Markdown, or text file, paste raw text, or add a structured requirement. Choose <strong>Import with AI</strong> to have your configured local Ollama model structure the source into testable requirements. Review all AI drafts before relying on them.</p><details><summary>Paste raw requirement text</summary><textarea id="rawRequirementInput" maxlength="1000000" placeholder="Describe the product behavior or paste requirement notes here."></textarea><button class="button button-primary" id="pasteRequirementBtn">Structure text with local AI</button></details><button class="link-button" id="configureModelBtn">Configure Ollama in Test cases</button></section><section class="panel"><div class="panel-head"><div><div class="panel-title">Requirement source documents</div><div class="panel-caption">Original files are retained in this project's local storage. Extracted requirements need QA review.</div></div><span class="badge">${state.requirementDocuments.length} ${state.requirementDocuments.length===1?"document":"documents"}</span></div>${documents||'<div class="muted">No requirement document imported yet.</div>'}</section>
  <div class="filter-row"><input class="search-input" id="searchTable" placeholder="⌕  Search requirements"></div><div class="table-wrap"><table class="data-table"><thead><tr><th>Requirement</th><th>Priority</th><th>Coverage</th><th>Review</th><th>Source</th><th></th></tr></thead><tbody>${state.requirements.map(r=>`<tr data-search="${esc(`${r.requirement_key} ${r.title} ${r.description} ${r.acceptance_criteria} ${r.business_rules} ${r.validation_rules} ${r.dependencies}`.toLowerCase())}"><td><span class="cell-main mono">${esc(r.requirement_key)}</span><span class="cell-sub">${esc(r.title)}${r.description?` · ${esc(r.description)}`:""}${r.source_section?` · Section: ${esc(r.source_section)}`:""}</span></td><td><span class="badge">${esc(r.priority)}</span></td><td><span class="badge ${r.test_case_count?"covered":"Not-Covered"}">${r.test_case_count?`${r.test_case_count} linked`:"Not Covered"}</span></td><td><span class="badge ${r.review_status.replace(" ","-")}">${esc(r.review_status)}</span></td><td class="muted">${esc(r.source_document||"Manual")}</td><td><div class="row-actions"><button class="link-button edit-record" data-kind="requirement" data-id="${r.id}">Review / edit</button><button class="link-button delete-requirement" data-id="${r.id}" aria-label="Delete ${esc(r.requirement_key)}">Delete</button></div></td></tr>`).join("")||'<tr><td colspan="6" class="empty-row"><strong>No requirements in this project</strong>Add one manually or import a DOCX, TXT, or Markdown document for review.</td></tr>'}</tbody></table></div>`;
}
function apiDocsView() {
  const endpoints=state.apiDocs.flatMap(doc=>doc.endpoints.map(endpoint=>({...endpoint,doc})));
  const docs=state.apiDocs.map(doc=>`<span class="tag">${esc(doc.title)} · ${esc(doc.spec_version)} · ${doc.endpoints.length} operations</span>`).join(" ");
  return `<div class="panel"><div class="panel-head"><div><div class="panel-title">Project API specifications</div><div class="panel-caption">Imported local OpenAPI 3.x / Swagger 2.0 JSON documents. URLs are never fetched.</div></div><span class="badge">${state.apiDocs.length} ${state.apiDocs.length===1?"document":"documents"}</span></div>${docs||'<div class="muted">No API documents imported yet.</div>'}</div>
  <div class="filter-row"><input class="search-input" id="searchTable" placeholder="⌕  Search endpoints"></div><div class="table-wrap"><table class="data-table endpoint-table"><thead><tr><th>Method</th><th>Path</th><th>Summary / operation</th><th>Tags</th><th>Specification</th></tr></thead><tbody>${endpoints.map(e=>`<tr data-search="${esc(`${e.method} ${e.path} ${e.summary} ${e.operation_id} ${e.tags.join(" ")}`.toLowerCase())}"><td><span class="method-badge method-${esc(e.method.toLowerCase())}">${esc(e.method)}</span></td><td class="mono endpoint-path">${esc(e.path)}</td><td><span class="cell-main">${esc(e.summary||"—")}</span><span class="cell-sub">${esc(e.operation_id||"No operation ID")}</span></td><td>${e.tags.map(tag=>`<span class="tag">${esc(tag)}</span>`).join(" ")||"—"}</td><td class="muted">${esc(e.doc.title)}<span class="cell-sub">${esc(e.doc.filename)}</span></td></tr>`).join("")||'<tr><td colspan="5" class="empty-row"><strong>No endpoints found</strong>Import a valid OpenAPI or Swagger JSON file to build an inventory.</td></tr>'}</tbody></table></div>`;
}
function applicationsView() {
  const cards=state.applications.map(application=>{
    const analysis=application.analysis||{};
    const visible=state.inventoryVisible[application.id]||100;
    const inventory=application.inventory||[];
    const frameworks=(analysis.frameworks||[]).map(name=>`<span class="tag">${esc(name)}</span>`).join(" ")||'<span class="muted">Not confidently identified</span>';
    const rows=inventory.slice(0,visible).map(file=>`<tr data-search="${esc(`${file.path} ${file.type} ${file.extension}`.toLowerCase())}"><td class="mono">${esc(file.path)}</td><td>${esc(file.type)}</td><td class="mono">${Number(file.size_bytes).toLocaleString()} B</td></tr>`).join("");
    const coverage=application.coverage||[];
    const coverageMarkup=coverage.length?`<section class="panel"><div class="panel-head"><div><div class="panel-title">Requirement coverage estimate</div><div class="panel-caption">Checked ${esc(application.coverage_checked_at||"")} · AI-assisted static evidence, not runtime proof</div></div></div>${application.coverage_is_stale?'<div class="analysis-error" role="alert">The requirements have changed since this check. Run validation again for a current report.</div>':""}${coverage.map(entry=>{const requirement=state.requirements.find(item=>item.id===entry.requirement_id);return `<article class="coverage-result"><div class="panel-head"><strong>${esc(requirement?.requirement_key||`Requirement #${entry.requirement_id}`)} · ${esc(requirement?.title||"")}</strong><span class="badge ${entry.status==="Covered"?"covered":entry.status==="Not found"?"Fail":"Needs-review"}">${esc(entry.status)}</span></div><p>${esc(entry.rationale)}</p>${entry.evidence.map(item=>`<div class="coverage-evidence"><code>${esc(item.file)}</code><pre>${esc(item.snippet)}</pre></div>`).join("")}</article>`}).join("")}</section>`:"";
    const findings=(title,items,label)=>`<div class="panel"><div class="panel-head"><div><div class="panel-title">${title}</div><div class="panel-caption">${items.length} statically observed</div></div></div>${items.length?`<div class="finding-list">${items.slice(0,30).map(item=>`<div class="finding-row"><strong>${esc(item[label]||item.kind||"Observation")}</strong><span class="mono">${esc(item.file)}:${esc(item.line)}</span></div>`).join("")}${items.length>30?`<p class="panel-caption">Showing 30 of ${items.length} observations.</p>`:""}`:'<div class="muted">No matching patterns were observed in the scanned source files.</div>'}</div>`;
    return `<article class="application-card">
      <div class="panel-head"><div><div class="panel-title">${esc(application.filename)}</div><div class="panel-caption">Imported ${esc(application.uploaded_at)} · ZIP ${Number(application.archive_size_bytes).toLocaleString()} bytes</div></div><span class="badge covered">${Number(analysis.total_files||inventory.length).toLocaleString()} files inventoried</span></div>
      <div class="framework-list">${frameworks}</div>
      <div class="analysis-stats">${analysisMetric("Files",analysis.total_files||inventory.length)}${analysisMetric("Source files scanned",analysis.scanned_source_files||0)}${analysisMetric("Routes",analysis.route_count||0)}${analysisMetric("UI components",analysis.component_count||0)}${analysisMetric("API call patterns",analysis.api_integration_count||0)}</div>
      <p class="panel-caption">${esc(analysis.analysis_note||"Static source inspection only. Uploaded code is never executed.")}${analysis.scan_truncated?" Some source files were skipped due to analysis size limits.":""}</p>
      <div class="integration-note"><strong>Step 3 · Validate requirements against the finalized source</strong> The configured local Ollama model receives up to 80 source excerpts (300 KB total). Each reported match must cite an exact snippet from the uploaded files. This is an estimate, not proof that functionality works at runtime. <button class="button button-primary validate-coverage" data-application="${application.id}">Validate requirement coverage</button></div>
      ${coverageMarkup}
      <div class="panel-head"><div><div class="panel-title">File inventory</div><div class="panel-caption">Type totals: ${Object.entries(analysis.file_type_counts||{}).map(([type,count])=>`${esc(type)} ${Number(count).toLocaleString()}`).join(" · ")||"No files"}</div></div></div>
      <div class="table-wrap"><table class="data-table inventory-table"><thead><tr><th>Relative path</th><th>Detected type</th><th>Size</th></tr></thead><tbody>${rows||'<tr><td colspan="3" class="empty-row">No files inventoried.</td></tr>'}</tbody></table></div>
      ${visible<inventory.length?`<div class="inventory-more"><span class="panel-caption">Showing ${Math.min(visible,inventory.length)} of ${inventory.length} files.</span><button class="button button-quiet button-small load-inventory" data-application="${application.id}">Load 100 more</button></div>`:`<p class="panel-caption">All ${inventory.length} inventoried files are shown.</p>`}
      <details class="analysis-limitations"><summary>Configuration files (${(analysis.configuration_files||[]).length})</summary><ul>${(analysis.configuration_files||[]).map(file=>`<li><code>${esc(file)}</code></li>`).join("")||"<li>No recognized configuration files found.</li>"}</ul></details>
      ${findings("Detected routes",analysis.routes||[],"route")}
      ${findings("UI component candidates",analysis.components||[],"name")}
      ${findings("API integration candidates",analysis.api_integrations||[],"kind")}
    </article>`;
  }).join("");
  return `<section class="panel application-upload-panel"><div class="panel-head"><div><div class="panel-title">Static application ZIP analysis</div><div class="panel-caption">Securely inventory an uploaded build and look for common frameworks, routes, components, and API clients.</div></div><span class="badge">CODE IS NOT EXECUTED</span></div>
    <div class="integration-note"><strong>Step 3 · Upload finalized source after requirements and test cases.</strong> ZIP paths and symlinks are rejected, archive size and expansion are capped, and only bounded text files are scanned. AI coverage validation uses only a capped selection of source excerpts and the local Ollama model; code is never sent to an external service or executed.</div>
    <div class="application-actions"><button class="button button-primary" id="applicationUploadInlineBtn">Choose application ZIP</button><span class="panel-caption">ZIP up to 25 MB · at most 3,000 entries · 100 MB extracted</span></div>
  </section>
  <div class="application-history">${cards||'<div class="panel empty-row"><strong>No application ZIP uploaded</strong>Choose a developer build ZIP to inspect its structure without running it.</div>'}</div>`;
}
function casesTable() {
  const ready=state.requirements.length>0;
  const aiMessage=state.caseGenerator==="ollama"&&!state.aiSettings.ollama_model
    ?'<div class="panel-caption">Choose an installed model in Settings (Beta) to enable AI generation.</div>':"";
  return `<div class="integration-note case-generation-note"><strong>Step 2 · Generate mapped test cases from reviewed requirements.</strong> Clicking Generate creates cases from the requirements only, with every case linked to its source requirement. Each case stores its scenario and expected result as separate fields. All generated cases are drafts; review them before execution.${!ready?'<div class="panel-caption">Add or import requirements first to enable test-case generation.</div>':""}</div>
    <section class="panel model-connection-panel"><div class="panel-head"><div><div class="panel-title">Test-case generator</div><div class="panel-caption">Choose the local deterministic generator or connect an open-source model through Ollama.</div></div><span class="badge">OPTIONAL AI</span></div>
      <div class="generator-select"><label for="caseGenerator">Generation method</label><select id="caseGenerator"><option value="deterministic" ${state.caseGenerator==="deterministic"?"selected":""}>Local deterministic (no model connection)</option><option value="ollama" ${state.caseGenerator==="ollama"?"selected":""}>Open-source AI model (Ollama)</option></select></div>
      ${aiMessage}<p class="panel-caption">Configure the local Ollama URL and installed model in <button class="link-button" id="openSettingsBtn">Settings (Beta)</button>. Deterministic generation does not require Ollama.</p>
    </section>
    <div class="filter-row"><input class="search-input" id="searchTable" placeholder="⌕  Search test cases"></div><div class="table-wrap"><table class="data-table test-cases-table"><thead><tr><th>Case ID</th><th>Requirement ID</th><th>Type</th><th>Scenario</th><th>Description</th><th>Steps</th><th>Expected result</th><th>Status</th><th>Actual result</th><th>Actions</th></tr></thead><tbody>${state.cases.map(c=>`<tr data-search="${esc(`${c.case_key} ${c.scenario} ${c.description} ${c.steps} ${c.expected_result} ${c.actual_result} ${c.requirement_key||""} ${c.test_type}`.toLowerCase())}"><td class="mono">${esc(c.case_key)}${c.generation_key?'<span class="cell-sub generated-label">GENERATED DRAFT</span>':""}</td><td class="mono">${esc(c.requirement_key||"—")}</td><td>${esc(c.test_type||"Functional")}</td><td>${esc(c.scenario)}</td><td>${esc(c.description||"—")}</td><td class="case-long-text">${pretty(c.steps||"—")}</td><td class="case-long-text">${pretty(c.expected_result||"—")}</td><td><select class="case-status-select" data-id="${c.id}" aria-label="Status for ${esc(c.case_key)}">${["Not Executed","In Progress","Pass","Fail","Blocked","Skipped"].map(status=>`<option ${status===c.status?"selected":""}>${status}</option>`).join("")}</select></td><td class="case-long-text">${pretty(c.actual_result||"—")}</td><td><div class="row-actions"><button class="link-button edit-record" data-kind="case" data-id="${c.id}">Edit</button>${c.execution_id?`<button class="link-button evidence-record" data-execution="${c.execution_id}" data-case="${c.id}">History (${c.execution_count||1})</button>`:""}${c.status==="Fail"?`<button class="link-button bug-from-case" data-case="${c.id}">＋ Bug</button>`:""}<button class="link-button delete-case" data-id="${c.id}">Delete</button></div></td></tr>`).join("")||'<tr><td colspan="10" class="empty-row"><strong>No test cases yet</strong>Generate draft cases from reviewed requirements, then upload the finalized source to validate coverage.</td></tr>'}</tbody></table></div>`;
}
function settingsView() {
  const models=state.ollamaModels.length?state.ollamaModels:state.aiSettings.ollama_model?[state.aiSettings.ollama_model]:[];
  const options=models.map(model=>`<option value="${esc(model)}" ${model===state.aiSettings.ollama_model?"selected":""}>${esc(model)}</option>`).join("");
  return `<section class="panel model-connection-panel"><div class="panel-head"><div><div class="panel-title">Local AI model</div><div class="panel-caption">Optional Ollama settings for generating test cases and validating source coverage.</div></div><span class="badge">BETA</span></div>
    <div class="ollama-fields"><label>Ollama local URL<input id="ollamaBaseUrl" value="${esc(state.aiSettings.ollama_base_url)}" placeholder="http://127.0.0.1:11434"></label><label for="ollamaModel">Choose installed model<select id="ollamaModel"><option value="">${state.ollamaModels.length?"Select an installed model":"Connect to Ollama to load models"}</option>${options}</select></label></div>
    <div class="model-actions"><button class="button button-quiet" id="saveOllamaSettingsBtn">Save model settings</button><button class="button button-quiet" id="testOllamaConnectionBtn">Connect / refresh model list</button></div>
    <p class="panel-caption">If the model list is empty, install a model with <code>ollama pull llama3.1:8b</code>, keep Ollama running, and refresh the list. Only loopback Ollama URLs are accepted. Requirement text and bounded code excerpts are sent only to the local model.</p>
    <div class="model-connection-status" id="ollamaConnectionStatus" role="status">${esc(state.ollamaStatus)}</div>
  </section>`;
}
function projectFilesTable(result) {
  const rows=result.files.map(file=>`<tr><td>${esc(file.path)}</td><td>${file.size.toLocaleString()} bytes</td><td><button class="link-button download-project-file" data-path="${esc(file.path)}">Download</button></td></tr>`).join("");
  const folderLabels={requirements:"Requirements",applications:"Applications",exports:"Exports"};
  const folders=result.folders.map(folder=>`<span class="tag">${esc(folderLabels[folder]||folder)}</span>`).join("");
  return `<section class="panel"><div class="panel-head"><div><div class="panel-title">Project folder</div><div class="panel-caption">Files are stored locally in this project's folder. CSV exports and uploaded documents appear here.</div><div class="project-meta">${folders}</div></div><span class="badge">${result.files.length} FILES</span></div>${result.truncated?'<p class="panel-caption">Showing the first 2,000 files.</p>':""}<div class="table-wrap"><table class="data-table"><thead><tr><th>File</th><th>Size</th><th></th></tr></thead><tbody>${rows||'<tr><td colspan="3" class="empty-row"><strong>No project files yet</strong>Upload a requirements document or application ZIP, or export a report.</td></tr>'}</tbody></table></div></section>`;
}
async function saveOllamaSettings() {
  try {
    state.aiSettings=await api("/api/ai/settings",{method:"POST",body:JSON.stringify({
      ollama_base_url:$("#ollamaBaseUrl").value,
      ollama_model:$("#ollamaModel").value
    })});
    state.ollamaStatus="Ollama settings saved.";
    toast("Ollama settings saved");
    await render();
  } catch(error) { toast(error.message); }
}
async function testOllamaConnection() {
  const status=$("#ollamaConnectionStatus");
  const saveButton=$("#saveOllamaSettingsBtn"), testButton=$("#testOllamaConnectionBtn");
  saveButton.disabled=testButton.disabled=true;
  if(status) status.textContent="Connecting to local Ollama…";
  try {
    state.aiSettings=await api("/api/ai/settings",{method:"POST",body:JSON.stringify({
      ollama_base_url:$("#ollamaBaseUrl").value,
      ollama_model:$("#ollamaModel").value
    })});
    const result=await api("/api/ai/ollama/test",{method:"POST",body:JSON.stringify({})});
    state.ollamaModels=result.models;
    if(state.aiSettings.ollama_model&&!result.models.includes(state.aiSettings.ollama_model)) {
      state.aiSettings=await api("/api/ai/settings",{method:"POST",body:JSON.stringify({
        ollama_base_url:state.aiSettings.ollama_base_url,
        ollama_model:"",
      })});
    }
    state.ollamaStatus=result.models.length
      ?`Connected · choose an installed model from the list: ${result.models.join(", ")}`
      :`Connected to Ollama, but no models are installed. Run ollama pull llama3.1:8b in a terminal, then refresh the model list.`;
    await render();
  } catch(error) {
    state.ollamaStatus=error.message;
    if(status) status.textContent=error.message;
  } finally {
    if($("#saveOllamaSettingsBtn")) $("#saveOllamaSettingsBtn").disabled=false;
    if($("#testOllamaConnectionBtn")) $("#testOllamaConnectionBtn").disabled=false;
  }
}
function bugsTable() {
  return `<div class="filter-row"><input class="search-input" id="searchTable" placeholder="⌕  Search bugs"></div><div class="table-wrap"><table class="data-table"><thead><tr><th>Bug</th><th>Linked case</th><th>Severity / Priority</th><th>Assigned to</th><th>Status</th><th></th></tr></thead><tbody>${state.bugs.map(b=>`<tr data-search="${esc(`${b.bug_key} ${b.title} ${b.case_key||""}`.toLowerCase())}"><td><span class="cell-main mono">${esc(b.bug_key)}</span><span class="cell-sub">${esc(b.title)}</span></td><td class="mono">${esc(b.case_key||"—")}</td><td><span class="badge">${esc(b.severity)} · ${esc(b.priority)}</span></td><td>${esc(b.assigned_to||"—")}</td><td><span class="badge ${b.status.replace(" ","-")}">${esc(b.status)}</span></td><td><button class="link-button edit-record" data-kind="bug" data-id="${b.id}">Update</button></td></tr>`).join("")||'<tr><td colspan="6" class="empty-row"><strong>No bugs logged</strong>Failures can be linked to a bug from the test case list.</td></tr>'}</tbody></table></div>`;
}
function filterTable(event) { const q=event.target.value.toLowerCase(); $$("[data-search]").forEach(row=>row.hidden=!row.dataset.search.includes(q)); }
function input(name,label,value="",type="text",full=false,placeholder="",required=false) {
  return `<div class="field ${full?"full":""}"><label for="f_${name}">${label}${required?" *":""}</label>${type==="textarea"?`<textarea id="f_${name}" name="${name}" placeholder="${esc(placeholder)}" ${required?"required":""}>${esc(value)}</textarea>`:`<input id="f_${name}" name="${name}" type="${type}" value="${esc(value)}" placeholder="${esc(placeholder)}" ${required?"required":""}>`}</div>`;
}
function selectField(name,label,options,value,full=false) { return `<div class="field ${full?"full":""}"><label for="f_${name}">${label}</label><select id="f_${name}" name="${name}">${options.map(o=>`<option value="${esc(o.value??o)}" ${String(o.value??o)===String(value)?"selected":""}>${esc(o.label??o)}</option>`).join("")}</select></div>`; }
function openModal(kind,id=null,linkCase=null) {
  const titles={project:id?"Edit project":"Create project",requirement:id?"Review requirement":"Add requirement",case:id?"Edit test case":"Add test case",execution:"Record execution",bug:id?"Update bug":"Log a bug"};
  const data=kind==="project"?state.projects.find(x=>String(x.id)===String(id)):kind==="requirement"?state.requirements.find(x=>x.id==id):kind==="case"?state.cases.find(x=>x.id==id):kind==="bug"?state.bugs.find(x=>x.id==id):null;
  const linkedCase=kind==="bug"&&linkCase?state.cases.find(x=>x.id==linkCase):null;
  $("#saveModal").hidden=false;
  $("#modalTitle").textContent=titles[kind]; $("#modalEyebrow").textContent=kind==="project"?"PROJECT SETUP":kind==="execution"?"TEST EXECUTION":kind.toUpperCase()+" MANAGEMENT";
  $("#formError").textContent="";
  const reqOptions=[{value:"",label:"Not linked"},...state.requirements.map(r=>({value:r.id,label:`${r.requirement_key} · ${r.title}`}))];
  let fields="";
  if(kind==="project") fields=input("name","Project name",data?.name||"","text",true,"e.g. Customer Portal",true)+input("product_type","Type of product",data?.product_type||"","text",false,"e.g. Web application")+input("project_key","Project key",data?.project_key||"","text",false,"e.g. CP001",true)+input("qa_owner","QA owner",data?.qa_owner||"","text",false,"Your name",true)+input("version","Application version",data?.version||"","text",false,"2.4.0")+input("environment","Environment",data?.environment||"","text",false,"QA")+selectField("status","Status",["Active","Inactive"],data?.status||"Active")+input("description","Description",data?.description||"","textarea",true,"Project context");
  if(kind==="requirement") fields=input("title","Title",data?.title||"","text",true,"Describe the requirement")+input("description","Description",data?.description||"","textarea",true,"Requirement details")+input("acceptance_criteria","Acceptance criteria",data?.acceptance_criteria||"","textarea",false,"Expected behavior")+input("business_rules","Business rules",data?.business_rules||"","textarea",false,"Rules inferred from the source document")+input("validation_rules","Validation rules",data?.validation_rules||"","textarea",false,"Input and validation constraints")+input("dependencies","Dependencies",data?.dependencies||"","textarea",false,"Related requirements or dependencies")+input("source_section","Source section",data?.source_section||"","text",false,"Heading in source document")+selectField("priority","Priority",["High","Medium","Low"],data?.priority||"Medium")+selectField("review_status","Review status",["Needs review","Reviewed"],data?.review_status||"Needs review");
  if(kind==="case") fields=input("scenario","Scenario",data?.scenario||"","text",true,"e.g. Login with valid credentials")+selectField("requirement_id","Requirement ID",reqOptions,data?.requirement_id||"")+selectField("test_type","Type",["Functional","Negative","Boundary","Validation","Permission","API","Integration"],data?.test_type||"Functional")+input("description","Description",data?.description||"","textarea",true,"What this test verifies")+input("steps","Steps",data?.steps||"","textarea",true,"Enter steps here, one per line")+input("expected_result","Expected result",data?.expected_result||"","textarea",true,"Observable expected outcome")+selectField("status","Status",["Not Executed","In Progress","Pass","Fail","Blocked","Skipped"],data?.status||"Not Executed")+input("actual_result","Actual result",data?.actual_result||"","textarea",true,"Record the observed result");
  if(kind==="execution") { const c=state.cases.find(x=>x.id==id); fields=`<div class="field full"><div class="integration-note"><strong>${esc(c?.case_key)} · ${esc(c?.scenario)}</strong><br>${pretty(c?.steps||"No steps specified")}<br><br><span class="muted">Expected: ${esc(c?.expected_result||"Not specified")}</span></div></div>`+selectField("status","Execution status",["Not Executed","In Progress","Pass","Fail","Blocked","Skipped"],c?.status||"Not Executed")+input("executed_by","Executed by",state.project.qa_owner)+input("actual_result","Actual result",c?.actual_result||"","textarea",true,"What happened when you ran the test?")+input("comments","Comments",c?.comments||"","textarea",true,"Optional notes or observations")+`<div class="field full"><label for="f_evidence">Screenshot evidence (optional, PNG/JPEG/GIF/WebP; maximum 5 MB)</label><input id="f_evidence" name="evidence" type="file" accept="image/png,image/jpeg,image/gif,image/webp"><small class="muted">Evidence is attached to this saved execution in this project's local database.</small></div>`; }
  if(kind==="bug") {
    const copied=`Test case: ${linkedCase?.case_key||""} — ${linkedCase?.scenario||""}\nSteps: ${linkedCase?.steps||""}\nExpected: ${linkedCase?.expected_result||""}\nActual: ${linkedCase?.actual_result||""}`;
    fields=input("title","Bug title",data?.title||(linkedCase?`${linkedCase.case_key}: ${linkedCase.scenario} failed`:""),"text",true,"Short summary of the defect")+selectField("test_case_id","Linked test case",[{value:"",label:"No linked case"},...state.cases.map(c=>({value:c.id,label:`${c.case_key} · ${c.scenario}`}))],data?.test_case_id||linkCase||"")+input("description","Description",data?.description||(linkedCase?copied:""),"textarea",true,"Steps, expected and actual behavior")+selectField("severity","Severity",["Critical","High","Medium","Low"],data?.severity||"Medium")+selectField("priority","Priority",["P1","P2","P3","P4"],data?.priority||"P2")+selectField("status","Status",["Open","In Progress","Resolved","Closed"],data?.status||"Open")+input("assigned_to","Assigned to",data?.assigned_to||"","text",true,"Developer or team");
  }
  $("#modalFields").innerHTML=fields;
  $("#saveModal").textContent=kind==="execution"?"Save execution":kind==="project"?(id?"Save project":"Create project"):id?"Save changes":kind==="bug"?"Log bug":`Add ${kind}`;
  form.onsubmit=async event=>{
    event.preventDefault(); $("#formError").textContent="";
    const payload=Object.fromEntries(new FormData(form).entries());
    try {
      if(kind==="project") {
        const project=id
          ?await api(`/api/projects/${id}`,{method:"PATCH",body:JSON.stringify(payload)})
          :await api("/api/projects",{method:"POST",body:JSON.stringify(payload)});
        modal.close();
        if(!id) state.view="project-details";
        await loadProjects(id?state.project?.id:project.id);
        toast(id?"Project updated":"Project created");
        return;
      }
      const suffix={requirement:"requirements",case:"test-cases",bug:"bugs"}[kind];
      if(kind==="case") {
        const status=payload.status;
        const actualResult=payload.actual_result;
        delete payload.status;
        delete payload.actual_result;
        if(id) {
          await api(projectPath(`${suffix}/${id}`),{method:"PATCH",body:JSON.stringify(payload)});
          const existing=state.cases.find(item=>String(item.id)===String(id));
          if(existing && (existing.status!==status || existing.actual_result!==actualResult)) {
            await api(projectPath(`executions/${id}`),{method:"POST",body:JSON.stringify({
              status,actual_result:actualResult,executed_by:state.project.qa_owner
            })});
          }
        } else {
          const created=await api(projectPath(suffix),{method:"POST",body:JSON.stringify(payload)});
          if(status!=="Not Executed" || actualResult) {
            await api(projectPath(`executions/${created.id}`),{method:"POST",body:JSON.stringify({
              status,actual_result:actualResult,executed_by:state.project.qa_owner
            })});
          }
        }
        modal.close();await refresh();toast(id?"Test case updated":"Test case added");return;
      }
      if(kind==="execution") {
        const evidenceFile=form.elements.evidence?.files[0];
        delete payload.evidence;
        const execution=await api(projectPath(`executions/${id}`),{method:"POST",body:JSON.stringify(payload)});
        if(evidenceFile) {
          try { await uploadEvidence(execution.id,evidenceFile); }
          catch(error) { modal.close();await refresh();toast(`Execution saved; evidence upload failed: ${error.message}`);return; }
        }
      }
      else if(id) await api(projectPath(`${suffix}/${id}`),{method:"PATCH",body:JSON.stringify(payload)});
      else await api(projectPath(suffix),{method:"POST",body:JSON.stringify(payload)});
      modal.close();await refresh();toast(kind==="execution"?"Execution saved":kind==="bug"&&!id?"Bug logged":"Changes saved");
    } catch(error) {$("#formError").textContent=error.message;}
  };
  modal.showModal();
}
function importRequirements(generator="deterministic") {
  const picker=document.createElement("input");picker.type="file";picker.accept=".docx,.txt,.md,application/vnd.openxmlformats-officedocument.wordprocessingml.document,text/plain,text/markdown";
  picker.onchange=async()=>{
    const file=picker.files[0];if(!file)return;
    const isDocx=/\.docx$/i.test(file.name);
    if(!/\.(docx|txt|md)$/i.test(file.name)){toast("Choose a DOCX, TXT, or Markdown document");return;}
    if(file.size>(isDocx?5*1024*1024:1024*1024)){toast(`Document is too large (maximum ${isDocx?"5 MB":"1 MB"})`);return;}
    try {
      let payload={filename:file.name,generator};
      if(isDocx) {
        const dataUrl=await new Promise((resolve,reject)=>{
          const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=()=>reject(new Error("Could not read DOCX file"));reader.readAsDataURL(file);
        });
        payload.document_data=String(dataUrl).split(",",2)[1];
      } else payload.content=await file.text();
      const result=await api(projectPath("requirements/import"),{method:"POST",body:JSON.stringify(payload)});
      await refresh();
      const created=`${result.count} new requirement${result.count===1?"":"s"}`;
      const skipped=result.skipped_duplicates||0;
      toast(skipped
        ? `${created} added; ${skipped} duplicate${skipped===1?"":"s"} skipped`
        : `${created} ${generator==="ollama"?"AI-generated":"extracted"} for review`);
    }
    catch(error){toast(error.message);}
  };picker.click();
}
async function pasteRequirement() {
  const input=$("#rawRequirementInput");
  const content=input?.value||"";
  if(!content.trim()) {toast("Enter requirement text to structure.");return;}
  try {
    const result=await api(projectPath("requirements/import"),{
      method:"POST",
      body:JSON.stringify({
        filename:"pasted-requirements.txt",
        content,
        generator:"ollama",
      }),
    });
    await refresh();
    toast(`${result.count} AI-generated requirement${result.count===1?"":"s"} added; review before use`);
  } catch(error) {toast(error.message);}
}
async function generateTestCases() {
  const button=$("#generateCasesBtn");
  if(button) {
    button.disabled=true;
    button.textContent=state.caseGenerator==="ollama"?"Generating with Ollama…":"Preparing test cases…";
  }
  try {
    const result=await api(projectPath("test-cases/generate"),{
      method:"POST",body:JSON.stringify({generator:state.caseGenerator})
    });
    await refresh();
    if(result.count) {
      const limit=result.truncated?" The 300-case per-request limit was reached.":"";
      const omitted=result.ai_input_truncated?" Some source records were omitted due to AI context limits.":"";
      toast(`${result.count} ${state.caseGenerator==="ollama"?"AI ":""}draft test case${result.count===1?"":"s"} generated; ${result.skipped_existing} existing case${result.skipped_existing===1?"":"s"} skipped.${limit}${omitted}`);
      state.view="cases";
      await render();
    } else toast(`No new cases generated; ${result.skipped_existing} existing draft case${result.skipped_existing===1?"":"s"} already cover the extracted items.`);
  } catch(error) {
    toast(error.message);
  } finally {
    if($("#generateCasesBtn")) {
      $("#generateCasesBtn").disabled=false;
      $("#generateCasesBtn").textContent="✦ Generate mapped test cases";
    }
  }
}
function importApiDoc() {
  const picker=document.createElement("input");picker.type="file";picker.accept=".json,application/json";
  picker.onchange=async()=>{
    const file=picker.files[0];if(!file)return;
    if(!/\.json$/i.test(file.name)){toast("Choose a local OpenAPI/Swagger .json file");return;}
    if(file.size>1024*1024){toast("OpenAPI JSON is too large (maximum 1 MB)");return;}
    try {
      const content=await file.text();
      const result=await api(projectPath("api-docs/import"),{method:"POST",body:JSON.stringify({filename:file.name,content})});
      await refresh();toast(`${result.title}: API document imported`);
    } catch(error){toast(error.message);}
  };
  picker.click();
}
function importApplicationZip() {
  const picker=document.createElement("input");picker.type="file";picker.accept=".zip,application/zip,application/x-zip-compressed";
  picker.onchange=async()=>{
    const file=picker.files[0];if(!file)return;
    if(!/\.zip$/i.test(file.name)){toast("Choose an application ZIP file");return;}
    if(file.size>25*1024*1024){toast("Application ZIP is too large (maximum 25 MB)");return;}
    try {
      const result=await api(`${projectPath("applications/import")}?filename=${encodeURIComponent(file.name)}`,{
        method:"POST",
        headers:{"Content-Type":"application/zip"},
        body:await file.arrayBuffer()
      });
      await refresh();
      toast(`${result.filename}: static inventory complete`);
    } catch(error) {toast(error.message);}
  };
  picker.click();
}
async function uploadEvidence(executionId,file) {
  if(file.size>5*1024*1024)throw new Error("Image is too large (maximum 5 MB)");
  const allowed=["image/png","image/jpeg","image/gif","image/webp"];
  if(!allowed.includes(file.type))throw new Error("Choose a PNG, JPEG, GIF, or WebP image");
  const dataUrl=await new Promise((resolve,reject)=>{
    const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=()=>reject(new Error("Could not read image file"));reader.readAsDataURL(file);
  });
  const imageData=String(dataUrl).split(",",2)[1];
  await api(projectPath(`executions/${executionId}/evidence`),{
    method:"POST",body:JSON.stringify({filename:file.name,media_type:file.type,image_data:imageData})
  });
}
async function showEvidence(caseId) {
  try {
    const history=await api(projectPath(`test-cases/${caseId}/executions`));
    const c=state.cases.find(item=>item.id==caseId);
    $("#modalTitle").textContent=`Execution evidence · ${c?.case_key||""}`;
    $("#modalEyebrow").textContent="SCREENSHOTS";
    $("#formError").textContent="";
    $("#saveModal").hidden=true;
    $("#modalFields").innerHTML=history.map(execution=>`<section class="evidence-history"><div class="panel-title">${esc(execution.status)} · ${esc(execution.executed_at)}${execution.executed_by?` · ${esc(execution.executed_by)}`:""}</div>${execution.evidence.length?execution.evidence.map(item=>{
      const base=projectPath(`evidence/${item.id}`);
      return `<div class="evidence-card"><a href="${base}" target="_blank" rel="noopener"><img src="${base}" alt="Evidence screenshot ${esc(item.filename)}"></a><div class="evidence-meta"><strong>${esc(item.filename)}</strong><span>${(item.size/1024).toFixed(1)} KB · ${esc(item.created_at)}</span><a class="link-button" href="${base}/download">Download image</a></div></div>`;
    }).join(""):'<div class="muted evidence-empty">No screenshot attached to this execution.</div>'}</section>`).join("")||'<div class="integration-note full"><strong>No executions yet.</strong> Use Execute / Update on this case to record a run and attach screenshots.</div>';
    modal.showModal();
  } catch(error){toast(error.message);}
}
$("#projectSelect").addEventListener("change",async e=>{state.project=state.projects.find(p=>String(p.id)===e.target.value)||null;state.view="dashboard";if(state.project)await refresh();else{state.requirements=[];state.requirementDocuments=[];state.cases=[];state.bugs=[];state.apiDocs=[];state.applications=[];state.analyses=[];$("#reqNavCount").textContent=$("#caseNavCount").textContent=$("#bugNavCount").textContent=$("#apiDocNavCount").textContent=$("#applicationNavCount").textContent=$("#analysisNavCount").textContent="0";render()}});
$("#workspaceNavigation").addEventListener("click",e=>{const button=e.target.closest(".nav-item");if(button){state.view=button.dataset.view;render()}});
$("#navigation").addEventListener("click",e=>{const button=e.target.closest(".nav-item");if(button){state.view=button.dataset.view;render()}});
$("#settingsNavigation").addEventListener("click",e=>{const button=e.target.closest(".nav-item");if(button){state.view=button.dataset.view;render()}});
$("#newProjectBtn").onclick=$("#emptyCreateBtn").onclick=()=>openModal("project");
$("#closeModal").onclick=$("#cancelModal").onclick=()=>modal.close();
loadProjects().catch(error=>toast(error.message));
