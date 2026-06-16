/* UNC Research Intelligence — client-side graph explorer.
   Loads graph.json from the same origin; runs matching entirely in the browser.
   API_BASE can optionally point to the Vercel backend for /match calls. */

const API_BASE = "https://institution-intelligence-graph.vercel.app";
const GRAPH_URL = "graph.json";

const STOPWORDS = new Set("the a an and or of in to for with on at by from is are was inc llc corp co ltd company group holdings international plc study research".split(" "));
const EDGE_LABEL = { grant:"Grant", paper:"Paper", trial:"Clinical trial", contract:"Contract", patent:"Patent" };

let GRAPH = null;
let UNIT_BY_ID = {};
let COMPANY_INDEX = [];
let API_OK = false;       // is the live backend reachable?
let SEARCH_SEQ = 0;       // guards against out-of-order async results

// ── helpers ─────────────────────────────────────────────────────────────────

const $ = (s) => document.querySelector(s);
const esc = (s) => (s == null ? "" : String(s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])));
const fmtUSD = (n) => { if (!n) return "—"; if (n>=1e9) return "$"+(n/1e9).toFixed(1)+"B"; if (n>=1e6) return "$"+(n/1e6).toFixed(1)+"M"; if (n>=1e3) return "$"+(n/1e3).toFixed(0)+"K"; return "$"+n; };

function normName(s) {
  return (s||"").toLowerCase()
    .replace(/\b(inc|llc|corp|co|ltd|plc|lp|incorporated|corporation|limited|company)\b\.?/g," ")
    .replace(/[^\w\s&]/g," ").replace(/\s+/g," ").trim();
}
function tokenize(s) {
  return new Set((s||"").toLowerCase().replace(/[^\w\s]/g," ").split(/\s+/)
    .filter(t => t.length >= 3 && !STOPWORDS.has(t)));
}

// ── load ─────────────────────────────────────────────────────────────────────

async function load() {
  try {
    const res = await fetch(GRAPH_URL, { cache: "no-cache" });
    if (!res.ok) throw new Error("HTTP " + res.status);
    GRAPH = await res.json();
  } catch(e) {
    console.error("Failed to load graph.json:", e);
    $("#nav-stats").innerHTML = `<span class="nav-stat" style="color:#c00">graph.json not found — run the build &amp; export</span>`;
    return;
  }
  UNIT_BY_ID = Object.fromEntries(GRAPH.units.map(u => [u.id, u]));
  COMPANY_INDEX = GRAPH.companies.map(c => ({ norm: normName(c.name), company: c }));

  renderNavStats();
  renderUnitGrid();
  renderCompanyChips();
  wireSearch();
  $("#built-at").textContent = GRAPH.meta.built_at || "—";
  const total = GRAPH.units.filter(u => u.id !== "unc:root").length;
  $("#unit-count").textContent = `${total} units mapped · anchored on ROR ${GRAPH.meta.unc_ror || "0130frc33"}`;
  checkApiHealth();
}

// ── backend connectivity ───────────────────────────────────────────────────

async function checkApiHealth() {
  try {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), 4000);
    const res = await fetch(`${API_BASE}/health`, { signal: ctrl.signal });
    clearTimeout(t);
    API_OK = res.ok;
  } catch { API_OK = false; }
  renderApiStatus();
}

function renderApiStatus() {
  const el = $("#api-status");
  if (!el) return;
  el.className = "api-status " + (API_OK ? "live" : "local");
  el.title = API_OK
    ? "Connected to the live research-graph API"
    : "Live API unreachable — running matcher locally";
  el.innerHTML = `<span class="api-dot"></span>${API_OK ? "Live API" : "Local mode"}`;
}

// Query the deployed backend; returns {company, topical_matches} or null on failure.
async function fetchBackendMatch(query) {
  try {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), 3500);
    const res = await fetch(`${API_BASE}/match/${encodeURIComponent(query)}`, { signal: ctrl.signal });
    clearTimeout(t);
    if (!res.ok) return null;
    return await res.json();
  } catch { return null; }
}

// Local, in-browser matching — always available as an instant fallback.
function localMatch(query) {
  return { company: matchCompany(query), topical: matchTopical(query) };
}

// ── nav stats ─────────────────────────────────────────────────────────────────

