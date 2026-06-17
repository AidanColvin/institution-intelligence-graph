/* UNC Research Intelligence — client-side graph explorer.
   Loads graph.json from the same origin; runs matching entirely in the browser.
   API_BASE can optionally point to the Vercel backend for /match calls. */

// Same-origin: the frontend and the Python API are served from one Vercel
// deployment, so API calls are relative ("/health", "/match/…"). No CORS,
// no hardcoded host — works wherever it's deployed.
const API_BASE = "";
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

// Honest coverage/freshness helpers — every label traces to real graph data.
const SOURCE_LABEL = { grant:"NIH/NSF grants", trial:"ClinicalTrials.gov", paper:"Crossref papers", contract:"federal contracts", patent:"patents" };
function freshnessLabel() {
  const d = GRAPH && GRAPH.meta && GRAPH.meta.built_at;
  if (!d) return "";
  try { return new Date(d).toLocaleDateString(undefined, { year:"numeric", month:"short", day:"numeric" }); }
  catch { return d; }
}
function sourceTypesOf(co) {
  const set = new Set();
  (co.units || []).forEach(u => Object.keys(u.counts || {}).forEach(t => set.add(t)));
  return [...set];
}

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
    GRAPH = null;
  }

  // Distinct third failure state: the graph itself never loaded (missing/stale
  // deploy). Keep the search box interactive, flag it so runSearch/renderResults
  // show a dedicated "not built" message, and surface it now instead of leaving
  // a blank panel.
  if (!GRAPH || !GRAPH.units) {
    window.__GRAPH_LOAD_FAILED = true;
    $("#nav-stats").innerHTML = `<span class="nav-stat" style="color:#c00">graph.json not built — run the build &amp; export</span>`;
    wireSearch();
    const results = $("#results");
    if (results) { results.hidden = false; renderResults("", null, []); }
    return;
  }

  UNIT_BY_ID = Object.fromEntries(GRAPH.units.map(u => [u.id, u]));
  COMPANY_INDEX = GRAPH.companies.map(c => ({ norm: normName(c.name), company: c }));

  renderNavStats();
  renderUnitGrid();
  renderCompanyChips();
  wireSearch();
  buildNetwork3D();
  $("#built-at").textContent = GRAPH.meta.built_at || "—";
  const total = GRAPH.units.filter(u => u.id !== "unc:root").length;
  $("#unit-count").textContent = `${total} units mapped · anchored on ROR ${GRAPH.meta.unc_ror || "0130frc33"}`;
  checkApiHealth();
}

// ── 3D research network ───────────────────────────────────────────────────────

let NETGRAPH = null;
const NET_KIND = { root: "UNC–Chapel Hill", unit: "School / lab", company: "Company" };

