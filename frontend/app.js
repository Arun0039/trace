/* ── Trace · app.js ────────────────────────────────────────────────────
   Single-file vanilla JS frontend.
   Every view renders into #page. Navigation is handled by state.view.
   API calls use the configured backend origin, or the current origin by default.
────────────────────────────────────────────────────────────────────── */

/* ── Utilities ────────────────────────────────────────────────────── */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = v => String(v ?? "").replace(/[&<>"']/g, c =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pretty = v => esc(v).replace(/\n/g, "<br>");
const fmtDate = v => { if (!v) return "—"; const d = new Date(v); return isNaN(d) ? v : d.toLocaleDateString(); };
const THEME_PREFERENCE_KEY = "trace-theme-preference";
const THEME_PREFERENCES = ["dark", "light", "system"];

function readThemePreference() {
  try {
    const preference = localStorage.getItem(THEME_PREFERENCE_KEY);
    return THEME_PREFERENCES.includes(preference) ? preference : "dark";
  } catch (error) {
    console.warn("Could not read the saved theme preference.", error);
    return "dark";
  }
}

/* ── State ────────────────────────────────────────────────────────── */
const S = {
  view: "projects",
  viewHistory: [],
  settingsTab: "ai",        // which settings sub-pane is active
  themePreference: readThemePreference(),
  project: null,
  projects: [],
  requirements: [],
  requirementSuggestions: [],
  requirementDocs: [],
  brdAnalysis: null,
  cases: [],
  bugs: [],
  apiDocs: [],
  applications: [],
  aiSettings: {
    provider: "local",
    model: "deterministic",
    configured: true,
    providers: [],
  },
  auth: { authenticated: false, admin_configured: false, user: null, permissions: [] },
  authMode: "login",
  legacyAdminLogin: false,
  users: [],
  teams: [],
  roles: [],
  permissionModules: {},
  profile: null,
  customFields: [],
  aiDraft: null,
  aiStatus: "",
  caseGenerator: "deterministic",
  assistedInput: "",
  assistedCategories: ["Functional", "Non-Functional", "Sanity", "Smoke"],
  assistedAnalysis: null,
  inventoryVisible: {},
};

function currentPath() {
  return window.location.pathname.replace(/\/+$/, "") || "/";
}

function isPublicPath(path = currentPath()) {
  return ["/", "/about", "/privacy", "/terms"].includes(path);
}

function publicHeader(active = "home") {
  return `
    <header class="public-header">
      <a class="public-brand" href="/" data-public-nav aria-label="TRACE home">
        <picture class="brand-picture">
          <source srcset="/assets/Logo-light.svg" type="image/svg+xml" data-brand-theme="light">
          <source srcset="/assets/Logo-light.png" type="image/png" data-brand-theme="light">
          <source srcset="/assets/Logo.svg" type="image/svg+xml">
          <img class="public-brand-logo" src="/assets/Logo.png" alt="TRACE — AI-powered QA workspace">
        </picture>
      </a>
      <button class="public-menu-toggle" type="button" aria-expanded="false" aria-controls="publicNav" aria-label="Open navigation">
        <span></span><span></span><span></span>
      </button>
      <nav class="public-nav" id="publicNav" aria-label="Main navigation">
        <a href="/#top" data-public-nav class="${active === "home" ? "active" : ""}">Home</a>
        <a href="/#features" data-public-nav>Features</a>
        <a href="/#how-it-works" data-public-nav>How It Works</a>
        <a href="/about" data-public-nav class="${active === "about" ? "active" : ""}">About</a>
        <a href="/privacy" data-public-nav class="${active === "privacy" ? "active" : ""}">Privacy</a>
        <a href="/terms" data-public-nav class="${active === "terms" ? "active" : ""}">Terms</a>
        <span class="public-nav-actions">
          <a class="public-signin" href="/login" data-public-nav>Sign In</a>
          <a class="public-button public-button-small" href="/signup" data-public-nav>Get Started <span aria-hidden="true">↗</span></a>
        </span>
      </nav>
    </header>`;
}

function publicFooter() {
  return `
    <footer class="public-footer">
      <div class="public-footer-main">
        <a class="public-brand" href="/" data-public-nav aria-label="TRACE home">
          <picture class="brand-picture">
            <source srcset="/assets/Logo-light.svg" type="image/svg+xml" data-brand-theme="light">
            <source srcset="/assets/Logo-light.png" type="image/png" data-brand-theme="light">
            <source srcset="/assets/Logo.svg" type="image/svg+xml">
            <img class="public-brand-logo" src="/assets/Logo.png" alt="TRACE — AI-powered QA workspace">
          </picture>
        </a>
        <p>Connect business requirements to practical, reviewable software testing.</p>
        <div class="public-footer-links">
          <div><strong>Explore</strong><a href="/#features" data-public-nav>Features</a><a href="/#how-it-works" data-public-nav>How it works</a><a href="/about" data-public-nav>About</a></div>
          <div><strong>Policies</strong><a href="/privacy" data-public-nav>Privacy policy</a><a href="/terms" data-public-nav>Terms &amp; conditions</a></div>
          <div><strong>Workspace</strong><a href="/login" data-public-nav>Sign in</a><a href="/signup" data-public-nav>Get started</a></div>
        </div>
      </div>
      <div class="public-footer-bottom"><span>© ${new Date().getFullYear()} TRACE</span><span>Local-first QA workspace · Draft policies require legal review</span></div>
    </footer>`;
}

function publicLayout(content, active) {
  return `${publicHeader(active)}${content}${publicFooter()}`;
}

function homePage() {
  const benefits = [
    ["▤", "Manual analysis takes time", "Turn supported BRD content into structured records your team can inspect and confirm."],
    ["◇", "Ambiguity is easy to miss", "Review reported gaps and clarification points before they become assumptions in test design."],
    ["↻", "Test preparation is repetitive", "Generate editable test-case drafts from requirements that have been reviewed."],
    ["⌁", "Traceability gets fragmented", "Keep generated cases connected to their source requirements for easier review."],
    ["☷", "Documentation varies by author", "Use a consistent structure for requirements, steps, expected results, and priorities."],
    ["▥", "Coverage is hard to see", "Review requirement coverage and uncovered items alongside your project test work."]
  ];
  const features = [
    ["BRD upload & analysis", "Import supported PDF, DOCX, CSV, TXT, and Markdown documents. Scanned PDFs need OCR before import.", "AVAILABLE"],
    ["Rule-based extraction", "Deterministic extraction confirms only source-supported content; headings and unstructured text are not treated as requirements by themselves.", "AVAILABLE"],
    ["AI-assisted analysis", "Optional configured providers can propose analysis and requirements. Suggestions stay separate until a reviewer acts.", "AVAILABLE · OPTIONAL"],
    ["Requirement gap review", "Review potential missing information and ambiguity reported during analysis; use it as guidance, not verified fact.", "AVAILABLE · REVIEW REQUIRED"],
    ["Requirement traceability", "Link generated cases to reviewed requirements and inspect reported coverage.", "AVAILABLE"],
    ["Test-case generation", "Create editable, requirement-linked drafts with the local deterministic generator or an optional configured provider.", "AVAILABLE"],
    ["Project management", "Organize requirements, test cases, execution status, defects, files, and reports by project.", "AVAILABLE"],
    ["Structured QA documentation", "Capture consistent requirement and test-case fields, attachments, and CSV reports.", "AVAILABLE"],
    ["Application code analysis", "The current workspace labels this module Coming soon; its analysis workflow is not available yet.", "COMING SOON"]
  ];
  const benefitCards = benefits.map(([icon, title, copy]) => `
    <article class="public-card benefit-card"><span class="benefit-icon" aria-hidden="true">${icon}</span><h3>${title}</h3><p>${copy}</p></article>`).join("");
  const featureCards = features.map(([title, copy, status]) => `
    <article class="public-card feature-card"><span class="feature-mark" aria-hidden="true">✓</span><div><span class="feature-status">${status}</span><h3>${title}</h3><p>${copy}</p></div></article>`).join("");
  return publicLayout(`
    <main id="top">
      <section class="public-hero">
        <div class="hero-circuit hero-circuit-left" aria-hidden="true"></div>
        <div class="hero-copy">
          <p class="public-eyebrow"><span class="status-pulse"></span> REQUIREMENTS IN · CONFIDENCE OUT</p>
          <h1>Turn Requirements Into <span>Test-Ready Confidence.</span></h1>
          <p class="hero-description">Analyze business requirements, identify gaps, and generate structured test cases with TRACE — your workspace for smarter QA.</p>
          <div class="hero-actions">
            <a class="public-button" href="/signup" data-public-nav>Get Started <span aria-hidden="true">↗</span></a>
            <a class="public-button public-button-secondary" href="/login" data-public-nav>Sign In</a>
          </div>
          <p class="hero-note"><span aria-hidden="true">●</span> Local-first today · Optional model providers · Human review stays in the loop</p>
        </div>
        <div class="workflow-visual" role="img" aria-label="TRACE workflow overview: upload a BRD, analyze requirements, review gaps, then prepare test cases.">
          <div class="workflow-topline"><span>THE TRACE WORKFLOW</span><span class="workflow-live"><i></i> REVIEW-LED</span></div>
          <div class="workflow-path" aria-hidden="true"><span></span></div>
          <div class="workflow-steps">
            <div class="workflow-step"><b>01</b><span class="workflow-icon">↑</span><strong>Upload BRD</strong><small>Provide source material</small></div>
            <div class="workflow-step"><b>02</b><span class="workflow-icon">⌕</span><strong>Analyze</strong><small>Extract supported detail</small></div>
            <div class="workflow-step"><b>03</b><span class="workflow-icon">◇</span><strong>Review gaps</strong><small>Confirm what matters</small></div>
            <div class="workflow-step"><b>04</b><span class="workflow-icon">☷</span><strong>Prepare tests</strong><small>Keep source links</small></div>
          </div>
          <div class="workflow-caption"><span>WORKFLOW OVERVIEW</span><span>Illustrative · not a live dashboard</span></div>
        </div>
        <div class="hero-bottomline"><span>BUILT FOR QA TEAMS, ANALYSTS &amp; BUILDERS</span><span>01 / REQUIREMENTS TO TESTS</span></div>
      </section>

      <section class="public-section intro-section" id="about-trace">
        <div class="section-kicker">01 / WHAT IS TRACE?</div>
        <div class="intro-grid"><h2>A clearer path from <span>what to build</span> to how to test it.</h2><div><p>TRACE is a QA productivity workspace designed to connect business requirements with software testing. Keep source documents, structured requirements, review decisions, and test cases together in a project-focused workflow.</p><p>Deterministic extraction provides a source-grounded starting point. Optional AI analysis can suggest interpretations and gaps for people to review—not silently replace the source or your team’s judgment.</p><a class="text-link" href="/about" data-public-nav>Learn about TRACE <span aria-hidden="true">→</span></a></div></div>
      </section>

      <section class="public-section benefits-section" id="benefits">
        <div class="section-heading"><div><div class="section-kicker">02 / WHY TRACE?</div><h2>Make the work between <span>requirements and tests</span> easier to see.</h2></div><p>Purpose-built support for common QA handoffs—with review and traceability kept visible.</p></div>
        <div class="benefit-grid">${benefitCards}</div>
      </section>

      <section class="public-section features-section" id="features">
        <div class="section-heading"><div><div class="section-kicker">03 / PRODUCT CAPABILITIES</div><h2>Practical tools for a <span>reviewable QA workflow.</span></h2></div><p>Available capabilities reflect the current application. AI providers are optional, and generated suggestions require human review.</p></div>
        <div class="feature-grid">${featureCards}</div>
      </section>

      <section class="public-section how-section" id="how-it-works">
        <div class="section-kicker">04 / HOW IT WORKS</div><h2>From source document to <span>reviewed test draft.</span></h2>
        <div class="how-grid">
          <article><span>STEP 01</span><b>↑</b><h3>Upload BRD</h3><p>Provide a supported requirements document or paste its text into a project.</p></article>
          <article><span>STEP 02</span><b>⌕</b><h3>Analyze requirements</h3><p>Use deterministic extraction for source-supported requirements; optionally request AI analysis.</p></article>
          <article><span>STEP 03</span><b>◇</b><h3>Review results</h3><p>Inspect extracted records, potential gaps, and clearly separated AI suggestions. Confirm and review before generation.</p></article>
          <article><span>STEP 04</span><b>☷</b><h3>Generate test cases</h3><p>Create structured, editable drafts linked to eligible reviewed requirements.</p></article>
        </div>
        <p class="how-note">AI proposals are not confirmed source facts. Review the source and approve requirements before relying on generated drafts.</p>
      </section>
      <section class="public-cta"><div><div class="section-kicker">READY TO TRACE THE DETAILS?</div><h2>Bring your requirements and testing closer together.</h2><p>Start in the existing TRACE workspace. Local deployment and sign-up constraints apply.</p></div><a class="public-button" href="/signup" data-public-nav>Get Started <span aria-hidden="true">↗</span></a></section>
    </main>`, "home");
}

function aboutPage() {
  return publicLayout(`
    <main class="public-subpage">
      <section class="subpage-hero"><p class="public-eyebrow">ABOUT TRACE · REQUIREMENTS TO TESTING</p><h1>Make the connection between <span>requirements and testing</span> easier to follow.</h1><p>TRACE is a QA productivity workspace built to help teams carry source requirements into reviewable test preparation.</p></section>
      <section class="public-section about-content">
        <article><span class="section-kicker">PURPOSE</span><h2>Turn scattered handoffs into a reviewable workflow.</h2><p>Business requirements describe intended outcomes; software testing checks whether those outcomes are supported by the product. TRACE brings source documents, extracted requirements, reviewer decisions, and linked test cases into a shared project workspace.</p></article>
        <article><span class="section-kicker">VISION</span><h2>Clarity first. Traceability throughout.</h2><p>The goal is to help teams identify unclear source material, make review decisions explicit, and prepare structured tests without losing the connection to the requirement that informed them.</p></article>
        <article><span class="section-kicker">WHO IT’S FOR</span><h2>Designed around cross-functional QA work.</h2><p>QA engineers, test leads, business analysts, and development teams can use TRACE to collaborate on requirement review, test preparation, execution tracking, and project-level visibility.</p></article>
        <article class="about-callout"><span class="section-kicker">CURRENT PRODUCT</span><p>TRACE is currently a local-first application. Deterministic workflows run locally; optional AI integrations use a provider configured by the workspace administrator. AI output is a suggestion and needs human review.</p></article>
      </section>
      <div class="subpage-action"><a class="public-button" href="/signup" data-public-nav>Explore the workspace <span aria-hidden="true">↗</span></a></div>
    </main>`, "about");
}

function legalPage(kind) {
  const isPrivacy = kind === "privacy";
  const title = isPrivacy ? "Privacy Policy" : "Terms & Conditions";
  const lastUpdated = "27 October 2026";
  const sections = isPrivacy ? [
    ["scope", "1. Scope and draft status", "<p>This draft describes the current local-first TRACE application, not a separately hosted cloud service. It is a working product document, not legal advice; have qualified counsel review it before production use. Operator identity, governing law, and the final contact process remain to be supplied.</p>"],
    ["data", "2. Information handled", "<p>Depending on use, TRACE stores account names and email addresses, profile and role information, project details, requirements, test cases, execution and defect records, audit history, and files or attachments uploaded by users. Account passwords are stored as salted PBKDF2 hashes rather than plaintext.</p>"],
    ["documents", "3. Uploaded requirements and files", "<p>Uploaded BRDs and other project files are stored in the workspace’s configured storage directory; structured records are stored in the configured database. The default setup uses SQLite and local storage. PostgreSQL can be configured for database records, but project files still require persistent shared storage. Operators should secure and back up both locations.</p><p>Deleting a project removes its database records but archives its project folder rather than erasing its files. Account deactivation preserves related records and audit history. TRACE does not currently define an automatic retention period; archived files and backups may remain until separately removed by the operator.</p>"],
    ["ai", "4. AI and model processing", "<p>Local deterministic processing is available. If an administrator configures Ollama, Google Gemini, or an OpenAI-compatible provider, document-derived content, requirements, or prompts submitted for AI-assisted analysis or generation may be sent to that provider for processing. The provider’s own terms, retention, and privacy practices apply to that processing.</p><p>Review provider settings and applicable agreements before sending confidential, personal, regulated, or otherwise restricted information. Provider API credentials are stored encrypted in the database when configured with the required encryption key. Do not treat AI output as verified source content.</p>"],
    ["services", "5. Storage, third parties, and deployment", "<p>TRACE does not currently provide cloud sync or a hosted account service. The default backend listens on the local loopback interface; deployment settings can change this. The operator is responsible for deployment, network transport, server/database/filesystem access, backups, and any third-party model service they configure. The current stylesheet requests Geist font assets from Google Fonts, so a browser may contact Google when loading the interface. Optional model providers also receive the content submitted for processing. No analytics or advertising integration is described by the current application; verify deployment-specific integrations before use.</p>"],
    ["retention", "6. Retention and deletion", "<p>Workspace data remains in the configured database and storage until an operator or authorized user removes it. Project deletion archives files as described above, so it is not complete erasure. No automatic expiry or backup-purge schedule is currently provided. Define and document a deletion and backup-retention process before production use.</p>"],
    ["rights", "7. User requests and choices", "<p>Users should contact the workspace operator to ask about access, correction, export, or deletion of their account information. TRACE does not include an email-based recovery or centralized privacy-request service. The operator must define applicable rights, identity verification, response timelines, and deletion handling for the deployment.</p>"],
    ["security", "8. Security", "<p>The application uses role-based API permissions, salted password hashes, and HttpOnly, SameSite session cookies with a 12-hour lifetime. These implementation details are not a guarantee of security. Keep the application and its data on trusted infrastructure, protect administrator credentials and encryption keys, use appropriate transport protection if exposed beyond localhost, and maintain backups and access controls.</p>"],
    ["contact", "9. Contact and unresolved details", "<p>Workspace operator / legal entity: <strong>[OPERATOR NAME TO BE PROVIDED]</strong><br>Privacy contact: <strong>[CONTACT EMAIL OR PROCESS TO BE PROVIDED]</strong><br>Jurisdiction and applicable privacy law: <strong>[TO BE DETERMINED]</strong></p><p>Replace these placeholders and confirm the policy against the actual deployment and applicable law before publication.</p>"]
  ] : [
    ["draft", "1. Draft and operator", "<p>These draft terms describe the current TRACE application and require review by qualified counsel before production use. They do not identify a contracting entity or governing law. Operator: <strong>[OPERATOR NAME TO BE PROVIDED]</strong>. Governing law and venue: <strong>[TO BE DETERMINED]</strong>.</p>"],
    ["account", "2. Accounts and responsibilities", "<p>Provide accurate account details, protect passwords and administrator credentials, and use only accounts and permissions assigned to you. Workspace administrators control accounts and roles. The current application is local-first and account creation and authentication are restricted to local requests; it is not a public hosted signup service.</p>"],
    ["content", "3. Uploaded content and acceptable use", "<p>You retain responsibility for the content you upload or enter, including having the rights and authority to use it. Do not upload material unlawfully or in violation of another party’s rights, or content you are not authorized to process. You are responsible for choosing appropriate storage, access, and configured model providers for your information.</p>"],
    ["ai", "4. AI-generated output and review", "<p>AI-assisted results can be incomplete, inaccurate, or inconsistent with the source. They are suggestions, not professional advice or verified facts. Review the original requirements and all extracted content, gap findings, and generated test cases before using them. TRACE does not guarantee defect prevention, complete coverage, or suitability for a particular purpose.</p>"],
    ["service", "5. Service limitations and availability", "<p>TRACE is provided as a local-first QA workspace. Availability depends on the operator’s machine, configuration, database, storage, and any optional AI provider. The application does not currently provide cloud synchronization, subscription billing, or a service-level guarantee. Features may change; the operator should maintain independent backups.</p>"],
    ["ownership", "6. Intellectual property", "<p>Users retain rights in their submitted content. TRACE’s software, branding, and associated materials remain with their respective rights holders. To operate the application, users authorize the workspace operator and any explicitly configured model provider to process submitted content for the requested workspace functions, subject to applicable agreements.</p>"],
    ["billing", "7. Subscription terms", "<p>No subscription or payment workflow is implemented in the current application. If a paid service is introduced, pricing, renewal, cancellation, taxes, and refund terms must be presented separately and accepted before payment.</p>"],
    ["termination", "8. Suspension, termination, and data", "<p>Workspace administrators can deactivate accounts and delete projects. Deactivation preserves related records and audit history; project deletion archives project files rather than erasing them. Operators should define access termination, data export, deletion, and archive-purge practices for their deployment.</p>"],
    ["liability", "9. Warranty and liability", "<p>To the extent permitted by applicable law, TRACE is provided without a guarantee that it will be uninterrupted, error-free, secure, or suitable for a particular testing purpose. The operator must define any applicable warranty disclaimer, liability limits, and statutory exceptions with legal counsel: <strong>[LEGAL TERMS TO BE REVIEWED]</strong>.</p>"],
    ["contact", "10. Contact and changes", "<p>Operator / legal entity: <strong>[OPERATOR NAME TO BE PROVIDED]</strong><br>Terms contact: <strong>[CONTACT EMAIL OR PROCESS TO BE PROVIDED]</strong><br>Governing law: <strong>[TO BE DETERMINED]</strong></p><p>Replace placeholders and publish a dated, legally reviewed version before production use.</p>"]
  ];
  const links = sections.map(([id, heading]) => `<a href="#${id}">${heading.replace(/^\d+\.\s*/, "")}</a>`).join("");
  const content = sections.map(([id, heading, body]) => `<section class="legal-section" id="${id}"><h2>${heading}</h2>${body}</section>`).join("");
  return publicLayout(`
    <main class="public-subpage legal-page">
      <section class="subpage-hero legal-hero"><p class="public-eyebrow">TRACE · ${isPrivacy ? "DATA & PRIVACY" : "SERVICE TERMS"}</p><h1>${title}</h1><p>Draft for review · Last updated ${lastUpdated}</p><div class="draft-notice"><strong>Draft — not legal advice.</strong> The operator, contact, jurisdiction, and deployment-specific details are placeholders or require confirmation. Obtain legal review before production use.</div></section>
      <div class="legal-layout"><nav class="legal-toc" aria-label="${title} contents"><strong>On this page</strong>${links}</nav><div class="legal-content">${content}</div></div>
    </main>`, isPrivacy ? "privacy" : "terms");
}

function renderPublicPage() {
  const root = $("#publicRoot");
  if (!root) return;
  const path = currentPath();
  document.body.classList.remove("auth-required");
  document.body.classList.add("public-site");
  root.hidden = false;
  root.innerHTML = path === "/about" ? aboutPage()
    : path === "/privacy" ? legalPage("privacy")
    : path === "/terms" ? legalPage("terms")
    : homePage();
  applyBrandTheme(S.themePreference);
  const pageTitles = {
    "/": "TRACE | AI-Powered QA Workspace",
    "/about": "About TRACE | AI-Powered QA Workspace",
    "/privacy": "Privacy Policy | TRACE",
    "/terms": "Terms & Conditions | TRACE"
  };
  document.title = pageTitles[path] || pageTitles["/"];
  bindPublicActions();
  if (window.location.hash) {
    requestAnimationFrame(() => document.getElementById(decodeURIComponent(window.location.hash.slice(1)))?.scrollIntoView());
  } else {
    window.scrollTo(0, 0);
  }
}

function bindPublicActions() {
  const root = $("#publicRoot");
  if (root.dataset.publicActionsBound) return;
  root.dataset.publicActionsBound = "true";
  root.addEventListener("keydown", event => {
    if (event.key !== "Escape") return;
    const toggle = $(".public-menu-toggle", root);
    if (toggle?.getAttribute("aria-expanded") !== "true") return;
    toggle.setAttribute("aria-expanded", "false");
    toggle.setAttribute("aria-label", "Open navigation");
    $("#publicNav", root).classList.remove("is-open");
    toggle.focus();
  });
  root.addEventListener("click", event => {
    const toggle = event.target.closest(".public-menu-toggle");
    if (toggle) {
      const nav = $("#publicNav");
      const expanded = toggle.getAttribute("aria-expanded") === "true";
      toggle.setAttribute("aria-expanded", String(!expanded));
      toggle.setAttribute("aria-label", expanded ? "Open navigation" : "Close navigation");
      nav.classList.toggle("is-open", !expanded);
      return;
    }
    const link = event.target.closest("a[data-public-nav]");
    if (!link || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const target = new URL(link.href, window.location.href);
    if (target.origin !== window.location.origin) return;
    if (target.pathname === currentPath() && target.hash) {
      $("#publicNav", root)?.classList.remove("is-open");
      const menuToggle = $(".public-menu-toggle", root);
      menuToggle?.setAttribute("aria-expanded", "false");
      menuToggle?.setAttribute("aria-label", "Open navigation");
      return;
    }
    event.preventDefault();
    window.history.pushState({}, "", `${target.pathname}${target.hash}`);
    handleRouteChange();
  });
}

function handleRouteChange() {
  const path = currentPath();
  if (isPublicPath(path)) {
    renderPublicPage();
    return;
  }
  if (path === "/signup") S.authMode = "signup";
  else S.authMode = "login";
  $("#publicRoot").hidden = true;
  document.body.classList.remove("public-site");
  render();
  loadProjects().catch(error => toast(error.message));
}

function applyThemePreference(preference) {
  document.documentElement.dataset.theme = preference;
  const prefersLight = window.matchMedia("(prefers-color-scheme: light)").matches;
  const isLight = preference === "light" || (preference === "system" && prefersLight);
  applyBrandTheme(preference);
  const themeColor = $('meta[name="theme-color"]');
  if (themeColor) themeColor.content = isLight ? "#f5f7f4" : "#0c0e0d";
}

function applyBrandTheme(preference) {
  const mediaForTheme = theme => preference === "system"
    ? `(prefers-color-scheme: ${theme})`
    : preference === theme ? "all" : "not all";

  document.querySelectorAll("source[data-brand-theme], link[data-brand-theme]").forEach(asset => {
    asset.media = mediaForTheme(asset.dataset.brandTheme);
  });
}

function saveThemePreference(preference) {
  if (!THEME_PREFERENCES.includes(preference)) return;
  S.themePreference = preference;
  applyThemePreference(preference);
  try {
    localStorage.setItem(THEME_PREFERENCE_KEY, preference);
    toast("Appearance preference saved");
  } catch (error) {
    console.error("Could not save the theme preference.", error);
    toast("Theme changed for this session, but could not be saved in this browser.");
  }
}

applyThemePreference(S.themePreference);
window.matchMedia("(prefers-color-scheme: light)").addEventListener("change", () => {
  if (S.themePreference === "system") applyThemePreference(S.themePreference);
});

const modal = $("#modal");
const modalForm = $("#modalForm");

/* ── API helper ───────────────────────────────────────────────────── */
const API_BASE_URL = String(window.QA_API_BASE_URL || "").replace(/\/+$/, "");

async function api(path, opts = {}) {
  let res;
  try {
    res = await fetch(`${API_BASE_URL}${path}`, {
      credentials: "include",
      ...opts,
      headers: {
        "Content-Type": "application/json",
        ...(S.auth.user?.tenant_id ? { "X-Tenant-ID": String(S.auth.user.tenant_id) } : {}),
        ...(opts.headers || {}),
      },
    });
  } catch (e) {
    if (e instanceof TypeError) throw new Error("Cannot reach the QA server. Make sure app.py is running.");
    throw e;
  }

  const ct = res.headers.get("content-type") || "";
  const data = ct.includes("application/json") ? await res.json() : null;
  if (!res.ok) {
    const error = new Error(data?.error || `Request failed (${res.status})`);
    error.status = res.status;
    if (res.status === 401 && !path.startsWith("/api/auth/")) {
      S.auth = { authenticated: false, admin_configured: S.auth.admin_configured, user: null, permissions: [] };
      S.project = null;
      S.view = "projects";
      S.viewHistory = [];
      render();
    }
    throw error;
  }
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

function profileInitials(user) {
  const first = String(user?.first_name || "").trim();
  const last = String(user?.last_name || "").trim();
  const initials = `${first[0] || ""}${last[0] || ""}`;
  return initials || String(user?.email || "?")[0] || "?";
}

function syncProfileMenu() {
  const trigger = $("#profileMenuTrigger");
  const menu = $("#profileMenu");
  if (!trigger || !menu) return;
  const authenticated = Boolean(S.auth.authenticated && S.auth.user);
  trigger.hidden = !authenticated;
  if (!authenticated) {
    trigger.setAttribute("aria-expanded", "false");
    menu.hidden = true;
    return;
  }

  const user = S.auth.user;
  const profile = S.profile;
  const currentProfile = profile && (
    profile.global_admin
      ? Boolean(user.global_admin)
      : String(profile.id) === String(user.id)
  ) ? profile : null;
  const name = [user.first_name, user.last_name].filter(Boolean).join(" ").trim()
    || user.email || "Account";
  const initials = profileInitials(user);
  const imageTypes = ["image/png", "image/jpeg", "image/gif", "image/webp"];
  const image = currentProfile?.profile_image_data
    && imageTypes.includes(currentProfile.profile_image_type)
    ? `data:${currentProfile.profile_image_type};base64,${currentProfile.profile_image_data}`
    : "";

  ["#profileMenuAvatar", "#profileMenuAvatarLarge"].forEach(selector => {
    const avatar = $(selector);
    if (!avatar) return;
    avatar.replaceChildren();
    if (image) {
      const img = document.createElement("img");
      img.src = image;
      img.alt = "";
      avatar.append(img);
    } else {
      avatar.textContent = initials;
    }
  });
  $("#profileMenuName").textContent = name;
  $("#profileMenuEmail").textContent = user.email || "";
  $("#profileMenuRole").textContent = user.role_name || (user.global_admin ? "Administrator" : "");
  trigger.setAttribute("aria-label", `Open account menu for ${name}`);
}

function closeProfileMenu(returnFocus = false) {
  const trigger = $("#profileMenuTrigger");
  const menu = $("#profileMenu");
  if (!trigger || !menu) return;
  trigger.setAttribute("aria-expanded", "false");
  menu.hidden = true;
  if (returnFocus && !trigger.hidden) trigger.focus();
}

async function signOut() {
  try {
    await api("/api/auth/logout", { method: "POST", body: JSON.stringify({}) });
    S.auth = await api("/api/auth/status");
    S.project = null;
    S.profile = null;
    S.aiDraft = null;
    S.aiStatus = "";
    S.view = "projects";
    S.viewHistory = [];
    closeProfileMenu();
    await render();
  } catch (error) {
    toast(error.message);
    throw error;
  }
}

function bindProfileMenu() {
  const container = $("#profileMenuContainer");
  const trigger = $("#profileMenuTrigger");
  const menu = $("#profileMenu");
  if (!container || !trigger || !menu) return;

  trigger.addEventListener("click", async () => {
    const opening = trigger.getAttribute("aria-expanded") !== "true";
    if (!opening) {
      closeProfileMenu();
      return;
    }
    trigger.setAttribute("aria-expanded", "true");
    menu.hidden = false;
    const userId = S.auth.user?.id;
    const profileIsCurrent = S.profile && (
      S.profile.global_admin
        ? Boolean(S.auth.user?.global_admin)
        : String(S.profile.id) === String(userId)
    );
    if (!profileIsCurrent) {
      try {
        const profile = await api("/api/profile");
        if (S.auth.authenticated && String(userId) === String(S.auth.user?.id)) {
          S.profile = profile;
          syncProfileMenu();
        }
      } catch (error) {
        toast(error.message);
      }
    }
  });
  trigger.addEventListener("keydown", event => {
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    event.preventDefault();
    trigger.click();
    const items = $$('[role="menuitem"]', menu).filter(item => !item.disabled);
    items[event.key === "ArrowDown" ? 0 : items.length - 1]?.focus();
  });

  menu.addEventListener("click", async event => {
    const item = event.target.closest("[data-profile-action]");
    if (!item) return;
    const action = item.dataset.profileAction;
    closeProfileMenu();
    if (action === "signout") {
      item.disabled = true;
      item.textContent = "Signing out…";
      try {
        await signOut();
      } catch {
        item.disabled = false;
        item.innerHTML = '<span aria-hidden="true">↪</span>Sign out';
      }
      return;
    }

    if (action === "profile" || action === "security") {
      S.settingsTab = "profile";
      navigate("settings");
      render();
      await loadSettingsTab("profile");
      if (action === "security") {
        const passwordInput = $("#passwordForm input");
        passwordInput?.scrollIntoView({ block: "center" });
        passwordInput?.focus({ preventScroll: true });
      }
    } else if (action === "settings") {
      navigate("settings");
      render();
    }
  });

  document.addEventListener("pointerdown", event => {
    if (!container.contains(event.target)) closeProfileMenu();
  });
  container.addEventListener("focusout", () => {
    setTimeout(() => {
      if (!container.contains(document.activeElement)) closeProfileMenu();
    });
  });
  document.addEventListener("keydown", event => {
    if (trigger.getAttribute("aria-expanded") !== "true") return;
    const items = $$('[role="menuitem"]', menu).filter(item => !item.disabled);
    if (event.key === "Escape") {
      event.preventDefault();
      closeProfileMenu(true);
      return;
    }
    if (!items.length) return;
    const currentIndex = items.indexOf(document.activeElement);
    let nextIndex = null;
    if (event.key === "ArrowDown") nextIndex = (currentIndex + 1) % items.length;
    if (event.key === "ArrowUp") nextIndex = (currentIndex <= 0 ? items.length : currentIndex) - 1;
    if (event.key === "Home") nextIndex = 0;
    if (event.key === "End") nextIndex = items.length - 1;
    if (nextIndex !== null) {
      event.preventDefault();
      items[nextIndex].focus();
    }
  });
}

/* ── Project API path shortcut ────────────────────────────────────── */
function pp(suffix) { return `/api/projects/${S.project.id}/${suffix}`; }

/* ── Load everything from the backend ────────────────────────────── */
async function loadProjects(preferred) {
  S.auth = await api("/api/auth/status");
  if (!S.auth.authenticated) {
    await render();
    return;
  }
  if (["/login", "/signup"].includes(currentPath())) {
    window.history.replaceState({}, "", "/workspace");
  }
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
  const loadIfAllowed = (permission, path) =>
    hasPermission(permission) ? api(pp(path)) : Promise.resolve([]);
  const suggestionsRequest = hasPermission("requirements.view")
    ? api(pp("requirements/suggestions")).catch(error => {
      if (error.status !== 404) throw error;
      const message = "This server does not support requirement suggestions. Stop it, start qa-platform\\start-qa-platform.bat, then refresh.";
      console.warn(message);
      toast(message);
      return [];
    })
    : Promise.resolve([]);
  [
    S.requirements, S.requirementSuggestions, S.requirementDocs, S.cases, S.bugs,
    S.apiDocs, S.applications,
  ] = await Promise.all([
    loadIfAllowed("requirements.view", "requirements"),
    suggestionsRequest,
    loadIfAllowed("requirements.view", "requirements/documents"),
    loadIfAllowed("test-cases.view", "test-cases"),
    loadIfAllowed("bugs.view", "bugs"),
    loadIfAllowed("api-docs.view", "api-docs"),
    loadIfAllowed("applications.view", "applications"),
  ]);
  // Update sidebar counts
  const rc = $("#reqCount"); if (rc) rc.textContent = S.requirements.length;
  const cc = $("#caseCount"); if (cc) cc.textContent = S.cases.length;
  const dc = $("#defectCount"); if (dc) dc.textContent = S.bugs.length;
  await render();
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
    btn.hidden = v === "dashboard"
      ? !hasPermission("reports.view")
      : !hasPermission("projects.view");
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
  const projectPermissions = {
    overview: "projects.view", brd: "requirements.view",
    requirements: "requirements.view", cases: "test-cases.view",
    defects: "bugs.view", reports: "reports.view",
    "project-settings": "projects.edit", "api-docs": "api-docs.view",
    applications: "applications.view", "application-analysis": "app-analysis.view",
    files: "projects.view",
  };
  $$("#projectNav .nav-item").forEach(btn => {
    btn.hidden = !hasPermission(projectPermissions[btn.dataset.view] || "projects.view");
    btn.classList.toggle("active", btn.dataset.view === S.view);
  });

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
   AUTHENTICATION
════════════════════════════════════════════════════════════════════ */
function authView() {
  const isSignup = S.authMode === "signup";
  return `
    <section class="auth-shell" aria-labelledby="authHeading">
      <div class="auth-backdrop" aria-hidden="true">
        <svg class="auth-circuit" viewBox="0 0 1440 900" preserveAspectRatio="xMidYMid slice">
          <path d="M0 180h210l42 42h160m-180 0h90v120h120l38 38h112M0 640h170l48-48h115v-86l54-54h112M1440 165h-170l-48 48h-144v106l-54 54h-142m558 280h-186l-45-45h-112v-94l-38-38h-128" />
          <path d="M0 330h120l35 35h110m-265 360h240l44-44h98M1440 350h-110l-42 42h-142m294 350h-228l-42-42h-100" />
          <circle cx="462" cy="342" r="4" /><circle cx="399" cy="506" r="4" />
          <circle cx="978" cy="373" r="4" /><circle cx="1018" cy="650" r="4" />
          <circle cx="252" cy="222" r="3" /><circle cx="1180" cy="213" r="3" />
        </svg>
        <span class="auth-orbit auth-orbit-one"></span>
        <span class="auth-orbit auth-orbit-two"></span>
        <div class="auth-tech-panel auth-tech-panel-left"><span>TRACE / QA</span><i></i><b>BUILD</b><em>LOCAL · VERIFIED</em></div>
        <div class="auth-tech-panel auth-tech-panel-right"><span>RUN STATUS</span><i></i><b>PASS</b><em>EXECUTION READY</em></div>
        <span class="auth-tech-label auth-label-top">AUTOMATION&nbsp; / &nbsp;TEST</span>
        <span class="auth-tech-label auth-label-bottom">DEFECT&nbsp; → &nbsp;PASS</span>
      </div>
      <div class="auth-card">
        <header class="auth-header">
          <picture class="brand-picture">
            <source srcset="/assets/Logo-light.svg" type="image/svg+xml" data-brand-theme="light">
            <source srcset="/assets/Logo-light.png" type="image/png" data-brand-theme="light">
            <source srcset="/assets/Logo.svg" type="image/svg+xml">
            <img class="auth-brand-logo" src="/assets/Logo.png" alt="TRACE">
          </picture>
        </header>
        <h1 id="authHeading">${isSignup ? "Create your account" : "Welcome back"}</h1>
        <p class="auth-description">${isSignup
          ? "Set up your account to get started with your QA workspace."
          : "Sign in to continue to your QA workspace."}</p>
        <div class="auth-tabs" role="tablist" aria-label="Authentication">
          <button type="button" role="tab" id="authLoginTab" aria-selected="${!isSignup}" aria-controls="authPanel" class="${isSignup ? "" : "active"}" data-auth-mode="login">Sign in</button>
          <button type="button" role="tab" id="authSignupTab" aria-selected="${isSignup}" aria-controls="authPanel" class="${isSignup ? "active" : ""}" data-auth-mode="signup">Create account</button>
        </div>
        ${isSignup ? `
          <div id="authPanel" role="tabpanel" aria-labelledby="authSignupTab">
            <form id="signupForm" class="auth-form" novalidate>
              <label for="signupFullName">Full name
                <input id="signupFullName" name="full_name" autocomplete="name" maxlength="161" required aria-invalid="false" aria-describedby="signupFullNameError">
                <span class="auth-field-error" id="signupFullNameError" hidden></span>
              </label>
              <label for="signupEmail">Email
                <input id="signupEmail" name="email" type="email" autocomplete="email" maxlength="254" required aria-invalid="false" aria-describedby="signupEmailError">
                <span class="auth-field-error" id="signupEmailError" hidden></span>
              </label>
              <label for="signupPassword">Password
                <span class="password-control"><input id="signupPassword" name="password" type="password" autocomplete="new-password" minlength="12" required aria-invalid="false" aria-describedby="signupPasswordError passwordGuidance"><button type="button" class="password-toggle" data-toggle-password aria-label="Show password">Show</button></span>
                <span class="auth-field-error" id="signupPasswordError" hidden></span>
              </label>
              <label for="signupConfirmPassword">Confirm password
                <span class="password-control"><input id="signupConfirmPassword" name="confirm_password" type="password" autocomplete="new-password" minlength="12" required aria-invalid="false" aria-describedby="signupConfirmPasswordError"><button type="button" class="password-toggle" data-toggle-password aria-label="Show confirm password">Show</button></span>
                <span class="auth-field-error" id="signupConfirmPasswordError" hidden></span>
              </label>
              <div class="password-guidance" id="passwordGuidance">
                <span class="password-guidance-title">Password requirements</span>
                <ul class="password-rules">
                  <li data-password-rule="length"><span aria-hidden="true">○</span> At least 12 characters</li>
                  <li data-password-rule="uppercase"><span aria-hidden="true">○</span> One uppercase letter</li>
                  <li data-password-rule="lowercase"><span aria-hidden="true">○</span> One lowercase letter</li>
                  <li data-password-rule="number"><span aria-hidden="true">○</span> One number</li>
                </ul>
              </div>
              <p class="auth-hint">New accounts receive the User role. A Global Admin can assign additional permissions.</p>
              <div class="form-error" id="authError" role="alert" aria-live="assertive"></div>
              <button class="btn btn-primary auth-submit" type="submit">Create account</button>
            </form>
            <p class="auth-switch">Already have an account? <button type="button" data-auth-mode="login">Sign in</button></p>
          </div>` : `
          <div id="authPanel" role="tabpanel" aria-labelledby="authLoginTab">
            <form id="loginForm" class="auth-form" novalidate>
              ${S.legacyAdminLogin ? "" : `<label for="loginEmail">Email
                <input id="loginEmail" name="email" type="email" autocomplete="username" maxlength="254" required aria-invalid="false" aria-describedby="loginEmailError">
                <span class="auth-field-error" id="loginEmailError" hidden></span>
              </label>`}
              <label for="loginPassword">Password
                <span class="password-control"><input id="loginPassword" name="password" type="password" autocomplete="current-password" required aria-invalid="false" aria-describedby="loginPasswordError"><button type="button" class="password-toggle" data-toggle-password aria-label="Show password">Show</button></span>
                <span class="auth-field-error" id="loginPasswordError" hidden></span>
              </label>
              <div class="form-error" id="authError" role="alert" aria-live="assertive"></div>
              <button class="btn btn-primary auth-submit" type="submit">Sign in</button>
              <button class="auth-link" type="button" id="toggleAdminLogin">${S.legacyAdminLogin
                ? "Sign in with an email account"
                : "Use the configured Admin password"}</button>
              <p class="auth-hint">Forgot your password? Ask your workspace Administrator to reset it locally.</p>
            </form>
            <p class="auth-switch">New to TRACE? <button type="button" data-auth-mode="signup">Create an account</button></p>
          </div>`}
      </div>
    </section>`;
}

function bindAuthActions() {
  bindPasswordToggles();
  $$("[data-auth-mode]").forEach(button => button.addEventListener("click", () => {
    S.authMode = button.dataset.authMode;
    S.legacyAdminLogin = false;
    window.history.replaceState({}, "", S.authMode === "signup" ? "/signup" : "/login");
    render();
  }));
  $("#toggleAdminLogin")?.addEventListener("click", () => {
    S.legacyAdminLogin = !S.legacyAdminLogin;
    render();
  });
  const signupForm = $("#signupForm");
  signupForm?.querySelectorAll("input").forEach(input => input.addEventListener("input", () => {
    $("#authError").textContent = "";
    updatePasswordRules();
    if (input.getAttribute("aria-invalid") === "true") {
      showAuthFieldError(signupForm, input.name, authFieldError(signupForm, input.name));
    }
    if (input.name === "password" || input.name === "confirm_password") {
      const confirm = signupForm.elements.confirm_password;
      if (confirm.value && confirm.getAttribute("aria-invalid") === "true") {
        showAuthFieldError(signupForm, "confirm_password", authFieldError(signupForm, "confirm_password"));
      }
    }
  }));
  signupForm?.addEventListener("submit", async event => {
    event.preventDefault();
    const form = event.currentTarget;
    const invalid = validateAuthForm(form, true);
    if (invalid) {
      invalid.focus();
      return;
    }
    const values = new FormData(form);
    const nameParts = String(values.get("full_name")).trim().split(/\s+/);
    const firstName = nameParts.shift();
    const payload = {
      first_name: firstName,
      last_name: nameParts.join(" ") || firstName,
      email: String(values.get("email")).trim(),
      password: values.get("password"),
      confirm_password: values.get("confirm_password"),
    };
    const submit = setAuthLoading(form, "Creating account…");
    $("#authError").textContent = "";
    try {
      await api("/api/auth/signup", { method: "POST", body: JSON.stringify(payload) });
      window.history.replaceState({}, "", "/workspace");
      await loadProjects();
      toast("Account created");
    } catch (reason) {
      if (reason.status === 409) {
        showAuthFieldError(form, "email", reason.message);
        form.elements.email.focus();
      } else {
        $("#authError").textContent = reason.message;
      }
    } finally { resetAuthLoading(submit, "Create account"); }
  });
  const loginForm = $("#loginForm");
  loginForm?.querySelectorAll("input").forEach(input => input.addEventListener("input", () => {
    $("#authError").textContent = "";
    if (input.getAttribute("aria-invalid") === "true") {
      showAuthFieldError(loginForm, input.name, authFieldError(loginForm, input.name));
    }
  }));
  loginForm?.addEventListener("submit", async event => {
    event.preventDefault();
    const form = event.currentTarget;
    const invalid = validateAuthForm(form, false);
    if (invalid) {
      invalid.focus();
      return;
    }
    const payload = Object.fromEntries(new FormData(form).entries());
    if (payload.email) payload.email = String(payload.email).trim();
    const submit = setAuthLoading(form, "Signing in…");
    $("#authError").textContent = "";
    try {
      await api("/api/auth/login", { method: "POST", body: JSON.stringify(payload) });
      window.history.replaceState({}, "", "/workspace");
      await loadProjects();
      toast("Signed in");
    } catch (reason) {
      $("#authError").textContent = reason.message;
    } finally { resetAuthLoading(submit, "Sign in"); }
  });
}

function authFieldError(form, name) {
  const input = form.elements[name];
  if (!input) return "";
  const value = input.value.trim();
  if (name === "full_name") {
    if (!value) return "Enter your full name.";
    const [first, ...rest] = value.split(/\s+/);
    return first.length > 80 || rest.join(" ").length > 80
      ? "Keep each name at 80 characters or fewer."
      : "";
  }
  if (name === "email") {
    if (!value) return "Enter your email address.";
    return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value) ? "" : "Enter a valid email address.";
  }
  if (name === "password") {
    if (!value) return "Enter your password.";
    if (form.id === "signupForm") {
      if (value.length < 12) return "Use at least 12 characters.";
      if (!/[A-Z]/.test(value) || !/[a-z]/.test(value) || !/\d/.test(value)) {
        return "Include uppercase and lowercase letters and a number.";
      }
    }
    return "";
  }
  if (name === "confirm_password") {
    if (!value) return "Confirm your password.";
    return value === form.elements.password.value ? "" : "Passwords do not match.";
  }
  return "";
}

function showAuthFieldError(form, name, message) {
  const input = form.elements[name];
  if (!input) return;
  const error = $(`#${input.id}Error`);
  input.setAttribute("aria-invalid", message ? "true" : "false");
  if (error) {
    error.textContent = message;
    error.hidden = !message;
  }
}

function validateAuthForm(form, isSignup) {
  const fields = isSignup
    ? ["full_name", "email", "password", "confirm_password"]
    : [...(form.elements.email ? ["email"] : []), "password"];
  let firstInvalid = null;
  fields.forEach(name => {
    const input = form.elements[name];
    const message = authFieldError(form, name);
    showAuthFieldError(form, name, message);
    if (message && !firstInvalid) firstInvalid = input;
  });
  return firstInvalid;
}

function updatePasswordRules() {
  const password = $("#signupPassword")?.value || "";
  const rules = {
    length: password.length >= 12,
    uppercase: /[A-Z]/.test(password),
    lowercase: /[a-z]/.test(password),
    number: /\d/.test(password),
  };
  Object.entries(rules).forEach(([name, met]) => {
    const item = $(`[data-password-rule="${name}"]`);
    if (!item) return;
    item.classList.toggle("is-met", met);
    item.querySelector("span").textContent = met ? "✓" : "○";
  });
}

function setAuthLoading(form, text) {
  const button = form.querySelector('[type="submit"]');
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  button.textContent = text;
  return button;
}

function resetAuthLoading(button, text) {
  button.disabled = false;
  button.removeAttribute("aria-busy");
  button.textContent = text;
}

function bindPasswordToggles() {
  $$("[data-toggle-password]").forEach(button => button.addEventListener("click", () => {
    const input = button.parentElement.querySelector("input");
    const show = input.type === "password";
    input.type = show ? "text" : "password";
    button.textContent = show ? "Hide" : "Show";
    button.setAttribute("aria-label", `${show ? "Hide" : "Show"} password`);
  }));
}

/* ════════════════════════════════════════════════════════════════════
   MAIN RENDER
════════════════════════════════════════════════════════════════════ */
async function render() {
  if (isPublicPath()) {
    renderPublicPage();
    return;
  }
  $("#publicRoot").hidden = true;
  document.body.classList.remove("public-site");
  syncNav();
  syncProfileMenu();
  const pg = $("#page");
  if (!pg) return;
  if (!S.auth.authenticated) {
    document.body.classList.add("auth-required");
    pg.innerHTML = authView();
    applyBrandTheme(S.themePreference);
    bindAuthActions();
    return;
  }
  document.body.classList.remove("auth-required");

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
  const genReady = S.requirements.some(r => r.review_status === "Reviewed");
  const genOk = S.caseGenerator === "deterministic" || Boolean(S.aiSettings.configured);

  const extraActions =
    S.view === "requirements"
      ? `${hasPermission("requirements.create") ? `<button class="btn btn-ghost" id="importBtn">↑ Upload BRD</button>
         <button class="btn btn-ghost" id="addBtn">＋ Add requirement</button>` : ""}
         ${hasPermission("reports.view") ? `<button class="btn btn-ghost module-export" data-module="requirements">↓ Download Report</button>` : ""}`
      : S.view === "cases"
      ? `${hasPermission("test-cases.create") ? `<button class="btn btn-primary" id="genCasesBtn" ${genReady && genOk ? "" : "disabled"}>✦ Generate test cases</button>
         <button class="btn btn-ghost" id="addBtn">＋ Add test case</button>` : ""}
         ${hasPermission("reports.view") ? `<button class="btn btn-ghost module-export" data-module="test-cases">↓ Download Report</button>` : ""}`
      : S.view === "defects"
      ? `${hasPermission("bugs.create") ? `<button class="btn btn-primary" id="addBtn">＋ Log defect</button>` : ""}
         ${hasPermission("reports.view") ? `<button class="btn btn-ghost module-export" data-module="bugs">↓ Download Report</button>` : ""}`
      : S.view === "api-docs"
      ? `${hasPermission("api-docs.create") ? `<button class="btn btn-primary" id="apiImportBtn">↑ Import OpenAPI JSON</button>` : ""}`
      : S.view === "applications"
      ? `${hasPermission("applications.create") ? `<button class="btn btn-primary" id="appUploadBtn">↑ Upload application ZIP</button>` : ""}`
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
        ${S.view === "reports" && hasPermission("reports.view") ? `<button class="btn btn-ghost" id="exportBtn">↓ Export full project report</button>` : ""}
        ${hasPermission("projects.delete") ? `<button class="btn btn-danger" id="delProjectBtn">Delete project</button>` : ""}
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
        ${hasPermission("projects.create") ? `<button class="btn btn-primary" id="dashCreateBtn">＋ Create project</button>` : ""}
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
        ${hasPermission("projects.view") ? `<button class="act-btn" onclick="openProject(${p.id},'overview')">Open</button>` : ""}
        ${hasPermission("projects.edit") ? `<button class="act-btn" onclick="openModal('project','${p.id}')">Edit</button>` : ""}
        ${hasPermission("projects.delete") ? `<button class="act-btn act-btn-danger" onclick="deleteProject(${p.id})">Delete</button>` : ""}
      </div></td>
    </tr>`).join("");
  return `
    <div class="page-head">
      <div class="page-head-text">
        <div class="eyebrow">WORKSPACE</div>
        <h1>Projects</h1>
        <p>Create and manage your QA projects.</p>
      </div>
      <div class="page-head-actions">${hasPermission("projects.create") ? `<button class="btn btn-primary" id="createProjectBtn">＋ Create project</button>` : ""}</div>
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
            <strong>Trace workflow</strong><br>
            1. Upload BRD → 2. Review requirements → 3. Generate test cases → 4. Execute &amp; track
            <br><br>
            <button class="act-btn" id="goBrd">Upload BRD →</button>
          </div>
        </div>
      </section>
    </div>`;
}

/* ── BRD Documents ────────────────────────────────────────────────── */
function brdAnalysisView() {
  const selected = S.brdAnalysis;
  if (!selected) return "";
  const report = selected.analysis;
  const docId = selected.documentId;
  return `
    <div class="panel brd-analysis">
      <div class="panel-head">
        <div>
          <div class="panel-title">BRD analysis · ${esc(selected.filename)}</div>
          <div class="panel-sub">${esc(report.summary)}</div>
        </div>
        <a class="btn btn-ghost" href="/api/projects/${S.project.id}/requirements/documents/${docId}/analysis.md">Download Markdown</a>
      </div>
      <div class="info-box">
        <strong>${esc(report.analysis_status || "NEEDS REVIEW")}</strong> · This analysis is an AI interpretation, not a confirmed requirement source. Only records shown as EXTRACTED below are rule-based requirements.
      </div>
      ${brdReportMarkup(report)}
    </div>`;
}

function requirementSuggestionsView() {
  const cards = S.requirementSuggestions.map(suggestion => {
    const content = suggestion.content;
    const related = S.requirements.find(item => item.id === suggestion.related_requirement_id);
    const history = (suggestion.history || []).map(entry =>
      `<li>${esc(entry.action)} · ${esc(entry.reviewer)} · ${fmtDate(entry.created_at)}</li>`
    ).join("");
    return `
      <article class="brd-preview-item suggestion-card">
        <div class="brd-preview-item-head">
          <span class="badge badge-amber">AI SUGGESTION</span>
          <span class="badge ${suggestion.status === "Accepted" ? "badge-green" : suggestion.status === "Rejected" ? "badge-red" : "badge-amber"}">${esc(suggestion.status)}</span>
        </div>
        <h3>${esc(content.title)}</h3>
        ${content.description ? `<p>${pretty(content.description)}</p>` : ""}
        ${content.acceptance_criteria ? `<p><strong>Acceptance criteria:</strong><br>${pretty(content.acceptance_criteria)}</p>` : ""}
        ${content.business_rules ? `<p><strong>Business rules:</strong><br>${pretty(content.business_rules)}</p>` : ""}
        ${content.validation_rules ? `<p><strong>Validation:</strong><br>${pretty(content.validation_rules)}</p>` : ""}
        ${content.dependencies ? `<p><strong>Dependencies:</strong><br>${pretty(content.dependencies)}</p>` : ""}
        <p><strong>Reason:</strong> ${esc(suggestion.reason)}</p>
        <p><strong>Related requirement:</strong> ${related ? `${esc(related.requirement_key)} · ${esc(related.title)}` : esc(suggestion.related_requirement_key || "None identified")}
          · <strong>Source reference:</strong> ${esc(suggestion.source_reference || "No exact source reference identified")}</p>
        ${suggestion.source_evidence ? `<blockquote class="source-evidence">${esc(suggestion.source_evidence)}</blockquote>` : ""}
        ${suggestion.status === "Pending" && hasPermission("requirements.edit") ? `
          <div class="row-actions suggestion-actions">
            <button class="act-btn suggestion-accept" data-id="${suggestion.id}">Accept</button>
            <button class="act-btn suggestion-edit-toggle" data-id="${suggestion.id}">Edit</button>
            <button class="act-btn act-btn-danger suggestion-reject" data-id="${suggestion.id}">Reject</button>
          </div>
          <details class="suggestion-editor" id="suggestion-editor-${suggestion.id}">
            <summary>Edit proposed content before saving</summary>
            ${["title", "description", "acceptance_criteria", "business_rules", "validation_rules", "dependencies"].map(field => `
              <label class="field full">
                <span>${esc(field.replace(/_/g, " "))}</span>
                ${field === "title"
                  ? `<input class="suggestion-field" data-field="${field}" value="${esc(content[field] || "")}">`
                  : `<textarea class="suggestion-field" data-field="${field}">${esc(content[field] || "")}</textarea>`}
              </label>`).join("")}
            <button class="btn btn-primary suggestion-save" data-id="${suggestion.id}">Save edit</button>
          </details>` : ""}
        ${history ? `<details><summary>Approval history</summary><ul>${history}</ul></details>` : ""}
      </article>`;
  }).join("");
  return `
    <div class="panel">
      <div class="panel-head">
        <div><div class="panel-title">AI suggestion review</div>
          <div class="panel-sub">Suggestions remain separate from extracted requirements and are excluded from test generation until explicitly accepted.</div>
        </div>
        <span class="badge">${S.requirementSuggestions.filter(item => item.status === "Pending").length} pending</span>
      </div>
      <div class="brd-preview-list">
        ${cards || '<p class="empty-row">No AI suggestions are waiting for review.</p>'}
      </div>
    </div>`;
}

function brdView() {
  const docs = S.requirementDocs.map(d => `
    <tr>
      <td>
        <strong class="cell-main">${esc(d.filename)}</strong>
        <span class="cell-sub">v${d.version || 1} · ${d.requirement_count} requirements extracted</span>
      </td>
      <td><span class="badge badge-blue">${esc(d.content_type.includes("docx") ? "DOCX" : d.content_type.includes("pdf") ? "PDF" : d.content_type.includes("csv") ? "CSV" : "TXT/MD")}</span></td>
      <td class="mono">${(d.file_size_bytes / 1024).toFixed(1)} KB</td>
      <td>${fmtDate(d.uploaded_at)}</td>
      <td><div class="row-actions">
        <a class="act-btn" href="/api/projects/${S.project.id}/requirements/documents/${d.id}">Download</a>
        ${d.has_analysis ? `<button class="act-btn view-brd-analysis" data-id="${d.id}" data-filename="${esc(d.filename)}">View analysis</button>
          <a class="act-btn" href="/api/projects/${S.project.id}/requirements/documents/${d.id}/analysis.md">Markdown</a>` : ""}
      </div></td>
    </tr>`).join("");
  return `
    <div class="workflow-box panel">
      <div class="panel-title">Upload Business Requirements Document</div>
      <div class="panel-sub" style="margin-bottom:12px">
        Supported formats: PDF, DOCX, CSV, TXT, and Markdown. Rule-based extraction alone creates confirmed requirements with source evidence.
        AI can provide a separate use-case analysis and suggestions; every suggestion requires an explicit reviewer decision before it can be used for test generation.
      </div>
      ${hasPermission("requirements.create") ? `<div style="display:flex;gap:8px;flex-wrap:wrap">
        <button class="btn btn-primary" id="uploadBrdBtn">↑ Upload BRD file</button>
        <button class="btn btn-ghost" id="configAiBtn">Configure AI model</button>
      </div>
      <details style="margin-top:14px">
        <summary>Or paste raw requirement text</summary>
        <textarea id="pasteText" maxlength="1000000" placeholder="Paste BRD content here…"></textarea>
        <button class="btn btn-primary" id="pasteBtn">Analyze with AI</button>
      </details>` : ""}
    </div>
    ${brdAnalysisView()}
    ${requirementSuggestionsView()}
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
    <tr data-search="${esc(`${r.requirement_key} ${r.title} ${r.description} ${r.requirement_type || ""} ${r.module_type || ""} ${r.scope_classification || ""}`.toLowerCase())}">
      <td><span class="mono cell-main">${esc(r.requirement_key)}</span><span class="cell-sub">${esc(r.title)}</span></td>
      <td>
        <span class="badge ${r.module_type === "Non-Functional" ? "badge-purple" : "badge-blue"}">${esc(r.requirement_type || "Functional Requirement")}</span>
        <span class="cell-sub">${esc(r.module_type || "Functional")}</span>
      </td>
      <td>${esc(r.module_category || "Functional")}</td>
      <td><span class="badge ${r.priority === "High" ? "badge-red" : r.priority === "Low" ? "badge-muted" : "badge-amber"}">${esc(r.priority)}</span></td>
      <td><span class="badge ${r.test_case_count ? "badge-green" : "badge-muted"}">${r.test_case_count ? `${r.test_case_count} linked` : "Not covered"}</span></td>
      <td><span class="badge ${r.review_status === "Reviewed" ? "badge-green" : r.review_status === "Rejected" ? "badge-red" : "badge-amber"}">${esc(r.review_status)}</span></td>
      <td class="mono" style="color:var(--muted)">${esc(r.source_document || "Manual")}
        <span class="cell-sub"><span class="badge ${r.origin === "extracted" ? "badge-green" : r.origin === "ai_suggestion" ? "badge-amber" : "badge-muted"}">${r.origin === "extracted" ? "EXTRACTED" : r.origin === "ai_suggestion" ? "AI SUGGESTION · APPROVED" : r.origin === "legacy_import" ? "LEGACY IMPORT · VERIFY ORIGIN" : "MANUAL"}</span></span>
        ${r.source_evidence ? `<span class="cell-sub">${esc(r.source_evidence.slice(0, 220))}</span>` : ""}
      </td>
      <td><div class="row-actions">
        ${hasPermission("requirements.edit") ? `<button class="act-btn edit-req" data-id="${r.id}">Edit</button>` : ""}
        ${hasPermission("requirements.delete") ? `<button class="act-btn act-btn-danger del-req" data-id="${r.id}">Delete</button>` : ""}
      </div></td>
    </tr>`).join("");
  return `
    <div class="workflow-box panel info-box" style="margin-bottom:14px">
      <strong>Step 1 — Provide requirement source</strong><br>
      Upload a BRD from the <button class="act-btn" id="goBrdFromReq">BRD Documents</button> section or add requirements manually below.
      Review each requirement and mark it <strong>Reviewed</strong> to approve it for test-case generation.
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
  const genOk = S.caseGenerator === "deterministic" || Boolean(S.aiSettings.configured);
  const provider = S.aiSettings.providers?.find(item => item.id === S.aiSettings.provider);
  const assistedGenOk = Boolean(S.aiSettings.configured && provider?.id !== "local");
  const reviewedRequirementCount = S.requirements.filter(r => r.review_status === "Reviewed").length;
  const unreviewedRequirementCount = S.requirements.length - reviewedRequirementCount;
  const aiMsg = S.caseGenerator !== "deterministic" && !S.aiSettings.configured
    ? `<div class="info-box warn-box" style="margin-top:8px">Configure ${esc(provider?.name || "the selected AI provider")} in <button class="act-btn" id="goAiSettings">Settings → AI Module</button> to enable AI generation.</div>` : "";
  const assistedAnalysis = S.assistedAnalysis
    ? `<div class="info-box" style="margin-top:12px">
        <strong>AI analysis · review before approval</strong>
        <div class="panel-sub">Explicit input facts are separated from AI assumptions and recommendations. Cases remain editable drafts.</div>
        ${S.assistedAnalysis.explicit_facts?.length ? `<p><strong>Explicitly stated</strong><br>${S.assistedAnalysis.explicit_facts.map(esc).join("<br>")}</p>` : ""}
        ${S.assistedAnalysis.category_assessment?.length ? `<p><strong>Category applicability</strong><br>${S.assistedAnalysis.category_assessment.map(item => `${esc(item.category)}: ${item.applicable ? "Applicable" : "Not applicable"} — ${esc(item.reason)}`).join("<br>")}</p>` : ""}
        ${[
          ["Clarification questions", "clarification_questions"],
          ["Assumptions", "assumptions"],
          ["Traceability warnings", "traceability_warnings"],
          ["Coverage gaps", "coverage_gaps"],
          ["Duplicates or overlap", "duplicates"],
          ["Unclear or conflicting", "unclear_or_conflicting"],
          ["Additional recommendations", "additional_recommendations"],
        ].filter(([, key]) => S.assistedAnalysis[key]?.length).map(([label, key]) =>
          `<p><strong>${label}</strong><br>${S.assistedAnalysis[key].map(esc).join("<br>")}</p>`).join("")}
      </div>` : "";
  const rows = S.cases.map(c => `
    <tr data-search="${esc(`${c.case_key} ${c.title || ""} ${c.scenario} ${c.requirement_key || ""} ${c.use_case_id || ""} ${c.test_type} ${c.test_category || ""} ${c.classification || ""} ${c.status} ${c.priority} ${c.preconditions || ""} ${c.test_data || ""} ${c.assumptions || ""} ${c.source_reference || ""}`.toLowerCase())}">
      <td class="mono">${esc(c.case_key)}<br>${c.generation_key ? '<span class="gen-label">AI DRAFT</span>' : ""}</td>
      <td class="mono">${esc(c.requirement_key || "—")}
        ${(c.requirement_origin || c.requirement_current_origin) === "ai_suggestion" ? `<span class="cell-sub"><span class="badge badge-amber">AI APPROVED · ${esc(c.requirement_approval_status || c.requirement_current_approval_status || "Reviewed")}</span></span>` : (c.requirement_origin || c.requirement_current_origin) === "extracted" ? '<span class="cell-sub"><span class="badge badge-green">EXTRACTED</span></span>' : ""}
      </td>
      <td class="mono">${esc(c.use_case_id || "—")}</td>
      <td><span class="badge">${esc(c.test_category || "Functional")}</span><span class="cell-sub">${esc(c.test_type || "Functional")} · ${esc(c.classification || "Unclassified")}</span></td>
      <td><span class="badge">${esc(c.priority || "Not specified")}</span></td>
      <td><strong>${esc(c.title || c.scenario)}</strong><span class="cell-sub">${esc(c.scenario)}</span></td>
      <td class="case-long">${pretty(c.preconditions || "—")}</td>
      <td class="case-long">${pretty(c.test_data || "—")}</td>
      <td class="case-long">${pretty(c.steps || "—")}</td>
      <td class="case-long">${pretty(c.expected_result || "—")}</td>
      <td class="case-long">${c.assumptions ? `<strong>Assumptions</strong><br>${pretty(c.assumptions)}<br>` : ""}${c.source_reference ? `<strong>Source</strong><br>${pretty(c.source_reference)}` : "—"}</td>
      <td>
        <select class="exec-select" data-id="${c.id}" aria-label="Status for ${esc(c.case_key)}" ${hasPermission("test-cases.edit") ? "" : "disabled"}>
          ${["Not Executed","In Progress","Pass","Fail","Blocked","Skipped"].map(s =>
            `<option ${s === c.status ? "selected" : ""}>${s}</option>`).join("")}
        </select>
      </td>
      <td class="case-long">${pretty(c.actual_result || "—")}</td>
      <td><div class="row-actions">
        ${hasPermission("test-cases.edit") ? `<button class="act-btn edit-case" data-id="${c.id}">Edit</button>` : ""}
        ${c.execution_id ? `<button class="act-btn ev-case" data-case="${c.id}">History</button>` : ""}
        ${c.status === "Fail" && hasPermission("bugs.create") ? `<button class="act-btn bug-from-case" data-case="${c.id}">＋ Defect</button>` : ""}
        ${hasPermission("test-cases.delete") ? `<button class="act-btn act-btn-danger del-case" data-id="${c.id}">Delete</button>` : ""}
      </div></td>
    </tr>`).join("");
  return `
    <div class="gen-note info-box">
      <strong>Step 2 — Generate test cases from reviewed requirements.</strong>
      Reviewed is the approval state. Cases are linked to their source requirement; use-case traceability is included when stated.
      ${!S.requirements.length
        ? "<br>Add or import requirements for BRD-based generation, or use the AI-assisted generator below without a BRD."
        : !reviewedRequirementCount
          ? "<br>Mark a requirement Reviewed for BRD-based generation, or use the AI-assisted generator below."
          : `<br>${reviewedRequirementCount} requirement(s) ready for generation${unreviewedRequirementCount ? `; ${unreviewedRequirementCount} not reviewed and excluded` : ""}.`}
    </div>
    <div class="panel">
      <div class="panel-head">
        <div><div class="panel-title">Test-case generator</div><div class="panel-sub">Generate coverage from approved requirements using the test-case skill.</div></div>
        <span class="badge">OPTIONAL AI</span>
      </div>
      <div class="gen-select">
        <label>Generation method
          <select id="caseGenerator">
            <option value="deterministic" ${S.caseGenerator === "deterministic" ? "selected" : ""}>Local deterministic (no model needed)</option>
            ${provider && provider.id !== "local" ? `<option value="${esc(provider.id)}" ${S.caseGenerator === provider.id ? "selected" : ""}>${esc(provider.name)} · ${esc(provider.model)}</option>` : ""}
          </select>
        </label>
        <label>&nbsp;</label>
      </div>
      ${aiMsg}
    </div>
    <div class="panel" style="margin-top:14px">
      <div class="panel-head">
        <div><div class="panel-title">AI-assisted generation · no BRD required</div><div class="panel-sub">Paste draft requirements, feature notes, workflows, or existing cases to analyze coverage and generate editable drafts.</div></div>
        <span class="badge">AI DRAFTS</span>
      </div>
      <div class="field full">
        <label for="assistedInput">Feature description, use cases, notes, or existing test cases</label>
        <textarea id="assistedInput" maxlength="20000" placeholder="Describe the workflow and any known expected behavior. You can also paste current test cases for gap and duplicate analysis.">${esc(S.assistedInput)}</textarea>
        <span class="cell-sub">Up to 20,000 characters. Unclear details are reported as assumptions or clarification questions, not confirmed rules.</span>
      </div>
      <div class="category-select">
        <fieldset class="field full" style="border:0;padding:0;margin:0">
          <legend>Testing categories · AI marks unsupported categories not applicable</legend>
          <div class="category-options">
            ${["Functional", "Non-Functional", "Sanity", "Smoke"].map(category =>
              `<label><input type="checkbox" name="assistedCategory" value="${category}" ${S.assistedCategories.includes(category) ? "checked" : ""}> ${category}</label>`).join("")}
          </div>
        </fieldset>
      </div>
      ${!assistedGenOk
        ? `<div class="info-box warn-box" style="margin-top:8px">Configure a model-based provider (Ollama, Google Gemini, or OpenAI-compatible) in <button class="act-btn" id="goAiSettings">Settings → AI Module</button> to use AI-assisted generation.</div>`
        : ""}
      ${hasPermission("test-cases.create") ? `<button class="btn btn-primary" id="assistedGenerateBtn" style="margin-top:12px" ${assistedGenOk ? "" : "disabled"}>✦ Analyze and generate</button>` : ""}
      ${assistedAnalysis}
    </div>
    <div class="filter-row">
      <input class="search-input" id="searchTable" placeholder="⌕  Search test cases…">
    </div>
    <div class="table-wrap">
      <table class="data-table tc-table">
      <thead><tr><th>Case ID</th><th>Req ID</th><th>Use Case</th><th>Category / Type</th><th>Priority</th><th>Title / Scenario</th><th>Preconditions</th><th>Test data</th><th>Steps</th><th>Expected result</th><th>Assumptions / Source</th><th>Status</th><th>Actual result</th><th>Actions</th></tr></thead>
      <tbody>${rows || '<tr><td colspan="14" class="empty-row"><strong>No test cases yet</strong>Generate cases from reviewed requirements or use AI-assisted generation above.</td></tr>'}</tbody>
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
      <td>
        <button class="act-btn view-bug" data-id="${b.id}">View</button>
        ${hasPermission("bugs.edit") ? `<button class="act-btn edit-bug" data-id="${b.id}">Update</button>` : ""}
        ${hasPermission("bugs.delete") ? `<button class="act-btn act-btn-danger delete-bug" data-id="${b.id}">Delete</button>` : ""}
      </td>
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
          <strong>Validate requirements against source</strong> — the configured AI provider will review source excerpts for coverage evidence.
          ${hasPermission("applications.create") ? `<button class="btn btn-ghost btn-sm validate-cov" data-app="${app.id}" style="margin-top:8px">Validate requirement coverage</button>` : ""}
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
      ${hasPermission("applications.create") ? `<div style="margin-top:12px"><button class="btn btn-primary" id="appUploadInlineBtn">↑ Choose application ZIP</button></div>` : ""}
    </div>
    <div class="app-history">${cards || '<div class="panel" style="text-align:center;padding:30px;color:var(--muted)"><strong style="color:var(--text2)">No application uploaded</strong></div>'}</div>`;
}

/* ── App Analysis ─────────────────────────────────────────────────── */
function appAnalysisView() {
  return `
    <div class="panel">
      <div class="panel-head">
        <div><div class="panel-title">App Analysis</div><div class="panel-sub">Application analysis is planned for a future release.</div></div>
        <span class="badge badge-muted">COMING SOON</span>
      </div>
      <div class="info-box">This module is temporarily unavailable while we prepare its next version. Your projects and other QA Platform features remain available.</div>
    </div>`;
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
function hasPermission(permission) {
  const permissions = S.auth.permissions || [];
  return permissions.includes("*") || permissions.includes(permission);
}

async function loadSettingsTab(tab) {
  try {
    if (tab === "profile") S.profile = await api("/api/profile");
    if (tab === "team") {
      S.teams = await api("/api/teams");
      S.users = hasPermission("users.view") ? await api("/api/users") : [];
    }
    if (tab === "users") {
      const [users, roleResult, teams] = await Promise.all([
        api("/api/users"), api("/api/roles"), api("/api/teams"),
      ]);
      S.users = users;
      S.roles = roleResult.roles;
      S.teams = teams;
    }
    if (tab === "roles") {
      const result = await api("/api/roles");
      S.roles = result.roles;
      S.permissionModules = result.permission_modules;
    }
    if (tab === "audit") S.auditEvents = await api("/api/audit-events");
    render();
  } catch (error) {
    toast(error.message);
  }
}

function profileSettingsView() {
  const profile = S.profile;
  if (!profile) return `<h2>User Profile</h2><div class="pane-sub">Loading profile…</div>`;
  const image = profile.profile_image_data
    ? `data:${profile.profile_image_type};base64,${profile.profile_image_data}`
    : "";
  return `
    <h2>${profile.global_admin ? "Global Admin Profile" : "User Profile"}</h2>
    <div class="pane-sub">${profile.global_admin
      ? "Manage the profile for the configured workspace Administrator."
      : "Manage your account details and password."}</div>
    ${profile.global_admin
      ? '<div class="info-box">The Admin sign-in password is managed with QA_ADMIN_PASSWORD in the local environment configuration.</div>'
      : ""}
    <form id="profileForm" class="management-form">
      <div class="profile-avatar">${image ? `<img src="${image}" alt="Profile image">` : esc(`${profile.first_name[0] || ""}${profile.last_name[0] || ""}`)}</div>
      <label class="field">Profile image<input id="profileImage" type="file" accept="image/png,image/jpeg,image/gif,image/webp"></label>
      <button class="btn btn-ghost" type="button" id="removeProfileImage" ${image ? "" : "disabled"}>Remove image</button>
      ${field("first_name","First name",profile.first_name,"text",false,"",true)}
      ${field("last_name","Last name",profile.last_name,"text",false,"",true)}
      ${field("email","Email",profile.email,"email",false,"",true)}
      ${field("phone","Phone",profile.phone || "","tel")}
      <div class="full"><button class="btn btn-primary" type="submit">Save profile</button></div>
    </form>
    ${profile.global_admin ? "" : `
      <h3 class="management-subhead">Change password</h3>
      <form id="passwordForm" class="management-form">
        ${field("current_password","Current password","","password",false,"",true)}
        ${field("new_password","New password","","password",false,"12+ characters, upper/lowercase and number",true)}
        ${field("confirm_password","Confirm new password","","password",false,"",true)}
        <div class="full"><button class="btn btn-primary" type="submit">Update password</button></div>
      </form>`}`;
}

function usersSettingsView() {
  const rows = S.users.map(user => `
    <tr data-user-row data-status="${esc(user.status)}">
      <td><strong class="cell-main">${esc(`${user.first_name} ${user.last_name}`)}</strong></td>
      <td>${esc(user.email)}</td><td>${esc(user.role_name)}</td><td>${esc(user.team_names || "—")}</td>
      <td><span class="badge">${esc(user.status)}</span></td><td>${fmtDate(user.created_at)}</td><td>${fmtDate(user.last_login_at)}</td>
      <td>${hasPermission("users.update") ? `<button class="act-btn edit-user" data-id="${user.id}">Edit</button>` : ""}
      ${user.status === "Active" && hasPermission("users.deactivate")
        ? `<button class="act-btn deactivate-user" data-id="${user.id}">Deactivate</button>`
        : user.status !== "Active" && hasPermission("users.activate")
          ? `<button class="act-btn activate-user" data-id="${user.id}">Activate</button>` : ""}
      ${hasPermission("users.delete") ? `<button class="act-btn delete-user" data-id="${user.id}">Delete</button>` : ""}</td>
    </tr>`).join("");
  return `
    <div class="custom-field-manager-head"><div><h2>User Management</h2>
      <div class="pane-sub">Manage accounts, status, teams, and role assignments.</div></div>
      ${hasPermission("users.create") ? `<button class="btn btn-primary" id="createUser">＋ Add user</button>` : ""}
    </div>
    <div class="management-filters"><input id="userSearch" type="search" placeholder="Search name or email">
      <select id="userStatusFilter"><option value="">All statuses</option>${["Active","Inactive","Invited","Suspended"].map(status => `<option>${status}</option>`).join("")}</select></div>
    <div class="table-wrap"><table class="data-table"><thead><tr><th>Name</th><th>Email</th><th>Role</th><th>Team</th><th>Status</th><th>Created</th><th>Last login</th><th>Actions</th></tr></thead>
      <tbody>${rows || '<tr><td colspan="8" class="empty-row"><strong>No users</strong></td></tr>'}</tbody></table></div>`;
}

function teamsSettingsView() {
  const rows = S.teams.map(team => `
    <tr><td><strong class="cell-main">${esc(team.name)}</strong><div class="pane-sub">${esc(team.description || "")}</div></td>
      <td>${esc(team.owner_name || "—")}</td><td>${team.member_count}</td><td>${fmtDate(team.created_at)}</td>
      <td><span class="badge">${esc(team.status)}</span></td><td>
        <button class="act-btn manage-team" data-id="${team.id}">Members</button>
        ${hasPermission("teams.edit") ? `<button class="act-btn edit-team" data-id="${team.id}">Edit</button>` : ""}
        ${hasPermission("teams.delete") && team.status === "Active" ? `<button class="act-btn archive-team" data-id="${team.id}">Archive</button>` : ""}
      </td></tr>`).join("");
  return `
    <div class="custom-field-manager-head"><div><h2>Team & Members</h2>
      <div class="pane-sub">Organize workspace users into teams. Team deletion archives the team and preserves memberships.</div></div>
      ${hasPermission("teams.create") ? `<button class="btn btn-primary" id="createTeam">＋ Create team</button>` : ""}
    </div>
    <div class="table-wrap"><table class="data-table"><thead><tr><th>Team</th><th>Owner</th><th>Members</th><th>Created</th><th>Status</th><th>Actions</th></tr></thead>
      <tbody>${rows || '<tr><td colspan="6" class="empty-row"><strong>No teams</strong></td></tr>'}</tbody></table></div>
    <div id="teamMembersPanel"></div>`;
}

function rolePermissionMatrix(selected = []) {
  return Object.entries(S.permissionModules).map(([module, actions]) => `
    <div class="permission-row"><strong>${esc(module)}</strong>
      ${actions.map(action => {
        const key = `${module}.${action}`;
        return `<label><input type="checkbox" name="permissions" value="${key}" ${selected.includes(key) ? "checked" : ""}>${esc(action)}</label>`;
      }).join("")}
    </div>`).join("");
}

function rolesSettingsView() {
  const rows = S.roles.map(role => `
    <tr><td><strong class="cell-main">${esc(role.name)}</strong></td><td>${esc(role.description || "")}</td>
      <td>${role.user_count}</td><td>${role.permission_count}</td><td>${fmtDate(role.created_at)}</td>
      <td><span class="badge">${esc(role.status)}</span></td><td>
        ${!role.is_system && hasPermission("roles.edit") ? `<button class="act-btn edit-role" data-id="${role.id}">Edit</button>` : ""}
        ${!role.is_system && hasPermission("roles.delete") ? `<button class="act-btn delete-role" data-id="${role.id}">Delete</button>` : ""}
      </td></tr>`).join("");
  return `
    <div class="custom-field-manager-head"><div><h2>Roles & Permissions</h2>
      <div class="pane-sub">Define reusable module permissions. The system Administrator role is protected.</div></div>
      ${hasPermission("roles.create") ? `<button class="btn btn-primary" id="createRole">＋ Create role</button>` : ""}
    </div>
    <div class="table-wrap"><table class="data-table"><thead><tr><th>Role</th><th>Description</th><th>Users</th><th>Permissions</th><th>Created</th><th>Status</th><th>Actions</th></tr></thead>
      <tbody>${rows || '<tr><td colspan="7" class="empty-row"><strong>No roles</strong></td></tr>'}</tbody></table></div>`;
}

function auditSettingsView() {
  const rows = (S.auditEvents || []).map(event => `
    <tr><td>${fmtDate(event.created_at)}</td><td>${esc(event.actor_email || event.actor_label || "System")}</td>
      <td>${esc(event.tenant_name || event.tenant_id || "—")}</td>
      <td>${esc(event.action)}</td><td>${esc(event.target_type)} ${esc(event.target_id)}</td>
      <td>${esc(JSON.stringify(event.details))}</td></tr>`).join("");
  return `<h2>Activity log</h2><div class="pane-sub">Recent user, team, role, and permission changes.</div>
    <div class="table-wrap"><table class="data-table"><thead><tr><th>Time</th><th>Actor</th><th>Tenant</th><th>Action</th><th>Target</th><th>Previous / new values</th></tr></thead>
    <tbody>${rows || '<tr><td colspan="6" class="empty-row"><strong>No activity recorded</strong></td></tr>'}</tbody></table></div>`;
}

function settingsView() {
  const tab = S.settingsTab;
  const navItems = [
    ["ai", "✦", "AI Module"],
    ...(hasPermission("users.view") ? [["users", "♙", "Users"]] : []),
    ...(hasPermission("teams.view") ? [["team", "◎", "Team & Members"]] : []),
    ...(hasPermission("roles.view") ? [["roles", "◈", "Roles & Permissions"]] : []),
    ...(hasPermission("admin") || hasPermission("*") ? [["custom-fields", "▤", "Custom Fields"]] : []),
    ["profile", "◉", "User Profile"],
    ["integrations", "⇄", "Integrations"],
    ["notifications", "◻", "Notifications"],
    ["preferences", "⚙", "App Preferences"],
    ...(hasPermission("*") ? [["archive", "▣", "Archive"]] : []),
    ...(hasPermission("*") ? [["audit", "≋", "Activity Log"]] : []),
  ];
  const navHtml = navItems.map(([id, icon, label]) => `
    <button class="settings-nav-item ${tab === id ? "active" : ""}" data-stab="${id}">${icon} ${label}</button>`).join("");

  let pane = "";
  if (tab === "ai") {
    const providerRows = S.aiSettings.providers || [];
    const draft = S.aiDraft || {
      provider: S.aiSettings.provider || "local",
      model: S.aiSettings.model || "deterministic",
      base_url: S.aiSettings.base_url || "",
      api_key: "",
      clear_api_key: false,
    };
    const selected = providerRows.find(item => item.id === draft.provider);
    const setupStatus = S.aiSettings.configuration_error
      || (S.aiSettings.configured
        ? `${selected?.name || "Provider"} configuration is ready.`
        : "Complete the provider settings and test the connection.");
    const adminForm = !S.auth.authenticated ? `
      <form id="adminLoginForm" class="ai-login-form">
        <h3>Admin sign-in</h3>
        <p class="pane-sub">${S.auth.admin_configured
          ? "Sign in to manage AI providers, run AI analysis, and configure custom fields."
          : "Copy qa-platform/.env.example to qa-platform/.env, set QA_ADMIN_PASSWORD to a unique password of at least 12 characters, then restart the server."}</p>
        <label for="adminPassword">Admin password
          <input id="adminPassword" type="password" autocomplete="current-password" required
            ${S.auth.admin_configured ? "" : "disabled"}>
        </label>
        <button class="btn btn-primary" type="submit" ${S.auth.admin_configured ? "" : "disabled"}>Sign in</button>
        <div class="ai-status" id="loginStatus" role="status"></div>
      </form>` : "";
    const settingsForm = hasPermission("*") ? `
      <div class="ai-fields">
        <label for="aiProvider">Provider
          <select id="aiProvider">
            ${providerRows.map(item => `<option value="${esc(item.id)}" ${item.id === draft.provider ? "selected" : ""}>${esc(item.name)}</option>`).join("")}
          </select>
        </label>
        <label for="aiModel">Model
          <input id="aiModel" value="${esc(draft.model)}" ${selected?.id === "local" ? "readonly" : ""}
            ${selected?.id !== "local" ? "required" : ""} maxlength="200">
        </label>
        ${selected?.requires_base_url ? `<label for="aiBaseUrl">API base URL
          <input id="aiBaseUrl" type="url" value="${esc(draft.base_url)}" required maxlength="500"
            placeholder="${selected.id === "ollama" ? "http://127.0.0.1:11434/v1" : "https://provider.example/v1"}">
        </label>` : ""}
        ${selected?.requires_api_key ? `<label for="aiApiKey">API key
          <input id="aiApiKey" type="password" autocomplete="new-password" maxlength="4096"
            placeholder="${selected.api_key_configured ? "Saved securely; leave blank to keep it" : "Enter provider API key"}">
        </label>` : ""}
      </div>
      ${selected?.api_key_configured ? `<label class="ai-clear-key"><input id="aiClearKey" type="checkbox" ${draft.clear_api_key ? "checked" : ""}> Clear the saved API key</label>` : ""}
      <div class="ai-actions">
        <button class="btn btn-primary" id="saveAi">Save provider settings</button>
        <button class="btn btn-ghost" id="testAi" ${S.aiDraft || !S.aiSettings.configured ? "disabled" : ""}>Test connection</button>
      </div>
      <div class="ai-status" id="aiStatus" role="status">${esc(S.aiStatus || setupStatus)}</div>
      <div class="info-box" style="margin-top:14px">
        API keys are encrypted in the local database using <code>QA_AI_ENCRYPTION_KEY</code> and are never returned to the browser.
        Local mode stays on this machine. Ollama uses its local server. Gemini and Other send analysis text to the selected provider.
      </div>` : `<div class="info-box">Only an Administrator can change the AI provider, credentials, or workspace-level AI settings.</div>`;
    pane = `
      <h2>AI Module</h2>
      <div class="pane-sub">Configure Local deterministic, Ollama, Google Gemini, or an OpenAI-compatible model.</div>
      ${adminForm}
      ${settingsForm}`;
  } else if (tab === "custom-fields") {
    pane = hasPermission("*")
      ? `<div class="custom-field-manager-head"><div><h2>Custom Fields</h2>
          <div class="pane-sub">Create reusable fields for Project, Requirement, Test Case, and Defect forms.</div></div>
          <button class="btn btn-primary" id="addCustomField">＋ Add field</button></div>
          <div id="customFieldList"><div class="pane-sub">Loading custom fields…</div></div>`
      : `<h2>Custom Fields</h2><div class="pane-sub">Only an Admin can create or manage workspace custom fields.</div>
          <button class="btn btn-primary" id="goCustomFieldAdmin">Sign in as Admin</button>`;
  } else if (tab === "profile") {
    pane = profileSettingsView();
  } else if (tab === "team") {
    pane = teamsSettingsView();
  } else if (tab === "users") {
    pane = usersSettingsView();
  } else if (tab === "roles") {
    pane = rolesSettingsView();
  } else if (tab === "audit") {
    pane = auditSettingsView();
  } else if (tab === "archive") {
    pane = `<h2>Archive</h2><div class="pane-sub">Files retained from deleted projects.</div><div id="archiveContent"><div style="color:var(--muted);font-size:12px">Loading…</div></div>`;
  } else if (tab === "preferences") {
    pane = `
      <h2>App Preferences</h2>
      <div class="pane-sub">Choose how Trace looks. System follows your device's light or dark appearance.</div>
      <div class="preference-setting">
        <label for="themePreference">Color theme</label>
        <select id="themePreference">
          <option value="dark" ${S.themePreference === "dark" ? "selected" : ""}>Dark</option>
          <option value="light" ${S.themePreference === "light" ? "selected" : ""}>Light</option>
          <option value="system" ${S.themePreference === "system" ? "selected" : ""}>System (match device)</option>
        </select>
        <div class="pane-sub">This preference is saved in this browser.</div>
      </div>`;
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
      <div class="page-head-actions"><span class="pane-sub">${esc(S.auth.user?.email || "Administrator")}</span></div>
    </div>
    <div class="settings-grid">
      <div class="settings-nav">${navHtml}</div>
      <div class="settings-pane">${pane}</div>
    </div>`;
}

function renderCustomFieldList() {
  const container = $("#customFieldList");
  if (!container) return;
  const rows = S.customFields.map(item => `
    <article class="custom-field-card">
      <div>
        <strong>${esc(item.label)}</strong>
        <span class="badge ${item.active ? "badge-green" : "badge-muted"}">${item.active ? "ACTIVE" : "INACTIVE"}</span>
        <div class="pane-sub">${esc(item.module_label)} · ${esc(item.field_type)}${item.required ? " · Required" : ""}</div>
        ${item.description ? `<div class="pane-sub">${esc(item.description)}</div>` : ""}
        ${item.field_type === "dropdown" ? `<div class="pane-sub">Options: ${item.options.map(option => esc(option.label)).join(", ")}</div>` : ""}
      </div>
      <div class="custom-field-actions">
        <button class="act-btn edit-custom-field" data-id="${item.id}">Edit</button>
        <button class="act-btn toggle-custom-field" data-id="${item.id}" data-active="${item.active ? "true" : "false"}">${item.active ? "Deactivate" : "Activate"}</button>
        <button class="act-btn delete-custom-field" data-id="${item.id}">Delete</button>
      </div>
    </article>`).join("");
  container.innerHTML = rows || '<div class="empty-row"><strong>No custom fields configured</strong>Add a field to make it available on a form.</div>';
  $$(".edit-custom-field", container).forEach(button => button.addEventListener("click", () =>
    openCustomFieldModal(button.dataset.id)));
  $$(".toggle-custom-field", container).forEach(button => button.addEventListener("click", () =>
    updateCustomField(button.dataset.id, { active: button.dataset.active !== "true" })));
  $$(".delete-custom-field", container).forEach(button => button.addEventListener("click", () =>
    deleteCustomField(button.dataset.id)));
}

async function loadCustomFields() {
  S.customFields = await api("/api/custom-fields");
  renderCustomFieldList();
}

function openCustomFieldModal(id = null) {
  const existing = S.customFields.find(item => String(item.id) === String(id));
  $("#saveModal").hidden = false;
  $("#modalEyebrow").textContent = "WORKSPACE CONFIGURATION";
  $("#modalTitle").textContent = existing ? "Edit custom field" : "Create custom field";
  $("#saveModal").textContent = existing ? "Save field" : "Create field";
  $("#formError").textContent = "";
  $("#modalFields").innerHTML =
    selectField("module_key", "Form / module", [
      { value: "projects", label: "Project" },
      { value: "requirements", label: "Requirement" },
      { value: "test-cases", label: "Test Case" },
      { value: "bugs", label: "Defect" },
    ], existing?.module_key || "bugs", true) +
    field("name", "Field name", existing?.name || "", "text", false, "e.g. build_number", true) +
    field("label", "Field label", existing?.label || "", "text", false, "e.g. Build Number", true) +
    selectField("field_type", "Field type", [
      { value: "text", label: "Text" },
      { value: "number", label: "Number" },
      { value: "dropdown", label: "Dropdown" },
    ], existing?.field_type || "text") +
    field("display_order", "Display order", existing?.display_order ?? 0, "number") +
    field("description", "Help text", existing?.description || "", "textarea", true, "Optional field guidance") +
    `<div class="field full" id="customFieldOptionsRow" ${existing?.field_type === "dropdown" ? "" : "hidden"}>
      <label for="f_options">Dropdown options</label>
      <textarea id="f_options" name="options" placeholder="One option per line; use Label | value for a distinct stored value">${esc(
        existing?.options.map(option => option.label === option.value
          ? option.label : `${option.label} | ${option.value}`).join("\n") || ""
      )}</textarea>
    </div>
    <label class="custom-field-check"><input id="f_required" name="required" type="checkbox" ${existing?.required ? "checked" : ""}> Required</label>
    <label class="custom-field-check"><input id="f_active" name="active" type="checkbox" ${existing ? (existing.active ? "checked" : "") : "checked"}> Active</label>`;
  $("#cancelModal").onclick = () => modal.close();
  $("#closeModal").onclick = () => modal.close();
  $("#f_field_type").addEventListener("change", event => {
    $("#customFieldOptionsRow").hidden = event.target.value !== "dropdown";
  });
  modalForm.onsubmit = async event => {
    event.preventDefault();
    $("#formError").textContent = "";
    const form = new FormData(modalForm);
    const options = String(form.get("options") || "").split(/\r?\n/)
      .map(line => line.trim()).filter(Boolean)
      .map(line => {
        const [label, value] = line.split("|", 2).map(item => item.trim());
        return { label, value: value || label };
      });
    const payload = {
      module_key: form.get("module_key"),
      name: form.get("name"),
      label: form.get("label"),
      field_type: form.get("field_type"),
      description: form.get("description"),
      display_order: Number(form.get("display_order") || 0),
      required: $("#f_required").checked,
      active: $("#f_active").checked,
      options,
    };
    try {
      await api(existing ? `/api/custom-fields/${existing.id}` : "/api/custom-fields", {
        method: existing ? "PATCH" : "POST",
        body: JSON.stringify(payload),
      });
      modal.close();
      await loadCustomFields();
      toast(existing ? "Custom field updated" : "Custom field created");
    } catch (error) {
      $("#formError").textContent = error.message;
    }
  };
  modal.showModal();
}

async function updateCustomField(id, changes) {
  try {
    await api(`/api/custom-fields/${id}`, {
      method: "PATCH",
      body: JSON.stringify(changes),
    });
    await loadCustomFields();
    toast(changes.active ? "Custom field activated" : "Custom field deactivated");
  } catch (error) { toast(error.message); }
}

async function deleteCustomField(id) {
  if (!confirm("Delete this custom field? Fields with saved values must be deactivated instead.")) return;
  try {
    await api(`/api/custom-fields/${id}`, { method: "DELETE" });
    await loadCustomFields();
    toast("Custom field deleted");
  } catch (error) { toast(error.message); }
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
  $("#importBtn")?.addEventListener("click", () => importBrd());
  $("#uploadBrdBtn")?.addEventListener("click", () => importBrd());
  $("#configAiBtn")?.addEventListener("click", () => { S.settingsTab = "ai"; navigate("settings"); render(); });
  $("#pasteBtn")?.addEventListener("click", pasteRequirement);
  $("#goBrdFromReq")?.addEventListener("click", () => { navigate("brd"); render(); });
  $("#goBrd")?.addEventListener("click", () => { navigate("brd"); render(); });
  $("#goRequirements")?.addEventListener("click", () => { navigate("requirements"); render(); });
  $("#goAiSettings")?.addEventListener("click", () => { S.settingsTab = "ai"; navigate("settings"); render(); });

  $("#genCasesBtn")?.addEventListener("click", generateCases);
  $("#caseGenerator")?.addEventListener("change", e => { S.caseGenerator = e.target.value; render(); });
  $("#assistedInput")?.addEventListener("input", e => { S.assistedInput = e.target.value; });
  $$('input[name="assistedCategory"]').forEach(input => input.addEventListener("change", () => {
    S.assistedCategories = $$('input[name="assistedCategory"]:checked').map(item => item.value);
  }));
  $("#assistedGenerateBtn")?.addEventListener("click", generateAdHocCases);

  $("#apiImportBtn")?.addEventListener("click", importApiDoc);
  $("#appUploadBtn")?.addEventListener("click", importAppZip);
  $("#appUploadInlineBtn")?.addEventListener("click", importAppZip);
  $$(".validate-cov").forEach(b => b.addEventListener("click", () => validateCoverage(b.dataset.app)));
  $$(".load-inv").forEach(b => b.addEventListener("click", () => {
    S.inventoryVisible[b.dataset.app] = (S.inventoryVisible[b.dataset.app] || 100) + 100;
    render();
  }));


  $("#exportBtn")?.addEventListener("click", exportReport);
  $$(".export-csv").forEach(b => b.addEventListener("click", exportReport));
  $$(".module-export").forEach(b => b.addEventListener("click", () =>
    exportModuleReport(b.dataset.module)));

  $("#editProjBtn")?.addEventListener("click", () => openModal("project", S.project.id));
  $("#delProjectBtn")?.addEventListener("click", () => deleteProject(S.project.id));

  /* Table search */
  $("#searchTable")?.addEventListener("input", e => {
    const q = e.target.value.toLowerCase();
    $$("[data-search]").forEach(r => r.hidden = !r.dataset.search.includes(q));
  });

  /* Requirement actions */
  $$(".view-brd-analysis").forEach(b => b.addEventListener("click", () =>
    showBrdAnalysis(b.dataset.id, b.dataset.filename)));
  $$(".suggestion-accept").forEach(b => b.addEventListener("click", () =>
    decideRequirementSuggestion(b.dataset.id, "accept")));
  $$(".suggestion-reject").forEach(b => b.addEventListener("click", () =>
    decideRequirementSuggestion(b.dataset.id, "reject")));
  $$(".suggestion-edit-toggle").forEach(b => b.addEventListener("click", () => {
    const editor = $(`#suggestion-editor-${b.dataset.id}`);
    if (editor) editor.open = !editor.open;
  }));
  $$(".suggestion-save").forEach(b => b.addEventListener("click", () =>
    decideRequirementSuggestion(b.dataset.id, "edit")));
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
  $$(".view-bug").forEach(b => b.addEventListener("click", () => showDefect(b.dataset.id)));
  $$(".edit-bug").forEach(b => b.addEventListener("click", () => openModal("bug", b.dataset.id)));
  $$(".delete-bug").forEach(b => b.addEventListener("click", () => deleteBug(b.dataset.id)));

  /* File download */
  $$(".dl-file").forEach(b => b.addEventListener("click", () => {
    location.href = pp(`files/download?path=${encodeURIComponent(b.dataset.path)}`);
  }));
}

/* ── Settings actions ─────────────────────────────────────────────── */
function openManagementModal(kind, id = null) {
  const record = kind === "user" ? S.users.find(item => String(item.id) === String(id))
    : kind === "team" ? S.teams.find(item => String(item.id) === String(id))
    : S.roles.find(item => String(item.id) === String(id));
  const isEdit = Boolean(record);
  $("#saveModal").hidden = false;
  $("#formError").textContent = "";
  $("#modalEyebrow").textContent = "WORKSPACE MANAGEMENT";
  $("#modalTitle").textContent = `${isEdit ? "Edit" : "Create"} ${kind}`;
  $("#saveModal").textContent = isEdit ? "Save changes" : `Create ${kind}`;
  if (kind === "user") {
    const roleOptions = S.roles.filter(role => role.status === "Active")
      .map(role => ({ value: role.id, label: role.name }));
    const defaultRole = roleOptions.find(role => role.label === "User");
    const teamOptions = S.teams.filter(team => team.status === "Active")
      .map(team => `<option value="${team.id}" ${(record?.team_ids || []).includes(team.id) ? "selected" : ""}>${esc(team.name)}</option>`)
      .join("");
    $("#modalFields").innerHTML =
      field("first_name","First name",record?.first_name || "","text",false,"",true) +
      field("last_name","Last name",record?.last_name || "","text",false,"",true) +
      field("email","Email",record?.email || "","email",false,"",true) +
      field("phone","Phone",record?.phone || "","tel") +
      selectField("role_id","Role",roleOptions,record?.role_id || defaultRole?.value) +
      `<label class="field">Teams<select name="team_ids" multiple size="4">${teamOptions}</select>
        <span class="field-hint">Use Ctrl or Command to select multiple teams.</span></label>` +
      selectField("status","Status",["Active","Inactive","Invited","Suspended"],record?.status || "Active") +
      field("password",isEdit ? "Reset password (optional)" : "Temporary password","","password",true,
        isEdit ? "Leave blank to keep the current password" : "12+ chars, upper/lowercase and number",!isEdit);
  } else if (kind === "team") {
    const activeUsers = S.users.filter(user => user.status === "Active")
      .map(user => ({ value: user.id, label: `${user.first_name} ${user.last_name} · ${user.email}` }));
    $("#modalFields").innerHTML =
      field("name","Team name",record?.name || "","text",true,"",true) +
      selectField("owner_user_id","Owner / lead",activeUsers,record?.owner_user_id || activeUsers[0]?.value) +
      selectField("status","Status",["Active","Inactive"],record?.status || "Active") +
      field("description","Description",record?.description || "","textarea",true);
  } else {
    $("#modalFields").innerHTML =
      field("name","Role name",record?.name || "","text",true,"",true) +
      selectField("status","Status",["Active","Inactive"],record?.status || "Active") +
      field("description","Description",record?.description || "","textarea",true) +
      `<div class="field full"><label>Permission matrix</label>${rolePermissionMatrix(record?.permissions || [])}</div>`;
  }
  $("#cancelModal").onclick = () => modal.close();
  $("#closeModal").onclick = () => modal.close();
  modalForm.onsubmit = async event => {
    event.preventDefault();
    const form = new FormData(modalForm);
    const payload = Object.fromEntries(form.entries());
    if (kind === "role") payload.permissions = form.getAll("permissions");
    if (kind === "user") payload.team_ids = form.getAll("team_ids").map(Number);
    try {
      const base = kind === "user" ? "/api/users" : kind === "team" ? "/api/teams" : "/api/roles";
      await api(isEdit ? `${base}/${record.id}` : base, {
        method: isEdit ? "PATCH" : "POST",
        body: JSON.stringify(payload),
      });
      modal.close();
      await loadSettingsTab(S.settingsTab);
      toast(`${kind[0].toUpperCase()}${kind.slice(1)} ${isEdit ? "updated" : "created"}`);
    } catch (error) {
      $("#formError").textContent = error.message;
    }
  };
  modal.showModal();
}

async function showTeamMembers(teamId) {
  const panel = $("#teamMembersPanel");
  if (!panel) return;
  try {
    const members = await api(`/api/teams/${teamId}/members`);
    const team = S.teams.find(item => String(item.id) === String(teamId));
    const availableUsers = S.users.filter(user =>
      user.status === "Active" && !members.some(member => member.id === user.id)
    );
    panel.innerHTML = `
      <div class="team-members-head"><div><h3>${esc(team?.name || "Team")} members</h3>
        <div class="pane-sub">Removing a member only removes this team membership.</div></div></div>
      ${hasPermission("teams.manage-members") ? `<form id="addTeamMember" class="inline-management-form">
        <select name="user_id" required>${availableUsers.map(user => `<option value="${user.id}">${esc(user.first_name)} ${esc(user.last_name)} · ${esc(user.email)}</option>`).join("")}</select>
        <button class="btn btn-primary" type="submit" ${availableUsers.length ? "" : "disabled"}>Add member</button>
      </form>` : ""}
      <label class="member-search">Search members<input id="memberSearch" type="search" placeholder="Name or email"></label>
      <div class="table-wrap"><table class="data-table"><thead><tr><th>Name</th><th>Email</th><th>Role</th><th>Actions</th></tr></thead>
      <tbody>${members.map(member => `<tr data-member-row><td>${esc(member.first_name)} ${esc(member.last_name)}</td><td>${esc(member.email)}</td><td>${esc(member.role_name)}</td>
        <td>${hasPermission("teams.manage-members") && team?.owner_user_id !== member.id ? `<button class="act-btn remove-team-member" data-user-id="${member.id}" data-team-id="${teamId}">Remove</button>` : ""}</td></tr>`).join("")
        || '<tr><td colspan="4" class="empty-row"><strong>No members</strong></td></tr>'}</tbody></table></div>`;
    $("#addTeamMember")?.addEventListener("submit", async event => {
      event.preventDefault();
      const userId = new FormData(event.currentTarget).get("user_id");
      try {
        await api(`/api/teams/${teamId}/members`, {
          method: "POST", body: JSON.stringify({ user_id: Number(userId) }),
        });
        await loadSettingsTab("team");
        await showTeamMembers(teamId);
      } catch (error) { toast(error.message); }
    });
    $("#memberSearch")?.addEventListener("input", event => {
      const query = event.target.value.toLowerCase();
      $$("[data-member-row]", panel).forEach(row =>
        row.hidden = !row.textContent.toLowerCase().includes(query));
    });
    $$(".remove-team-member", panel).forEach(button => button.addEventListener("click", async () => {
      if (!confirm("Remove this user from the team? Their other access will remain unchanged.")) return;
      try {
        await api(`/api/teams/${teamId}/members/${button.dataset.userId}`, { method: "DELETE" });
        await loadSettingsTab("team");
        await showTeamMembers(teamId);
      } catch (error) { toast(error.message); }
    }));
  } catch (error) { toast(error.message); }
}

function bindProfileActions() {
  $("#profileForm")?.addEventListener("submit", async event => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const payload = Object.fromEntries(form.entries());
    const image = $("#profileImage")?.files[0];
    if (image) {
      if (!["image/png","image/jpeg","image/gif","image/webp"].includes(image.type) || image.size > 1024 * 1024) {
        toast("Choose a PNG, JPEG, GIF, or WebP profile image no larger than 1 MB.");
        return;
      }
      const dataUrl = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = () => reject(new Error("Could not read the selected profile image."));
        reader.readAsDataURL(image);
      });
      payload.profile_image_data = String(dataUrl).split(",", 2)[1];
      payload.profile_image_type = image.type;
    } else if (S.removeProfileImage) {
      payload.profile_image_data = null;
      payload.profile_image_type = "";
    }
    try {
      await api("/api/profile", { method: "PATCH", body: JSON.stringify(payload) });
      S.removeProfileImage = false;
      await loadSettingsTab("profile");
      if (S.profile && S.auth.user) {
        Object.assign(S.auth.user, {
          first_name: S.profile.first_name,
          last_name: S.profile.last_name,
          email: S.profile.email,
        });
      }
      syncProfileMenu();
      toast("Profile saved");
    } catch (error) { toast(error.message); }
  });
  $("#removeProfileImage")?.addEventListener("click", () => {
    S.removeProfileImage = true;
    $("#profileImage").value = "";
    $("#removeProfileImage").disabled = true;
  });
  $("#passwordForm")?.addEventListener("submit", async event => {
    event.preventDefault();
    const payload = Object.fromEntries(new FormData(event.currentTarget).entries());
    try {
      await api("/api/profile/password", { method: "PATCH", body: JSON.stringify(payload) });
      event.currentTarget.reset();
      toast("Password updated");
    } catch (error) { toast(error.message); }
  });
}

