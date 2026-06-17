"""
Vercel serverless API for UNC Research Graph.
Loads graph.json at cold-start and serves matching entirely in-memory.
No DuckDB, no API keys, no external calls at request time.
"""
from __future__ import annotations
import json
import math
import re
import os
from pathlib import Path
from functools import lru_cache
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote

# ── data loading ────────────────────────────────────────────────────────────

GRAPH_PATH = Path(__file__).parent.parent / "frontend" / "graph.json"

@lru_cache(maxsize=1)
def _load_graph() -> dict:
    # Graceful cold-start: a missing or malformed graph.json must degrade to an
    # empty graph (HTTP 200 "degraded"), never crash the function with a 500.
    if not GRAPH_PATH.exists():
        return {"meta": {"built_at": None, "n_companies": 0, "n_units_with_data": 0}, "units": [], "companies": [], "_empty": True}
    try:
        with open(GRAPH_PATH) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        return {"meta": {"built_at": None, "error": str(exc)}, "units": [], "companies": [], "_empty": True}

def _graph():
    return _load_graph()

PARTNERSHIPS_PATH = Path(__file__).parent.parent / "frontend" / "partnerships.json"

@lru_cache(maxsize=1)
def _load_partnerships() -> dict:
    # Same graceful cold-start contract as the graph: missing/malformed
    # partnerships.json degrades to an empty inventory, never a 500.
    empty = {"meta": {"built_at": None, "n_units": 0, "n_faculty": 0, "n_partnerships": 0},
             "units": [], "faculty": [], "partnerships": [], "_empty": True}
    if not PARTNERSHIPS_PATH.exists():
        return empty
    try:
        with open(PARTNERSHIPS_PATH) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        return {**empty, "meta": {"error": str(exc)}}

def _partnerships():
    return _load_partnerships()

# ── normalisation helpers ────────────────────────────────────────────────────

_STRIP = re.compile(r"\b(inc|llc|corp|co|ltd|plc|lp|incorporated|corporation|limited|company)\b\.?", re.I)
_PUNCT = re.compile(r"[^\w\s&]")
_WS    = re.compile(r"\s+")

def norm_name(s: str) -> str:
    s = (s or "").lower()
    s = _STRIP.sub(" ", s)
    s = _PUNCT.sub(" ", s)
    return _WS.sub(" ", s).strip()

STOPWORDS = set("the a an and or of in to for with on at by from is are was inc llc corp co ltd company group holdings international plc the study research".split())

def tokenize(s: str) -> set[str]:
    tokens = re.sub(r"[^\w\s]", " ", (s or "").lower()).split()
    return {t for t in tokens if len(t) >= 3 and t not in STOPWORDS}

# ── matching ─────────────────────────────────────────────────────────────────

def match_company(query: str, graph: dict) -> dict | None:
    q = norm_name(query)
    if len(q) < 2:
        return None
    companies = graph.get("companies", [])
    # exact
    for c in companies:
        if norm_name(c["name"]) == q:
            return c
    # substring
    subs = [c for c in companies if norm_name(c["name"]) in q or q in norm_name(c["name"])]
    if subs:
        return max(subs, key=lambda c: c.get("total_edges", 0))
    return None

def match_topical(query: str, graph: dict, top_n: int = 6) -> list[dict]:
    q = tokenize(query)
    if not q:
        return []
    results = []
    for u in graph.get("units", []):
        kw = u.get("keywords") or []
        if not kw:
            continue
        kw_set = set(kw)
        hits = [t for t in q if t in kw_set]
        if not hits:
            continue
        score = len(hits) / math.sqrt(len(q) * len(kw))
        results.append({"unit": u, "score": score, "hits": hits})
    results.sort(key=lambda x: -x["score"])
    return results[:top_n]

# ── response helpers ──────────────────────────────────────────────────────────

def json_response(data, status=200):
    body = json.dumps(data, ensure_ascii=False).encode()
    return status, {"Content-Type": "application/json"}, body

def cors_headers():
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, OPTIONS",
        "Access-Control-Allow-Headers": "*",
    }

# ── partnership inventory helpers ─────────────────────────────────────────────

def _cors_json(data, status=200):
    s, headers, body = json_response(data, status)
    return s, {**headers, **cors_headers()}, body

def _unit_tree(units: list) -> list:
    """Nest units into schools -> departments -> labs/centers via parent_unit_id."""
    by_id = {u["unit_id"]: {**u, "children": []} for u in units}
    roots = []
    for u in by_id.values():
        parent = u.get("parent_unit_id")
        if parent and parent in by_id and parent != u["unit_id"]:
            by_id[parent]["children"].append(u)
        else:
            roots.append(u)
    return roots

