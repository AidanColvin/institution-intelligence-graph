/* UNC Research Intelligence — multi-page SPA.
   A hash-routed client that fetches EVERY view live from the same-origin Python
   API (api/index.py). No static graph.json / partnerships.json is read in the
   browser — the backend serves all data, so the frontend is genuinely connected.

   Routes:
     #/                     home + search
     #/search/<q>           company footprint + topical matches  (/match)
     #/units                schools & units explorer             (/units)
     #/unit/<id>            unit detail (overview/partnerships/faculty)
     #/partnerships         partnership inventory + filters      (/partnerships)
     #/faculty              faculty directory                    (/faculty)
     #/network              3D research network                  (/api/graph)
     #/about                methodology + freshness              (/freshness)
*/
(function () {
  "use strict";

  // ── API client ───────────────────────────────────────────────────────────
  const API = "";                       // same-origin
  const elView = () => document.getElementById("view");
  const $ = (s, r = document) => r.querySelector(s);

  async function api(path, { signal, timeoutMs = 12000 } = {}) {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort("timeout"), timeoutMs);
    const sig = signal || ctrl.signal;
    try {
      const res = await fetch(API + path, { signal: sig, headers: { Accept: "application/json" } });
      if (!res.ok) {
        const err = new Error("HTTP " + res.status);
        err.status = res.status;
        throw err;
      }
      return await res.json();
    } finally {
      clearTimeout(t);
    }
  }

  // Distinct, friendly failure copy (never a raw stack trace to the user).
  function friendlyError(err, ctx) {
    if (err && (err.name === "AbortError" || String(err.message).includes("abort") || err === "timeout")) {
      return { title: "Some sources timed out", msg: "The request took too long — results may be incomplete. Try again in a moment." };
    }
    if (err && err.status === 404) {
      return { title: "Nothing found", msg: ctx?.notFound || "No record matched that request in public data." };
    }
    if (err && err.status >= 500) {
      return { title: "The service hit a snag", msg: "The backend returned an error. This is usually transient — please retry." };
    }
    return { title: "Couldn't reach the backend", msg: "Check your connection and retry. If this persists, the API may be redeploying." };
  }

  // ── helpers ────────────────────────────────────────────────────────────────
  const esc = (s) => (s == null ? "" : String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])));
  const fmtUSD = (n) => { if (!n) return ""; n = +n; if (n >= 1e9) return "$" + (n / 1e9).toFixed(1) + "B"; if (n >= 1e6) return "$" + (n / 1e6).toFixed(1) + "M"; if (n >= 1e3) return "$" + (n / 1e3).toFixed(0) + "K"; return "$" + n; };
  const fmtDate = (d) => { if (!d) return ""; try { const dt = new Date(d); if (isNaN(dt)) return d; return dt.toLocaleDateString(undefined, { year: "numeric", month: "short", day: d.length > 7 ? "numeric" : undefined }); } catch { return d; } };
  const EDGE_LABEL = { grant: "Grant", paper: "Paper", trial: "Trial", contract: "Contract", patent: "Patent" };
  const loadingHTML = (label) => `<div class="loading"><div class="spinner"></div>${esc(label || "Loading…")}</div>`;
  function errorHTML(err, ctx) { const f = friendlyError(err, ctx); return `<div class="error"><h3>${esc(f.title)}</h3><p>${esc(f.msg)}</p><p style="margin-top:14px"><button class="btn ghost" onclick="location.reload()">Retry</button></p></div>`; }
  function emptyHTML(title, msg) { return `<div class="empty"><h3>${esc(title)}</h3><p>${esc(msg)}</p></div>`; }
  const enc = encodeURIComponent;

  let FRESHNESS = null;
  function coverageBar() {
    if (!FRESHNESS) return "";
    const c = FRESHNESS.counts || {};
    const built = FRESHNESS.built_at ? fmtDate(FRESHNESS.built_at) : "—";
    const parts = [];
    if (c.nih_grants != null) parts.push(`${(c.nih_grants).toLocaleString()} NIH`);
    if (c.nsf_awards != null) parts.push(`${(c.nsf_awards).toLocaleString()} NSF`);
    if (c.usaspending != null) parts.push(`${(c.usaspending).toLocaleString()} contracts`);
    if (c.clinical_trials != null) parts.push(`${(c.clinical_trials).toLocaleString()} trials`);
    if (c.crossref_papers != null) parts.push(`${(c.crossref_papers).toLocaleString()} papers`);
    return `<div class="coverage"><b>${parts.length} sources</b> · ${esc(parts.join(" · "))} <span class="dot-sep">·</span> as of <b>${esc(built)}</b></div>`;
  }

  // ── view: HOME / SEARCH LANDING ──────────────────────────────────────────────
  const STARTERS = [
    { q: "Pfizer", tag: "Company", cls: "evidence", title: "See Pfizer's UNC footprint", desc: "Which schools & labs link to Pfizer — by grants, papers & trials." },
    { q: "gene therapy", tag: "Topic", cls: "topical", title: "Find labs for gene therapy", desc: "UNC units whose grant & paper keywords match this field." },
    { q: "Gilead Sciences", tag: "Company", cls: "evidence", title: "Trace Gilead Sciences", desc: "Confirmed links via trial sponsors & SEC-matched records." },
    { q: "cancer immunotherapy", tag: "Topic", cls: "topical", title: "Map cancer immunotherapy", desc: "Which UNC units are most active in this research area." },
  ];

  async function renderHome() {
    const v = elView();
    v.innerHTML = `
      <section class="hero wrap">
        <span class="eyebrow">UNC–Chapel Hill · Public Research Footprint</span>
        <h1>See how the world<br/>connects to UNC research</h1>
        <p>Type a <b>company</b> or a <b>research topic</b> — get the UNC schools, labs and faculty behind it, each link backed by a real public record you can open.</p>
        <form class="search-box" id="search-form">
          <input id="q" type="search" autocomplete="off" spellcheck="false" placeholder="Try a company like “Pfizer” or a topic like “gene therapy”…" aria-label="Search" />
          <button class="go" type="submit">Search</button>
        </form>
        <div class="statbar" id="statbar"></div>
        <div class="starter-grid">
          ${STARTERS.map((s) => `
            <a class="starter-card" href="#/search/${enc(s.q)}">
              <span class="tag ${s.cls}">${esc(s.tag)}</span>
              <span class="st-title">${esc(s.title)}</span>
              <span class="st-desc">${esc(s.desc)}</span>
            </a>`).join("")}
        </div>
      </section>`;

    $("#search-form").addEventListener("submit", (e) => {
      e.preventDefault();
      const q = $("#q").value.trim();
      if (q) location.hash = "#/search/" + enc(q);
    });

    try {
      const s = await api("/stats");
      const m = s.meta || {};
      const c = m.counts || {};
      const pills = [
        [s.n_companies ?? m.n_companies, "companies"],
        [c.edges, "connections"],
        [c.faculty, "faculty"],
        [s.n_units ?? c.unc_units, "units"],
      ].filter(([n]) => n != null);
      $("#statbar").innerHTML = pills.map(([n, l]) => `<span class="stat-pill"><b>${(+n).toLocaleString()}</b> ${esc(l)}</span>`).join("");
    } catch (e) { console.error("home /stats failed:", e); $("#statbar").innerHTML = ""; }
  }

  // ── view: SEARCH RESULTS ─────────────────────────────────────────────────────
  async function renderSearch(q) {
    const v = elView();
    v.innerHTML = `<div class="page wrap">
      <form class="search-box" id="search-form" style="margin:0 0 24px">
        <input id="q" type="search" value="${esc(q)}" aria-label="Search" />
        <button class="go" type="submit">Search</button>
      </form>
      <div id="search-results">${loadingHTML("Querying the research graph…")}</div>
    </div>`;
    $("#search-form").addEventListener("submit", (e) => { e.preventDefault(); const nq = $("#q").value.trim(); if (nq) location.hash = "#/search/" + enc(nq); });

    const out = $("#search-results");
    let data;
    try { data = await api("/match/" + enc(q)); }
    catch (e) { console.error("search /match failed:", e, "query=", q); out.innerHTML = errorHTML(e, {}); return; }

    const co = data.company;
    const topical = data.topical_matches || [];
    let html = "";

    if (co) {
      const tier = co.confidence || "probable";
      html += `<div class="page-head">
        <div class="card-top">
          <div><span class="eyebrow">Company footprint</span>
            <h1 class="page-title">${esc(co.name)}</h1>
            <p class="page-sub">${co.cik ? `SEC CIK ${esc(co.cik)} · ` : ""}${(co.total_edges || 0).toLocaleString()} public records linking to UNC across ${(co.units || []).length} unit(s).</p>
          </div>
          <span class="badge ${esc(tier)}">${esc(tier)}</span>
        </div></div>`;
      html += (co.units || []).map((u) => {
        const counts = Object.entries(u.counts || {}).map(([t, n]) => `<span class="ev-count">${n} ${esc(EDGE_LABEL[t] || t)}${n > 1 ? "s" : ""}</span>`).join("");
        const samples = (u.samples || []).slice(0, 5).map((s) => `
          <div class="ev-sample">
            <span class="ev-type">${esc(EDGE_LABEL[s.type] || s.type)}</span>
            <span style="flex:1">${s.url ? `<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.title || s.url)}</a>` : esc(s.title || "")}</span>
            <span class="ev-date">${esc(fmtDate(s.date))}</span>
          </div>`).join("");
        return `<div class="ev-unit">
          <div class="ev-unit-head">
            <a href="#/unit/${enc(u.unit_id)}"><strong>${esc(u.unit_name || u.unit_id)}</strong></a>
            <div class="ev-counts">${counts}</div>
          </div>
          <div class="ev-samples">${samples || '<p class="page-sub">Evidence links available via the records above.</p>'}</div>
        </div>`;
      }).join("");
    }

    if (topical.length) {
      html += `<div class="page-head" style="margin-top:34px"><span class="eyebrow topical-eye">Topical matches</span>
        <h2 class="page-title" style="font-size:24px">UNC units working on “${esc(q)}”</h2>
        <p class="page-sub">Units whose grant & paper keywords overlap your query.</p></div>`;
      html += `<div class="grid">` + topical.map((t) => `
        <a class="card" href="#/unit/${enc(t.unit_id)}">
          <h3>${esc(t.unit_name || t.unit_id)}</h3>
          <div class="meta">match score ${(t.score || 0).toFixed(3)}</div>
          <div class="chips">${(t.hits || []).slice(0, 6).map((h) => `<span class="chip">${esc(h)}</span>`).join("")}</div>
        </a>`).join("") + `</div>`;
    }

    if (!co && !topical.length) {
      html = emptyHTML("No UNC connections found", `No company match or topical overlap for “${q}” in public records. Try a company name (e.g. Pfizer) or a research topic (e.g. oncology).`);
    }
    out.innerHTML = html;
  }

  // ── view: UNITS EXPLORER ─────────────────────────────────────────────────────
  let UNITS_CACHE = null;
  async function renderUnits() {
    const v = elView();
    v.innerHTML = `<div class="page wrap">
      <div class="page-head"><span class="eyebrow">Explorer</span><h1 class="page-title">UNC Schools &amp; Units</h1>
        <p class="page-sub">Every school, department, lab and center in the graph — with its industry partnership count. Click through for evidence.</p></div>
      ${coverageBar()}
      <div class="toolbar">
        <input type="search" id="u-search" placeholder="Filter units by name…" />
        <select id="u-type"><option value="">All types</option></select>
        <span class="count" id="u-count"></span>
      </div>
      <div id="u-grid">${loadingHTML("Loading units…")}</div>
    </div>`;

    let units;
    try { units = UNITS_CACHE || (UNITS_CACHE = await api("/units")); }
    catch (e) { console.error("/units failed:", e); $("#u-grid").innerHTML = errorHTML(e); return; }

    const types = [...new Set(units.map((u) => u.unit_type).filter(Boolean))].sort();
    $("#u-type").innerHTML = `<option value="">All types</option>` + types.map((t) => `<option value="${esc(t)}">${esc(t)}</option>`).join("");

    const draw = () => {
      const term = $("#u-search").value.toLowerCase();
      const type = $("#u-type").value;
      const rows = units.filter((u) => (!type || u.unit_type === type) && (!term || (u.unit_name || "").toLowerCase().includes(term)))
        .sort((a, b) => (b.partnership_count || 0) - (a.partnership_count || 0));
      $("#u-count").textContent = `${rows.length} of ${units.length} units`;
      $("#u-grid").innerHTML = rows.length ? `<div class="grid">` + rows.map((u) => `
        <a class="card" href="#/unit/${enc(u.unit_id)}">
          <div class="card-top"><h3>${esc(u.unit_name)}</h3><span class="kind">${esc(u.unit_type || "unit")}</span></div>
          <div class="meta">${(u.partnership_count || 0)} partnership${u.partnership_count === 1 ? "" : "s"}${u.faculty_count ? ` · ${u.faculty_count} faculty` : ""}</div>
          ${(u.top_companies && u.top_companies.length) ? `<div class="chips">${u.top_companies.slice(0, 3).map((c) => `<span class="chip">${esc(typeof c === "string" ? c : c.name || "")}</span>`).join("")}</div>` : ""}
        </a>`).join("") + `</div>` : emptyHTML("No units match", "Try a different name or type.");
    };
    $("#u-search").addEventListener("input", draw);
    $("#u-type").addEventListener("change", draw);
    draw();
  }

  // ── view: UNIT DETAIL ────────────────────────────────────────────────────────
  async function renderUnit(id, tab) {
    tab = tab || "overview";
    const v = elView();
    v.innerHTML = `<div class="page wrap"><div id="unit-body">${loadingHTML("Loading unit…")}</div></div>`;
    const body = $("#unit-body");
    let unit;
    try { unit = await api("/unit/" + enc(id)); }
    catch (e) { console.error("/unit failed:", e, id); body.innerHTML = errorHTML(e, { notFound: "That unit isn't in the graph." }); return; }
    if (unit.error) { body.innerHTML = emptyHTML("Unit not found", unit.error); return; }

    const head = `<div class="crumb"><a href="#/units">← Schools &amp; Units</a></div>
      <div class="page-head"><div class="card-top"><div>
        <h1 class="page-title">${esc(unit.unit_name)}</h1>
        <p class="page-sub">${esc(unit.description || "")}</p>
      </div><span class="kind">${esc(unit.unit_type || "unit")}</span></div>
      ${(unit.focus_areas && unit.focus_areas.length) ? `<div class="chips">${(Array.isArray(unit.focus_areas) ? unit.focus_areas : String(unit.focus_areas).split(/[;,]/)).map((f) => `<span class="chip">${esc(f.trim())}</span>`).join("")}</div>` : ""}
      </div>
      <div class="tabs">
        <a class="tab ${tab === "overview" ? "active" : ""}" href="#/unit/${enc(id)}">Overview</a>
        <a class="tab ${tab === "partnerships" ? "active" : ""}" href="#/unit/${enc(id)}/partnerships">Partnerships ${unit.partnership_count != null ? `(${unit.partnership_count})` : ""}</a>
        <a class="tab ${tab === "faculty" ? "active" : ""}" href="#/unit/${enc(id)}/faculty">Faculty ${unit.faculty_count != null ? `(${unit.faculty_count})` : ""}</a>
      </div>
      <div id="unit-tab">${loadingHTML("Loading…")}</div>`;
    body.innerHTML = head;
    const tabEl = $("#unit-tab");

    if (tab === "overview") {
      const rows = [
        ["Faculty", unit.faculty_count],
        ["Partnerships", unit.partnership_count],
        ["Website", unit.website_url ? `<a href="${esc(unit.website_url)}" target="_blank" rel="noopener">${esc(unit.website_url)}</a>` : ""],
        ["Disciplines", Array.isArray(unit.disciplines) ? unit.disciplines.join(", ") : unit.disciplines],
        ["Researched by", unit.research_by],
        ["As of", fmtDate(unit.date_of_research)],
      ].filter(([, val]) => val != null && val !== "");
      tabEl.innerHTML = `<div class="grid">${rows.map(([k, val]) => `<div class="card"><div class="meta">${esc(k)}</div><div class="bignum" style="font-size:17px;margin-top:4px">${typeof val === "number" ? val.toLocaleString() : val}</div></div>`).join("")}</div>
        ${unit.notes ? `<p class="page-sub" style="margin-top:18px">${esc(unit.notes)}</p>` : ""}`;
    } else if (tab === "partnerships") {
      try {
        const d = await api("/unit/" + enc(id) + "/partnerships");
        tabEl.innerHTML = d.count ? partnershipTable(d.partnerships) : emptyHTML("No partnerships recorded", "No industry partnerships for this unit in public data yet.");
      } catch (e) { console.error("/unit partnerships failed:", e); tabEl.innerHTML = errorHTML(e); }
    } else if (tab === "faculty") {
      try {
        const d = await api("/unit/" + enc(id) + "/faculty");
        tabEl.innerHTML = d.count ? `<div class="grid">${d.faculty.map(facultyCard).join("")}</div>` : emptyHTML("No faculty listed", "No faculty are mapped to this unit yet.");
      } catch (e) { console.error("/unit faculty failed:", e); tabEl.innerHTML = errorHTML(e); }
    }
  }

  // ── view: PARTNERSHIPS ───────────────────────────────────────────────────────
  function partnershipTable(rows) {
    return `<div class="table-wrap"><table class="data">
      <thead><tr><th>Unit</th><th>Area</th><th>Company</th><th>Tier</th><th>Funding</th><th>Source</th><th>Date</th></tr></thead>
      <tbody>${rows.map((r) => `<tr>
        <td><a href="#/unit/${enc(r.unit_id)}">${esc(r.unit_name || r.unit_id)}</a></td>
        <td>${esc(r.area || "")}</td>
        <td>${esc(r.company_name || "")}</td>
        <td><span class="badge ${esc(r.verification_tier || "")}">${esc(r.verification_tier || "")}</span></td>
        <td>${esc(fmtUSD(r.funding_value))}</td>
        <td class="src-link">${r.source_url ? `<a href="${esc(r.source_url)}" target="_blank" rel="noopener">record ↗</a>` : ""}</td>
        <td>${esc(fmtDate(r.start_date || r.date_of_research))}</td>
      </tr>`).join("")}</tbody></table></div>`;
  }

  async function renderPartnerships(query) {
    const v = elView();
    v.innerHTML = `<div class="page wrap">
      <div class="page-head"><div class="card-top"><div><span class="eyebrow">Inventory</span>
        <h1 class="page-title">UNC Partnerships</h1>
        <p class="page-sub">Industry partnerships in public data — filter by area or verification tier, open any source record, or export the full inventory.</p></div>
        <button class="btn" id="export-btn">⤓ Export to Excel</button></div></div>
      ${coverageBar()}
      <div class="toolbar">
        <select id="f-area"><option value="">All areas</option></select>
        <select id="f-tier"><option value="">All tiers</option></select>
        <button class="btn ghost" id="f-reset">Reset</button>
        <span class="count" id="p-count"></span>
      </div>
      <div id="p-body">${loadingHTML("Loading partnerships…")}</div>
    </div>`;

    $("#export-btn").addEventListener("click", () => { window.location.href = API + "/partnerships/export"; });

    let all;
    try { all = await api("/partnerships"); }
    catch (e) { console.error("/partnerships failed:", e); $("#p-body").innerHTML = errorHTML(e); return; }
    const rows = all.partnerships || [];
    const areas = [...new Set(rows.map((r) => r.area).filter(Boolean))].sort();
    const tiers = [...new Set(rows.map((r) => r.verification_tier).filter(Boolean))].sort();
    $("#f-area").innerHTML = `<option value="">All areas</option>` + areas.map((a) => `<option value="${esc(a)}">${esc(a)}</option>`).join("");
    $("#f-tier").innerHTML = `<option value="">All tiers</option>` + tiers.map((t) => `<option value="${esc(t)}">${esc(t)}</option>`).join("");
    if (query.area) $("#f-area").value = query.area;
    if (query.tier) $("#f-tier").value = query.tier;

    const draw = () => {
      const area = $("#f-area").value, tier = $("#f-tier").value;
      const filtered = rows.filter((r) => (!area || r.area === area) && (!tier || r.verification_tier === tier));
      $("#p-count").textContent = `${filtered.length.toLocaleString()} of ${rows.length.toLocaleString()} partnerships`;
      $("#p-body").innerHTML = filtered.length ? partnershipTable(filtered) : emptyHTML("No partnerships match", "Loosen the filters to see more.");
    };
    $("#f-area").addEventListener("change", draw);
    $("#f-tier").addEventListener("change", draw);
    $("#f-reset").addEventListener("click", () => { $("#f-area").value = ""; $("#f-tier").value = ""; draw(); });
    draw();
  }

  // ── view: FACULTY ────────────────────────────────────────────────────────────
  function facultyCard(f) {
    return `<div class="card">
      <div class="card-top"><h3>${esc(f.full_name)}</h3>${f.partnership_count ? `<span class="kind">${f.partnership_count} partnership${f.partnership_count === 1 ? "" : "s"}</span>` : ""}</div>
      <div class="meta">${esc(f.title || "")}${f.unit_name ? `${f.title ? " · " : ""}<a href="#/unit/${enc(f.unit_id)}">${esc(f.unit_name)}</a>` : ""}</div>
      ${f.top_company ? `<div class="chips"><span class="chip">${esc(f.top_company)}</span></div>` : ""}
      ${f.profile_url ? `<div style="margin-top:10px"><a class="src-link" href="${esc(f.profile_url)}" target="_blank" rel="noopener">profile ↗</a></div>` : ""}
    </div>`;
  }
  let FACULTY_CACHE = null;
  async function renderFaculty() {
    const v = elView();
    v.innerHTML = `<div class="page wrap">
      <div class="page-head"><span class="eyebrow">Directory</span><h1 class="page-title">UNC Faculty</h1>
        <p class="page-sub">Researchers mapped to units through public grants, papers and trials. Filter by name; click a unit to see its evidence.</p></div>
      ${coverageBar()}
      <div class="toolbar">
        <input type="search" id="fac-search" placeholder="Filter by faculty name…" />
        <label style="font-size:13px;color:var(--muted)"><input type="checkbox" id="fac-partners" /> with partnerships only</label>
        <span class="count" id="fac-count"></span>
      </div>
      <div id="fac-grid">${loadingHTML("Loading faculty…")}</div>
    </div>`;

    let fac;
    try { fac = FACULTY_CACHE || (FACULTY_CACHE = await api("/faculty")); }
    catch (e) { console.error("/faculty failed:", e); $("#fac-grid").innerHTML = errorHTML(e); return; }

    const draw = () => {
      const term = $("#fac-search").value.toLowerCase();
      const partnersOnly = $("#fac-partners").checked;
      let rows = fac.filter((f) => (!term || (f.full_name || "").toLowerCase().includes(term)) && (!partnersOnly || (f.partnership_count || 0) > 0));
      rows = rows.sort((a, b) => (b.partnership_count || 0) - (a.partnership_count || 0)).slice(0, 300);
      $("#fac-count").textContent = `${rows.length} shown of ${fac.length.toLocaleString()}`;
      $("#fac-grid").innerHTML = rows.length ? `<div class="grid">${rows.map(facultyCard).join("")}</div>` : emptyHTML("No faculty match", "Try a different name.");
    };
    $("#fac-search").addEventListener("input", draw);
    $("#fac-partners").addEventListener("change", draw);
    draw();
  }

  // ── view: NETWORK (3D, API-driven) ────────────────────────────────────────────
  async function renderNetwork() {
    const v = elView();
    v.innerHTML = `<div class="page wrap">
      <div class="page-head"><span class="eyebrow">Live graph</span><h1 class="page-title">Research network</h1>
        <p class="page-sub">Every dot is the UNC anchor, a unit, or a partner company; every line is a public record. Drag to rotate, scroll to zoom, click a node.</p></div>
      ${coverageBar()}
      <div class="network-stage"><div id="graph-3d"></div>
        <div class="net-legend">
          <span><i style="background:#1d1d1f"></i> UNC–Chapel Hill</span>
          <span><i style="background:#3f7d6e"></i> School / unit</span>
          <span><i style="background:#5b8def"></i> Company · confirmed</span>
          <span><i style="background:#9a8654"></i> Company · probable</span>
        </div></div>
    </div>`;

    let g;
    try { g = await api("/api/graph", { timeoutMs: 20000 }); }
    catch (e) { console.error("/api/graph failed:", e); $("#graph-3d").parentElement.innerHTML = errorHTML(e); return; }
    if (typeof ForceGraph3D === "undefined") { $("#graph-3d").innerHTML = `<div class="error info" style="padding:40px">Network library failed to load (CDN blocked). The data is live at <code>/api/graph</code>.</div>`; return; }

    const nodes = [{ id: "unc:root", label: "UNC–Chapel Hill", group: "root", val: 30 }];
    const links = [];
    (g.units || []).forEach((u) => {
      if (u.id === "unc:root") return;
      const fp = u.footprint || {};
      const total = Object.values(fp).reduce((a, b) => a + (typeof b === "number" ? b : 0), 0);
      nodes.push({ id: u.id, label: u.name, group: "unit", val: 6 + Math.min(total, 30) });
      links.push({ source: "unc:root", target: u.id });
    });
    const unitIds = new Set(nodes.map((n) => n.id));
    (g.companies || []).forEach((c) => {
      nodes.push({ id: c.id, label: c.name, group: c.confidence === "confirmed" ? "confirmed" : "probable", val: 3 + Math.min(c.total_edges || 1, 14) });
      (c.units || []).forEach((cu) => { if (unitIds.has(cu.unit_id)) links.push({ source: c.id, target: cu.unit_id }); });
    });

    const COLORS = { root: "#1d1d1f", unit: "#3f7d6e", confirmed: "#5b8def", probable: "#9a8654" };
    const stage = $("#graph-3d");
    const Graph = ForceGraph3D()(stage)
      .graphData({ nodes, links })
      .backgroundColor("rgba(0,0,0,0)")
      .nodeLabel((n) => n.label)
      .nodeVal("val")
      .nodeColor((n) => COLORS[n.group] || "#999")
      .nodeOpacity(0.95)
      .linkColor(() => "rgba(63,125,110,0.18)")
      .linkWidth(0.5)
      .linkDirectionalParticles(1)
      .linkDirectionalParticleWidth(1.4)
      .linkDirectionalParticleColor(() => "#3f7d6e")
      .onNodeClick((n) => { if (n.group === "unit") location.hash = "#/unit/" + enc(n.id); else if (n.group !== "root") location.hash = "#/search/" + enc(n.label); });
    Graph.width(stage.clientWidth).height(stage.clientHeight);
    window.addEventListener("resize", () => Graph.width(stage.clientWidth).height(stage.clientHeight), { passive: true });
  }

  // ── view: ABOUT ────────────────────────────────────────────────────────────────
  async function renderAbout() {
    const v = elView();
    let fr = FRESHNESS;
    try { fr = fr || await api("/freshness"); } catch (e) { console.error("/freshness failed:", e); }
    const c = (fr && fr.counts) || {};
    const rows = [
      ["NIH RePORTER", c.nih_grants, "Federal biomedical grants"],
      ["NSF Awards", c.nsf_awards, "National Science Foundation awards"],
      ["USAspending", c.usaspending, "Federal contracts & grants"],
      ["ClinicalTrials.gov", c.clinical_trials, "Industry-sponsored trials"],
      ["Crossref", c.crossref_papers, "Published papers w/ UNC affiliation"],
    ].filter(([, n]) => n != null);
    v.innerHTML = `<div class="page wrap">
      <div class="page-head"><span class="eyebrow">Methodology</span><h1 class="page-title">How this works</h1>
        <p class="page-sub">A precomputed graph of UNC–Chapel Hill's public research footprint, served live by a same-origin API. No LLM in the data path; entity resolution is deterministic; every source is free, public and keyless.</p></div>
      ${coverageBar()}
      <div class="grid">${rows.map(([name, n, desc]) => `<div class="card"><div class="card-top"><h3>${esc(name)}</h3></div><div class="bignum" style="margin-top:8px">${(+n).toLocaleString()}</div><div class="meta">${esc(desc)}</div></div>`).join("")}</div>
      <div class="page-head" style="margin-top:36px"><h2 class="page-title" style="font-size:22px">Confidence tiers</h2></div>
      <div class="grid">
        <div class="card"><span class="badge confirmed">confirmed</span><p class="meta" style="margin-top:10px">A structured identifier matched — SEC CIK, ORCID, or a named trial sponsor. High trust.</p></div>
        <div class="card"><span class="badge probable">probable</span><p class="meta" style="margin-top:10px">A normalized-name match only. Plausible, but not anchored to a unique identifier.</p></div>
      </div>
      <p class="page-sub" style="margin-top:28px">Graph built <b>${esc(fr && fr.built_at ? fmtDate(fr.built_at) : "—")}</b>. Anchor: UNC–Chapel Hill (ROR <a href="https://ror.org/0130frc33" target="_blank" rel="noopener">0130frc33</a>).</p>
    </div>`;
  }

  // ── router ─────────────────────────────────────────────────────────────────────
  function parseHash() {
    let h = location.hash.replace(/^#\/?/, "");
    let queryStr = "";
    const qi = h.indexOf("?");
    if (qi >= 0) { queryStr = h.slice(qi + 1); h = h.slice(0, qi); }
    const query = {};
    queryStr.split("&").forEach((kv) => { if (!kv) return; const [k, val] = kv.split("="); query[decodeURIComponent(k)] = decodeURIComponent(val || ""); });
    const parts = h.split("/").filter(Boolean).map(decodeURIComponent);
    return { parts, query };
  }

  async function route() {
    const { parts, query } = parseHash();
    const name = parts[0] || "";
    document.querySelectorAll(".nav-link").forEach((a) => a.classList.toggle("active", (a.dataset.route || "") === name));
    window.scrollTo(0, 0);
    try {
      switch (name) {
        case "": return await renderHome();
        case "search": return await renderSearch(parts.slice(1).join("/") || "");
        case "units": return await renderUnits();
        case "unit": return await renderUnit(parts[1], parts[2]);
        case "partnerships": return await renderPartnerships(query);
        case "faculty": return await renderFaculty();
        case "network": return await renderNetwork();
        case "about": return await renderAbout();
        default: elView().innerHTML = `<div class="page wrap">${emptyHTML("Page not found", "That route doesn't exist. Head back to search.")}</div>`;
      }
    } catch (e) {
      console.error("route render failed:", name, e);
      elView().innerHTML = `<div class="page wrap">${errorHTML(e)}</div>`;
    }
  }

  // ── boot: health check + freshness, then route ──────────────────────────────────
  async function boot() {
    const statusEl = document.getElementById("api-status");
    Promise.resolve().then(async () => {
      try {
        const h = await api("/health");
        const ok = h.status === "ok";
        statusEl.className = "api-status " + (ok ? "ok" : "down");
        statusEl.innerHTML = `<span class="api-dot"></span>${ok ? "Live API" : "API degraded"}`;
      } catch (e) {
        console.error("/health failed:", e);
        statusEl.className = "api-status down";
        statusEl.innerHTML = `<span class="api-dot"></span>API offline`;
      }
    });
    try { FRESHNESS = await api("/freshness"); const fb = document.getElementById("footer-built"); if (fb && FRESHNESS.built_at) fb.textContent = fmtDate(FRESHNESS.built_at); }
    catch (e) { console.error("/freshness failed:", e); }
    window.addEventListener("hashchange", route);
    route();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