function renderNavStats() {
  const c = GRAPH.meta.counts || {};
  const stats = [
    [GRAPH.meta.n_companies, "companies"],
    [c.edges, "connections"],
    [c.faculty, "faculty"],
  ].filter(([v]) => v != null);
  $("#nav-stats").innerHTML = stats.map(([v,l]) =>
    `<span class="nav-stat"><b>${Number(v).toLocaleString()}</b> ${l}</span>`
  ).join("");
}

// ── search ────────────────────────────────────────────────────────────────────

function wireSearch() {
  const input = $("#q");
  const clearBtn = $("#search-clear");
  let timer;

  input.addEventListener("input", () => {
    clearBtn.hidden = !input.value;
    clearTimeout(timer);
    timer = setTimeout(() => runSearch(input.value), 140);
  });
  clearBtn.addEventListener("click", () => {
    input.value = "";
    clearBtn.hidden = true;
    $("#results").hidden = true;
    input.focus();
  });
  document.querySelectorAll(".pill").forEach(b =>
    b.addEventListener("click", () => {
      input.value = b.dataset.q;
      clearBtn.hidden = false;
      runSearch(b.dataset.q);
      input.focus();
    })
  );
}

function runSearch(query) {
  query = (query || "").trim();
  const results = $("#results");
  if (!query) { results.hidden = true; return; }
  results.hidden = false;

  const seq = ++SEARCH_SEQ;

  // 1) Render local matches instantly — snappy, always works.
  const local = localMatch(query);
  renderResults(query, local.company, local.topical);

  // 2) Refresh from the live backend in the background (authoritative,
  //    same precomputed graph → identical data, so no visible flicker).
  if (API_OK) {
    fetchBackendMatch(query).then(backend => {
      if (seq !== SEARCH_SEQ || !backend) return;   // superseded or unavailable
      const company = backend.company || null;
      const topical = (backend.topical_matches || [])
        .map(m => ({
          unit: UNIT_BY_ID[m.unit_id] || { name: m.unit_name, id: m.unit_id, keywords: [], footprint: m.footprint || {} },
          score: m.score,
          hits: m.hits || [],
        }))
        .filter(x => x.unit);
      renderResults(query, company, topical);
    }).catch(() => { /* local result stands */ });
  }
}

function renderResults(query, company, topical) {
  const evPanel  = $("#evidence-panel");
  const topPanel = $("#topical-panel");
  const noRes    = $("#no-results");

  evPanel.hidden  = !company;
  topPanel.hidden = !topical.length;
  noRes.hidden    = !!(company || topical.length);

  if (company) evPanel.innerHTML = renderEvidence(company);
  if (topical.length) topPanel.innerHTML = renderTopical(query, topical);

  if (!company && !topical.length) {
    $("#no-results-msg").textContent = `No partnership records or topical matches for "${query}". Try a topic like "oncology" or a known partner like "Pfizer".`;
  }
}

function matchCompany(query) {
  const q = normName(query);
  if (q.length < 2) return null;
  let exact = COMPANY_INDEX.find(c => c.norm === q);
  if (exact) return exact.company;
  const subs = COMPANY_INDEX
    .filter(c => c.norm.includes(q) || q.includes(c.norm))
    .sort((a,b) => b.company.total_edges - a.company.total_edges);
  return subs.length ? subs[0].company : null;
}

function matchTopical(query) {
  const q = tokenize(query);
  if (!q.size) return [];
  const out = [];
  for (const u of GRAPH.units) {
    const kw = u.keywords || [];
    if (!kw.length) continue;
    const kwSet = new Set(kw);
    const hits = [...q].filter(t => kwSet.has(t));
    if (!hits.length) continue;
    const score = hits.length / Math.sqrt(q.size * kw.length);
    out.push({ unit: u, score, hits });
  }
  return out.sort((a,b) => b.score - a.score).slice(0, 6);
}

// ── render: evidence ─────────────────────────────────────────────────────────

function renderEvidence(co) {
  const cikHtml = co.cik
    ? `<a href="https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=${esc(co.cik)}&type=10-K" target="_blank" rel="noopener">SEC CIK ${esc(co.cik)} ↗</a>`
    : "not in SEC filer registry";
  const rows = (co.units || []).map(u => renderUnitEvRow(u)).join("");
  return `
    <div class="result-card">
      <div class="rc-header">
        <div>
          <div class="rc-company-name">${esc(co.name)}</div>
          <div class="rc-meta">
            ${co.total_edges} public record${co.total_edges===1?"":"s"} across ${(co.units||[]).length} UNC unit${(co.units||[]).length===1?"":"s"} · ${cikHtml}
          </div>
        </div>
        <span class="badge ${co.confidence}">${co.confidence}</span>
      </div>
      <div class="rc-body">${rows}</div>
    </div>`;
}