function buildNetwork3D() {
  const el = $("#graph-3d");
  if (!el) return;
  if (!window.ForceGraph3D) {                 // CDN didn't load — graceful fallback
    el.innerHTML = `<div class="net-fallback">3D view unavailable — the visualization library couldn't load.</div>`;
    return;
  }

  const ROOT = "unc:root";
  const nodes = [{ id: ROOT, name: "UNC–Chapel Hill", type: "root", val: 60 }];
  const valid = new Set([ROOT]);

  const activeUnits = GRAPH.units.filter(u => u.id !== ROOT && (u.footprint?.total || 0) > 0);
  for (const u of activeUnits) {
    valid.add(u.id);
    nodes.push({
      id: u.id, name: u.name, type: "unit",
      val: Math.min(34, 8 + Math.sqrt(u.footprint.total || 1)), _unit: u,
    });
  }

  const links = activeUnits.map(u => ({ source: ROOT, target: u.id, kind: "org" }));

  for (const c of GRAPH.companies) {
    const cu = (c.units || []).filter(x => valid.has(x.unit_id));
    if (!cu.length) continue;
    nodes.push({
      id: c.id, name: c.name, type: "company", confidence: c.confidence,
      val: Math.min(9, 1.6 + Math.sqrt(c.total_edges || 1)), _company: c,
    });
    for (const x of cu) links.push({ source: c.id, target: x.unit_id, kind: "evidence" });
  }

  // People: the most-active faculty cluster around each unit (the third player type).
  for (const u of activeUnits) {
    (u.top_faculty || []).slice(0, 6).forEach((f, i) => {
      if (!f || !f.name) return;
      const fid = `fac:${u.id}:${i}`;
      nodes.push({
        id: fid, name: f.name, type: "faculty", unitName: u.name, _unit: u,
        val: Math.min(5, 1.2 + Math.sqrt(f.count || 1)),
      });
      links.push({ source: fid, target: u.id, kind: "faculty" });
    });
  }

  const colorOf = n =>
    n.type === "root"    ? "#1d1d1f" :                                   // UNC core
    n.type === "unit"    ? "#3f7d6e" :                                   // school / lab (sage)
    n.type === "faculty" ? "#8a8a8f" :                                   // person (neutral graphite)
    n.confidence === "confirmed" ? "#5b8f81" : "#b08d57";               // company: sage / taupe

  NETGRAPH = ForceGraph3D()(el)
    .graphData({ nodes, links })
    .backgroundColor("rgba(0,0,0,0)")        // transparent → DOM gradient shows through
    .showNavInfo(false)
    .nodeRelSize(4)
    .nodeVal("val")
    .nodeColor(colorOf)
    .nodeOpacity(0.95)
    .nodeLabel(n => n.type === "faculty"
      ? `<div class="net-tip">${esc(n.name)}<span class="net-tip-sub">${esc(n.unitName || "")}</span></div>`
      : `<div class="net-tip">${esc(n.name)}<span class="net-tip-sub">${esc(NET_KIND[n.type] || "")}</span></div>`)
    .linkColor(l => l.kind === "org" ? "rgba(63,125,110,0.80)"
      : l.kind === "faculty" ? "rgba(120,120,128,0.32)" : "rgba(63,125,110,0.34)")
    .linkWidth(l => l.kind === "org" ? 1.4 : l.kind === "faculty" ? 0.5 : 0.7)
    .linkOpacity(0.7)
    .linkDirectionalParticles(l => l.kind === "org" ? 4 : l.kind === "faculty" ? 0 : 2)
    .linkDirectionalParticleWidth(l => l.kind === "org" ? 2.6 : 1.7)
    .linkDirectionalParticleSpeed(l => l.kind === "org" ? 0.006 : 0.011)
    .linkDirectionalParticleColor(l => l.kind === "org" ? "#1d4d40" : "#3f7d6e")
    .width(el.clientWidth)
    .height(el.clientHeight)
    .onNodeClick(n => {
      if ((n.type === "unit" || n.type === "faculty") && n._unit) {
        openUnit(n._unit);                       // a person opens their school/lab profile
      } else if (n.type === "company" && n._company) {
        $("#q").value = n._company.name;
        $("#search-clear").hidden = false;
        runSearch(n._company.name);
        scrollToResults();
      } else if (n.type === "root") {
        NETGRAPH.cameraPosition({ x: 0, y: 0, z: 360 }, { x: 0, y: 0, z: 0 }, 900);
      }
    });

  // Spread the layout out so every connection is legible (not crushed into a ball).
  try {
    NETGRAPH.d3Force("charge").strength(-130);
    NETGRAPH.d3Force("link").distance(l => l.kind === "org" ? 58 : l.kind === "faculty" ? 15 : 46);
  } catch {}

  NETGRAPH.cameraPosition({ z: 440 });

  // Auto-orbit that yields to the user and resumes after inactivity.
  let paused = true, resumeT, angle = 0, radius = 440;   // start paused until layout settles & frames
  const syncAngle = () => {
    const p = NETGRAPH.camera().position;
    radius = Math.hypot(p.x, p.z) || radius;
    angle = Math.atan2(p.x, p.z);
  };
  const hold = () => { paused = true; clearTimeout(resumeT); };
  const release = () => { clearTimeout(resumeT); resumeT = setTimeout(() => { syncAngle(); paused = false; }, 2500); };
  el.addEventListener("pointerdown", hold);
  el.addEventListener("pointerup", release);
  el.addEventListener("wheel", () => { hold(); release(); }, { passive: true });

  // Frame the whole graph once the layout has actually settled, then orbit.
  // (Ignore early engine-stops fired while the cluster is still a tight ball.)
  let fitted = false;
  const t0 = performance.now();
  const fitAndSpin = () => {
    if (fitted || !NETGRAPH) return;
    if (performance.now() - t0 < 2600) return;   // too early — layout still expanding
    fitted = true;
    NETGRAPH.zoomToFit(900, 80);
    setTimeout(() => { syncAngle(); paused = false; }, 1000);
  };
  NETGRAPH.onEngineStop(fitAndSpin);
  setTimeout(fitAndSpin, 4200);    // fallback once settling is reliably done

  setInterval(() => {
    if (paused || !NETGRAPH) return;
    angle += 0.004;                // a touch livelier
    const y = NETGRAPH.camera().position.y;
    NETGRAPH.cameraPosition({ x: radius * Math.sin(angle), y, z: radius * Math.cos(angle) }, undefined, 0);
  }, 33);

  // Keep the canvas sized to its container.
  let rt;
  window.addEventListener("resize", () => {
    clearTimeout(rt);
    rt = setTimeout(() => { if (NETGRAPH) NETGRAPH.width(el.clientWidth).height(el.clientHeight); }, 200);
  }, { passive: true });
}

