/* UNC Research Footprint Graph — client-side matcher.
   Loads graph.json (precomputed at build time) and answers both questions
   entirely in the browser: "is company X a UNC partner?" (evidence mode) and
   "which UNC units fit topic Y?" (topical mode). No backend, no API keys. */

const STOPWORDS = new Set("the a an and or of in to for with on at by from is are was inc llc corp co ltd company group holdings international plc the study research".split(" "));
const EDGE_LABEL = {grant:"Grant", paper:"Paper", trial:"Clinical trial", contract:"Contract", patent:"Patent"};

let GRAPH = null;
let UNIT_BY_ID = {};
let COMPANY_INDEX = [];   // [{norm, company}]

// ---------- helpers ----------
const $ = (sel) => document.querySelector(sel);
const el = (tag, cls, html) => { const e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; };
const esc = (s) => (s == null ? "" : String(s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])));
const fmtUSD = (n) => { if (!n) return "—"; if (n >= 1e9) return "$" + (n/1e9).toFixed(1) + "B"; if (n >= 1e6) return "$" + (n/1e6).toFixed(1) + "M"; if (n >= 1e3) return "$" + (n/1e3).toFixed(0) + "K"; return "$" + n; };

function normName(s){
  return (s||"").toLowerCase()
    .replace(/\b(inc|llc|corp|co|ltd|plc|lp|incorporated|corporation|limited|company)\b\.?/g," ")
    .replace(/[^\w\s&]/g," ").replace(/\s+/g," ").trim();
}
function tokenize(s){
  return new Set((s||"").toLowerCase().replace(/[^\w\s]/g," ").split(/\s+/)
    .filter(t => t.length >= 3 && !STOPWORDS.has(t)));
}

// ---------- load ----------
async function load(){
  try{
    const res = await fetch("graph.json", {cache:"no-cache"});
    if(!res.ok) throw new Error("HTTP " + res.status);
    GRAPH = await res.json();
  }catch(e){
    $("#statchips").innerHTML = `<span class="chip">graph.json not found — run the build &amp; export</span>`;
    console.error(e); return;
  }
  UNIT_BY_ID = Object.fromEntries(GRAPH.units.map(u => [u.id, u]));
  COMPANY_INDEX = GRAPH.companies.map(c => ({norm: normName(c.name), company: c}));
  renderStats();
  renderUnitGrid();
  renderCompanyChips();
  wireSearch();
}

// ---------- stats ----------
function renderStats(){
  const c = GRAPH.meta.counts || {};
  const chips = [
    ["Schools &amp; units", GRAPH.meta.n_units_with_data],
    ["Partner companies", GRAPH.meta.n_companies],
    ["Edges", c.edges],
    ["Grants", c.nih_grants != null ? (c.nih_grants + (c.nsf_awards||0)) : null],
    ["Papers", c.crossref_papers],
    ["Trials", c.clinical_trials],
    ["Faculty", c.faculty],
  ].filter(([,v]) => v != null);
  $("#statchips").innerHTML = chips.map(([k,v]) => `<span class="chip"><b>${Number(v).toLocaleString()}</b> ${k}</span>`).join("");
  $("#built-at").textContent = GRAPH.meta.built_at || "—";
  if(GRAPH.meta.counts) {
    $("#unit-count").textContent = `${GRAPH.units.length} units mapped · anchored on ROR ${GRAPH.meta.unc_ror}`;
  }
}

// ---------- search ----------
function wireSearch(){
  const input = $("#q");
  let t;
  input.addEventListener("input", () => { clearTimeout(t); t = setTimeout(() => runSearch(input.value), 120); });
  document.querySelectorAll(".hint").forEach(b =>
    b.addEventListener("click", () => { input.value = b.dataset.q; runSearch(b.dataset.q); input.focus(); }));
}

function runSearch(query){
  const results = $("#results");
  const evPanel = $("#evidence-panel");
  const topPanel = $("#topical-panel");
  query = (query||"").trim();
  if(!query){ results.hidden = true; return; }
  results.hidden = false;

  // Evidence mode: company lookup
  const company = matchCompany(query);
  if(company){ evPanel.hidden = false; evPanel.innerHTML = renderEvidence(company); }
  else { evPanel.hidden = true; }

  // Topical mode: keyword overlap against unit profiles
  const topical = matchTopical(query);
  if(topical.length){ topPanel.hidden = false; topPanel.innerHTML = renderTopical(query, topical); }
  else { topPanel.hidden = true; }

  if(!company && !topical.length){
    evPanel.hidden = false;
    evPanel.innerHTML = `<div class="empty-state">No partnership records or topical matches for “${esc(query)}”.<br><span class="small">This graph covers companies linked to UNC by a public record. Try a topic like “oncology” or a known partner.</span></div>`;
  }
}