function bindSettingsActions() {
  bindPasswordToggles();
  $("#themePreference")?.addEventListener("change", event =>
    saveThemePreference(event.target.value));
  /* Tab switcher */
  $$(".settings-nav-item").forEach(btn => btn.addEventListener("click", async () => {
    S.settingsTab = btn.dataset.stab;
    render();
    if (S.settingsTab === "archive") loadArchive();
    else if (["profile","team","users","roles","audit"].includes(S.settingsTab)) {
      await loadSettingsTab(S.settingsTab);
    }
  }));
  $("#createUser")?.addEventListener("click", () => openManagementModal("user"));
  $("#createTeam")?.addEventListener("click", () => openManagementModal("team"));
  $("#createRole")?.addEventListener("click", () => openManagementModal("role"));
  $$(".edit-user").forEach(button => button.addEventListener("click", () =>
    openManagementModal("user", button.dataset.id)));
  $$(".edit-team").forEach(button => button.addEventListener("click", () =>
    openManagementModal("team", button.dataset.id)));
  $$(".edit-role").forEach(button => button.addEventListener("click", () =>
    openManagementModal("role", button.dataset.id)));
  $$(".manage-team").forEach(button => button.addEventListener("click", () =>
    showTeamMembers(button.dataset.id)));
  $$(".archive-team").forEach(button => button.addEventListener("click", async () => {
    if (!confirm("Archive this team? Membership records will be retained.")) return;
    try {
      await api(`/api/teams/${button.dataset.id}`, { method: "DELETE" });
      await loadSettingsTab("team");
      toast("Team archived");
    } catch (error) { toast(error.message); }
  }));
  const changeUserStatus = async (button, action) => {
    const activating = action === "activate";
    if (!confirm(activating
      ? "Activate this user? They will be able to sign in again."
      : "Deactivate this user? They will no longer be able to sign in.")) return;
    try {
      await api(`/api/users/${button.dataset.id}/${action}`, { method: "POST", body: "{}" });
      await loadSettingsTab("users");
      toast(`User ${activating ? "activated" : "deactivated"}`);
    } catch (error) { toast(error.message); }
  };
  $$(".deactivate-user").forEach(button => button.addEventListener("click", async () => {
    await changeUserStatus(button, "deactivate");
  }));
  $$(".activate-user").forEach(button => button.addEventListener("click", async () => {
    await changeUserStatus(button, "activate");
  }));
  $$(".delete-user").forEach(button => button.addEventListener("click", async () => {
    const systemAdmin = Boolean(S.auth.user?.global_admin);
    const message = systemAdmin
      ? "Permanently delete this user account? Historical project, test case, requirement, defect, and activity records will be retained."
      : "Remove this user's access from this workspace? Memberships in other workspaces and historical QA records will be preserved.";
    if (!confirm(message)) return;
    try {
      const result = await api(`/api/users/${button.dataset.id}`, { method: "DELETE" });
      await loadSettingsTab("users");
      toast(result.account_deleted === false
        ? "User removed from this workspace; account retained elsewhere"
        : "User deleted");
    } catch (error) { toast(error.message); }
  }));
  const filterUsers = () => {
    const query = ($("#userSearch")?.value || "").trim().toLowerCase();
    const status = $("#userStatusFilter")?.value || "";
    $$("[data-user-row]").forEach(row => {
      row.hidden = !row.textContent.toLowerCase().includes(query)
        || (status && row.dataset.status !== status);
    });
  };
  $("#userSearch")?.addEventListener("input", filterUsers);
  $("#userStatusFilter")?.addEventListener("change", filterUsers);
  $$(".delete-role").forEach(button => button.addEventListener("click", async () => {
    if (!confirm("Delete this role? It cannot be deleted while assigned to users.")) return;
    try {
      await api(`/api/roles/${button.dataset.id}`, { method: "DELETE" });
      await loadSettingsTab("roles");
      toast("Role deleted");
    } catch (error) { toast(error.message); }
  }));
  $("#addCustomField")?.addEventListener("click", () => openCustomFieldModal());
  $("#goCustomFieldAdmin")?.addEventListener("click", () => {
    S.settingsTab = "ai";
    render();
  });
  if (S.settingsTab === "custom-fields" && S.auth.authenticated) {
    loadCustomFields().catch(error => toast(error.message));
  }
  bindProfileActions();
  $("#adminLoginForm")?.addEventListener("submit", async event => {
    event.preventDefault();
    const password = $("#adminPassword")?.value || "";
    const status = $("#loginStatus");
    if (status) status.textContent = "Signing in…";
    try {
      await api("/api/auth/login", {
        method: "POST",
        body: JSON.stringify({ password }),
      });
      S.auth = await api("/api/auth/status");
      S.aiSettings = await api("/api/ai/settings");
      S.aiDraft = null;
      S.aiStatus = "";
      render();
    } catch (error) {
      if (status) status.textContent = error.message;
    } finally {
      const input = $("#adminPassword");
      if (input) input.value = "";
    }
  });

  $("#aiProvider")?.addEventListener("change", event => {
    const next = S.aiSettings.providers.find(item => item.id === event.target.value);
    if (!next) return;
    S.aiDraft = {
      provider: next.id,
      model: next.model,
      base_url: next.base_url,
      api_key: "",
      clear_api_key: false,
    };
    S.aiStatus = "";
    render();
  });
  $("#saveAi")?.addEventListener("click", saveAiSettings);
  $("#testAi")?.addEventListener("click", testAiConnection);
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
  const provider = $("#aiProvider")?.value;
  const model = $("#aiModel")?.value || "";
  const base_url = $("#aiBaseUrl")?.value || "";
  const api_key = $("#aiApiKey")?.value || "";
  const clear_api_key = Boolean($("#aiClearKey")?.checked);
  const status = $("#aiStatus");
  const button = $("#saveAi");
  if (button) button.disabled = true;
  if (status) status.textContent = "Saving provider settings…";
  try {
    S.aiSettings = await api("/api/ai/settings", {
      method: "POST",
      body: JSON.stringify({ provider, model, base_url, api_key, clear_api_key }),
    });
    S.auth = { ...S.auth, authenticated: true };
    S.aiDraft = null;
    S.caseGenerator = provider === "local" ? "deterministic" : provider;
    S.aiStatus = "Provider settings saved. Test the connection before running analysis.";
    render();
  } catch (error) {
    S.aiStatus = error.message;
    if (status) status.textContent = error.message;
  } finally {
    if ($("#saveAi")) $("#saveAi").disabled = false;
  }
}