// ── backend connectivity ───────────────────────────────────────────────────

async function checkApiHealth() {
  try {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), 4000);
    const res = await fetch(`${API_BASE}/health`, { signal: ctrl.signal });
    clearTimeout(t);
    API_OK = res.ok;
  } catch (err) { API_OK = false; console.warn("[unc-graph] health check failed:", err); }
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
  } catch (err) {
    if (err && err.name !== "AbortError") console.warn("[unc-graph] backend fetch failed:", err);
    return null;
  }
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

// Bring the results section into view below the sticky nav. Waits two frames —
// one for #results' `hidden` to be removed inside renderResults, one for layout —
// so we scroll to a visible element with its final geometry (a hidden element
// has no box, so scrolling would silently do nothing).
function scrollToResults() {
  requestAnimationFrame(() => requestAnimationFrame(() => {
    const r = document.getElementById("results");
    if (r && !r.hidden) {
      const navH = document.getElementById("nav")?.offsetHeight || 64;
      const top = r.getBoundingClientRect().top + window.scrollY - navH - 12;
      window.scrollTo({ top, behavior: "smooth" });
    }
  }));
}

function wireSearch() {
  const input = $("#q");
  const clearBtn = $("#search-clear");
  let timer;

  // Run a search now and bring the answer into view.
  const go = (q) => {
    clearTimeout(timer);
    runSearch(q);
    scrollToResults();
  };

  input.addEventListener("input", () => {
    clearBtn.hidden = !input.value;
    clearTimeout(timer);
    timer = setTimeout(() => runSearch(input.value), 140);
  });
  // Pressing Return runs the search immediately and scrolls to the result.
  input.addEventListener("keydown", e => {
    if (e.key === "Enter") { e.preventDefault(); go(input.value); input.blur(); }
  });
  // A form wrapper (if present) must not reload the page.
  const form = input.closest("form");
  if (form) form.addEventListener("submit", e => { e.preventDefault(); go(input.value); });

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
      go(b.dataset.q);
    })
  );
}

function runSearch(query) {
  query = (query || "").trim();
  const results = $("#results");
  if (!query) { results.hidden = true; return; }
  results.hidden = false;

  // Graph never loaded — render the dedicated "not built" state and skip local
  // matching, which would dereference a null GRAPH.
  if (window.__GRAPH_LOAD_FAILED) { renderResults(query, null, []); return; }

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
    }).catch((err) => {
      console.warn("[unc-graph] backend match failed, local result stands:", err);
    });
  }
}