function matchCompany(query){
  const q = normName(query);
  if(q.length < 2) return null;
  let exact = COMPANY_INDEX.find(c => c.norm === q);
  if(exact) return exact.company;
  // substring (query within company name or vice versa), prefer most edges
  const subs = COMPANY_INDEX
    .filter(c => c.norm.includes(q) || q.includes(c.norm))
    .sort((a,b) => b.company.total_edges - a.company.total_edges);
  return subs.length ? subs[0].company : null;
}

function matchTopical(query){
  const q = tokenize(query);
  if(!q.size) return [];
  const out = [];
  for(const u of GRAPH.units){
    const kw = u.keywords || [];
    if(!kw.length) continue;
    const kwSet = new Set(kw);
    const hits = [...q].filter(t => kwSet.has(t));
    if(!hits.length) continue;
    const score = hits.length / Math.sqrt(q.size * kw.length);
    out.push({unit:u, score, hits});
  }
  return out.sort((a,b) => b.score - a.score).slice(0,6);
}

// ---------- render: evidence ----------
function renderEvidence(co){
  const cikLink = co.cik
    ? `<a class="cik-link" href="https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=${esc(co.cik)}&type=10-K" target="_blank" rel="noopener">SEC CIK ${esc(co.cik)} ↗</a>`
    : "";
  const rows = co.units.map(u => renderUnitEvidence(u)).join("");
  return `
    <div class="panel-head">
      <div>
        <div class="co-title">
          <span class="name">${esc(co.name)}</span>
          <span class="badge ${co.confidence}">${co.confidence}</span>
        </div>
        <div class="sub">${co.total_edges} public record${co.total_edges===1?"":"s"} linking to ${co.units.length} UNC unit${co.units.length===1?"":"s"} · ${cikLink || "not matched to a public SEC filer"}</div>
      </div>
      <span class="badge topical">Partnership evidence</span>
    </div>
    <div class="panel-body">${rows}</div>`;
}

function renderUnitEvidence(u){
  const unit = UNIT_BY_ID[u.unit_id] || {name:u.unit_id};
  const maxScore = 20;
  const pct = Math.min(100, (u.score / maxScore) * 100);
  const counts = Object.entries(u.counts).map(([t,n]) =>
    `<span class="count">${n} ${EDGE_LABEL[t]||t}${n>1?"s":""}</span>`).join("");
  const samples = (u.samples||[]).map(s => `
    <li>
      <span class="ev-type">${esc(s.type)}</span>
      <span><a class="ev-link" href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.title || s.url)}</a>${s.date ? ` <span class="muted">· ${esc(s.date)}</span>` : ""}</span>
    </li>`).join("");
  return `
    <div class="unit-row">
      <div class="unit-row-top">
        <span class="unit-name">${esc(unit.name)}</span>
        <span class="badge ${u.confidence}">${u.confidence}</span>
        <div class="score-bar"><div class="score-fill" style="width:${pct}%"></div></div>
        <span class="score-num">${u.score.toFixed(0)}</span>
      </div>
      <div class="count-chips">${counts}</div>
      <ul class="evidence-list">${samples}</ul>
    </div>`;
}

// ---------- render: topical ----------
function renderTopical(query, items){
  const rows = items.map(({unit, score, hits}) => {
    const hitSet = new Set(hits);
    const kwTags = (unit.keywords||[]).slice(0,14).map(k =>
      `<span class="kw ${hitSet.has(k)?"hit":""}">${esc(k)}</span>`).join("");
    const fp = unit.footprint || {};
    return `
      <div class="unit-row">
        <div class="unit-row-top">
          <span class="unit-name">${esc(unit.name)}</span>
          <span class="badge topical">${hits.length} keyword${hits.length>1?"s":""}</span>
          <div class="score-bar"><div class="score-fill" style="width:${Math.min(100,score*140)}%"></div></div>
          <span class="score-num">${score.toFixed(2)}</span>
        </div>
        <div class="muted small" style="margin-top:4px">${fp.grant||0} grants · ${fp.paper||0} papers · ${fp.trial||0} trials · ${fmtUSD(fp.total_usd)} federal funding</div>
        <div class="kw-match">${kwTags}</div>
      </div>`;
  }).join("");
  return `
    <div class="panel-head">
      <div><h2>Topical alignment</h2><div class="sub">UNC units whose grant &amp; publication keywords overlap “${esc(query)}”</div></div>
      <span class="badge topical">Topical match</span>
    </div>
    <div class="panel-body">${rows}</div>`;
}