function renderUnitEvRow(u) {
  const unit = UNIT_BY_ID[u.unit_id] || { name: u.unit_id };
  const pct = Math.min(100, (u.score / 20) * 100);
  const counts = Object.entries(u.counts || {}).map(([t,n]) =>
    `<span class="ev-count">${n} ${EDGE_LABEL[t]||t}${n>1?"s":""}</span>`).join("");
  const samples = (u.samples || []).slice(0, 3).map(s => `
    <li>
      <span class="ev-type-tag">${esc(s.type)}</span>
      <a class="ev-link" href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.title || s.url)}</a>
      ${s.date ? `<span class="ev-date">· ${esc(s.date)}</span>` : ""}
    </li>`).join("");
  return `
    <div class="unit-ev">
      <div class="unit-ev-top">
        <span class="unit-ev-name">${esc(unit.name)}</span>
        <div class="score-track"><div class="score-fill" style="width:${pct}%"></div></div>
        <span class="score-val">${u.score.toFixed(0)}</span>
        <span class="badge ${u.confidence}">${u.confidence}</span>
      </div>
      <div class="ev-counts">${counts}</div>
      <ul class="ev-samples">${samples}</ul>
    </div>`;
}

// ── render: topical ──────────────────────────────────────────────────────────

function renderTopical(query, items) {
  const rows = items.map(({ unit, score, hits }) => {
    const hitSet = new Set(hits);
    const kws = (unit.keywords || []).slice(0, 14).map(k =>
      `<span class="kw ${hitSet.has(k)?"hit":""}">${esc(k)}</span>`).join("");
    const fp = unit.footprint || {};
    return `
      <div class="tunit-row">
        <div class="tunit-top">
          <span class="tunit-name">${esc(unit.name)}</span>
          <span class="badge topical">${hits.length} keyword match${hits.length>1?"es":""}</span>
          <div class="score-track"><div class="score-fill" style="width:${Math.min(100,score*140)}%"></div></div>
        </div>
        <div class="tunit-fp">${fp.grant||0} grants · ${fp.paper||0} papers · ${fp.trial||0} trials · ${fmtUSD(fp.total_usd)} federal funding</div>
        <div class="kw-row">${kws}</div>
      </div>`;
  }).join("");
  return `
    <div class="topical-card">
      <div class="tc-header">
        <div class="tc-title">Topical alignment</div>
        <div class="tc-sub">UNC units whose research keywords overlap your query</div>
      </div>
      <div class="tc-body">${rows}</div>
    </div>`;
}

// ── unit grid ────────────────────────────────────────────────────────────────

function renderUnitGrid() {
  const grid = $("#unit-grid");
  const units = GRAPH.units
    .filter(u => u.id !== "unc:root")
    .sort((a,b) => (b.footprint.total||0) - (a.footprint.total||0));

  grid.innerHTML = units.map(u => {
    const fp = u.footprint || {};
    const has = (fp.total || 0) > 0;
    const kwText = (u.keywords||[]).slice(0,5).map(esc).join(" · ") ||
      (has ? "Linked via trials &amp; awards" : "No indexed records yet");
    return `
      <button class="unit-card${has?"":" empty"}" data-uid="${esc(u.id)}">
        <div class="uc-name">${esc(u.name)}</div>
        <div class="uc-nums">
          <div class="uc-num"><b>${fp.total||0}</b><span>records</span></div>
          <div class="uc-num"><b>${(u.top_companies||[]).length}</b><span>partners</span></div>
          <div class="uc-num"><b>${fmtUSD(fp.total_usd)}</b><span>funding</span></div>
        </div>
        <div class="uc-kw">${kwText}</div>
      </button>`;
  }).join("");

  grid.querySelectorAll(".unit-card:not(.empty)").forEach(card =>
    card.addEventListener("click", () => openUnit(UNIT_BY_ID[card.dataset.uid]))
  );
}

// ── company chips ─────────────────────────────────────────────────────────────