function renderResults(query, company, topical) {
  const evPanel  = $("#evidence-panel");
  const topPanel = $("#topical-panel");
  const noRes    = $("#no-results");

  // State 1 of 3: the graph never loaded. Distinct from "too short" and
  // "genuinely no match" below — show build instructions, not a search miss.
  if (window.__GRAPH_LOAD_FAILED) {
    noRes.hidden = false; evPanel.hidden = true; topPanel.hidden = true;
    const titleEl = noRes.querySelector("h3");
    const msgEl = $("#no-results-msg");
    if (titleEl) titleEl.textContent = "Data not yet available";
    msgEl.innerHTML = `The research graph hasn't been built yet. Run <code>python scripts/build_graph.py</code> locally, then <code>python scripts/export_graph.py</code>, and commit <code>frontend/graph.json</code>.`;
    return;
  }

  evPanel.hidden  = !company;
  topPanel.hidden = !topical.length;
  noRes.hidden    = !!(company || topical.length);

  if (company) {
    evPanel.innerHTML = renderEvidence(company);
    // Each unit row opens its full profile (faculty / partners / keywords).
    evPanel.querySelectorAll(".unit-ev.is-clickable").forEach(row => {
      const open = () => { const u = UNIT_BY_ID[row.dataset.unitId]; if (u) openUnit(u); };
      row.addEventListener("click", e => { if (e.target.closest("a")) return; open(); });
      row.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); } });
    });
  }
  if (topical.length) topPanel.innerHTML = renderTopical(query, topical);

  if (!company && !topical.length) {
    // Honest, differentiated states — "bad input" vs "genuinely no match".
    const titleEl = noRes.querySelector("h3");
    const msgEl = $("#no-results-msg");
    if (query.replace(/[^a-z0-9]/gi, "").length < 3) {
      if (titleEl) titleEl.textContent = "Keep typing…";
      msgEl.innerHTML = `Type a few more letters — a company like <b>Pfizer</b> or a topic like <b>oncology</b>.`;
    } else {
      if (titleEl) titleEl.textContent = "No public records for that — yet";
      msgEl.innerHTML = `We found no public record linking <b>&ldquo;${esc(query)}&rdquo;</b> to UNC, and no UNC unit keywords match it. That means none were found in the public sources — not that none exist. Try a known partner (<b>Merck</b>, <b>Gilead</b>) or a broader topic (<b>genomics</b>, <b>HIV</b>).`;
    }
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
    : "not in the SEC filer registry";
  const rows = (co.units || []).map(u => renderUnitEvRow(u)).join("");
  const types = sourceTypesOf(co);
  const sourceText = types.length
    ? "Sourced from " + types.map(t => SOURCE_LABEL[t] || t).join(", ")
    : "";
  const fresh = freshnessLabel();
  const coverage = [sourceText, fresh ? `as of ${fresh}` : ""].filter(Boolean).join(" · ");
  const tierNote = co.confidence === "confirmed"
    ? "structured identifier matched (SEC CIK / trial sponsor)"
    : "normalized-name match only";
  return `
    <div class="result-card">
      <div class="rc-header">
        <div>
          <div class="rc-company-name">${esc(co.name)}</div>
          <div class="rc-meta">
            ${co.total_edges} public record${co.total_edges===1?"":"s"} across ${(co.units||[]).length} UNC unit${(co.units||[]).length===1?"":"s"} · ${cikHtml}
          </div>
        </div>
        <span class="badge ${co.confidence}" title="${esc(tierNote)}">${co.confidence}</span>
      </div>
      ${coverage ? `<div class="rc-coverage"><svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l7 3v5c0 4.3-3 7.6-7 9-4-1.4-7-4.7-7-9V6l7-3z"/><polyline points="9 12 11.2 14.2 15.5 9.8"/></svg>${esc(coverage)}</div>` : ""}
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
  // Non-root units have a full profile (faculty/partners/keywords) — make the row open it.
  const clickable = u.unit_id && u.unit_id !== "unc:root" && !!UNIT_BY_ID[u.unit_id];
  const attrs = clickable ? ` data-unit-id="${esc(u.unit_id)}" role="button" tabindex="0"` : "";
  return `
    <div class="unit-ev${clickable ? " is-clickable" : ""}"${attrs}>
      <div class="unit-ev-top">
        <span class="unit-ev-name">${esc(unit.name)}</span>
        <div class="score-track"><div class="score-fill" style="width:${pct}%"></div></div>
        <span class="score-val">${u.score.toFixed(0)}</span>
        <span class="badge ${u.confidence}">${u.confidence}</span>
      </div>
      <div class="ev-counts">${counts}</div>
      <ul class="ev-samples">${samples}</ul>
      ${clickable ? `<div class="ev-more">View faculty &amp; details →</div>` : ""}
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
      scrollToResults();
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
      scrollToResults();
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