// ---------- explorer grid ----------
function renderUnitGrid(){
  const grid = $("#unit-grid");
  const units = GRAPH.units
    .filter(u => u.id !== "unc:root")
    .sort((a,b) => (b.footprint.total||0) - (a.footprint.total||0));
  grid.innerHTML = "";
  for(const u of units){
    const fp = u.footprint;
    const has = fp.total > 0;
    const card = el("button", "unit-card" + (has?"":" empty"));
    card.innerHTML = `
      <h3>${esc(u.name)}</h3>
      <div class="uc-stats">
        <span><b>${fp.total||0}</b> records</span>
        <span><b>${(u.top_companies||[]).length}</b> partners</span>
        <span><b>${fmtUSD(fp.total_usd)}</b> funding</span>
      </div>
      <div class="uc-kw">${(u.keywords||[]).slice(0,6).map(esc).join(" · ") || "<span class='muted'>" + (has ? "Linked via trials &amp; awards (no topic profile)" : "No indexed records yet") + "</span>"}</div>`;
    if(has) card.addEventListener("click", () => openUnit(u));
    grid.appendChild(card);
  }
}

// ---------- company chips ----------
function renderCompanyChips(){
  const box = $("#company-chips");
  const cos = [...GRAPH.companies].sort((a,b) => b.total_edges - a.total_edges);
  box.innerHTML = "";
  if(!cos.length){ box.innerHTML = `<span class="muted small">No company links in this build.</span>`; return; }
  for(const c of cos){
    const chip = el("button", "co-chip");
    chip.innerHTML = `<span class="dot ${c.confidence}"></span>${esc(c.name)} <span class="n">${c.total_edges}</span>`;
    chip.addEventListener("click", () => { $("#q").value = c.name; runSearch(c.name); window.scrollTo({top:0,behavior:"smooth"}); });
    box.appendChild(chip);
  }
}

// ---------- unit modal ----------
function openUnit(u){
  const fp = u.footprint;
  const companies = (u.top_companies||[]).map(c =>
    `<button class="co-chip" data-co="${esc(c.name)}"><span class="dot ${c.confidence}"></span>${esc(c.name)} <span class="n">${c.count}</span></button>`).join("") || "<span class='muted small'>No industry partners on record.</span>";
  const faculty = (u.top_faculty||[]).slice(0,8).map(f =>
    `<li>${esc(f.name)} <span class="c">· ${f.count} record${f.count>1?"s":""}</span></li>`).join("") || "<li class='muted'>No faculty indexed.</li>";
  const kw = (u.keywords||[]).slice(0,24).map(k => `<span class="kw">${esc(k)}</span>`).join("") || "<span class='muted small'>No topic profile.</span>";

  $("#modal-body").innerHTML = `
    <h2>${esc(u.name)}</h2>
    <div class="muted small">${esc(u.short_name||"")} · part of UNC-Chapel Hill</div>
    <div class="modal-stats">
      <div class="ms"><b>${fp.grant||0}</b><span>Grants</span></div>
      <div class="ms"><b>${fp.paper||0}</b><span>Papers</span></div>
      <div class="ms"><b>${fp.trial||0}</b><span>Trials</span></div>
      <div class="ms"><b>${fp.contract||0}</b><span>Contracts</span></div>
      <div class="ms"><b>${fmtUSD(fp.total_usd)}</b><span>Federal funding</span></div>
    </div>
    <div class="modal-section">
      <h4>Industry partners</h4>
      <div class="tag-list">${companies}</div>
    </div>
    <div class="modal-section">
      <h4>Most active faculty (by indexed records)</h4>
      <ul class="fac-list">${faculty}</ul>
    </div>
    <div class="modal-section">
      <h4>Research keywords</h4>
      <div class="tag-list">${kw}</div>
    </div>`;
  $("#modal-body").querySelectorAll("[data-co]").forEach(b =>
    b.addEventListener("click", () => { closeModal(); $("#q").value = b.dataset.co; runSearch(b.dataset.co); window.scrollTo({top:0,behavior:"smooth"}); }));
  $("#unit-modal").hidden = false;
}
function closeModal(){ $("#unit-modal").hidden = true; }

$("#modal-close").addEventListener("click", closeModal);
$("#unit-modal").addEventListener("click", (e) => { if(e.target.id === "unit-modal") closeModal(); });
document.addEventListener("keydown", (e) => { if(e.key === "Escape") closeModal(); });

load();