function renderCompanyChips() {
  const box = $("#company-chips");
  const cos = [...GRAPH.companies].sort((a,b) => b.total_edges - a.total_edges);
  if (!cos.length) { box.innerHTML = `<span style="color:var(--muted);font-size:.9rem">No company links in this build.</span>`; return; }
  box.innerHTML = cos.map(c => `
    <button class="co-chip" data-co="${esc(c.name)}">
      <span class="co-dot ${c.confidence}"></span>
      ${esc(c.name)}
      <span class="co-count">${c.total_edges}</span>
    </button>`).join("");
  box.querySelectorAll(".co-chip").forEach(btn =>
    btn.addEventListener("click", () => {
      $("#q").value = btn.dataset.co;
      $("#search-clear").hidden = false;
      runSearch(btn.dataset.co);
      window.scrollTo({ top: 0, behavior: "smooth" });
    })
  );
}

// ── unit modal ────────────────────────────────────────────────────────────────

function openUnit(u) {
  if (!u) return;
  const fp = u.footprint || {};
  const partners = (u.top_companies||[]).map(c =>
    `<button class="co-chip" data-co="${esc(c.name)}"><span class="co-dot ${c.confidence}"></span>${esc(c.name)} <span class="co-count">${c.count}</span></button>`
  ).join("") || `<span style="color:var(--muted);font-size:.85rem">No industry partners on record.</span>`;
  const faculty = (u.top_faculty||[]).slice(0,8).map(f =>
    `<li>${esc(f.name)}<span class="fac-count">${f.count} record${f.count>1?"s":""}</span></li>`
  ).join("") || `<li style="color:var(--muted)">No faculty indexed.</li>`;
  const kw = (u.keywords||[]).slice(0,24).map(k => `<span class="kw">${esc(k)}</span>`).join("") ||
    `<span style="color:var(--muted);font-size:.85rem">No topic profile.</span>`;

  $("#modal-body").innerHTML = `
    <h2 class="modal-unit-name">${esc(u.name)}</h2>
    <p class="modal-unit-sub">${esc(u.short_name||"")} · UNC–Chapel Hill</p>
    <div class="modal-stats">
      <div class="mstat"><div class="mstat-val">${fp.grant||0}</div><div class="mstat-lbl">Grants</div></div>
      <div class="mstat"><div class="mstat-val">${fp.paper||0}</div><div class="mstat-lbl">Papers</div></div>
      <div class="mstat"><div class="mstat-val">${fp.trial||0}</div><div class="mstat-lbl">Trials</div></div>
      <div class="mstat"><div class="mstat-val">${fp.contract||0}</div><div class="mstat-lbl">Contracts</div></div>
      <div class="mstat"><div class="mstat-val">${fmtUSD(fp.total_usd)}</div><div class="mstat-lbl">Federal funding</div></div>
    </div>
    <div class="modal-section">
      <div class="modal-section-title">Industry partners</div>
      <div class="tag-list">${partners}</div>
    </div>
    <div class="modal-section">
      <div class="modal-section-title">Most active faculty</div>
      <ul class="fac-list">${faculty}</ul>
    </div>
    <div class="modal-section">
      <div class="modal-section-title">Research keywords</div>
      <div class="tag-list">${kw}</div>
    </div>`;

  $("#modal-body").querySelectorAll("[data-co]").forEach(btn =>
    btn.addEventListener("click", () => {
      closeModal();
      $("#q").value = btn.dataset.co;
      $("#search-clear").hidden = false;
      runSearch(btn.dataset.co);
      window.scrollTo({ top: 0, behavior: "smooth" });
    })
  );
  $("#unit-modal").hidden = false;
  document.body.style.overflow = "hidden";
}

function closeModal() {
  $("#unit-modal").hidden = true;
  document.body.style.overflow = "";
}

$("#modal-close").addEventListener("click", closeModal);
$("#unit-modal").addEventListener("click", e => { if (e.target.id === "unit-modal") closeModal(); });
document.addEventListener("keydown", e => { if (e.key === "Escape") closeModal(); });

// ── nav scroll effect ─────────────────────────────────────────────────────────

window.addEventListener("scroll", () => {
  const nav = $("#nav");
  if (window.scrollY > 10) nav.style.borderBottomColor = "var(--border-mid)";
  else nav.style.borderBottomColor = "var(--border)";
}, { passive: true });

load();
