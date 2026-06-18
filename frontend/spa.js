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
    // Always fetch with our own controller (so the timeout fires); chain any
    // caller-supplied signal into it so an external abort still cancels.
    if (signal) {
      if (signal.aborted) ctrl.abort(signal.reason);
      else signal.addEventListener("abort", () => ctrl.abort(signal.reason), { once: true });
    }
    try {
      const res = await fetch(API + path, { signal: ctrl.signal, headers: { Accept: "application/json" } });
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
  const esc = (s) => (s == null ? "" : String(s).replace(/[&<>"'`]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;", "`": "&#96;" }[c])));
  const fmtUSD = (n) => { if (!n) return ""; n = +n; if (n >= 1e9) return "$" + (n / 1e9).toFixed(1) + "B"; if (n >= 1e6) return "$" + (n / 1e6).toFixed(1) + "M"; if (n >= 1e3) return "$" + (n / 1e3).toFixed(0) + "K"; return "$" + n; };
  const fmtDate = (d) => { if (!d) return ""; try { const dt = new Date(d); if (isNaN(dt)) return d; return dt.toLocaleDateString(undefined, { year: "numeric", month: "short", day: d.length > 7 ? "numeric" : undefined }); } catch { return d; } };
  const EDGE_LABEL = { grant: "Grant", paper: "Paper", trial: "Trial", contract: "Contract", patent: "Patent" };
  // ── confidence / verification: self-explaining badges (hover = what it means) ──
  const CONF_TIP = {
    confirmed: "Identity confirmed — matched a unique SEC filer (CIK). Verified against ClinicalTrials.gov. High trust.",
    probable: "Matched by company name (no SEC CIK), but linked to UNC by public clinical-trial records below — open one to verify.",
  };
  const TIER_TIP = {
    Verified: "A ClinicalTrials.gov record names this company as sponsor/collaborator on a trial UNC ran.",
    Reported: "A co-authored publication links a UNC author with a company-affiliated author.",
    Inferred: "A weaker, indirect signal — treat as a lead.",
  };
  const confBadge = (tier) => `<span class="badge ${esc(tier || "probable")}" title="${esc(CONF_TIP[tier] || "")}">${esc(tier || "probable")}</span>`;
  const tierBadge = (t) => `<span class="badge ${esc(t || "")}" title="${esc(TIER_TIP[t] || "")}">${esc(t || "")}</span>`;

  // ── data verification: each data type cross-checked against an authoritative
  //    public source (results of the source-verification passes). ──
  const VERIFY = [
    { src: "SEC EDGAR", what: "Company identity", detail: "All 84 SEC-matched companies' CIKs resolve to the named filer in SEC EDGAR (100%)." },
    { src: "ClinicalTrials.gov", what: "Company ↔ UNC links", detail: "All 307 companies and 664 clinical-trial partnerships confirmed — the company is a sponsor/collaborator and UNC is on the trial." },
    { src: "PubMed", what: "Publications", detail: "338 of 360 co-authored papers are PubMed-indexed (93%); the rest are real papers outside PubMed's scope (computer science, chemistry, materials)." },
    { src: "UNC official website", what: "Schools & units", detail: "159 unit profiles sourced from official UNC pages; every unit website link checked and fixed." },
  ];
  const verifiedStrip = () =>
    `<a class="verified-strip" href="#/about" title="See how the data is verified">
      <span class="vs-check">✓</span> Source-verified — <b>SEC&nbsp;EDGAR</b> · <b>PubMed</b> · <b>ClinicalTrials.gov</b> · <b>UNC.edu</b>
      <span class="vs-more">How&nbsp;we&nbsp;verify →</span>
    </a>`;
  // copy a shareable link to the current (filtered) view — wired globally in boot()
  const copyLinkBtn = () => `<button class="btn ghost" data-copylink title="Copy a shareable link to this view">⧉ Copy link</button>`;
  const loadingHTML = (label) => `<div class="loading"><div class="spinner"></div>${esc(label || "Loading…")}</div>`;
  // content-shaped loading placeholders (perceived-speed > spinner)
  const skeletonGrid = (n = 8) => `<div class="grid">${Array.from({ length: n }, () => `<div class="card sk-card"><div class="sk sk-line w55"></div><div class="sk sk-line w90"></div><div class="sk sk-line w35"></div></div>`).join("")}</div>`;
  const skeletonTable = (n = 10) => `<div class="table-wrap sk-wrap">${Array.from({ length: n }, () => `<div class="sk-row"><div class="sk sk-line w30"></div><div class="sk sk-line w16"></div><div class="sk sk-line w44"></div><div class="sk sk-line w10"></div></div>`).join("")}</div>`;
  function errorHTML(err, ctx) { const f = friendlyError(err, ctx); return `<div class="error"><h3>${esc(f.title)}</h3><p>${esc(f.msg)}</p><p style="margin-top:14px"><button class="btn ghost" data-reload>Retry</button></p></div>`; }
  function emptyHTML(title, msg) { return `<div class="empty"><h3>${esc(title)}</h3><p>${esc(msg)}</p></div>`; }
  const enc = encodeURIComponent;
  // recent searches (palette + future use), capped & deduped in localStorage
  const RECENT_KEY = "iig_recent";
  const recentList = () => { try { return JSON.parse(localStorage.getItem(RECENT_KEY) || "[]"); } catch { return []; } };
  const pushRecent = (q) => { q = (q || "").trim(); if (!q) return; try { let l = recentList().filter((x) => x.toLowerCase() !== q.toLowerCase()); l.unshift(q); localStorage.setItem(RECENT_KEY, JSON.stringify(l.slice(0, 6))); } catch {} };

  // shareable filters: rewrite the URL query in place (no navigation/re-render)
  const syncQuery = (route, params) => {
    const qs = Object.entries(params).filter(([, v]) => v != null && v !== "").map(([k, v]) => enc(k) + "=" + enc(v)).join("&");
    try { history.replaceState(null, "", "#/" + route + (qs ? "?" + qs : "")); } catch {}
  };
  // keyboard list nav: ↑/↓ highlight a row, ↵ activates it. Self-unwires when the
  // container is gone (route change). Skips when the ⌘K palette is open.
  function wireListNav(containerId, rowSelector, onEnter) {
    let idx = -1;
    const rows = () => { const c = document.getElementById(containerId); return c ? Array.from(c.querySelectorAll(rowSelector)) : null; };
    const move = (d) => { const r = rows(); if (!r || !r.length) return; r.forEach((x) => x.classList.remove("kbd-row")); idx = (idx + d + r.length) % r.length; r[idx].classList.add("kbd-row"); r[idx].scrollIntoView({ block: "nearest" }); };
    function onKey(e) {
      const r = rows();
      if (r === null) { document.removeEventListener("keydown", onKey); return; }  // view gone
      if (document.getElementById("cmdk")) return;
      if (e.key === "ArrowDown") { e.preventDefault(); move(1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); move(-1); }
      else if (e.key === "Enter" && idx >= 0 && r[idx]) { e.preventDefault(); onEnter(r[idx]); }
    }
    document.addEventListener("keydown", onKey);
  }

  // ── write helpers: edit/add/delete against the keyless write API ─────────────
  // Edit token: only required when the server has EDIT_TOKEN configured. We
  // prompt lazily on the first 401 and remember it in localStorage.
  function promptEditToken() {
    const t = (window.prompt("Enter the edit access token to make changes:") || "").trim();
    if (t) localStorage.setItem("iig_edit_token", t);
    return t;
  }

  async function apiWrite(method, path, bodyObj, _retried) {
    const token = localStorage.getItem("iig_edit_token") || "";
    const res = await fetch(API + path, {
      method,
      headers: { "Content-Type": "application/json", Accept: "application/json", ...(token ? { "X-Edit-Token": token } : {}) },
      body: bodyObj ? JSON.stringify(bodyObj) : undefined,
    });
    let data = null;
    try { data = await res.json(); } catch {}
    if (res.status === 401 && !_retried) {
      localStorage.removeItem("iig_edit_token");
      if (promptEditToken()) return apiWrite(method, path, bodyObj, true);
    }
    if (!res.ok) {
      const err = new Error((data && (data.message || data.error)) || ("HTTP " + res.status));
      err.status = res.status; err.data = data;
      throw err;
    }
    return data;
  }

  let _toastTimer = null;
  function toast(msg, kind = "ok") {
    let el = document.getElementById("toast");
    if (!el) { el = document.createElement("div"); el.id = "toast"; document.body.appendChild(el); }
    el.className = "toast " + kind + " show";
    el.textContent = msg;
    clearTimeout(_toastTimer);
    _toastTimer = setTimeout(() => { el.className = "toast " + kind; }, 2600);
  }

  // picklists (prompt-specified, plus any values already present in the data)
  const AREA_OPTS = ["Events", "Scholarships", "Talent Pipeline", "Programs", "Research Grant", "Clinical Trial", "Co-authored Publication"];
  const STATUS_OPTS = ["Active", "Past", "In Discussion", "Lapsed"];
  const RECURRING_OPTS = ["", "one-time", "annual", "ongoing"];
  const FUNDING_TYPE_OPTS = ["", "grant", "gift", "sponsorship", "in-kind", "none"];
  const TIER_OPTS = ["Verified", "Reported", "Inferred"];
  const UNIT_TYPE_OPTS = ["School", "College", "Department", "Center", "Institute", "Lab", "Program"];

  const optionsHTML = (opts, val) => {
    const list = opts.slice();
    if (val != null && val !== "" && !list.includes(val)) list.unshift(val); // keep current value selectable
    return list.map((o) => `<option value="${esc(o)}" ${o === (val ?? "") ? "selected" : ""}>${esc(o === "" ? "—" : o)}</option>`).join("");
  };

  function fieldHTML(f) {
    if (f.type === "select") return `<label class="fld"><span>${esc(f.label)}</span><select name="${f.key}">${optionsHTML(f.options || [], f.value)}</select></label>`;
    if (f.type === "textarea") return `<label class="fld wide"><span>${esc(f.label)}</span><textarea name="${f.key}" rows="2" placeholder="${esc(f.placeholder || "")}">${esc(f.value || "")}</textarea></label>`;
    return `<label class="fld"><span>${esc(f.label)}</span><input name="${f.key}" type="${f.type || "text"}" value="${esc(f.value ?? "")}" placeholder="${esc(f.placeholder || "")}" /></label>`;
  }

  // Modal form. fields: [{key,label,type,options,value,placeholder}]. onSubmit(values)→Promise.
  function openModal(title, fields, onSubmit) {
    const back = document.createElement("div");
    back.className = "modal-back";
    back.innerHTML = `<div class="modal" role="dialog" aria-modal="true" aria-label="${esc(title)}">
      <div class="modal-head"><h3>${esc(title)}</h3><button class="modal-x" aria-label="Close">×</button></div>
      <form class="modal-form">${fields.map(fieldHTML).join("")}
        <div class="modal-actions"><button type="button" class="btn ghost" data-cancel>Cancel</button><button type="submit" class="btn">Save</button></div>
      </form></div>`;
    document.body.appendChild(back);
    const close = () => back.remove();
    back.querySelector(".modal-x").onclick = close;
    back.querySelector("[data-cancel]").onclick = close;
    back.addEventListener("click", (e) => { if (e.target === back) close(); });
    document.addEventListener("keydown", function esckey(e) { if (e.key === "Escape") { close(); document.removeEventListener("keydown", esckey); } });
    back.querySelector(".modal-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const out = {};
      fields.forEach((f) => { const el = back.querySelector(`[name="${f.key}"]`); let val = el ? el.value : ""; if (f.type === "number") val = val === "" ? null : Number(val); out[f.key] = val; });
      const btn = back.querySelector('button[type="submit"]');
      btn.disabled = true; btn.textContent = "Saving…";
      try { await onSubmit(out); close(); }
      catch (err) { toast(err.message || "Save failed", "err"); btn.disabled = false; btn.textContent = "Save"; }
    });
    setTimeout(() => { const first = back.querySelector("input,select,textarea"); if (first) first.focus(); }, 30);
  }

  // ── downloads: Excel (.xlsx) + PDF, generated client-side from CDN libs ───────
  // Libraries are lazy-loaded on first click (keyless, free). Exports reflect the
  // rows currently shown (after filters/search), not the whole dataset.
  const XLSX_CDN = "https://cdn.jsdelivr.net/npm/xlsx@0.18.5/dist/xlsx.full.min.js";
  const JSPDF_CDN = "https://cdn.jsdelivr.net/npm/jspdf@2.5.1/dist/jspdf.umd.min.js";
  const AUTOTABLE_CDN = "https://cdn.jsdelivr.net/npm/jspdf-autotable@3.8.2/dist/jspdf.plugin.autotable.min.js";
  // Subresource Integrity — pin each CDN script to a known hash so a tampered
  // CDN response is rejected by the browser.
  const SRI = {
    [XLSX_CDN]: "sha384-vtjasyidUo0kW94K5MXDXntzOJpQgBKXmE7e2Ga4LG0skTTLeBi97eFAXsqewJjw",
    [JSPDF_CDN]: "sha384-JcnsjUPPylna1s1fvi1u12X5qjY5OL56iySh75FdtrwhO/SWXgMjoVqcKyIIWOLk",
    [AUTOTABLE_CDN]: "sha384-fCAW/rDWORTbQXSiB7mOg0QtQ5c+r0f544y6XoKjuVva0nMBlCpNUjiFeG5iMdS3",
  };

  function loadScript(src) {
    return new Promise((resolve, reject) => {
      if ([...document.scripts].some((s) => s.src === src)) return resolve();
      const s = document.createElement("script");
      s.src = src; s.async = true;
      if (SRI[src]) { s.integrity = SRI[src]; s.crossOrigin = "anonymous"; s.referrerPolicy = "no-referrer"; }
      s.onload = () => resolve();
      s.onerror = () => reject(new Error("Couldn't load export library (network blocked?)"));
      document.head.appendChild(s);
    });
  }

  // Only allow http(s) (and in-app hash) links to be rendered as clickable —
  // blocks javascript:/data: and other script-bearing URI schemes.
  function safeUrl(u) {
    if (!u) return "";
    const s = String(u).trim();
    if (/^https?:\/\//i.test(s) || s.startsWith("#") || s.startsWith("/")) return s;
    return "";
  }

  const cellVal = (col, row) => { const v = typeof col.get === "function" ? col.get(row) : row[col.key]; return v == null ? "" : v; };

  async function exportExcel(filename, columns, rows) {
    await loadScript(XLSX_CDN);
    const aoa = [columns.map((c) => c.label)].concat(rows.map((r) => columns.map((c) => cellVal(c, r))));
    const ws = window.XLSX.utils.aoa_to_sheet(aoa);
    ws["!cols"] = columns.map((c) => ({ wch: c.w || 18 }));
    const wb = window.XLSX.utils.book_new();
    window.XLSX.utils.book_append_sheet(wb, ws, "Data");
    window.XLSX.writeFile(wb, filename.replace(/[^\w.-]+/g, "_") + ".xlsx");
  }

  async function exportPDF(title, columns, rows) {
    await loadScript(JSPDF_CDN);
    await loadScript(AUTOTABLE_CDN);
    const doc = new window.jspdf.jsPDF({ orientation: "landscape", unit: "pt", format: "a4" });
    doc.setFontSize(15); doc.text(title, 40, 40);
    doc.setFontSize(9); doc.setTextColor(120);
    doc.text(`${rows.length} rows · exported ${new Date().toLocaleDateString()}`, 40, 56);
    doc.autoTable({
      startY: 70,
      head: [columns.map((c) => c.label)],
      body: rows.map((r) => columns.map((c) => String(cellVal(c, r)))),
      styles: { fontSize: 7, cellPadding: 3, overflow: "linebreak" },
      headStyles: { fillColor: [29, 29, 31], textColor: 255 },
      alternateRowStyles: { fillColor: [248, 248, 250] },
      margin: { left: 40, right: 40 },
    });
    doc.save(title.replace(/[^\w]+/g, "_") + ".pdf");
  }

  // Two download buttons + their wiring. getData() → {title, filename, columns, rows}.
  const exportButtons = (id) =>
    `<button class="btn ghost" id="${id}-xls" title="Download as Excel">⤓ Excel</button>` +
    `<button class="btn ghost" id="${id}-pdf" title="Download as PDF">⤓ PDF</button>`;

  function wireExport(id, getData) {
    const run = async (kind, btn) => {
      const { title, filename, columns, rows } = getData();
      if (!rows || !rows.length) { toast("Nothing to export", "err"); return; }
      const label = btn.textContent; btn.disabled = true; btn.textContent = "Preparing…";
      try {
        if (kind === "excel") await exportExcel(filename, columns, rows);
        else await exportPDF(title, columns, rows);
        toast(kind === "excel" ? "Excel downloaded" : "PDF downloaded");
      } catch (e) { toast(e.message || "Export failed", "err"); }
      finally { btn.disabled = false; btn.textContent = label; }
    };
    const xls = document.getElementById(id + "-xls"), pdf = document.getElementById(id + "-pdf");
    if (xls) xls.addEventListener("click", () => run("excel", xls));
    if (pdf) pdf.addEventListener("click", () => run("pdf", pdf));
  }

  let FRESHNESS = null;
  // Network view: render epoch + in-flight fetch handle, so a route change that
  // re-enters #/network can supersede a still-loading render (prevents stacking
  // two ForceGraph3D instances on the same #graph-3d node).
  let NET_TOKEN = 0, NET_ABORT = null;

  // ── shared graph cache (companies+units), reused by Network, the company index
  //    and search autocomplete so /api/graph is fetched at most once. ──
  let GRAPH_CACHE = null, GRAPH_INFLIGHT = null;
  async function getGraph() {
    if (GRAPH_CACHE) return GRAPH_CACHE;
    if (!GRAPH_INFLIGHT) {
      GRAPH_INFLIGHT = api("/api/graph", { timeoutMs: 20000 })
        .then((d) => { GRAPH_CACHE = d; GRAPH_INFLIGHT = null; return d; })
        .catch((e) => { GRAPH_INFLIGHT = null; throw e; });
    }
    return GRAPH_INFLIGHT;
  }

  // fuzzy/typo-tolerant company ranking (no deps): exact > prefix > substring >
  // in-order subsequence; ties broken by record volume.
  function subseqGaps(q, name) {
    let i = 0, gaps = 0, last = -1;
    for (let j = 0; j < name.length && i < q.length; j++) {
      if (name[j] === q[i]) { if (last >= 0) gaps += j - last - 1; last = j; i++; }
    }
    return i === q.length ? gaps : null;
  }
  function rankCompanies(query, companies, limit = 8) {
    const q = (query || "").toLowerCase().trim();
    if (!q || !companies) return [];
    const out = [];
    for (const c of companies) {
      const name = (c.name || "").toLowerCase();
      if (!name) continue;
      let score = 0;
      if (name === q) score = 1000;
      else if (name.startsWith(q)) score = 600 - name.length;
      else { const idx = name.indexOf(q); if (idx >= 0) score = 400 - idx - name.length * 0.1;
        else if (q.length >= 3) { const g = subseqGaps(q, name); if (g != null) score = 150 - g; } }
      if (score > 0) out.push({ c, score: score + Math.min(c.total_edges || 0, 60) * 0.4 });
    }
    out.sort((a, b) => b.score - a.score);
    return out.slice(0, limit).map((x) => x.c);
  }

  // Attach a type-ahead dropdown to a search <input>. Company list is lazy-loaded
  // from the cached graph on first focus. Enter/click on a company → its footprint;
  // a "Search '<q>'" row always lets topics through.
  function attachAutocomplete(input) {
    if (!input) return;
    let companies = null, loading = false, items = [], active = -1, box = null;
    const wrap = input.closest(".search-box") || input.parentElement;
    if (wrap && getComputedStyle(wrap).position === "static") wrap.style.position = "relative";
    const close = () => { if (box) { box.remove(); box = null; } active = -1; };
    const go = (q) => { const s = (q || "").trim(); if (s) { close(); location.hash = "#/search/" + enc(s); } };
    const render = () => {
      const q = input.value.trim();
      const matches = rankCompanies(q, companies);
      items = matches.map((c) => ({ kind: "company", c })).concat(q ? [{ kind: "query", q }] : []);
      if (!items.length) { close(); return; }
      if (!box) { box = document.createElement("div"); box.className = "ac-box"; wrap.appendChild(box); }
      active = -1;
      box.innerHTML = items.map((it, i) => it.kind === "company"
        ? `<div class="ac-item" data-i="${i}"><span class="ac-name">${esc(it.c.name)}</span><span class="ac-meta">${confBadge(it.c.confidence)} ${(it.c.total_edges || 0).toLocaleString()} records</span></div>`
        : `<div class="ac-item ac-query" data-i="${i}">Search “<b>${esc(it.q)}</b>” as a topic →</div>`).join("");
      box.querySelectorAll(".ac-item").forEach((el) => {
        el.addEventListener("mousedown", (e) => { e.preventDefault(); const it = items[+el.dataset.i]; go(it.kind === "company" ? it.c.name : it.q); });
      });
    };
    const move = (d) => { if (!box) return; const els = box.querySelectorAll(".ac-item"); if (!els.length) return; active = (active + d + els.length) % els.length; els.forEach((el, i) => el.classList.toggle("active", i === active)); };
    const ensureLoaded = () => {
      if (companies !== null || loading) return;
      loading = true;
      getGraph().then((g) => { companies = g.companies || []; render(); }).catch(() => { companies = []; });
    };
    input.addEventListener("focus", () => { ensureLoaded(); if (companies) render(); });
    input.addEventListener("input", () => { ensureLoaded(); render(); });
    input.addEventListener("keydown", (e) => {
      if (!box) return;
      if (e.key === "ArrowDown") { e.preventDefault(); move(1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); move(-1); }
      else if (e.key === "Enter" && active >= 0) { e.preventDefault(); const it = items[active]; go(it.kind === "company" ? it.c.name : it.q); }
      else if (e.key === "Escape") close();
    });
    input.addEventListener("blur", () => setTimeout(close, 120));
  }
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
        ${verifiedStrip()}
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
    attachAutocomplete($("#q"));

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
      const sb = $("#statbar");                 // may be gone if the user navigated away mid-fetch
      if (sb) sb.innerHTML = pills.map(([n, l]) => `<span class="stat-pill"><b>${(+n).toLocaleString()}</b> ${esc(l)}</span>`).join("");
    } catch (e) { console.error("home /stats failed:", e); const sb = $("#statbar"); if (sb) sb.innerHTML = ""; }
  }

  // ── view: SEARCH RESULTS ─────────────────────────────────────────────────────
  async function renderSearch(q) {
    pushRecent(q);
    const v = elView();
    v.innerHTML = `<div class="page wrap">
      <form class="search-box" id="search-form" style="margin:0 0 24px">
        <input id="q" type="search" value="${esc(q)}" aria-label="Search" />
        <button class="go" type="submit">Search</button>
      </form>
      <div id="search-results">${loadingHTML("Querying the research graph…")}</div>
    </div>`;
    $("#search-form").addEventListener("submit", (e) => { e.preventDefault(); const nq = $("#q").value.trim(); if (nq) location.hash = "#/search/" + enc(nq); });
    attachAutocomplete($("#q"));

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
            <p class="page-sub">${tier === "confirmed"
              ? `<b>Identity SEC-confirmed</b>${co.cik ? ` (CIK ${esc(co.cik)})` : ""} · `
              : `<b>Matched by name</b> — verify via the records below · `}${(co.total_edges || 0).toLocaleString()} public records linking it to UNC across ${(co.units || []).length} unit(s).</p>
          </div>
          ${confBadge(tier)}
        </div></div>`;
      html += (co.units || []).map((u) => {
        const counts = Object.entries(u.counts || {}).map(([t, n]) => `<span class="ev-count">${n} ${esc(EDGE_LABEL[t] || t)}${n > 1 ? "s" : ""}</span>`).join("");
        const samples = (u.samples || []).slice(0, 5).map((s) => `
          <div class="ev-sample">
            <span class="ev-type">${esc(EDGE_LABEL[s.type] || s.type)}</span>
            <span style="flex:1">${safeUrl(s.url) ? `<a href="${esc(safeUrl(s.url))}" target="_blank" rel="noopener noreferrer">${esc(s.title || s.url)}</a>` : esc(s.title || "")}</span>
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

    // build an export of whatever this search surfaced
    let xTitle = "", xCols = null, xRows = null;
    if (co) {
      xTitle = `${co.name} — UNC footprint`;
      xRows = (co.units || []).flatMap((u) => (u.samples || []).map((s) => ({
        unit: u.unit_name || u.unit_id, type: EDGE_LABEL[s.type] || s.type || "", title: s.title || "", date: s.date || "", url: s.url || "",
      })));
      if (!xRows.length) xRows = (co.units || []).map((u) => ({ unit: u.unit_name || u.unit_id, type: "", title: "", date: "", url: "" }));
      xCols = [{ label: "UNC Unit", key: "unit", w: 30 }, { label: "Evidence Type", key: "type", w: 14 }, { label: "Title", key: "title", w: 60 }, { label: "Date", key: "date", w: 12 }, { label: "Source URL", key: "url", w: 40 }];
    } else if (topical.length) {
      xTitle = `Topical matches — ${q}`;
      xRows = topical.map((t) => ({ unit: t.unit_name || t.unit_id, score: (t.score || 0).toFixed(3), keywords: (t.hits || []).join(", ") }));
      xCols = [{ label: "UNC Unit", key: "unit", w: 30 }, { label: "Match Score", key: "score", w: 12 }, { label: "Matched Keywords", key: "keywords", w: 50 }];
    }
    if (xRows && xRows.length) html = `<div class="export-row">${copyLinkBtn()}${exportButtons("s-exp")}</div>` + html;
    out.innerHTML = html;
    if (xRows && xRows.length) wireExport("s-exp", () => ({ title: xTitle, filename: xTitle, columns: xCols, rows: xRows }));
    // ↑/↓/↵ through result cards/units → open the unit
    wireListNav("search-results", ".ev-unit, a.card", (el) => { const a = el.matches("a") ? el : el.querySelector("a[href]"); if (a) location.hash = a.getAttribute("href"); });
  }

  // ── view: COMPANIES INDEX (browse/sort/filter; shareable, keyboard-nav, expandable) ──
  async function renderCompanies(query = {}) {
    const v = elView();
    v.innerHTML = `<div class="page wrap">
      <div class="page-head"><div class="card-top"><div><span class="eyebrow">Directory</span>
        <h1 class="page-title">Companies</h1>
        <p class="page-sub">Every external organisation linked to UNC by a public record. Filter and sort, then expand a row for the evidence — or open its full footprint. Use <kbd class="ikbd">↑</kbd><kbd class="ikbd">↓</kbd> + <kbd class="ikbd">↵</kbd>.</p></div>
        <div class="head-actions">${copyLinkBtn()}${exportButtons("co-exp")}</div></div></div>
      ${coverageBar()}
      ${dataNote(COMPANY_NOTE)}
      <div class="toolbar">
        <input type="search" id="co-q" placeholder="Filter companies by name…" value="${esc(query.q || "")}" />
        <select id="co-conf"><option value="">All confidence</option><option value="confirmed">Confirmed</option><option value="probable">Probable</option></select>
        <select id="co-sort"><option value="records">Sort: Most records</option><option value="name">Sort: Name</option><option value="units">Sort: Most units</option></select>
        <span class="count" id="co-count"></span>
      </div>
      <div id="co-body">${skeletonTable(12)}</div>
    </div>`;
    if (query.conf) $("#co-conf").value = query.conf;
    if (query.sort) $("#co-sort").value = query.sort;

    let g;
    try { g = await getGraph(); }
    catch (e) { console.error("/api/graph (companies) failed:", e); const el = $("#co-body"); if (el) el.innerHTML = errorHTML(e); return; }
    if (!$("#co-body")) return;   // navigated away during the fetch

    const companies = g.companies || [];
    const uname = Object.fromEntries((g.units || []).map((u) => [u.id, u.name]));
    const linkedUnits = (c) => (c.units || []).map((cu) => uname[cu.unit_id] || cu.unit_id);
    const secUrl = (cik) => `https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=${enc(cik)}&type=&dateb=&owner=include&count=40`;
    const detailHTML = (c) => {
      const conf = c.confidence === "confirmed"
        ? `<b>Identity confirmed</b> in SEC EDGAR. <a href="${esc(secUrl(c.cik))}" target="_blank" rel="noopener noreferrer">SEC filings (CIK ${esc(c.cik)}) ↗</a>`
        : `<b>Matched by name</b> (no SEC CIK) — the UNC link is from the public clinical-trial records below.`;
      const units = (c.units || []).map((cu) => {
        const nm = uname[cu.unit_id] || cu.unit_id;
        const cnt = Object.entries(cu.counts || {}).map(([t, n]) => `${n} ${EDGE_LABEL[t] || t}${n > 1 ? "s" : ""}`).join(", ");
        const s = (cu.samples || []).find((x) => x.url) || (cu.samples || [])[0];
        const link = s && safeUrl(s.url) ? ` — <a href="${esc(safeUrl(s.url))}" target="_blank" rel="noopener noreferrer">${esc((s.title || "record").slice(0, 64))} ↗</a>` : "";
        return `<li><a href="#/unit/${enc(cu.unit_id)}">${esc(nm)}</a>${cnt ? ` <span class="meta">(${esc(cnt)})</span>` : ""}${link}</li>`;
      }).join("");
      return `<div class="co-ev"><p>${conf}</p>${units ? `<ul class="co-ev-units">${units}</ul>` : ""}<a class="src-link" href="#/search/${enc(c.name)}">View full footprint →</a></div>`;
    };
    const companyTable = (rows) => `<div class="table-wrap"><table class="data co-table">
      <thead><tr><th aria-label="expand"></th><th>Company</th><th>Confidence</th><th>Records</th><th>Linked units</th></tr></thead>
      <tbody>${rows.map((c) => `<tr class="co-row">
        <td class="co-exp-cell"><span class="co-chev">▸</span></td>
        <td><a href="#/search/${enc(c.name)}" class="co-name"><strong>${esc(c.name)}</strong></a></td>
        <td>${confBadge(c.confidence)}</td>
        <td>${(c.total_edges || 0).toLocaleString()}</td>
        <td>${linkedUnits(c).map((n) => esc(n)).join(", ") || "—"}</td>
      </tr>
      <tr class="co-detail" hidden><td></td><td colspan="4">${detailHTML(c)}</td></tr>`).join("")}</tbody></table></div>`;

    let lastRows = [];
    const CO_COLS = [
      { label: "Company", key: "name", w: 30 }, { label: "Confidence", key: "confidence", w: 14 },
      { label: "Records", key: "total_edges", w: 12 }, { label: "Linked Units", get: (c) => linkedUnits(c).join("; "), w: 60 },
    ];
    const draw = () => {
      const q = ($("#co-q").value || "").toLowerCase();
      const conf = $("#co-conf").value, sort = $("#co-sort").value;
      const rows = companies.filter((c) => (!conf || (c.confidence || "probable") === conf) && (!q || (c.name || "").toLowerCase().includes(q)));
      rows.sort((a, b) => sort === "name" ? (a.name || "").localeCompare(b.name || "")
        : sort === "units" ? (b.units || []).length - (a.units || []).length
        : (b.total_edges || 0) - (a.total_edges || 0));
      lastRows = rows;
      $("#co-count").textContent = `${rows.length.toLocaleString()} of ${companies.length.toLocaleString()} companies`;
      $("#co-body").innerHTML = rows.length ? companyTable(rows) : emptyHTML("No companies match", "Try a different name or confidence filter.");
      syncQuery("companies", { q: $("#co-q").value.trim(), conf, sort: sort === "records" ? "" : sort });
    };
    const toggleRow = (row) => {
      const d = row.nextElementSibling; if (!d || !d.classList.contains("co-detail")) return;
      const open = !d.hasAttribute("hidden");
      if (open) d.setAttribute("hidden", ""); else d.removeAttribute("hidden");
      row.classList.toggle("open", !open);
      const ch = row.querySelector(".co-chev"); if (ch) ch.textContent = open ? "▸" : "▾";
    };
    $("#co-body").addEventListener("click", (e) => { if (e.target.closest("a")) return; const row = e.target.closest("tr.co-row"); if (row) toggleRow(row); });
    wireExport("co-exp", () => ({ title: "UNC-Linked Companies", filename: "UNC_Companies", columns: CO_COLS, rows: lastRows }));
    ["co-q", "co-conf", "co-sort"].forEach((id) => { const el = document.getElementById(id); el.addEventListener(id === "co-q" ? "input" : "change", draw); });
    wireListNav("co-body", "tr.co-row", toggleRow);
    draw();
  }

  // ── view: UNITS MASTER LIST (editable) ───────────────────────────────────────
  let UNITS_CACHE = null;

  // Whether edits will persist (backend writable: KV configured or local FS). On
  // the read-only deployment this is false, so the edit UI is hidden and a
  // read-only note shown instead of inviting edits that would 503.
  let CAN_EDIT = false, HEALTH = null;
  async function getHealth() {
    if (HEALTH) return HEALTH;
    try { HEALTH = await api("/health"); } catch { HEALTH = {}; }
    return HEALTH;
  }
  const readOnlyNote = () => CAN_EDIT ? "" :
    `<div class="ro-note">Viewing published data — editing is disabled on this deployment.</div>`;

  // "how to read this" provenance note — these tables are auto-compiled from
  // public records, so be explicit about what each row actually is.
  const dataNote = (html) => `<div class="data-note"><span class="dn-i">ⓘ</span><span>${html}</span></div>`;
  const PARTNERSHIP_NOTE =
    "Auto-compiled from public records — these are <b>evidence links, not confirmed business partnerships</b>, and no funding figures are implied. Open each row's source to verify it. " +
    "<b>Clinical Trial</b> (tier <i>Verified</i>) = the company is a sponsor/collaborator on a ClinicalTrials.gov study UNC ran. " +
    "<b>Co-authored Publication</b> (tier <i>Reported</i>) = a UNC researcher co-authored a paper with a company-affiliated author.";
  const COMPANY_NOTE =
    "Auto-matched from public records, then source-checked. <b>confirmed</b> = matched a unique SEC filer (CIK); <b>probable</b> = matched by name (no SEC CIK) — but each is linked to UNC by public clinical-trial records you can open in its footprint.";

  // editable cell: contenteditable text that PUTs on blur when changed (or a
  // plain read-only cell when editing is disabled)
  const editCell = (id, field, val, cls) =>
    CAN_EDIT
      ? `<td class="ec ${cls || ""}" contenteditable="true" data-id="${esc(id)}" data-field="${field}" data-orig="${esc(val ?? "")}">${esc(val ?? "")}</td>`
      : `<td class="${cls || ""}">${esc(val ?? "")}</td>`;

  function unitsTable(rows, schools) {
    const schoolOpts = [{ id: "unc:root", name: "University of North Carolina at Chapel Hill" }, ...schools];
    const schoolName = (val) => (schoolOpts.find((s) => s.id === val) || {}).name || val || "";
    const parentSel = (id, val) => CAN_EDIT
      ? `<td><select class="es" data-id="${esc(id)}" data-field="parent_unit_id">${schoolOpts.map((s) => `<option value="${esc(s.id)}" ${s.id === val ? "selected" : ""}>${esc(s.name)}</option>`).join("")}</select></td>`
      : `<td>${esc(schoolName(val))}</td>`;
    const typeSel = (id, val) => CAN_EDIT
      ? `<td><select class="es" data-id="${esc(id)}" data-field="unit_type">${optionsHTML(UNIT_TYPE_OPTS, val)}</select></td>`
      : `<td>${esc(val || "")}</td>`;
    return `<div class="table-wrap"><table class="data edit">
      <thead><tr>
        <th>Name</th><th>Parent School</th><th>Type</th><th>Description / Focus Areas</th>
        <th>Disciplines</th><th>Faculty</th><th>Students</th><th>Research By</th><th>Date</th><th>Notes</th><th></th>
      </tr></thead>
      <tbody>${rows.map((u) => `<tr data-id="${esc(u.unit_id)}">
        ${editCell(u.unit_id, "unit_name", u.unit_name, "strong")}
        ${parentSel(u.unit_id, u.parent_unit_id || "unc:root")}
        ${typeSel(u.unit_id, u.unit_type)}
        ${editCell(u.unit_id, "focus_areas", u.focus_areas || u.description)}
        ${editCell(u.unit_id, "disciplines", u.disciplines)}
        ${editCell(u.unit_id, "faculty_count", u.faculty_count, "num")}
        ${editCell(u.unit_id, "student_count", u.student_count, "num")}
        ${editCell(u.unit_id, "research_by", u.research_by)}
        ${editCell(u.unit_id, "date_of_research", u.date_of_research, "nowrap")}
        ${editCell(u.unit_id, "notes", u.notes)}
        <td class="rowtools"><a class="mini" href="#/unit/${enc(u.unit_id)}" title="Open profile">↗</a>${CAN_EDIT ? `<button class="mini del" data-del-unit="${esc(u.unit_id)}" title="Delete">×</button>` : ""}</td>
      </tr>`).join("")}</tbody></table></div>`;
  }

  async function renderUnits() {
    const v = elView();
    CAN_EDIT = (await getHealth()).writable === true;
    v.innerHTML = `<div class="page wrap">
      <div class="page-head"><div class="card-top"><div><span class="eyebrow">Master list</span><h1 class="page-title">UNC Schools &amp; Units</h1>
        <p class="page-sub">Every school, center, institute and department at UNC–Chapel Hill. ${CAN_EDIT ? "Click any cell to edit — changes save live. " : ""}Open a profile with ↗.</p></div>
        <div class="head-actions">${exportButtons("u-exp")}${CAN_EDIT ? '<button class="btn" id="u-add">＋ Add Unit</button>' : ""}</div></div></div>
      ${coverageBar()}
      ${readOnlyNote()}
      <div class="toolbar">
        <input type="search" id="u-search" placeholder="Search units by name…" />
        <select id="u-type"><option value="">All types</option></select>
        <select id="u-sort"><option value="name">Sort: Name</option><option value="partnerships">Sort: Partnerships</option><option value="faculty">Sort: Faculty size</option></select>
        <span class="count" id="u-count"></span>
      </div>
      <div id="u-grid">${skeletonTable(12)}</div>
    </div>`;

    let units;
    try { units = UNITS_CACHE || (UNITS_CACHE = await api("/units")); }
    catch (e) { console.error("/units failed:", e); const el = $("#u-grid"); if (el) el.innerHTML = errorHTML(e); return; }
    if (!$("#u-grid")) return;   // navigated away during the fetch

    const schools = units.filter((u) => u.unit_type === "School" || u.unit_type === "College")
      .map((u) => ({ id: u.unit_id, name: u.unit_name })).sort((a, b) => a.name.localeCompare(b.name));
    const nameById = Object.fromEntries(units.map((u) => [u.unit_id, u.unit_name]));
    const types = [...new Set(units.map((u) => u.unit_type).filter(Boolean))].sort();
    $("#u-type").innerHTML = `<option value="">All types</option>` + types.map((t) => `<option value="${esc(t)}">${esc(t)}</option>`).join("");

    let lastRows = [];
    const UNIT_EXPORT_COLS = [
      { label: "Name", key: "unit_name", w: 32 },
      { label: "Parent School", get: (u) => nameById[u.parent_unit_id] || "", w: 30 },
      { label: "Type", key: "unit_type", w: 12 },
      { label: "Description / Focus Areas", get: (u) => u.focus_areas || u.description || "", w: 40 },
      { label: "Disciplines", key: "disciplines", w: 28 },
      { label: "Faculty", key: "faculty_count", w: 9 },
      { label: "Students", key: "student_count", w: 9 },
      { label: "Partnerships", key: "partnership_count", w: 11 },
      { label: "Research By", key: "research_by", w: 16 },
      { label: "Date", key: "date_of_research", w: 12 },
      { label: "Notes", key: "notes", w: 30 },
      { label: "Website", key: "website_url", w: 30 },
    ];

    const draw = () => {
      const term = $("#u-search").value.toLowerCase();
      const type = $("#u-type").value;
      const sort = $("#u-sort").value;
      const rows = units.filter((u) => u.unit_id !== "unc:root" && (!type || u.unit_type === type) && (!term || (u.unit_name || "").toLowerCase().includes(term)));
      rows.sort((a, b) => sort === "partnerships" ? (b.partnership_count || 0) - (a.partnership_count || 0)
        : sort === "faculty" ? (b.faculty_count || 0) - (a.faculty_count || 0)
        : (a.unit_name || "").localeCompare(b.unit_name || ""));
      lastRows = rows;
      $("#u-count").textContent = `${rows.length} of ${units.length} units`;
      $("#u-grid").innerHTML = rows.length ? unitsTable(rows, schools) : emptyHTML("No units match", "Try a different name or type, or add a new unit.");
    };
    wireExport("u-exp", () => ({ title: "UNC Schools & Units", filename: "UNC_Units", columns: UNIT_EXPORT_COLS, rows: lastRows }));
    $("#u-search").addEventListener("input", draw);
    $("#u-type").addEventListener("change", draw);
    $("#u-sort").addEventListener("change", draw);

    // inline edit: text cells (blur) + selects (change)
    $("#u-grid").addEventListener("focusout", async (e) => {
      const td = e.target.closest("td.ec"); if (!td) return;
      const val = td.textContent.trim();
      if (val === (td.dataset.orig || "")) return;
      const u = units.find((x) => x.unit_id === td.dataset.id);
      await saveField("/api/units/", td.dataset.id, td.dataset.field, val, u);
      td.dataset.orig = val;
    });
    $("#u-grid").addEventListener("change", async (e) => {
      const sel = e.target.closest("select.es"); if (!sel) return;
      const u = units.find((x) => x.unit_id === sel.dataset.id);
      await saveField("/api/units/", sel.dataset.id, sel.dataset.field, sel.value, u);
    });
    $("#u-grid").addEventListener("click", async (e) => {
      const btn = e.target.closest("[data-del-unit]"); if (!btn) return;
      const id = btn.dataset.delUnit;
      const u = units.find((x) => x.unit_id === id);
      if (!confirm(`Delete “${u ? u.unit_name : id}”? This can't be undone.`)) return;
      try { await apiWrite("DELETE", "/api/units/" + enc(id)); toast("Unit deleted"); UNITS_CACHE = null; units = await api("/units"); draw(); }
      catch (err) { toast(err.message || "Delete failed", "err"); }
    });

    const uAdd = $("#u-add");
    if (uAdd) uAdd.addEventListener("click", () => {
      openModal("Add Unit", [
        { key: "unit_name", label: "Name", placeholder: "e.g. Department of Statistics" },
        { key: "unit_type", label: "Type", type: "select", options: UNIT_TYPE_OPTS, value: "Department" },
        { key: "parent_unit_id", label: "Parent school", type: "select", options: schools.map((s) => s.id), value: schools[0] && schools[0].id },
        { key: "focus_areas", label: "Focus areas", type: "textarea" },
        { key: "disciplines", label: "Disciplines", type: "textarea" },
        { key: "faculty_count", label: "Faculty", type: "number" },
        { key: "student_count", label: "Students", type: "number" },
        { key: "website_url", label: "Website", placeholder: "https://" },
        { key: "research_by", label: "Research by" },
        { key: "date_of_research", label: "Date of research", type: "date" },
        { key: "notes", label: "Notes", type: "textarea" },
      ], async (vals) => {
        const created = await apiWrite("POST", "/api/units", vals);
        toast("Unit added");
        UNITS_CACHE = null; units = await api("/units"); draw();
        return created;
      });
    });

    draw();
  }

  // shared inline-save: PUT one field, update local row, toast
  async function saveField(base, id, field, raw, localRow) {
    let value = raw;
    if (field === "faculty_count" || field === "student_count" || field === "funding_value")
      value = raw === "" ? null : Number(raw);
    try {
      await apiWrite("PUT", base + enc(id), { [field]: value });
      if (localRow) localRow[field] = value;
      toast("Saved");
    } catch (e) {
      toast(e.message || "Save failed", "err");
    }
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
        ["Website", safeUrl(unit.website_url) ? `<a href="${esc(safeUrl(unit.website_url))}" target="_blank" rel="noopener noreferrer">${esc(unit.website_url)}</a>` : ""],
        ["Disciplines", Array.isArray(unit.disciplines) ? unit.disciplines.join(", ") : unit.disciplines],
        ["Researched by", unit.research_by],
        ["As of", fmtDate(unit.date_of_research)],
      ].filter(([, val]) => val != null && val !== "");
      tabEl.innerHTML = `<div class="grid">${rows.map(([k, val]) => `<div class="card"><div class="meta">${esc(k)}</div><div class="bignum" style="font-size:17px;margin-top:4px">${typeof val === "number" ? val.toLocaleString() : val}</div></div>`).join("")}</div>
        ${unit.notes ? `<p class="page-sub" style="margin-top:18px">${esc(unit.notes)}</p>` : ""}`;
    } else if (tab === "partnerships") {
      try {
        const d = await api("/unit/" + enc(id) + "/partnerships");
        if (!d.count) { tabEl.innerHTML = emptyHTML("No partnerships recorded", "No industry partnerships for this unit in public data yet."); }
        else {
          tabEl.innerHTML = `<div class="export-row">${exportButtons("up-exp")}</div>` + partnershipTable(d.partnerships);
          const cols = [
            { label: "Area", key: "area" }, { label: "Company", key: "company_name" }, { label: "Description", key: "description", w: 50 },
            { label: "Status", key: "status" }, { label: "Funding", key: "funding_value" }, { label: "Funding Type", key: "funding_type" },
            { label: "Source / Evidence", key: "source_url", w: 30 }, { label: "Verified", key: "verification_tier" },
            { label: "Start Date", key: "start_date" }, { label: "Date", key: "date_of_research" },
          ];
          wireExport("up-exp", () => ({ title: `${unit.unit_name} — Partnerships`, filename: `${unit.unit_name}_Partnerships`, columns: cols, rows: d.partnerships }));
        }
      } catch (e) { console.error("/unit partnerships failed:", e); tabEl.innerHTML = errorHTML(e); }
    } else if (tab === "faculty") {
      try {
        const d = await api("/unit/" + enc(id) + "/faculty");
        if (!d.count) { tabEl.innerHTML = emptyHTML("No faculty listed", "No faculty are mapped to this unit yet."); }
        else {
          tabEl.innerHTML = `<div class="export-row">${exportButtons("uf-exp")}</div><div class="grid">${d.faculty.map(facultyCard).join("")}</div>`;
          const cols = [
            { label: "Name", key: "full_name" }, { label: "Title", key: "title" }, { label: "Partnerships", key: "partnership_count" },
            { label: "Top Company", key: "top_company" }, { label: "Profile", key: "profile_url", w: 36 },
          ];
          wireExport("uf-exp", () => ({ title: `${unit.unit_name} — Faculty`, filename: `${unit.unit_name}_Faculty`, columns: cols, rows: d.faculty }));
        }
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
        <td>${tierBadge(r.verification_tier)}</td>
        <td>${esc(fmtUSD(r.funding_value))}</td>
        <td class="src-link">${safeUrl(r.source_url) ? `<a href="${esc(safeUrl(r.source_url))}" target="_blank" rel="noopener noreferrer">record ↗</a>` : ""}${r.pubmed_url ? ` <a class="pm-link" href="${esc(safeUrl(r.pubmed_url))}" target="_blank" rel="noopener noreferrer" title="Open in PubMed (verified)">PubMed ↗</a>` : ""}</td>
        <td>${esc(fmtDate(r.start_date || r.date_of_research))}</td>
      </tr>`).join("")}</tbody></table></div>`;
  }

  // editable partnerships table (the unit column is a live picklist of unc_units)
  function editPartnershipTable(rows, unitOpts) {
    const unitSel = (id, val) => `<select class="es" data-id="${esc(id)}" data-field="unit_id">${unitOpts.map((u) => `<option value="${esc(u.unit_id)}" ${u.unit_id === val ? "selected" : ""}>${esc(u.unit_name)}</option>`).join("")}</select>`;
    const sel = (id, field, opts, val) => `<select class="es" data-id="${esc(id)}" data-field="${field}">${optionsHTML(opts, val)}</select>`;
    return `<div class="table-wrap"><table class="data edit">
      <thead><tr>
        <th>UNC Unit</th><th>Area</th><th>Company</th><th>Description</th><th>Status</th>
        <th>Start</th><th>End / Renewal</th><th>Recurring</th><th>Funding</th><th>Type</th>
        <th>UNC POC</th><th>Company POC</th><th>Source</th><th>Verified</th><th>Research By</th><th>Date</th><th></th>
      </tr></thead>
      <tbody>${rows.map((r) => { const id = r.partnership_id; return `<tr data-id="${esc(id)}">
        <td>${unitSel(id, r.unit_id)}</td>
        <td>${sel(id, "area", AREA_OPTS, r.area)}</td>
        ${editCell(id, "company_name", r.company_name, "strong")}
        ${editCell(id, "description", r.description)}
        <td>${sel(id, "status", STATUS_OPTS, r.status)}</td>
        ${editCell(id, "start_date", r.start_date, "nowrap")}
        ${editCell(id, "end_date", r.end_date || r.renewal_date, "nowrap")}
        <td>${sel(id, "recurring", RECURRING_OPTS, r.recurring)}</td>
        ${editCell(id, "funding_value", r.funding_value, "num")}
        <td>${sel(id, "funding_type", FUNDING_TYPE_OPTS, r.funding_type)}</td>
        ${editCell(id, "unc_poc", r.unc_poc)}
        ${editCell(id, "company_poc", r.company_poc)}
        <td class="src-link">${safeUrl(r.source_url) ? `<a href="${esc(safeUrl(r.source_url))}" target="_blank" rel="noopener noreferrer">link ↗</a>` : `<span class="ec" contenteditable="true" data-id="${esc(id)}" data-field="source_url" data-orig="${esc(r.source_url || "")}">${r.source_url ? esc(r.source_url) : "add…"}</span>`}</td>
        <td>${sel(id, "verification_tier", TIER_OPTS, r.verification_tier)}</td>
        ${editCell(id, "research_by", r.research_by)}
        ${editCell(id, "date_of_research", r.date_of_research, "nowrap")}
        <td class="rowtools"><button class="mini del" data-del-p="${esc(id)}" title="Delete">×</button></td>
      </tr>`; }).join("")}</tbody></table></div>`;
  }

  async function renderPartnerships(query) {
    const v = elView();
    CAN_EDIT = (await getHealth()).writable === true;
    v.innerHTML = `<div class="page wrap">
      <div class="page-head"><div class="card-top"><div><span class="eyebrow">Inventory</span>
        <h1 class="page-title">UNC Partnerships</h1>
        <p class="page-sub">Every external partnership, linked to a UNC unit. ${CAN_EDIT ? "Click any cell to edit — changes save live. " : ""}The unit column pulls from the same master list as Schools &amp; Units.</p></div>
        <div class="head-actions">${copyLinkBtn()}${exportButtons("p-exp")}${CAN_EDIT ? '<button class="btn" id="p-add">＋ Add Partnership</button>' : ""}</div></div></div>
      ${coverageBar()}
      ${dataNote(PARTNERSHIP_NOTE)}
      ${readOnlyNote()}
      <div class="toolbar">
        <input type="search" id="f-q" placeholder="Search company or unit…" />
        <select id="f-area"><option value="">All areas</option></select>
        <select id="f-status"><option value="">All statuses</option></select>
        <select id="f-tier"><option value="">All tiers</option></select>
        <button class="btn ghost" id="f-reset">Reset</button>
        <span class="count" id="p-count"></span>
      </div>
      <div id="p-body">${skeletonTable(12)}</div>
    </div>`;

    let rows, unitOpts;
    try {
      const [all, units] = await Promise.all([api("/partnerships"), api("/units")]);
      rows = all.partnerships || [];
      unitOpts = units.filter((u) => u.unit_id !== "unc:root")
        .map((u) => ({ unit_id: u.unit_id, unit_name: u.unit_name }))
        .sort((a, b) => a.unit_name.localeCompare(b.unit_name));
    } catch (e) { console.error("/partnerships failed:", e); const el = $("#p-body"); if (el) el.innerHTML = errorHTML(e); return; }
    if (!$("#p-body")) return;   // navigated away during the fetch

    const areas = [...new Set(rows.map((r) => r.area).filter(Boolean))].sort();
    const statuses = [...new Set(rows.map((r) => r.status).filter(Boolean))].sort();
    const tiers = [...new Set(rows.map((r) => r.verification_tier).filter(Boolean))].sort();
    $("#f-area").innerHTML = `<option value="">All areas</option>` + areas.map((a) => `<option value="${esc(a)}">${esc(a)}</option>`).join("");
    $("#f-status").innerHTML = `<option value="">All statuses</option>` + statuses.map((s) => `<option value="${esc(s)}">${esc(s)}</option>`).join("");
    $("#f-tier").innerHTML = `<option value="">All tiers</option>` + tiers.map((t) => `<option value="${esc(t)}">${esc(t)}</option>`).join("");
    if (query.area) $("#f-area").value = query.area;
    if (query.tier) $("#f-tier").value = query.tier;
    if (query.status) $("#f-status").value = query.status;
    if (query.q) $("#f-q").value = query.q;

    let lastRows = [];
    const P_EXPORT_COLS = [
      { label: "UNC Unit", key: "unit_name", w: 30 },
      { label: "Area", key: "area", w: 14 },
      { label: "Company", key: "company_name", w: 24 },
      { label: "Description", key: "description", w: 50 },
      { label: "Status", key: "status", w: 12 },
      { label: "Start Date", key: "start_date", w: 12 },
      { label: "End / Renewal", get: (r) => r.end_date || r.renewal_date || "", w: 13 },
      { label: "Recurring", key: "recurring", w: 11 },
      { label: "Funding", key: "funding_value", w: 12 },
      { label: "Funding Type", key: "funding_type", w: 12 },
      { label: "UNC POC", key: "unc_poc", w: 18 },
      { label: "Company POC", key: "company_poc", w: 18 },
      { label: "Source / Evidence", key: "source_url", w: 30 },
      { label: "PubMed", key: "pubmed_url", w: 34 },
      { label: "Verified", key: "verification_tier", w: 11 },
      { label: "Research By", key: "research_by", w: 16 },
      { label: "Date", key: "date_of_research", w: 12 },
    ];

    const draw = () => {
      const q = ($("#f-q").value || "").toLowerCase();
      const area = $("#f-area").value, status = $("#f-status").value, tier = $("#f-tier").value;
      const filtered = rows.filter((r) =>
        (!area || r.area === area) && (!status || r.status === status) && (!tier || r.verification_tier === tier) &&
        (!q || (r.company_name || "").toLowerCase().includes(q) || (r.unit_name || "").toLowerCase().includes(q)));
      lastRows = filtered;
      $("#p-count").textContent = `${filtered.length.toLocaleString()} of ${rows.length.toLocaleString()} partnerships`;
      $("#p-body").innerHTML = filtered.length ? (CAN_EDIT ? editPartnershipTable(filtered, unitOpts) : partnershipTable(filtered))
        : emptyHTML("No partnerships match", rows.length ? "Loosen the filters to see more." : (CAN_EDIT ? "Add one to get started — click ＋ Add Partnership." : "No partnerships in this view."));
      syncQuery("partnerships", { q: $("#f-q").value.trim(), area, status, tier });
    };
    wireExport("p-exp", () => ({ title: "UNC Partnerships", filename: "UNC_Partnerships", columns: P_EXPORT_COLS, rows: lastRows }));
    ["f-q", "f-area", "f-status", "f-tier"].forEach((id) => { const el = document.getElementById(id); el.addEventListener(id === "f-q" ? "input" : "change", draw); });
    $("#f-reset").addEventListener("click", () => { ["f-q", "f-area", "f-status", "f-tier"].forEach((id) => (document.getElementById(id).value = "")); draw(); });

    $("#p-body").addEventListener("focusout", async (e) => {
      const td = e.target.closest(".ec"); if (!td) return;
      const val = td.textContent.trim() === "add…" ? "" : td.textContent.trim();
      if (val === (td.dataset.orig || "")) return;
      const r = rows.find((x) => x.partnership_id === td.dataset.id);
      await saveField("/api/partnerships/", td.dataset.id, td.dataset.field, val, r);
      td.dataset.orig = val;
    });
    $("#p-body").addEventListener("change", async (e) => {
      const s = e.target.closest("select.es"); if (!s) return;
      const r = rows.find((x) => x.partnership_id === s.dataset.id);
      await saveField("/api/partnerships/", s.dataset.id, s.dataset.field, s.value, r);
      if (s.dataset.field === "unit_id" && r) { const u = unitOpts.find((u) => u.unit_id === s.value); if (u) r.unit_name = u.unit_name; }
    });
    $("#p-body").addEventListener("click", async (e) => {
      const btn = e.target.closest("[data-del-p]"); if (!btn) return;
      const id = btn.dataset.delP;
      if (!confirm("Delete this partnership? This can't be undone.")) return;
      try { await apiWrite("DELETE", "/api/partnerships/" + enc(id)); rows = rows.filter((r) => r.partnership_id !== id); toast("Partnership deleted"); draw(); }
      catch (err) { toast(err.message || "Delete failed", "err"); }
    });

    const pAdd = $("#p-add");
    if (pAdd) pAdd.addEventListener("click", () => {
      openModal("Add Partnership", [
        { key: "unit_id", label: "UNC unit", type: "select", options: unitOpts.map((u) => u.unit_id), value: unitOpts[0] && unitOpts[0].unit_id },
        { key: "area", label: "Area", type: "select", options: AREA_OPTS, value: "Programs" },
        { key: "company_name", label: "Company / partner", placeholder: "e.g. Cisco" },
        { key: "description", label: "Description", type: "textarea" },
        { key: "status", label: "Status", type: "select", options: STATUS_OPTS, value: "In Discussion" },
        { key: "start_date", label: "Start date", type: "date" },
        { key: "end_date", label: "End / renewal date", type: "date" },
        { key: "recurring", label: "Recurring", type: "select", options: RECURRING_OPTS, value: "" },
        { key: "funding_value", label: "Funding / value (USD)", type: "number" },
        { key: "funding_type", label: "Funding type", type: "select", options: FUNDING_TYPE_OPTS, value: "" },
        { key: "unc_poc", label: "UNC point of contact" },
        { key: "company_poc", label: "Company point of contact" },
        { key: "source_url", label: "Source / evidence URL", placeholder: "https://" },
        { key: "verification_tier", label: "Verified status", type: "select", options: TIER_OPTS, value: "Inferred" },
        { key: "research_by", label: "Research by" },
        { key: "date_of_research", label: "Date of research", type: "date" },
      ], async (vals) => {
        const created = await apiWrite("POST", "/api/partnerships", vals);
        const u = unitOpts.find((u) => u.unit_id === created.unit_id);
        if (u) created.unit_name = u.unit_name;
        rows.unshift(created);
        toast("Partnership added");
        // refresh filter option lists in case new values appeared
        if (created.area && !areas.includes(created.area)) { areas.push(created.area); areas.sort(); $("#f-area").innerHTML = `<option value="">All areas</option>` + areas.map((a) => `<option value="${esc(a)}">${esc(a)}</option>`).join(""); }
        draw();
        return created;
      });
    });

    draw();
  }

  // ── view: FACULTY ────────────────────────────────────────────────────────────
  function facultyCard(f) {
    const name = f.faculty_id ? `<a href="#/faculty/${enc(f.faculty_id)}">${esc(f.full_name)}</a>` : esc(f.full_name);
    return `<div class="card">
      <div class="card-top"><h3>${name}</h3>${f.partnership_count ? `<span class="kind">${f.partnership_count} partnership${f.partnership_count === 1 ? "" : "s"}</span>` : ""}</div>
      <div class="meta">${esc(f.title || "")}${f.unit_name ? `${f.title ? " · " : ""}<a href="#/unit/${enc(f.unit_id)}">${esc(f.unit_name)}</a>` : ""}</div>
      ${f.top_company ? `<div class="chips"><span class="chip">${esc(f.top_company)}</span></div>` : ""}
      ${safeUrl(f.profile_url) ? `<div style="margin-top:10px"><a class="src-link" href="${esc(safeUrl(f.profile_url))}" target="_blank" rel="noopener noreferrer">profile ↗</a></div>` : ""}
    </div>`;
  }
  let FACULTY_CACHE = null;
  async function renderFaculty(query = {}) {
    const v = elView();
    v.innerHTML = `<div class="page wrap">
      <div class="page-head"><div class="card-top"><div><span class="eyebrow">Directory</span><h1 class="page-title">UNC Faculty</h1>
        <p class="page-sub">Researchers mapped to units through public grants, papers and trials. Filter by name; <kbd class="ikbd">↑</kbd><kbd class="ikbd">↓</kbd>+<kbd class="ikbd">↵</kbd> to open a profile.</p></div>
        <div class="head-actions">${copyLinkBtn()}${exportButtons("fac-exp")}</div></div></div>
      ${coverageBar()}
      <div class="toolbar">
        <input type="search" id="fac-search" placeholder="Filter by faculty name…" value="${esc(query.q || "")}" />
        <label style="font-size:13px;color:var(--muted)"><input type="checkbox" id="fac-partners" ${query.partners ? "checked" : ""}/> with partnerships only</label>
        <span class="count" id="fac-count"></span>
      </div>
      <div id="fac-grid">${skeletonGrid(9)}</div>
    </div>`;

    let fac;
    try { fac = FACULTY_CACHE || (FACULTY_CACHE = await api("/faculty")); }
    catch (e) { console.error("/faculty failed:", e); const el = $("#fac-grid"); if (el) el.innerHTML = errorHTML(e); return; }
    if (!$("#fac-grid")) return;   // navigated away during the fetch

    let lastRows = [];
    const FAC_EXPORT_COLS = [
      { label: "Name", key: "full_name", w: 28 },
      { label: "Title", key: "title", w: 28 },
      { label: "Unit", key: "unit_name", w: 30 },
      { label: "Partnerships", key: "partnership_count", w: 12 },
      { label: "Top Company", key: "top_company", w: 22 },
      { label: "Profile", key: "profile_url", w: 36 },
    ];

    let facShown = 150;
    const STEP = 150;
    const draw = () => {
      const term = $("#fac-search").value.toLowerCase();
      const partnersOnly = $("#fac-partners").checked;
      let rows = fac.filter((f) => (!term || (f.full_name || "").toLowerCase().includes(term)) && (!partnersOnly || (f.partnership_count || 0) > 0));
      rows = rows.sort((a, b) => (b.partnership_count || 0) - (a.partnership_count || 0));
      lastRows = rows;
      const shown = rows.slice(0, facShown);
      $("#fac-count").textContent = `${shown.length.toLocaleString()} of ${rows.length.toLocaleString()} shown`;
      const more = rows.length > shown.length
        ? `<div class="more-row"><button class="btn ghost" id="fac-more">Show ${Math.min(STEP, rows.length - shown.length).toLocaleString()} more</button></div>` : "";
      $("#fac-grid").innerHTML = shown.length ? `<div class="grid">${shown.map(facultyCard).join("")}</div>${more}` : emptyHTML("No faculty match", "Try a different name.");
      const mb = $("#fac-more"); if (mb) mb.addEventListener("click", () => { facShown += STEP; draw(); });
      syncQuery("faculty", { q: $("#fac-search").value.trim(), partners: $("#fac-partners").checked ? "1" : "" });
    };
    wireExport("fac-exp", () => ({ title: "UNC Faculty", filename: "UNC_Faculty", columns: FAC_EXPORT_COLS, rows: lastRows }));
    $("#fac-search").addEventListener("input", () => { facShown = STEP; draw(); });
    $("#fac-partners").addEventListener("change", () => { facShown = STEP; draw(); });
    wireListNav("fac-grid", ".card", (card) => { const a = card.querySelector("h3 a"); if (a) location.hash = a.getAttribute("href"); });
    draw();
  }

  // ── view: FACULTY PROFILE ────────────────────────────────────────────────────
  async function renderFacultyProfile(id) {
    const v = elView();
    v.innerHTML = `<div class="page wrap"><div id="facp-body">${loadingHTML("Loading researcher…")}</div></div>`;
    const body = $("#facp-body");
    let f;
    try { f = await api("/faculty/" + enc(id)); }
    catch (e) { console.error("/faculty/{id} failed:", e, id); if (body) body.innerHTML = errorHTML(e, { notFound: "That researcher isn't in the directory." }); return; }
    if (!$("#facp-body")) return;
    if (f.error) { body.innerHTML = emptyHTML("Researcher not found", f.error); return; }
    const ps = f.partnerships || [];
    body.innerHTML = `
      <div class="crumb"><a href="#/faculty">← Faculty</a></div>
      <div class="page-head"><div class="card-top"><div>
        <h1 class="page-title">${esc(f.full_name)}</h1>
        <p class="page-sub">${esc(f.title || "")}${f.unit_name ? `${f.title ? " · " : ""}<a href="#/unit/${enc(f.unit_id)}">${esc(f.unit_name)}</a>` : ""}</p>
      </div>${f.partnership_count != null ? `<span class="kind">${f.partnership_count} partnership${f.partnership_count === 1 ? "" : "s"}</span>` : ""}</div>
      ${safeUrl(f.profile_url) ? `<div style="margin-top:6px"><a class="src-link" href="${esc(safeUrl(f.profile_url))}" target="_blank" rel="noopener noreferrer">External profile ↗</a></div>` : ""}
      </div>
      ${ps.length ? `<div class="export-row">${exportButtons("facp-exp")}</div>` + partnershipTable(ps)
        : emptyHTML("No partnerships recorded", "No industry partnerships are mapped to this researcher in public data yet.")}`;
    if (ps.length) {
      const cols = [
        { label: "Unit", key: "unit_name", w: 28 }, { label: "Area", key: "area", w: 14 }, { label: "Company", key: "company_name", w: 22 },
        { label: "Status", key: "status", w: 12 }, { label: "Funding", key: "funding_value", w: 12 },
        { label: "Source / Evidence", key: "source_url", w: 30 }, { label: "Verified", key: "verification_tier", w: 12 },
      ];
      wireExport("facp-exp", () => ({ title: `${f.full_name} — Partnerships`, filename: `${f.full_name}_Partnerships`, columns: cols, rows: ps }));
    }
  }

  // ── view: NETWORK (3D, API-driven) ────────────────────────────────────────────
  async function renderNetwork() {
    const v = elView();
    v.innerHTML = `<div class="page wrap">
      <div class="page-head"><div class="card-top"><div><span class="eyebrow">Live graph</span><h1 class="page-title">Research network</h1>
        <p class="page-sub">Every dot is the UNC anchor, a unit, or a partner company; every line is a public record. Drag to rotate, scroll to zoom, click a node.</p></div>
        <div class="head-actions">${exportButtons("net-exp")}</div></div></div>
      ${coverageBar()}
      <div class="network-stage"><div id="graph-3d"></div>
        <div class="net-bar">
          <div class="net-status" id="net-status">Assembling network…</div>
          <div class="net-tools">
            <input type="search" id="net-find" class="net-find" placeholder="Find a node…" aria-label="Find a node in the graph" autocomplete="off" />
            <select id="net-mode" class="net-sel" aria-label="Grouping mode">
              <option value="auto">Auto-cycle</option>
              <option value="school">By school</option>
              <option value="tier">By confidence tier</option>
              <option value="layered">By role</option>
            </select>
            <button class="net-btn" id="net-pause" title="Pause motion" aria-label="Pause motion">⏸</button>
            <button class="net-btn" id="net-2d" title="Toggle 2D / 3D" aria-label="Toggle 2D / 3D">2D</button>
            <button class="net-btn" id="net-replay" title="Replay the network growth" aria-label="Replay growth">↻</button>
          </div>
        </div>
        <div class="net-legend">
          <span><i style="background:#1d1d1f"></i> UNC–Chapel Hill</span>
          <span><i style="background:#3f7d6e"></i> School / unit</span>
          <span><i style="background:#5b8def"></i> Company · confirmed</span>
          <span><i style="background:#9a8654"></i> Company · probable</span>
        </div></div>
    </div>`;

    // Concurrency guard: claim a render epoch, capture the stage NOW (before any
    // await), and cancel any earlier in-flight network fetch. If the user leaves
    // and re-enters #/network during the fetch, the stale render bails instead of
    // building a second graph on the new live node.
    const myToken = ++NET_TOKEN;
    const stage = $("#graph-3d");
    if (NET_ABORT) { try { NET_ABORT.abort("superseded"); } catch (_) {} }
    NET_ABORT = new AbortController();

    let g;
    try { g = GRAPH_CACHE || await api("/api/graph", { timeoutMs: 20000, signal: NET_ABORT.signal }); }
    catch (e) {
      if (myToken !== NET_TOKEN) return;            // superseded — don't touch the new view
      console.error("/api/graph failed:", e);
      if (document.body.contains(stage)) stage.parentElement.innerHTML = errorHTML(e);
      return;
    }
    if (myToken !== NET_TOKEN || !document.body.contains(stage)) return;  // superseded mid-fetch
    GRAPH_CACHE = g;   // share with the company index + search autocomplete

    wireExport("net-exp", () => ({
      title: "UNC Research Network — Companies", filename: "UNC_Network_Companies",
      columns: [
        { label: "Company", key: "name", w: 30 },
        { label: "Confidence", key: "confidence", w: 14 },
        { label: "Total Records", key: "total_edges", w: 14 },
        { label: "Linked Units", get: (c) => (c.units || []).map((u) => u.unit_name || u.unit_id).join("; "), w: 60 },
      ],
      rows: g.companies || [],
    }));

    if (typeof ForceGraph3D === "undefined") { $("#graph-3d").innerHTML = `<div class="error info" style="padding:40px">Network library failed to load (CDN blocked). The data is live at <code>/api/graph</code>.</div>`; return; }

    // ── staged data for the "evolution" build: the anchor + its schools/units
    //    appear first, then partner companies stream in over a couple of
    //    seconds, so you watch the network grow and reorganise rather than
    //    having it pop in fully-formed. ──
    const unitIdSet = new Set(["unc:root"]);
    (g.units || []).forEach((u) => { if (u.id !== "unc:root") unitIdSet.add(u.id); });

    const seed = () => {
      const nodes = [{ id: "unc:root", label: "UNC–Chapel Hill", group: "root", val: 42 }];
      const links = [];
      (g.units || []).forEach((u) => {
        if (u.id === "unc:root") return;
        const total = Object.values(u.footprint || {}).reduce((a, b) => a + (typeof b === "number" ? b : 0), 0);
        nodes.push({ id: u.id, label: u.name, group: "unit", val: 8 + Math.min(total, 34), weight: total });
        links.push({ source: "unc:root", target: u.id, kind: "anchor" });
      });
      return { nodes, links };
    };
    // a fresh company node + its evidence links, rebuilt per run so a replay
    // re-grows from the centre instead of snapping back into place
    const makeCompany = (c) => {
      const edges = c.total_edges || 1;
      const node = { id: c.id, label: c.name, group: c.confidence === "confirmed" ? "confirmed" : "probable", val: 3 + Math.min(edges, 16), weight: edges };
      const links = [];
      (c.units || []).forEach((cu) => { if (unitIdSet.has(cu.unit_id)) links.push({ source: c.id, target: cu.unit_id, kind: "evidence", weight: edges }); });
      return { node, links };
    };

    const COLORS = { root: "#1d1d1f", unit: "#3f7d6e", confirmed: "#5b8def", probable: "#9a8654" };
    const GROUP_LABEL = { root: "UNC–Chapel Hill", unit: "School / unit", confirmed: "Company · confirmed", probable: "Company · probable" };
    // `stage` was captured up front (before the await) for the concurrency guard.

    // hover state: highlight a node, its neighbours and the links between them
    const hlNodes = new Set();
    const hlLinks = new Set();
    let hoverNode = null;

    const Graph = ForceGraph3D({ controlType: "orbit", rendererConfig: { antialias: true, alpha: true } })(stage)
      .graphData({ nodes: [], links: [] })
      .backgroundColor("rgba(0,0,0,0)")
      .showNavInfo(false)
      .nodeResolution(16)
      .nodeOpacity(0.95)
      .nodeRelSize(4.2)
      .nodeLabel((n) => `<div class="g3d-tip"><b>${esc(n.label)}</b><span>${esc(GROUP_LABEL[n.group] || n.group)}${n.weight ? ` · ${(+n.weight).toLocaleString()} records` : ""}</span></div>`)
      .nodeVal((n) => (n === hoverNode ? 1.9 : hlNodes.has(n) ? 1.35 : 1) * (n.val || 3))
      .nodeColor((n) => COLORS[n.group] || "#999")
      .linkCurvature((l) => (l.kind === "evidence" ? 0.22 : 0.06))
      .linkColor((l) => hlLinks.has(l) ? "rgba(29,29,31,0.72)" : (l.kind === "anchor" ? "rgba(63,125,110,0.62)" : "rgba(63,125,110,0.4)"))
      .linkWidth((l) => hlLinks.has(l) ? 2.6 : (l.kind === "anchor" ? 1.5 : 0.95))
      .linkDirectionalParticles((l) => hlLinks.has(l) ? 6 : (l.kind === "anchor" ? 3 : 2))
      .linkDirectionalParticleWidth((l) => hlLinks.has(l) ? 3 : 1.5)
      .linkDirectionalParticleSpeed((l) => hlLinks.has(l) ? 0.012 : 0.005)
      .linkDirectionalParticleColor((l) => hlLinks.has(l) ? "#1d1d1f" : "#3f7d6e")
      .onNodeHover((node) => {
        if (node === hoverNode) return;
        hoverNode = node || null;
        stage.style.cursor = node ? "pointer" : "";
        applyHighlight(hoverNode);
      })
      .onNodeClick((n) => { if (n.group === "unit") location.hash = "#/unit/" + enc(n.id); else if (n.group !== "root") location.hash = "#/search/" + enc(n.label); });

    // highlight a node + its neighbours/links, then re-evaluate the visual
    // accessors so it takes effect. Used by hover AND the in-graph Find control.
    function applyHighlight(node) {
      hlNodes.clear(); hlLinks.clear();
      if (node) {
        hlNodes.add(node);
        Graph.graphData().links.forEach((l) => {
          if (l.source === node || l.target === node) {
            hlLinks.add(l);
            hlNodes.add(l.source === node ? l.target : l.source);
          }
        });
      }
      Graph.nodeVal(Graph.nodeVal()).linkColor(Graph.linkColor()).linkWidth(Graph.linkWidth())
        .linkDirectionalParticles(Graph.linkDirectionalParticles())
        .linkDirectionalParticleWidth(Graph.linkDirectionalParticleWidth())
        .linkDirectionalParticleSpeed(Graph.linkDirectionalParticleSpeed())
        .linkDirectionalParticleColor(Graph.linkDirectionalParticleColor());
    }

    // baseline forces; the morph loop below nudges these over time so the
    // structure keeps reorganising. Lower velocity decay = more fluid, neuron-
    // like drift between states.
    try {
      Graph.d3Force("charge").strength(-120);
      const lf = Graph.d3Force("link");
      if (lf) lf.distance((l) => (l.kind === "anchor" ? 46 : 30));
    } catch (_) {}
    Graph.cooldownTime(4000).warmupTicks(30).d3VelocityDecay(0.28);

    // ── grouping force: a small custom d3 force (no extra deps) that, in the
    //    'tier'/'layered' modes, pulls each node toward a target position so the
    //    SAME relationships visibly RE-GROUP in 3D — hub-and-spoke by school,
    //    then split into confidence-tier clusters, then concentric role shells.
    //    In 'school' mode it does nothing, leaving the natural force layout. ──
    let layoutMode = "school";
    let _gnodes = [];
    function groupForce(alpha) {
      if (layoutMode === "school") return;
      const k = 0.085 * alpha;
      for (const n of _gnodes) {
        if (layoutMode === "tier") {
          const tx = n.group === "confirmed" ? -150 : n.group === "probable" ? 150 : 0;
          const ty = n.group === "unit" ? 120 : n.group === "root" ? 0 : -45;
          n.vx += (tx - n.x) * k;
          n.vy += (ty - n.y) * k;
        } else if (layoutMode === "layered") {
          const tr = n.group === "root" ? 0 : n.group === "unit" ? 70 : 160;
          const d = Math.sqrt(n.x * n.x + n.y * n.y + n.z * n.z) || 1e-6;
          const f = ((tr - d) / d) * k;
          n.vx += n.x * f; n.vy += n.y * f; n.vz += n.z * f;
        }
      }
    }
    groupForce.initialize = (nodes) => { _gnodes = nodes; };
    try { Graph.d3Force("group", groupForce); } catch (_) {}
    const MODE_ORDER = ["school", "tier", "layered"];
    const MODE_LABEL = { school: "Grouped by school", tier: "Grouped by confidence tier", layered: "Layered by role" };

    Graph.width(stage.clientWidth).height(stage.clientHeight);
    const onResize = () => Graph.width(stage.clientWidth).height(stage.clientHeight);
    window.addEventListener("resize", onResize, { passive: true });

    // ── auto-orbit: OrbitControls slowly spins the camera around the graph's
    //    centre (set by zoomToFit) so the view is always alive and stays
    //    framed. Drag/scroll pauses the spin; it resumes after a short idle.
    let rafId = 0, resumeTimer = null;
    // user-facing control state (wired to the toolbar below)
    let paused = false, autoCycle = true, is2D = false;
    const spinOK = () => !paused && !is2D && !document.hidden;   // when auto-orbit may run
    const controls = Graph.controls();
    if (controls) {
      controls.enableDamping = true;
      controls.dampingFactor = 0.08;
      controls.rotateSpeed = 0.7;
      controls.autoRotate = true;
      controls.autoRotateSpeed = 0.5;
      if (controls.addEventListener) {
        controls.addEventListener("start", () => { controls.autoRotate = false; clearTimeout(resumeTimer); });
        controls.addEventListener("end", () => { clearTimeout(resumeTimer); resumeTimer = setTimeout(() => { controls.autoRotate = spinOK(); }, 3500); });
      }
    }
    // frame the graph once it first settles; after that the morph loop keeps it
    // changing shape, so we don't re-fit every cycle (that would fight the orbit)
    let buildTimer = null, morphTimer = null, building = false, framed = false;
    Graph.onEngineStop(() => { if (!framed) { framed = true; Graph.zoomToFit(1000, 70); } });

    // ── evolution build: seed with the anchor + schools, then stream partner
    //    companies + their connections in over a couple of seconds so the graph
    //    visibly grows and reorganises. Re-runnable via the Replay button. ──
    const statusEl = document.getElementById("net-status");
    function build() {
      clearTimeout(buildTimer);
      building = true;
      const s = seed();
      const shownNodes = s.nodes.slice();
      const shownLinks = s.links.slice();
      Graph.graphData({ nodes: shownNodes, links: shownLinks });
      const comps = (g.companies || []).map(makeCompany);
      const total = comps.length;
      // ~14 chunky steps: enough to read as "growing", few enough that the
      // force engine isn't re-heated so often that the frame rate drops.
      const batch = Math.max(12, Math.ceil(total / 14));
      let i = 0;
      if (statusEl) statusEl.textContent = "Connecting UNC schools…";
      (function grow() {
        if (!document.body.contains(stage)) { clearTimeout(buildTimer); return; }
        if (i >= total) {
          building = false;
          framed = true;
          if (statusEl) statusEl.textContent = `Live · ${shownNodes.length.toLocaleString()} nodes · ${shownLinks.length.toLocaleString()} connections`;
          Graph.zoomToFit(1000, 70);
          return;
        }
        const end = Math.min(i + batch, total);
        for (; i < end; i++) { shownNodes.push(comps[i].node); comps[i].links.forEach((l) => shownLinks.push(l)); }
        Graph.graphData({ nodes: shownNodes, links: shownLinks });
        if (statusEl) statusEl.textContent = `Mapping connections… ${i.toLocaleString()} / ${total.toLocaleString()} partners`;
        buildTimer = setTimeout(grow, 150);
      })();
    }
    const replayBtn = document.getElementById("net-replay");
    if (replayBtn) replayBtn.addEventListener("click", build);
    build();

    // ── living neural net: cycle the grouping every several seconds so the same
    //    relationships visibly RESTRUCTURE in 3D — hub-and-spoke by school, then
    //    split into confidence-tier groups, then concentric role layers. Each
    //    transition re-heats the layout so nodes migrate to their new groups and
    //    the connections between them re-route on screen. ──
    let modeIdx = 0;
    (function regroup() {
      morphTimer = setTimeout(() => {
        if (!document.body.contains(stage)) return; // view torn down → stop
        // only auto-advance when not paused, not pinned to a mode, not building,
        // and the tab is visible (no point restructuring something unseen).
        if (autoCycle && !paused && !building && !document.hidden) {
          modeIdx = (modeIdx + 1) % MODE_ORDER.length;
          layoutMode = MODE_ORDER[modeIdx];
          try {
            // ease repulsion in the grouped modes so clusters separate cleanly
            Graph.d3Force("charge").strength(layoutMode === "school" ? -120 : -65);
            if (Graph.d3ReheatSimulation) Graph.d3ReheatSimulation();
          } catch (_) {}
          if (statusEl) {
            const nc = (Graph.graphData().nodes || []).length;
            statusEl.textContent = `${MODE_LABEL[layoutMode]} · ${nc.toLocaleString()} nodes`;
          }
        }
        regroup();
      }, 8500);
    })();

    // pause the camera spin when the tab is hidden; resume when it's visible
    const onVis = () => { if (controls) controls.autoRotate = spinOK(); };
    document.addEventListener("visibilitychange", onVis);

    // ── toolbar wiring: Find, grouping pin, pause/play, 2D/3D ──
    const findInput = document.getElementById("net-find");
    if (findInput) findInput.addEventListener("input", () => {
      const q = findInput.value.trim().toLowerCase();
      if (controls) controls.autoRotate = q ? false : spinOK();   // hold still while searching
      if (!q) { applyHighlight(null); return; }
      const node = (Graph.graphData().nodes || []).find((n) => (n.label || "").toLowerCase().includes(q));
      applyHighlight(node || null);
      if (node && typeof node.x === "number") Graph.cameraPosition({ x: node.x, y: node.y, z: node.z + 90 }, node, 800);
    });

    const modeSel = document.getElementById("net-mode");
    if (modeSel) modeSel.addEventListener("change", () => {
      const val = modeSel.value;
      autoCycle = val === "auto";
      if (!autoCycle) {
        layoutMode = val;
        try { Graph.d3Force("charge").strength(val === "school" ? -120 : -65); if (Graph.d3ReheatSimulation) Graph.d3ReheatSimulation(); } catch (_) {}
        if (statusEl) statusEl.textContent = `${MODE_LABEL[val]} · ${(Graph.graphData().nodes || []).length.toLocaleString()} nodes`;
      }
    });

    const pauseBtn = document.getElementById("net-pause");
    if (pauseBtn) pauseBtn.addEventListener("click", () => {
      paused = !paused;
      if (controls) controls.autoRotate = spinOK();
      pauseBtn.textContent = paused ? "▶" : "⏸";
      const lbl = paused ? "Resume motion" : "Pause motion";
      pauseBtn.title = lbl; pauseBtn.setAttribute("aria-label", lbl);
      pauseBtn.classList.toggle("on", paused);
    });

    const btn2d = document.getElementById("net-2d");
    if (btn2d) btn2d.addEventListener("click", () => {
      is2D = !is2D;
      try {
        Graph.numDimensions(is2D ? 2 : 3);
        if (controls) { controls.enableRotate = !is2D; controls.autoRotate = spinOK(); }
        if (is2D) Graph.cameraPosition({ x: 0, y: 0, z: 360 }, { x: 0, y: 0, z: 0 }, 800);
        if (Graph.d3ReheatSimulation) Graph.d3ReheatSimulation();
        setTimeout(() => { if (document.body.contains(stage)) Graph.zoomToFit(700, 70); }, 1100);
      } catch (_) {}
      btn2d.textContent = is2D ? "3D" : "2D";
      btn2d.classList.toggle("on", is2D);
    });

    // drive the controls every frame (autoRotate + damping) and tear everything
    // down when the network view is replaced, so nothing leaks across routes.
    (function tick() {
      if (!document.body.contains(stage)) {
        cancelAnimationFrame(rafId);
        clearTimeout(buildTimer);
        clearTimeout(morphTimer);
        clearTimeout(resumeTimer);
        window.removeEventListener("resize", onResize);
        document.removeEventListener("visibilitychange", onVis);
        // pauseAnimation() only stops the rAF; _destructor() releases the WebGL
        // context (renderer/controls/composer dispose) so repeat visits don't
        // exhaust the browser's ~16-context limit and blank the graph.
        try { Graph.pauseAnimation && Graph.pauseAnimation(); } catch (_) {}
        try { Graph._destructor && Graph._destructor(); } catch (_) {}
        return;
      }
      if (controls && controls.update) controls.update();
      rafId = requestAnimationFrame(tick);
    })();
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
      <div class="page-head"><div class="card-top"><div><span class="eyebrow">Methodology</span><h1 class="page-title">How this works</h1>
        <p class="page-sub">A precomputed graph of UNC–Chapel Hill's public research footprint, served live by a same-origin API. No LLM in the data path; entity resolution is deterministic; every source is free, public and keyless.</p></div>
        <div class="head-actions">${exportButtons("about-exp")}</div></div></div>
      ${coverageBar()}
      <div class="grid">${rows.map(([name, n, desc]) => `<div class="card"><div class="card-top"><h3>${esc(name)}</h3></div><div class="bignum" style="margin-top:8px">${(+n).toLocaleString()}</div><div class="meta">${esc(desc)}</div></div>`).join("")}</div>
      <div class="page-head" style="margin-top:36px"><h2 class="page-title" style="font-size:22px">How we verify</h2>
        <p class="page-sub">Every link in this graph has been cross-checked against an authoritative public source — not just compiled. Each data type and its verification source:</p></div>
      <div class="grid">
        ${VERIFY.map((v) => `<div class="card"><div class="card-top"><h3>${esc(v.src)}</h3><span class="kind">✓ verified</span></div><div class="meta" style="margin-top:6px"><b>${esc(v.what)}</b></div><p class="meta" style="margin-top:8px">${esc(v.detail)}</p></div>`).join("")}
      </div>

      <div class="page-head" style="margin-top:36px"><h2 class="page-title" style="font-size:22px">Confidence tiers</h2></div>
      <div class="grid">
        <div class="card"><span class="badge confirmed">confirmed</span><p class="meta" style="margin-top:10px">Company identity matched to a unique SEC filer (CIK) and the UNC link confirmed on ClinicalTrials.gov. High trust.</p></div>
        <div class="card"><span class="badge probable">probable</span><p class="meta" style="margin-top:10px">Matched by company name (no SEC CIK), but the UNC link is confirmed by public clinical-trial records.</p></div>
      </div>
      <p class="page-sub" style="margin-top:28px">Graph built <b>${esc(fr && fr.built_at ? fmtDate(fr.built_at) : "—")}</b>. Anchor: UNC–Chapel Hill (ROR <a href="https://ror.org/0130frc33" target="_blank" rel="noopener noreferrer">0130frc33</a>).</p>
    </div>`;
    wireExport("about-exp", () => ({
      title: "UNC Research Intelligence — Data Sources", filename: "UNC_Data_Sources",
      columns: [{ label: "Source", get: (r) => r[0], w: 24 }, { label: "Records", get: (r) => r[1], w: 12 }, { label: "Description", get: (r) => r[2], w: 50 }],
      rows: rows,
    }));
  }

  // ── ⌘K command palette: instant search + jump-to-page ────────────────────────
  const CMDK_PAGES = [
    { label: "Search", hash: "#/" }, { label: "Companies", hash: "#/companies" },
    { label: "Schools & Units", hash: "#/units" }, { label: "Partnerships", hash: "#/partnerships" },
    { label: "Faculty", hash: "#/faculty" }, { label: "Network", hash: "#/network" }, { label: "About", hash: "#/about" },
  ];
  let _cmdkCompanies = null;
  function openPalette() {
    if (document.getElementById("cmdk")) return;
    const back = document.createElement("div"); back.id = "cmdk"; back.className = "cmdk-back";
    back.innerHTML = `<div class="cmdk" role="dialog" aria-modal="true" aria-label="Quick search">
      <div class="cmdk-in"><span class="cmdk-in-ic">⌕</span><input id="cmdk-input" type="search" autocomplete="off" placeholder="Search a company or jump to a page…" aria-label="Quick search" /></div>
      <div class="cmdk-list" id="cmdk-list"></div>
      <div class="cmdk-foot"><span><kbd>↑</kbd><kbd>↓</kbd> navigate</span><span><kbd>↵</kbd> open</span><span><kbd>esc</kbd> close</span></div>
    </div>`;
    document.body.appendChild(back);
    const input = back.querySelector("#cmdk-input");
    const list = back.querySelector("#cmdk-list");
    let items = [], active = 0;
    if (_cmdkCompanies === null) getGraph().then((g) => { _cmdkCompanies = g.companies || []; render(); }).catch(() => { _cmdkCompanies = []; });
    const close = () => back.remove();
    const choose = (i) => { const it = items[i]; if (!it) return; close(); location.hash = it.kind === "page" ? it.p.hash : "#/search/" + enc(it.kind === "company" ? it.c.name : it.q); };
    const move = (d) => { if (!items.length) return; active = (active + d + items.length) % items.length; [...list.querySelectorAll(".cmdk-item")].forEach((el, i) => el.classList.toggle("active", i === active)); const cur = list.querySelector(".cmdk-item.active"); if (cur) cur.scrollIntoView({ block: "nearest" }); };
    const render = () => {
      const q = input.value.trim();
      const comps = q ? rankCompanies(q, _cmdkCompanies, 6) : [];
      const pages = CMDK_PAGES.filter((p) => !q || p.label.toLowerCase().includes(q.toLowerCase()));
      const recents = q ? [] : recentList().map((r) => ({ kind: "recent", q: r }));
      items = [...recents, ...comps.map((c) => ({ kind: "company", c })), ...(q ? [{ kind: "topic", q }] : []), ...pages.map((p) => ({ kind: "page", p }))];
      active = 0;
      list.innerHTML = items.length ? items.map((it, i) => {
        const a = i === 0 ? " active" : "";
        if (it.kind === "company") return `<div class="cmdk-item${a}" data-i="${i}"><span class="cmdk-ic">◆</span><span class="cmdk-lbl">${esc(it.c.name)}</span><span class="cmdk-rt">${confBadge(it.c.confidence)} ${(it.c.total_edges || 0).toLocaleString()}</span></div>`;
        if (it.kind === "topic") return `<div class="cmdk-item${a}" data-i="${i}"><span class="cmdk-ic">⌕</span><span class="cmdk-lbl">Search “${esc(it.q)}” as a topic</span></div>`;
        if (it.kind === "recent") return `<div class="cmdk-item${a}" data-i="${i}"><span class="cmdk-ic">↩</span><span class="cmdk-lbl">${esc(it.q)}</span><span class="cmdk-rt">recent</span></div>`;
        return `<div class="cmdk-item${a}" data-i="${i}"><span class="cmdk-ic">›</span><span class="cmdk-lbl">Go to ${esc(it.p.label)}</span></div>`;
      }).join("") : (q ? `<div class="cmdk-empty">No matches — press ↵ to search “${esc(q)}”</div>` : `<div class="cmdk-empty">Type a company name, a topic, or a page</div>`);
      if (!items.length && q) items = [{ kind: "topic", q }];
      list.querySelectorAll(".cmdk-item").forEach((el) => el.addEventListener("mousedown", (e) => { e.preventDefault(); choose(+el.dataset.i); }));
    };
    input.addEventListener("input", render);
    input.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown") { e.preventDefault(); move(1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); move(-1); }
      else if (e.key === "Enter") { e.preventDefault(); choose(active); }
      else if (e.key === "Escape") { e.preventDefault(); close(); }
    });
    back.addEventListener("mousedown", (e) => { if (e.target === back) close(); });
    render();
    setTimeout(() => input.focus(), 20);
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
        case "companies": return await renderCompanies(query);
        case "units": return await renderUnits();
        case "unit": return await renderUnit(parts[1], parts[2]);
        case "partnerships": return await renderPartnerships(query);
        case "faculty": return parts[1] ? await renderFacultyProfile(parts[1]) : await renderFaculty(query);
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
    // Await health up front so CAN_EDIT (writable) is known before the first
    // render, and reflect it in the status badge.
    try {
      const h = await getHealth();
      const ok = h.status === "ok";
      if (statusEl) {
        statusEl.className = "api-status " + (ok ? "ok" : "down");
        statusEl.innerHTML = `<span class="api-dot"></span>${ok ? "Live API" : (h.status === "degraded" ? "API degraded" : "API offline")}`;
      }
    } catch (e) {
      console.error("/health failed:", e);
      if (statusEl) { statusEl.className = "api-status down"; statusEl.innerHTML = `<span class="api-dot"></span>API offline`; }
    }
    try { FRESHNESS = await api("/freshness"); const fb = document.getElementById("footer-built"); if (fb && FRESHNESS.built_at) fb.textContent = fmtDate(FRESHNESS.built_at); }
    catch (e) { console.error("/freshness failed:", e); }
    // Delegated Retry handler (replaces an inline onclick so the CSP can forbid
    // inline script entirely).
    document.addEventListener("click", (e) => { if (e.target.closest("[data-reload]")) location.reload(); });
    // copy a shareable link to the current view
    document.addEventListener("click", async (e) => {
      const b = e.target.closest("[data-copylink]"); if (!b) return;
      try { await navigator.clipboard.writeText(location.href); toast("Link copied to clipboard"); }
      catch { toast("Press ⌘C to copy the URL", "err"); }
    });

    // ⌘K / Ctrl-K (or "/") opens the command palette; nav button opens it too.
    const isMac = /Mac|iP(hone|ad|od)/.test(navigator.platform || navigator.userAgent || "");
    const kbd = document.getElementById("cmdk-kbd"); if (kbd) kbd.textContent = isMac ? "⌘K" : "Ctrl K";
    const trig = document.getElementById("cmdk-open"); if (trig) trig.addEventListener("click", openPalette);
    document.addEventListener("keydown", (e) => {
      if ((e.key === "k" || e.key === "K") && (e.metaKey || e.ctrlKey)) { e.preventDefault(); openPalette(); return; }
      const el = document.activeElement;
      const typing = el && (/^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName) || el.isContentEditable);
      if (e.key === "/" && !typing && !document.getElementById("cmdk")) { e.preventDefault(); openPalette(); }
    });

    window.addEventListener("hashchange", route);
    route();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