async function testAiConnection() {
  const status = $("#aiStatus");
  const test = $("#testAi");
  if (test) test.disabled = true;
  if (status) status.textContent = "Testing provider connection…";
  try {
    const result = await api("/api/ai/test", { method: "POST", body: JSON.stringify({}) });
    S.aiSettings = await api("/api/ai/settings");
    S.aiStatus = `Connected to ${result.provider} · ${result.model}`;
    if (status) status.textContent = S.aiStatus;
  } catch (error) {
    S.aiStatus = error.message;
    if (status) status.textContent = error.message;
  } finally {
    if ($("#testAi")) $("#testAi").disabled = false;
  }
}

/* ════════════════════════════════════════════════════════════════════
   CRUD OPERATIONS
════════════════════════════════════════════════════════════════════ */

/* ── Open project ─────────────────────────────────────────────────── */
async function openProject(id, view = "overview") {
  const project = S.projects.find(p => String(p.id) === String(id)) || null;
  if (String(project?.id || "") !== String(S.project?.id || "")) {
    S.assistedInput = "";
    S.assistedAnalysis = null;
    S.assistedCategories = ["Functional", "Non-Functional", "Sanity", "Smoke"];
  }
  S.project = project;
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

async function deleteBug(id) {
  const defect = S.bugs.find(item => String(item.id) === String(id));
  if (!defect || !confirm(`Delete ${defect.bug_key} — ${defect.title}?`)) return;
  try {
    await api(pp(`bugs/${id}`), { method: "DELETE" });
    await refresh();
    toast("Defect deleted");
  } catch (error) {
    toast(error.message);
  }
}

/* ── BRD report rendering ─────────────────────────────────────────── */
function brdReportMarkup(analysis) {
  const summary = analysis.brd_summary || {};
  const useCases = analysis.use_cases || [];
  const groups = analysis.functional_groups || [];
  const nfrs = analysis.non_functional_requirements || [];
  const constraints = analysis.scope_constraints || [];
  const traceability = analysis.traceability || [];
  const gaps = analysis.requirement_gaps || [];
  const requirements = analysis.requirements || [];
  const applicationFlow = analysis.application_flow || [];
  const scope = analysis.scope || { in_scope: [], out_of_scope: [], requires_clarification: [] };
  const analysisLabel = analysis.analysis_status || "NEEDS REVIEW";
  const analysisBadge = `<span class="badge ${analysisLabel === "AI SUGGESTION" ? "badge-amber" : "badge-muted"}">${esc(analysisLabel)}</span>`;
  const table = (headers, rows, emptyMessage) => `
    <div class="table-wrap"><table class="data-table">
      <thead><tr>${headers.map(header => `<th>${esc(header)}</th>`).join("")}</tr></thead>
      <tbody>${rows || `<tr><td colspan="${headers.length}" class="empty-row">${esc(emptyMessage)}</td></tr>`}</tbody>
    </table></div>`;
  const useCaseRows = useCases.map(item => `
    <tr><td>${esc(item.id)}</td><td>${esc(item.name)}</td><td>${esc(item.actor || "Not specified")}</td><td>${esc(item.goal || "Not specified")}</td><td>${esc(item.source_reference || "Not specified")}</td></tr>`).join("");
  const functional = groups.map((group, groupIndex) => `
    <section class="analysis-group">
      <h4><strong>FR-${groupIndex + 1}:</strong> ${esc(group.name)}</h4>
      ${table(
        ["ID", "Functional Requirement", "Source Use Case", "Scenario / Acceptance Criterion", "Source Reference", "Priority"],
        (group.requirements || []).map(item => `
          <tr><td>${esc(item.id)}</td><td>${esc(item.title ? `${item.title}: ` : "")}${esc(item.statement)}</td>
            <td>${esc(item.source_use_case || "Not specified")}</td>
            <td>${esc(item.source_step || "Not specified")}</td>
            <td>${esc(item.source_reference || "Not specified")}</td>
            <td>${esc(item.priority || "Not specified")}</td></tr>`).join(""),
        "No functional requirements identified.",
      )}
    </section>`).join("") || '<p class="analysis-empty">No functional requirements identified.</p>';
  const nfrRows = nfrs.map(item => `
    <tr><td>${esc(item.id)}</td><td>${esc(item.statement)}</td>
      <td>${esc(item.source_use_case || "Not specified")}</td>
      <td>${esc(item.source_step || "Not specified")}</td>
      <td>${esc(item.source_reference || "Not specified")}</td>
      <td>${esc(item.category)}</td></tr>`).join("");
  const constraintRows = constraints.map(item => `
    <tr><td>${esc(item.id)}</td><td>${esc(item.statement)}</td>
      <td>${esc(item.source || item.basis || "Not specified")}${item.basis === "Inferred" ? " (Inferred — requires confirmation)" : ""}</td></tr>`).join("");
  const traceRows = traceability.map(item => `
    <tr><td>${esc(item.use_case)}</td><td>${esc(item.scenario_step)}</td>
      <td>${esc((item.requirement_ids || []).join(", ") || "No requirement extracted")}</td></tr>`).join("");
  const gapRows = gaps.map(item => `
    <tr><td>${esc(item.id)}</td><td>${esc(item.missing_information)}</td>
      <td>${esc(item.related_use_case || "Not specified")}</td><td>${esc(item.impact)}</td></tr>`).join("");
  const scopeList = entries => (entries || []).length
    ? entries.map(item => `<li>${esc(item)}</li>`).join("")
    : '<li class="analysis-empty">Not specified in the document.</li>';
  const requirementRecords = requirements.map(item => `
    <details class="brd-preview-item">
      <summary><strong>${esc(item.requirement_key || "Suggestion")}: ${esc(item.title)}</strong>
        <span class="badge badge-amber">AI SUGGESTION</span>
        <span class="badge ${item.module_type === "Non-Functional" ? "badge-purple" : "badge-blue"}">${esc(item.requirement_type || item.module_type)}</span>
      </summary>
      <p>${esc(item.description || "Description not specified.")}</p>
      <p><strong>Acceptance criteria:</strong> ${esc(item.acceptance_criteria || "Not specified.")}</p>
      <p><strong>Dependencies:</strong> ${esc(item.dependencies || "Not specified.")}</p>
      <p><strong>Validation rules:</strong> ${esc(item.validation_rules || "Not specified.")}</p>
      <p><strong>Business rules:</strong> ${esc(item.business_rules || "Not specified.")}</p>
      <p><strong>Scope:</strong> ${esc(item.scope_classification || "Requires Clarification")}
        · <strong>Review:</strong> ${esc(item.ai_confidence_status || "Needs clarification")}</p>
      <p><strong>Source:</strong> ${esc(item.source_section || "Not specified")}
        · <strong>Actor:</strong> ${esc(item.actor || "Not specified")}</p>
      ${item.preconditions ? `<p><strong>Preconditions:</strong> ${esc(item.preconditions)}</p>` : ""}
      ${item.trigger ? `<p><strong>Trigger:</strong> ${esc(item.trigger)}</p>` : ""}
      ${item.main_flow ? `<p><strong>Main flow:</strong> ${esc(item.main_flow)}</p>` : ""}
      ${item.alternative_flow ? `<p><strong>Alternative flow:</strong> ${esc(item.alternative_flow)}</p>` : ""}
      ${item.exception_flow ? `<p><strong>Exception flow:</strong> ${esc(item.exception_flow)}</p>` : ""}
      ${item.related_requirements ? `<p><strong>Related requirements:</strong> ${esc(item.related_requirements)}</p>` : ""}
      <p><strong>Priority:</strong> ${esc(item.priority || "Not specified")}</p>
    </details>`).join("");
  const flowRows = applicationFlow.map((item, index) => `
    <tr><td>${index + 1}</td><td>${esc(item.actor || "Not specified")}</td>
      <td>${esc(item.action)}</td><td>${esc(item.system_response || "Not specified")}</td>
      <td>${esc(item.source_status)}</td></tr>`).join("");
  const steps = useCases.map(item => `
    ${(item.steps || []).map(step => `
      <li><strong>${esc(item.id)} ${esc(step.step)}:</strong> ${esc(step.summary)}</li>`).join("")}
    ${(item.acceptance_criteria || []).map((criterion, index) => `
      <li><strong>${esc(item.id)} Acceptance Criterion ${index + 1}:</strong> ${esc(criterion)}</li>`).join("")}
  `).join("");

  return `
    <h3>1. BRD Summary ${analysisBadge}</h3>
    ${table(["Item", "Details"], [
      ["BRD name", summary.brd_name || "Not specified"],
      ["Product", summary.product || "Not specified"],
      ["Platform", summary.platform || "Not specified"],
      ["Primary users", (summary.primary_users || []).join(", ") || "Not specified"],
      ["Use cases identified", summary.use_case_count ?? useCases.length],
    ].map(([label, value]) => `<tr><td>${esc(label)}</td><td>${esc(value)}</td></tr>`).join(""), "Not specified in the document.")}
    <p>${esc(analysis.summary || "")}</p>
    <h3>Requirement Records ${analysisBadge}</h3>
    ${requirementRecords || '<p class="analysis-empty">No requirement records generated.</p>'}
    <h3>2. Use Cases Identified ${analysisBadge}</h3>
    ${table(["Use Case ID", "Use Case", "Actor", "Goal", "Source Reference"], useCaseRows, "No use cases identified.")}
    ${steps ? `<h4>Scenarios and Acceptance Criteria</h4><ul>${steps}</ul>` : ""}
    <h3>3. Functional Requirements ${analysisBadge}</h3>
    ${functional}
    <h3>4. Non-Functional Requirements ${analysisBadge}</h3>
    ${table(["ID", "Non-Functional Requirement", "Source Use Case", "Scenario / Acceptance Criterion", "Source Reference", "Category"], nfrRows, "No source-supported quality requirements identified.")}
    <h3>5. Business Rules / Constraints (separate from Functional and Non-Functional Requirements) ${analysisBadge}</h3>
    ${table(["ID", "Rule or Constraint", "Source"], constraintRows, "No business rules or constraints identified.")}
    <h4>Scope context</h4>
    <div class="analysis-columns">
      <div><h4>In scope</h4><ul>${scopeList(scope.in_scope)}</ul></div>
      <div><h4>Out of scope</h4><ul>${scopeList(scope.out_of_scope)}</ul></div>
      <div><h4>Requires clarification</h4><ul>${scopeList(scope.requires_clarification)}</ul></div>
    </div>
    <h3>Application Flow ${analysisBadge}</h3>
    ${table(["Step", "Actor", "Action", "System response", "Source status"], flowRows, "No application flow was identified.")}
    <h3>6. Requirement Traceability Matrix ${analysisBadge}</h3>
    ${table(["Use Case", "Scenario Step", "Requirement IDs"], traceRows, "No traceability entries identified.")}
    <h3>7. Requirement Gaps ${analysisBadge}</h3>
    ${table(["Gap ID", "Missing Information", "Related Use Case / Requirement", "Testing Impact"], gapRows, "No requirement gaps identified.")}
    <h3>8. QA Readiness Notes <span class="badge badge-muted">NEEDS REVIEW</span></h3>
    <p>${useCases.length && traceability.length
      ? "Requirements are organized around identified use cases and mapped to scenario steps."
      : "Use-case detail or traceability is incomplete; review the reported gaps before test-case generation."}</p>`;
}

async function decideRequirementSuggestion(id, action) {
  try {
    const payload = { action };
    if (action === "edit") {
      const editor = $(`#suggestion-editor-${id}`);
      const current = S.requirementSuggestions.find(item => String(item.id) === String(id));
      if (!editor || !current) throw new Error("AI suggestion editor is unavailable.");
      payload.content = { ...current.content };
      $$(".suggestion-field", editor).forEach(field => {
        payload.content[field.dataset.field] = field.value;
      });
    }
    await api(pp(`requirements/suggestions/${id}`), {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
    await refresh();
    render();
    toast(action === "accept" ? "AI suggestion accepted and approved for test generation"
      : action === "reject" ? "AI suggestion rejected" : "AI suggestion edit saved");
  } catch (e) { toast(e.message); }
}

/* ── Import BRD ───────────────────────────────────────────────────── */
function confirmBrdRequirements(preview) {
  const analysis = preview.analysis;
  const requirements = preview.requirements.map((item, index) => `
    <article class="brd-preview-item">
      <div class="brd-preview-item-head">
        <strong>${esc(item.requirement_key || `Requirement ${index + 1}`)}</strong>
        <span class="badge badge-green">EXTRACTED</span>
      </div>
      <h3>${esc(item.title)}</h3>
      ${item.description ? `<p>${esc(item.description)}</p>` : ""}
      <p><strong>Requirement type:</strong> ${esc(item.requirement_type || item.module_type || "Functional Requirement")}
        · <strong>Scope:</strong> ${esc(item.scope_classification || "Requires Clarification")}
        · <strong>AI confidence:</strong> ${esc(item.ai_confidence_status || "Needs clarification")}</p>
      ${item.acceptance_criteria ? `<p><strong>Acceptance criteria:</strong> ${esc(item.acceptance_criteria)}</p>` : ""}
      ${item.business_rules ? `<p><strong>Business rules:</strong> ${esc(item.business_rules)}</p>` : ""}
      ${item.validation_rules ? `<p><strong>Validation:</strong> ${esc(item.validation_rules)}</p>` : ""}
      ${item.dependencies ? `<p><strong>Dependencies:</strong> ${esc(item.dependencies)}</p>` : ""}
      ${item.source_section ? `<p><strong>Source reference:</strong> ${esc(item.source_section)}</p>` : ""}
      ${item.source_evidence ? `<blockquote class="source-evidence">${esc(item.source_evidence)}</blockquote>` : ""}
      ${item.actor ? `<p><strong>Actor:</strong> ${esc(item.actor)}</p>` : ""}
      ${item.main_flow ? `<p><strong>Main flow:</strong> ${esc(item.main_flow)}</p>` : ""}
    </article>`).join("");
  const suggestions = (preview.suggestions || []).map(item => `
    <article class="brd-preview-item">
      <div class="brd-preview-item-head"><span class="badge badge-amber">AI SUGGESTION</span></div>
      <h3>${esc(item.content.title)}</h3>
      <p>${esc(item.content.description || "No description supplied.")}</p>
      <p><strong>Reason:</strong> ${esc(item.reason)}</p>
      <p><strong>Related extracted requirement:</strong> ${esc(item.related_requirement_key || "None identified")}
        · <strong>Source reference:</strong> ${esc(item.source_reference || "No exact source reference identified")}</p>
      ${item.source_evidence ? `<blockquote class="source-evidence">${esc(item.source_evidence)}</blockquote>` : ""}
    </article>`).join("");
  const functionalCount = preview.requirements.filter(item => item.module_type === "Functional").length;
  const nonFunctionalCount = preview.requirements.length - functionalCount;
  $("#saveModal").hidden = false;
  $("#modalEyebrow").textContent = "REVIEW BRD REQUIREMENTS";
  $("#modalTitle").textContent = `${preview.requirements.length} requirement${preview.requirements.length === 1 ? "" : "s"} found`;
  $("#modalFields").innerHTML = `
    <div class="field full">
      <div class="info-box">
        Rule-based extraction is the only source for confirmed requirements. AI proposals are saved separately for review and are not added to the confirmed list.
        <br><strong>${preview.requirements.length}</strong> EXTRACTED (${functionalCount} Functional · ${nonFunctionalCount} Non-Functional)
        · <strong>${(preview.suggestions || []).length}</strong> AI SUGGESTION(S)
        ${preview.analysis_warning ? `<br><span class="analysis-empty">${esc(preview.analysis_warning)}</span>` : ""}
      </div>
    </div>
    <div class="field full brd-preview-list">
      <section class="brd-preview-report">
        ${brdReportMarkup(analysis)}
      </section>
      <details class="brd-preview-requirements">
        <summary>Review the ${preview.requirements.length} extracted requirement record(s) that will be confirmed</summary>
        ${requirements || '<p class="analysis-empty">No source-supported requirement was identified by the deterministic extraction rules.</p>'}
      </details>
      <details class="brd-preview-requirements" ${suggestions ? "open" : ""}>
        <summary>Review ${preview.suggestions?.length || 0} separate AI suggestion(s)</summary>
        ${suggestions || '<p class="analysis-empty">No AI suggestions were generated.</p>'}
      </details>
    </div>`;
  $("#formError").textContent = "";
  $("#saveModal").textContent = "Confirm extracted requirements and save suggestions separately";
  $("#saveModal").onclick = e => {
    e.preventDefault();
    modal.close("confirm");
  };
  $("#cancelModal").onclick = () => modal.close();
  modalForm.onsubmit = e => e.preventDefault();
  modal.returnValue = "";
  return new Promise(resolve => {
    modal.addEventListener("close", () => resolve(modal.returnValue === "confirm"), { once: true });
    modal.showModal();
  });
}

async function showBrdAnalysis(documentId, filename) {
  try {
    const result = await api(pp(`requirements/documents/${documentId}/analysis`));
    S.brdAnalysis = { documentId, filename, analysis: result.analysis };
    navigate("brd");
    render();
  } catch (e) { toast(e.message); }
}

function importBrd(generator = "auto") {
  const picker = document.createElement("input");
  picker.type = "file";
  picker.accept = ".pdf,.docx,.csv,.txt,.md";
  picker.onchange = async () => {
    const file = picker.files[0]; if (!file) return;
    if (!/\.(pdf|docx|csv|txt|md)$/i.test(file.name)) { toast("Choose PDF, DOCX, CSV, TXT, or Markdown"); return; }
    const isBinaryDocument = /\.(pdf|docx)$/i.test(file.name);
    if (file.size > (isBinaryDocument ? 5 : 1) * 1024 * 1024) { toast(`File too large (max ${isBinaryDocument ? "5 MB" : "1 MB"})`); return; }
    const btn = $("#uploadBrdBtn") || $("#importBtn");
    if (btn) { btn.disabled = true; btn.textContent = "Analyzing BRD…"; }
    toast("Analyzing the BRD with the configured provider. Large documents may take a few minutes.");
    try {
      const payload = { filename: file.name, generator };
      if (isBinaryDocument) {
        const dataUrl = await new Promise((res, rej) => {
          const fr = new FileReader(); fr.onload = () => res(fr.result); fr.onerror = () => rej(new Error("Read failed")); fr.readAsDataURL(file);
        });
        payload.document_data = String(dataUrl).split(",", 2)[1];
      } else {
        payload.content = await file.text();
      }
      const previewResult = await api(pp("requirements/import"), {
        method: "POST",
        body: JSON.stringify(payload),
      });
      if (!previewResult.preview_required) throw new Error("BRD analysis preview was not returned.");
      const confirmed = await confirmBrdRequirements(previewResult.preview);
      if (!confirmed) {
        await api(pp(`requirements/previews/${previewResult.preview.preview_id}`), {
          method: "DELETE",
        });
        toast("BRD analysis cancelled; no requirements were saved.");
        return;
      }
      const r = await api(pp("requirements/import"), {
        method: "POST",
        body: JSON.stringify({ ...payload, preview_id: previewResult.preview.preview_id }),
      });
      await refresh();
      S.brdAnalysis = {
        documentId: r.document.id,
        filename: r.document.filename,
        analysis: r.analysis,
      };
      toast(`${r.count} EXTRACTED requirement(s) added (${r.functional_count} Functional, ${r.non_functional_count} Non-Functional); ${r.suggestions_added} AI suggestion(s) saved separately${r.skipped_duplicates ? `; ${r.skipped_duplicates} duplicate(s) skipped` : ""}${r.analysis_warning ? " · " + r.analysis_warning : ""}`);
      navigate("brd"); render();
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
  if (btn) { btn.disabled = true; btn.textContent = "Analyzing BRD…"; }
  toast("Analyzing the BRD with the configured provider. Large documents may take a few minutes.");
  try {
    const payload = {
      filename: "pasted-requirements.txt",
      content,
      generator: "auto",
    };
    const previewResult = await api(pp("requirements/import"), {
      method: "POST",
      body: JSON.stringify(payload),
    });
    if (!previewResult.preview_required) throw new Error("BRD analysis preview was not returned.");
    const confirmed = await confirmBrdRequirements(previewResult.preview);
    if (!confirmed) {
      await api(pp(`requirements/previews/${previewResult.preview.preview_id}`), {
        method: "DELETE",
      });
      toast("BRD analysis cancelled; no requirements were saved.");
      return;
    }
    const r = await api(pp("requirements/import"), {
      method: "POST",
      body: JSON.stringify({ ...payload, preview_id: previewResult.preview.preview_id }),
    });
    await refresh();
    S.brdAnalysis = {
      documentId: r.document.id,
      filename: r.document.filename,
      analysis: r.analysis,
    };
    toast(`${r.count} EXTRACTED requirement(s) added; ${r.suggestions_added} AI suggestion(s) saved separately${r.analysis_warning ? " · " + r.analysis_warning : ""}`);
    navigate("brd"); render();
  } catch (e) { toast(e.message); }
  finally { if (btn) { btn.disabled = false; btn.textContent = "Analyze with AI"; } }
}

/* ── Generate test cases ──────────────────────────────────────────── */
async function generateCases() {
  const btn = $("#genCasesBtn");
  if (btn) { btn.disabled = true; btn.textContent = S.caseGenerator === "google" ? "Generating with AI…" : "Generating…"; }
  try {
    const r = await api(pp("test-cases/generate"), { method: "POST", body: JSON.stringify({ generator: S.caseGenerator }) });
    await refresh();
    const coverage = r.coverage;
    const uncoveredMore = coverage?.uncovered_count > coverage?.uncovered_requirements?.length
      ? ` and ${coverage.uncovered_count - coverage.uncovered_requirements.length} more`
      : "";
    const coverageMessage = coverage
      ? ` Coverage: ${coverage.covered_requirements}/${coverage.reviewed_requirements} reviewed requirement(s).${coverage.uncovered_count ? ` Uncovered: ${coverage.uncovered_requirements.map(item => item.requirement_key).join(", ")}${uncoveredMore}.` : ""}`
      : "";
    if (r.count) {
      toast(`${r.count} case(s) generated; ${r.skipped_existing} skipped.${r.truncated ? " 300-case limit reached." : ""}${coverageMessage}`);
      navigate("cases"); render();
    } else {
      toast(`No new cases; ${r.skipped_existing} matching case(s) already exist.${coverageMessage}`);
    }
  } catch (e) { toast(e.message); }
  finally { if ($("#genCasesBtn")) { $("#genCasesBtn").disabled = false; $("#genCasesBtn").textContent = "✦ Generate test cases"; } }
}

async function generateAdHocCases() {
  const btn = $("#assistedGenerateBtn");
  const input = $("#assistedInput");
  if (input) S.assistedInput = input.value;
  const categories = $$('input[name="assistedCategory"]:checked').map(item => item.value);
  S.assistedCategories = categories;
  if (btn) { btn.disabled = true; btn.textContent = "Analyzing and generating…"; }
  try {
    const result = await api(pp("test-cases/generate"), {
      method: "POST",
      body: JSON.stringify({
        mode: "ai_assisted",
        source_text: S.assistedInput,
        categories,
        generator: S.aiSettings.provider,
      }),
    });
    S.assistedAnalysis = result.analysis;
    if (result.status === "needs_clarification") {
      await render();
      toast("More detail is needed. Review the clarification questions before retrying.");
      return;
    }
    await refresh();
    toast(result.count
      ? `${result.count} AI-assisted case(s) added; ${result.skipped_existing} duplicate(s) skipped. Review and edit before execution.`
      : `No new cases; ${result.skipped_existing} matching case(s) already exist.`);
  } catch (e) { toast(e.message); }
  finally {
    if ($("#assistedGenerateBtn")) {
      $("#assistedGenerateBtn").disabled = false;
      $("#assistedGenerateBtn").textContent = "✦ Analyze and generate";
    }
  }
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

/* ── Export report ────────────────────────────────────────────────── */
function exportReport() {
  if (!S.project) return;
  location.href = pp("report/csv");
}

function exportModuleReport(module) {
  if (!S.project || !["requirements", "test-cases", "bugs"].includes(module)) return;
  const query = new URLSearchParams();
  const search = $("#searchTable")?.value.trim();
  if (search) query.set("q", search);
  const queryString = query.toString();
  const suffix = queryString ? `?${queryString}` : "";
  location.href = pp(`report/${module}.csv${suffix}`);
}

async function showDefect(id) {
  const defect = S.bugs.find(item => String(item.id) === String(id));
  if (!defect || !S.project) return;
  try {
    const [fieldResponse, attachments] = await Promise.all([
      api(`/api/custom-fields?module=bugs&record_id=${encodeURIComponent(id)}&include_inactive=true`),
      api(`/api/projects/${S.project.id}/attachments?module=bugs&record_id=${encodeURIComponent(id)}`),
    ]);
    const linkedCase = S.cases.find(item => String(item.id) === String(defect.test_case_id));
    const details = [
      ["Defect ID", defect.bug_key],
      ["Title", defect.title],
      ["Description", defect.description],
      ["Status", defect.status],
      ["Severity", defect.severity],
      ["Priority", defect.priority],
      ["Assignee", defect.assigned_to],
      ["Linked test case", linkedCase
        ? `${linkedCase.case_key} · ${linkedCase.title || linkedCase.scenario}`
        : defect.test_case_id ? `Test case #${defect.test_case_id}` : "—"],
      ["Created", defect.created_at ? `${fmtDate(defect.created_at)} · ${defect.created_at}` : "—"],
      ["Last updated", defect.updated_at ? `${fmtDate(defect.updated_at)} · ${defect.updated_at}` : "—"],
    ];
    const detailHtml = details.map(([label, value]) => `
      <div class="field${label === "Description" ? " full" : ""}">
        <label>${esc(label)}</label>
        <div class="defect-detail-value">${pretty(value || "—")}</div>
      </div>`).join("");
    const customHtml = fieldResponse.fields.length
      ? `<div class="field full custom-fields-divider"><strong>Custom fields</strong></div>` +
        fieldResponse.fields.map(item => `
          <div class="field">
            <label>${esc(item.label)}${item.active ? "" : " · Inactive"}</label>
            <div class="defect-detail-value">${pretty(item.value || "—")}</div>
            ${item.description ? `<small class="custom-field-help">${esc(item.description)}</small>` : ""}
          </div>`).join("")
      : "";
    const attachmentHtml = `<div class="field full attachment-section">
      <label>Attachments</label>
      ${attachments.length ? attachments.map(item => `
        <div class="attachment-item">
          <a href="/api/projects/${S.project.id}/attachments/${item.id}/view" target="_blank" rel="noopener">${esc(item.filename)}</a>
          <span>${(item.file_size_bytes / 1024).toFixed(1)} KB</span>
          <a class="act-btn" href="/api/projects/${S.project.id}/attachments/${item.id}/download">Download</a>
        </div>`).join("") : '<div class="defect-detail-value">No attachments.</div>'}
    </div>`;
    $("#modalEyebrow").textContent = "DEFECT · READ ONLY";
    $("#modalTitle").textContent = `${defect.bug_key} · ${defect.title}`;
    $("#formError").textContent = "";
    $("#saveModal").hidden = true;
    $("#cancelModal").onclick = () => modal.close();
    $("#closeModal").onclick = () => modal.close();
    modalForm.onsubmit = null;
    $("#modalFields").innerHTML = detailHtml + customHtml + attachmentHtml;
    modal.showModal();
  } catch (error) {
    toast(`Could not load defect details: ${error.message}`);
  }
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

const ATTACHMENT_EXTENSIONS = new Set([
  "csv", "docx", "gif", "jpeg", "jpg", "json", "md", "pdf",
  "png", "txt", "webp", "xlsx", "zip",
]);
function customFieldMarkup(fields) {
  if (!fields.length) return "";
  return `<div class="field full custom-fields-divider"><strong>Custom fields</strong></div>` +
    fields.map(item => {
      const required = item.required ? "required" : "";
      const description = item.description
        ? `<small class="custom-field-help">${esc(item.description)}</small>` : "";
      let control;
      if (item.field_type === "dropdown") {
        control = `<select id="cf_${item.id}" name="cf_${item.id}" ${required}>
          <option value="" ${item.value ? "" : "selected"} ${item.required ? "disabled" : ""}>${item.required ? "Select an option" : "Optional"}</option>
          ${item.options.map(option => `<option value="${esc(option.value)}" ${String(option.value) === String(item.value) ? "selected" : ""}>${esc(option.label)}</option>`).join("")}
        </select>`;
      } else {
        const type = item.field_type === "number" ? "number" : "text";
        control = `<input id="cf_${item.id}" name="cf_${item.id}" type="${type}"
          ${type === "number" ? 'step="any"' : 'maxlength="1000"'}
          value="${esc(item.value)}" ${required}>`;
      }
      return `<div class="field"><label for="cf_${item.id}">${esc(item.label)}${item.required ? " *" : ""}</label>
        ${control}${description}</div>`;
    }).join("");
}

function attachmentSectionHtml(attachments) {
  return `<div class="field full attachment-section">
    <label for="f_attachments">Attachments (PDF, Office, ZIP, CSV, JSON, Markdown, text, or image · max 5 MB each)</label>
    <input id="f_attachments" name="attachments" type="file" multiple
      accept=".csv,.docx,.gif,.jpeg,.jpg,.json,.md,.pdf,.png,.txt,.webp,.xlsx,.zip">
    <div id="attachmentStatus" class="pane-sub" role="status"></div>
    <div id="attachmentList">${renderAttachmentItems(attachments, [], new Set())}</div>
    <button type="button" class="btn btn-ghost" id="cancelAttachmentUpload" hidden>Cancel upload</button>
  </div>`;
}

function renderAttachmentItems(existing, pending, removed) {
  const existingHtml = existing.map(item => `
    <div class="attachment-item ${removed.has(String(item.id)) ? "attachment-removed" : ""}">
      <a href="/api/projects/${item.project_id}/attachments/${item.id}/view" target="_blank" rel="noopener">${esc(item.filename)}</a>
      <span>${(item.file_size_bytes / 1024).toFixed(1)} KB</span>
      <a class="act-btn" href="/api/projects/${item.project_id}/attachments/${item.id}/download">Download</a>
      <button type="button" class="act-btn toggle-existing-attachment" data-id="${item.id}">${removed.has(String(item.id)) ? "Undo" : "Remove"}</button>
    </div>`).join("");
  const pendingHtml = pending.map((file, index) => `
    <div class="attachment-item">
      <span>${esc(file.name)} <small>Pending · ${(file.size / 1024).toFixed(1)} KB</small></span>
      <button type="button" class="act-btn remove-pending-attachment" data-index="${index}">Remove</button>
    </div>`).join("");
  return existingHtml + pendingHtml || '<div class="pane-sub">No attachments.</div>';
}

async function saveRecordAttachments(
  projectId, moduleKey, recordId, pending, existing, removed, controller
) {
  if (!pending.length && !removed.size) return;
  const status = $("#attachmentStatus");
  const cancel = $("#cancelAttachmentUpload");
  const save = $("#saveModal");
  if (save) save.disabled = true;
  if (cancel) cancel.hidden = false;
  try {
    for (let index = 0; index < pending.length; index += 1) {
      const file = pending[index];
      if (status) status.textContent = `Uploading ${index + 1} of ${pending.length}: ${file.name}`;
      await api(
        `/api/projects/${projectId}/attachments?module=${encodeURIComponent(moduleKey)}`
          + `&record_id=${encodeURIComponent(recordId)}&filename=${encodeURIComponent(file.name)}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/octet-stream" },
          body: file,
          signal: controller.signal,
        }
      );
    }
    for (const attachmentId of removed) {
      if (status) status.textContent = "Removing attachment…";
      await api(`/api/projects/${projectId}/attachments/${attachmentId}`, {
        method: "DELETE",
        signal: controller.signal,
      });
    }
    if (status) status.textContent = "Attachments saved.";
  } finally {
    if (save) save.disabled = false;
    if (cancel) cancel.hidden = true;
  }
}

async function openModal(kind, id = null, linkCase = null) {
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
  const moduleKey = {
    project: "projects",
    requirement: "requirements",
    case: "test-cases",
    bug: "bugs",
  }[kind];
  let customFields = [];
  let existingAttachments = [];
  const pendingAttachments = [];
  const removedAttachments = new Set();
  const attachmentProjectId = kind === "project" ? (isEdit ? id : null) : S.project?.id;
  if (moduleKey) {
    try {
      const customQuery = new URLSearchParams({ module: moduleKey });
      if (isEdit) customQuery.set("record_id", id);
      const fieldResponse = await api(`/api/custom-fields?${customQuery}`);
      customFields = fieldResponse.fields;
      if (isEdit && attachmentProjectId) {
        const attachmentQuery = new URLSearchParams({
          module: moduleKey,
          record_id: String(id),
        });
        existingAttachments = await api(
          `/api/projects/${attachmentProjectId}/attachments?${attachmentQuery}`
        );
        existingAttachments.forEach(item => { item.project_id = attachmentProjectId; });
      }
    } catch (error) {
      toast(`Could not load form configuration: ${error.message}`);
      return;
    }
  }

  $("#saveModal").hidden = false;
  $("#saveModal").onclick = null;
  $("#cancelModal").onclick = () => modal.close();
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
      selectField("requirement_type", "Requirement type", ["Use Case", "User Story", "Feature", "Functional Requirement", "Non-Functional Requirement"], data?.requirement_type || "Functional Requirement") +
      selectField("scope_classification", "Scope classification", ["In Scope", "Out of Scope", "Requires Clarification"], data?.scope_classification || "Requires Clarification") +
      selectField("ai_confidence_status", "AI confidence / review", ["Supported", "Needs clarification"], data?.ai_confidence_status || "Needs clarification") +
      field("source_section", "Source reference", data?.source_section || "", "text", false, "Source heading, feature ID, or excerpt") +
      field("actor", "Actor / user role", data?.actor || "", "text", false, "If specified in the source") +
      field("preconditions", "Preconditions", data?.preconditions || "", "textarea", false, "If specified") +
      field("trigger", "Trigger", data?.trigger || "", "textarea", false, "If specified") +
      field("main_flow", "Main flow", data?.main_flow || "", "textarea", false, "If specified") +
      field("alternative_flow", "Alternative flow", data?.alternative_flow || "", "textarea", false, "If specified") +
      field("exception_flow", "Exception flow", data?.exception_flow || "", "textarea", false, "If specified") +
      field("related_requirements", "Related requirements", data?.related_requirements || "", "textarea", false, "Related or parent requirement references") +
      selectField("priority", "Priority", ["High", "Medium", "Low", "Not specified"], data?.priority || "Medium") +
      selectField("module_type", "Type", ["Functional", "Non-Functional"], data?.module_type || "Functional") +
      selectField("module_category", "Category", ["Functional", "Performance", "Security", "Usability", "Compatibility", "Reliability", "Other"], data?.module_category || "Functional") +
      selectField("review_status", "Review status", ["Needs review", "Reviewed", "Rejected"], data?.review_status || "Needs review");
  }
  if (kind === "case") {
    fields =
      field("title", "Test case title", data?.title || data?.scenario || "", "text", true, "e.g. Submit valid registration details", true) +
      field("scenario", "Scenario", data?.scenario || "", "text", true, "e.g. Login with valid credentials", true) +
      selectField("requirement_id", "Requirement", reqOpts, data?.requirement_id || "") +
      field("use_case_id", "Use Case ID", data?.use_case_id || "", "text", false, "e.g. UC-1") +
      selectField("test_type", "Type", ["Functional","Negative","Boundary","Validation","Permission","API","Integration","Performance","Security","Usability","Compatibility","Reliability","Non-Functional"], data?.test_type || "Functional") +
      selectField("test_category", "Test category", ["Functional","Non-Functional","Sanity","Smoke"], data?.test_category || "Functional") +
      selectField("classification", "Classification", [
        { value: "", label: "Not classified" }, "Positive", "Negative",
      ], data?.classification || "") +
      selectField("priority", "Priority", ["Critical","High","Medium","Low","Not specified"], data?.priority || "Not specified") +
      field("description", "Description", data?.description || "", "textarea", true, "What this test verifies") +
      field("preconditions", "Preconditions", data?.preconditions || "", "textarea", false, "Required setup only") +
      field("test_data", "Test data", data?.test_data || "", "textarea", false, "Values or descriptive placeholders") +
      field("steps", "Steps", data?.steps || "", "textarea", true, "Steps, one per line") +
      field("expected_result", "Expected result", data?.expected_result || "", "textarea", true, "Observable outcome") +
      field("assumptions", "Assumptions / dependencies", data?.assumptions || "", "textarea", true, "Keep unconfirmed assumptions explicitly labeled") +
      field("source_reference", "Source statement / traceability", data?.source_reference || "", "textarea", true, "Input statement this case is based on") +
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
  if (moduleKey) {
    fields += customFieldMarkup(customFields);
    fields += attachmentSectionHtml(existingAttachments);
  }

  $("#modalFields").innerHTML = fields;
  const attachmentList = $("#attachmentList");
  const attachmentInput = $("#f_attachments");
  const redrawAttachments = () => {
    if (!attachmentList) return;
    attachmentList.innerHTML = renderAttachmentItems(
      existingAttachments, pendingAttachments, removedAttachments
    );
  };
  attachmentInput?.addEventListener("change", () => {
    const invalid = [];
    for (const file of attachmentInput.files || []) {
      const extension = file.name.split(".").pop().toLowerCase();
      if (!ATTACHMENT_EXTENSIONS.has(extension)) {
        invalid.push(`${file.name}: unsupported file type`);
        continue;
      }
      if (!file.size || file.size > 5 * 1024 * 1024) {
        invalid.push(`${file.name}: file must be between 1 byte and 5 MB`);
        continue;
      }
      const duplicate = [...existingAttachments, ...pendingAttachments].some(item =>
        String(item.name || item.filename).toLowerCase() === file.name.toLowerCase()
          && (item.size ?? item.file_size_bytes) === file.size
          && !removedAttachments.has(String(item.id))
      );
      if (duplicate) {
        invalid.push(`${file.name}: duplicate file`);
        continue;
      }
      pendingAttachments.push(file);
    }
    attachmentInput.value = "";
    $("#formError").textContent = invalid.join("; ");
    redrawAttachments();
  });
  attachmentList?.addEventListener("click", event => {
    const removePending = event.target.closest(".remove-pending-attachment");
    if (removePending) {
      pendingAttachments.splice(Number(removePending.dataset.index), 1);
      redrawAttachments();
      return;
    }
    const toggleExisting = event.target.closest(".toggle-existing-attachment");
    if (toggleExisting) {
      const attachmentId = String(toggleExisting.dataset.id);
      if (removedAttachments.has(attachmentId)) removedAttachments.delete(attachmentId);
      else removedAttachments.add(attachmentId);
      redrawAttachments();
    }
  });
  let attachmentUploadController = null;
  $("#cancelAttachmentUpload")?.addEventListener("click", () =>
    attachmentUploadController?.abort());
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
    payload.custom_fields = Object.fromEntries(
      customFields.map(item => [String(item.id), $(`#cf_${item.id}`)?.value || ""])
    );
    delete payload.attachments;
    for (const key of Object.keys(payload)) {
      if (key.startsWith("cf_")) delete payload[key];
    }
    try {
      if (kind === "project") {
        const proj = isEdit
          ? await api(`/api/projects/${id}`, { method: "PATCH", body: JSON.stringify(payload) })
          : await api("/api/projects", { method: "POST", body: JSON.stringify(payload) });
        try {
          await saveRecordAttachments(
            proj.id, "projects", proj.id, pendingAttachments,
            existingAttachments, removedAttachments,
            attachmentUploadController = new AbortController()
          );
        } catch (error) {
          modal.close();
          await loadProjects(proj.id);
          toast(`Project saved; attachment update failed: ${error.message}`);
          return;
        }
        modal.close();
        if (!isEdit) S.view = "overview";
        await loadProjects(isEdit ? S.project?.id : proj.id);
        toast(isEdit ? "Project updated" : "Project created");
        return;
      }
      const suffix = { requirement: "requirements", case: "test-cases", bug: "bugs" }[kind];
      if (kind === "case") {
        let caseId = id;
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
          caseId = created.id;
          if (status !== "Not Executed" || actual_result) {
            await api(pp(`executions/${created.id}`), { method: "POST", body: JSON.stringify({ status, actual_result, executed_by: S.project.qa_owner }) });
          }
        }
        try {
          await saveRecordAttachments(
            S.project.id, "test-cases", caseId,
            pendingAttachments, existingAttachments, removedAttachments,
            attachmentUploadController = new AbortController()
          );
        } catch (error) {
          modal.close(); await refresh();
          toast(`Test case saved; attachment update failed: ${error.message}`);
          return;
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
      const saved = isEdit
        ? await api(pp(`${suffix}/${id}`), { method: "PATCH", body: JSON.stringify(payload) })
        : await api(pp(suffix), { method: "POST", body: JSON.stringify(payload) });
      try {
        await saveRecordAttachments(
          S.project.id, moduleKey, saved.id, pendingAttachments,
          existingAttachments, removedAttachments,
          attachmentUploadController = new AbortController()
        );
      } catch (error) {
        modal.close(); await refresh();
        toast(`${kind === "bug" ? "Defect" : "Requirement"} saved; attachment update failed: ${error.message}`);
        return;
      }
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
  const project = S.projects.find(p => String(p.id) === e.target.value) || null;
  if (String(project?.id || "") !== String(S.project?.id || "")) {
    S.assistedInput = "";
    S.assistedAnalysis = null;
    S.assistedCategories = ["Functional", "Non-Functional", "Sanity", "Smoke"];
  }
  S.project = project;
  S.brdAnalysis = null;
  S.view = "overview"; S.viewHistory = [];
  if (S.project) await refresh();
  else { S.requirements=[]; S.requirementSuggestions=[]; S.requirementDocs=[]; S.cases=[]; S.bugs=[]; S.apiDocs=[]; S.applications=[]; render(); }
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
bindProfileMenu();
window.addEventListener("popstate", handleRouteChange);
handleRouteChange();