def _filter_partnerships(rows: list, qs: dict) -> list:
    """Apply optional ?area=&status=&tier=&unit_id= filters."""
    def first(key):
        v = qs.get(key)
        return v[0] if v else None
    out = rows
    for key, field in (("area", "area"), ("status", "status"),
                       ("tier", "verification_tier"), ("unit_id", "unit_id")):
        val = first(key)
        if val:
            out = [r for r in out if r.get(field) == val]
    return out

_XLSX_UNIT_COLS = ["unit_id", "parent_unit_id", "unit_name", "unit_type", "description",
                   "focus_areas", "disciplines", "faculty_count", "student_count",
                   "website_url", "research_by", "date_of_research", "notes"]
_XLSX_PARTNERSHIP_COLS = ["unit_name", "unit_type", "faculty_name", "area", "company_name",
                          "description", "status", "start_date", "end_date", "recurring",
                          "funding_value", "funding_type", "unc_poc", "company_poc",
                          "source_url", "verification_tier", "verification_notes",
                          "research_by", "date_of_research"]
_XLSX_FACULTY_COLS = ["full_name", "title", "unit_name", "profile_url", "openalex_id",
                      "research_by", "date_of_research"]
_XLSX_TIER_FILL = {"Verified": "C6EFCE", "Reported": "FFEB9C", "Inferred": "D9D9D9"}

def _build_inventory_xlsx(data: dict) -> bytes:
    """Generate UNC_Partnership_Inventory.xlsx in-memory from partnerships.json."""
    from io import BytesIO
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    def sheet(ws, columns, rows):
        ws.append(columns)
        for cell in ws[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="1D1D1F")
        tier_idx = columns.index("verification_tier") + 1 if "verification_tier" in columns else None
        for r in rows:
            ws.append([r.get(c) for c in columns])
            if tier_idx:
                color = _XLSX_TIER_FILL.get(r.get("verification_tier"))
                if color:
                    ws.cell(row=ws.max_row, column=tier_idx).fill = PatternFill("solid", fgColor=color)
        ws.freeze_panes = "A2"

    wb = Workbook()
    sheet(wb.active, _XLSX_UNIT_COLS, data.get("units", []))
    wb.active.title = "UNC_Units"
    sheet(wb.create_sheet("Partnerships"), _XLSX_PARTNERSHIP_COLS, data.get("partnerships", []))
    sheet(wb.create_sheet("Faculty"), _XLSX_FACULTY_COLS, data.get("faculty", []))
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ── route handler ─────────────────────────────────────────────────────────────

