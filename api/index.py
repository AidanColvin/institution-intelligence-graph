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

    # /match/{company}
    m = re.match(r"^/?(?:api/)?match/(.+)$", path)
    if m:
        company_name = unquote(m.group(1))
        sector_hint = (qs.get("sector_hint") or [""])[0]
        top_n = int((qs.get("top_n") or ["10"])[0])

        co = match_company(company_name, graph)
        topical = match_topical(company_name + " " + sector_hint, graph, top_n)

        unit_by_id = {u["id"]: u for u in graph.get("units", [])}
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
