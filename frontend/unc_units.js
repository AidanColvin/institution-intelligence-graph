/* UNC Partnership Map — campus-wide partnership sector scan.
   Reads the precomputed frontend/partnerships.json (same data the /units,
   /faculty, /partnerships API serves) and renders three tabbed sub-views plus a
   filterable evidence table. Vanilla JS, no deps. Self-contained IIFE so it
   never collides with app.js. */
(function () {
  "use strict";

  const DATA_URL = "partnerships.json";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => (s == null ? "" : String(s).replace(/[&<>"]/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])));
  const fmtUSD = (n) => {
    if (!n) return "";
    if (n >= 1e9) return "$" + (n / 1e9).toFixed(1) + "B";
    if (n >= 1e6) return "$" + (n / 1e6).toFixed(1) + "M";
    if (n >= 1e3) return "$" + (n / 1e3).toFixed(0) + "K";
    return "$" + n;
  };
  const TIER_CLASS = { Verified: "verified", Reported: "reported", Inferred: "inferred" };
  const LAB_TYPES = new Set(["Lab", "Center", "Institute"]);

  let DATA = null;
  const state = { view: "schools", unitId: null, facultyId: null,
                  area: "", status: "", tier: "" };

  // ── load ───────────────────────────────────────────────────────────────────
  async function init() {
    const section = $("#partnership-map");
    if (!section) return;
    try {
      const res = await fetch(DATA_URL, { cache: "no-cache" });
      if (!res.ok) throw new Error("HTTP " + res.status);
      DATA = await res.json();
    } catch (err) {
      console.warn("[unc-pmap] partnerships.json unavailable:", err);
      $("#pmap-sub").innerHTML = "Partnership inventory not built yet — run " +
        "<code>python scripts/build_unc.py</code> and commit " +
        "<code>frontend/partnerships.json</code>.";
      return;
    }
    if (!DATA || !DATA.partnerships) { console.warn("[unc-pmap] empty inventory"); return; }
    const m = DATA.meta || {};
    $("#pmap-sub").textContent =
      `${m.n_units || 0} units · ${m.n_faculty || 0} faculty · ` +
      `${m.n_partnerships || 0} partnerships · ${m.n_companies || 0} companies in public data.`;
    populateFilters();
    wire();
    renderCards();
    renderTable();
  }

  // ── filter dropdowns ─────────────────────────────────────────────────────────
  function populateFilters() {
    const ps = DATA.partnerships;
    const fill = (sel, values) => {
      const el = $(sel);
      [...new Set(values.filter(Boolean))].sort().forEach(v => {
        const o = document.createElement("option"); o.value = v; o.textContent = v; el.appendChild(o);
      });
    };
    fill("#pmap-f-area", ps.map(p => p.area));
    fill("#pmap-f-status", ps.map(p => p.status));
    fill("#pmap-f-tier", ps.map(p => p.verification_tier));
  }

  // ── cards / rows ─────────────────────────────────────────────────────────────
  function renderCards() {
    const grid = $("#pmap-grid");
    grid.classList.toggle("list", state.view === "faculty");
    if (state.view === "faculty") { grid.innerHTML = facultyRows(); wireFacultyRows(); return; }
    const want = state.view === "schools"
      ? (u) => u.unit_type === "School" || u.unit_type === "Department"
      : (u) => LAB_TYPES.has(u.unit_type);
    const units = (DATA.units || []).filter(want)
      .sort((a, b) => (b.partnership_count || 0) - (a.partnership_count || 0) ||
                      (a.unit_name || "").localeCompare(b.unit_name || ""));
    grid.innerHTML = units.length ? units.map(unitCard).join("")
      : `<div class="pmap-empty">No ${esc(state.view)} units found.</div>`;
    grid.querySelectorAll(".pmap-card").forEach(card =>
      card.addEventListener("click", () => {
        state.unitId = state.unitId === card.dataset.uid ? null : card.dataset.uid;
        state.facultyId = null;
        markActive(grid, ".pmap-card", "uid", state.unitId);
        renderTable();
      }));
  }

  function unitCard(u) {
    const cos = (u.top_companies || []).map(c => `<span class="pmap-co">${esc(c)}</span>`).join("");
    return `
      <button class="pmap-card${state.unitId === u.unit_id ? " active" : ""}" data-uid="${esc(u.unit_id)}">
        <div class="pmap-card-top">
          <span class="pmap-card-name">${esc(u.unit_name)}</span>
          <span class="pmap-type">${esc(u.unit_type || "Unit")}</span>
        </div>
        <div class="pmap-card-stat"><b>${u.partnership_count || 0}</b> partnership${u.partnership_count === 1 ? "" : "s"}</div>
        ${cos ? `<div class="pmap-cos">${cos}</div>` : ""}
      </button>`;
  }

  function facultyRows() {
    const fac = (DATA.faculty || [])
      .filter(f => (f.partnership_count || 0) > 0)
      .sort((a, b) => (b.partnership_count || 0) - (a.partnership_count || 0))
      .slice(0, 200);
    if (!fac.length) return `<div class="pmap-empty">No faculty with public partnerships found.</div>`;
    return fac.map(f => `
      <div class="pmap-faculty-row${state.facultyId === f.faculty_id ? " active" : ""}" data-fid="${esc(f.faculty_id)}">
        <div><div class="pmap-fac-name">${esc(f.full_name)}</div>
          <div class="pmap-fac-sub">${esc(f.title || "Faculty")}</div></div>
        <div class="pmap-fac-mid pmap-fac-sub">${esc(f.unit_name || "")}${f.top_company ? " · " + esc(f.top_company) : ""}</div>
        <div class="pmap-fac-num"><b>${f.partnership_count}</b> partnership${f.partnership_count === 1 ? "" : "s"}</div>
      </div>`).join("");
  }

  function wireFacultyRows() {
    $("#pmap-grid").querySelectorAll(".pmap-faculty-row").forEach(row =>
      row.addEventListener("click", () => {
        state.facultyId = state.facultyId === row.dataset.fid ? null : row.dataset.fid;
        state.unitId = null;
        markActive($("#pmap-grid"), ".pmap-faculty-row", "fid", state.facultyId);
        renderTable();
      }));
  }

  function markActive(root, sel, attr, value) {
    root.querySelectorAll(sel).forEach(el =>
      el.classList.toggle("active", value != null && el.dataset[attr] === value));
  }

  // ── table ────────────────────────────────────────────────────────────────────
  function currentRows() {
    let rows = DATA.partnerships;
    if (state.unitId) rows = rows.filter(p => p.unit_id === state.unitId);
    if (state.facultyId) rows = rows.filter(p => p.faculty_id === state.facultyId);
    if (state.area) rows = rows.filter(p => p.area === state.area);
    if (state.status) rows = rows.filter(p => p.status === state.status);
    if (state.tier) rows = rows.filter(p => p.verification_tier === state.tier);
    return rows;
  }

  function renderTable() {
    const rows = currentRows();
    const tbody = $("#pmap-tbody");
    const label = state.unitId ? unitName(state.unitId)
      : state.facultyId ? facultyName(state.facultyId) : "All partnerships";
    $("#pmap-selected").textContent = label;
    if (!rows.length) {
      tbody.innerHTML = `<tr><td colspan="6"><div class="pmap-empty">No public partnerships match this selection — yet. Absence here means none were found in public sources, not that none exist.</div></td></tr>`;
      $("#pmap-tablecount").textContent = "";
      return;
    }
    const shown = rows.slice(0, 300);
    tbody.innerHTML = shown.map(rowHtml).join("");
    $("#pmap-tablecount").textContent =
      `Showing ${shown.length} of ${rows.length} partnership${rows.length === 1 ? "" : "s"}` +
      (rows.length > shown.length ? " (refine with filters or the Excel export for the full set)" : "");
  }

  function rowHtml(p) {
    const tier = p.verification_tier || "";
    const date = p.start_date || p.date_of_research || "";
    const fund = fmtUSD(p.funding_value);
    return `
      <tr>
        <td>${esc(p.area || "")}</td>
        <td><span class="pmap-company">${esc(p.company_name || "—")}</span>
          ${p.description ? `<span class="pmap-desc">${esc(p.description)}</span>` : ""}</td>
        <td>${esc(p.status || "—")}${fund ? `<span class="pmap-desc">${esc(fund)}</span>` : ""}</td>
        <td><span class="ptier ${TIER_CLASS[tier] || "inferred"}">${esc(tier || "—")}</span></td>
        <td>${p.source_url ? `<a class="pmap-src" href="${esc(p.source_url)}" target="_blank" rel="noopener">Source ↗</a>` : "—"}</td>
        <td>${esc(date)}</td>
      </tr>`;
  }

  const unitName = (id) => (DATA.units.find(u => u.unit_id === id) || {}).unit_name || id;
  const facultyName = (id) => (DATA.faculty.find(f => f.faculty_id === id) || {}).full_name || id;

  // ── wiring ───────────────────────────────────────────────────────────────────
  function wire() {
    $("#pmap-tabs").querySelectorAll(".pmap-tab").forEach(tab =>
      tab.addEventListener("click", () => {
        $("#pmap-tabs").querySelectorAll(".pmap-tab").forEach(t => t.classList.remove("active"));
        tab.classList.add("active");
        state.view = tab.dataset.view;
        state.unitId = null; state.facultyId = null;
        renderCards();
        renderTable();
      }));
    $("#pmap-f-area").addEventListener("change", e => { state.area = e.target.value; renderTable(); });
    $("#pmap-f-status").addEventListener("change", e => { state.status = e.target.value; renderTable(); });
    $("#pmap-f-tier").addEventListener("change", e => { state.tier = e.target.value; renderTable(); });
    $("#pmap-clear").addEventListener("click", () => {
      state.unitId = null; state.facultyId = null; state.area = state.status = state.tier = "";
      $("#pmap-f-area").value = ""; $("#pmap-f-status").value = ""; $("#pmap-f-tier").value = "";
      renderCards(); renderTable();
    });
    $("#pmap-export").addEventListener("click", () => {
      // Hit the API endpoint, which generates the xlsx from the same data.
      window.location.href = "partnerships/export";
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