def handle(method: str, path: str, qs: dict) -> tuple[int, dict, bytes]:
    graph = _graph()

    if method == "OPTIONS":
        return 204, cors_headers(), b""

    # /health
    if path in ("/health", "/api/health"):
        meta = graph.get("meta", {})
        if graph.get("_empty"):
            # Graph not built / unreadable — report degraded but stay HTTP 200 so
            # the frontend health check doesn't treat it as a hard failure.
            status, headers, body = json_response({"status": "degraded", "reason": "graph not built", "service": "unc-research-graph"})
            return status, {**headers, **cors_headers()}, body
        status, headers, body = json_response({"status": "ok", "service": "unc-research-graph", "n_companies": meta.get("n_companies"), "n_units": meta.get("n_units_with_data"), "built_at": meta.get("built_at")})
        return status, {**headers, **cors_headers()}, body

    # /stats
    if path in ("/stats", "/api/stats"):
        meta = graph.get("meta", {})
        status, headers, body = json_response({"meta": meta, "n_units": len(graph.get("units", [])), "n_companies": len(graph.get("companies", []))})
        return status, {**headers, **cors_headers()}, body

    # /freshness — the build/coverage stamp referenced by the mission
    if path in ("/freshness", "/api/freshness"):
        meta = graph.get("meta", {})
        status, headers, body = json_response({
            "built_at": meta.get("built_at"),
            "n_companies": meta.get("n_companies", 0),
            "n_units_with_data": meta.get("n_units_with_data", 0),
            "counts": meta.get("counts", {}),
        })
        return status, {**headers, **cors_headers()}, body

    # /graph — full node/link graph for the network view (API-driven, so the
    # frontend never reads the static graph.json file directly).
    if path in ("/graph", "/api/graph"):
        meta = graph.get("meta", {})
        status, headers, body = json_response({
            "meta": meta,
            "units": graph.get("units", []),
            "companies": graph.get("companies", []),
        })
        return status, {**headers, **cors_headers()}, body

    # /match/{company}
    m = re.match(r"^/?(?:api/)?match/(.+)$", path)
    if m:
        company_name = unquote(m.group(1))
        sector_hint = (qs.get("sector_hint") or [""])[0]
        top_n = int((qs.get("top_n") or ["10"])[0])

        co = match_company(company_name, graph)
        topical = match_topical(company_name + " " + sector_hint, graph, top_n)

        unit_by_id = {u["id"]: u for u in graph.get("units", [])}
        # Enrich each company→unit link with a human-readable unit name so the
        # frontend shows "UNC School of Medicine" instead of the raw id "unc:som".
        if co and co.get("units"):
            co = {**co, "units": [
                {**cu, "unit_name": cu.get("unit_name") or (unit_by_id.get(cu.get("unit_id"), {}).get("name")) or cu.get("unit_id")}
                for cu in co["units"]
            ]}
        topical_out = []
        for item in topical:
            u = item["unit"]
            topical_out.append({"unit_id": u["id"], "unit_name": u.get("name"), "score": round(item["score"], 4), "hits": item["hits"], "footprint": u.get("footprint", {})})

        result = {
            "query": company_name,
            "company": co,
            "topical_matches": topical_out,
        }
        status, headers, body = json_response(result)
        return status, {**headers, **cors_headers()}, body

    # /unit/{id}/profile
    m = re.match(r"^/?(?:api/)?unit/(.+)/profile$", path)
    if m:
        unit_id = unquote(m.group(1))
        unit_by_id = {u["id"]: u for u in graph.get("units", [])}
        u = unit_by_id.get(unit_id)
        if not u:
            status, headers, body = json_response({"error": f"Unit '{unit_id}' not found"}, 404)
            return status, {**headers, **cors_headers()}, body
        status, headers, body = json_response(u)
        return status, {**headers, **cors_headers()}, body

    # ── partnership inventory endpoints ──────────────────────────────────────
    pg = _partnerships()

    if path in ("/units", "/api/units"):
        return _cors_json(pg.get("units", []))
    if path in ("/units/tree", "/api/units/tree"):
        return _cors_json(_unit_tree(pg.get("units", [])))

    if path in ("/faculty", "/api/faculty"):
        return _cors_json(pg.get("faculty", []))
    m = re.match(r"^/?(?:api/)?faculty/(.+)$", path)
    if m:
        fid = unquote(m.group(1))
        fac = next((f for f in pg.get("faculty", []) if f.get("faculty_id") == fid), None)
        if not fac:
            return _cors_json({"error": f"Faculty '{fid}' not found"}, 404)
        ps = [p for p in pg.get("partnerships", []) if p.get("faculty_id") == fid]
        return _cors_json({**fac, "partnerships": ps})

    # export must precede the bare /partnerships match
    if path in ("/partnerships/export", "/api/partnerships/export"):
        if pg.get("_empty"):
            return _cors_json({"error": "inventory not built"}, 503)
        xlsx = _build_inventory_xlsx(pg)
        headers = {
            "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "Content-Disposition": "attachment; filename=UNC_Partnership_Inventory.xlsx",
            **cors_headers(),
        }
        return 200, headers, xlsx

    if path in ("/partnerships", "/api/partnerships"):
        rows = _filter_partnerships(pg.get("partnerships", []), qs)
        return _cors_json({"count": len(rows), "partnerships": rows})

    # /unit/{id}/partnerships, /unit/{id}/faculty, /unit/{id}
    m = re.match(r"^/?(?:api/)?unit/(.+)/partnerships$", path)
    if m:
        uid = unquote(m.group(1))
        rows = [p for p in pg.get("partnerships", []) if p.get("unit_id") == uid]
        return _cors_json({"unit_id": uid, "count": len(rows), "partnerships": rows})
    m = re.match(r"^/?(?:api/)?unit/(.+)/faculty$", path)
    if m:
        uid = unquote(m.group(1))
        fac = [f for f in pg.get("faculty", []) if f.get("unit_id") == uid]
        return _cors_json({"unit_id": uid, "count": len(fac), "faculty": fac})
    m = re.match(r"^/?(?:api/)?unit/([^/]+)$", path)
    if m:
        uid = unquote(m.group(1))
        unit = next((u for u in pg.get("units", []) if u.get("unit_id") == uid), None)
        if not unit:
            return _cors_json({"error": f"Unit '{uid}' not found"}, 404)
        return _cors_json(unit)

    # 404
    status, headers, body = json_response({"error": "Not found", "path": path}, 404)
    return status, {**headers, **cors_headers()}, body


# ── Vercel handler ─────────────────────────────────────────────────────────────

class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self._respond("OPTIONS")

    def do_GET(self):
        self._respond("GET")

    def _respond(self, method):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        status, headers, body = handle(method, parsed.path, qs)
        self.send_response(status)
        for k, v in headers.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass
