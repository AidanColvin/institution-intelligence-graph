"""
Standalone FastAPI service for querying the precomputed UNC research graph.
Runs separately from map-elt-research-graph-3.

Endpoints:
  GET /health
  GET /match/{company_name}?sector_hint=...&top_n=10
  GET /unit/{unit_id}/edges?edge_type=...&limit=50
  GET /unit/{unit_id}/profile
  GET /stats
"""
from __future__ import annotations
import logging
import os
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from ..matcher import scorer
from ..graph import store

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="UNC Research Graph API",
    description="Precomputed graph of UNC-Chapel Hill's public research footprint.",
    version="0.1.0",
)

_ALLOWED_ORIGINS = [
    o.strip()
    for o in os.environ.get(
        "ALLOWED_ORIGINS",
        "https://institution-intelligence-graph.vercel.app,"
        "https://map-omega-azure.vercel.app,"
        "http://localhost:3000",
    ).split(",")
    if o.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "unc-research-graph"}


@app.get("/match/{company_name}")
def match_company(
    company_name: str,
    sector_hint: str = Query("", description="Optional sector/domain hint for topical scoring"),
    top_n: int = Query(10, ge=1, le=50),
) -> dict:
    """
    Find UNC units most aligned with a given company.
    Returns ranked list with evidence + topical scores.
    """
    try:
        result = scorer.match(company_name, sector_hint=sector_hint, top_n=top_n)
    except Exception as exc:
        logger.error("Match failed for %s: %s", company_name, exc)
        raise HTTPException(status_code=500, detail=str(exc))
    return result


@app.get("/unit/{unit_id}/edges")
def unit_edges(
    unit_id: str,
    edge_type: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
) -> dict:
    """List the most recent edges for a UNC unit."""
    conditions = ["target_node = ?"]
    params: list = [unit_id]
    if edge_type:
        conditions.append("edge_type = ?")
        params.append(edge_type)
    where = " AND ".join(conditions)

    sql = f"""
        SELECT id, edge_type, source_node, faculty_id, record_id,
               source_url, record_date, confidence, amount_usd, edge_metadata
        FROM edges
        WHERE {where}
        ORDER BY record_date DESC NULLS LAST
        LIMIT ?
    """
    params.append(limit)

    with store.connection(read_only=True) as conn:
        rows = conn.execute(sql, params).fetchall()

    import json
    edges = []
    for row in rows:
        edges.append({
            "id": row[0],
            "edge_type": row[1],
            "source_node": row[2],
            "faculty_id": row[3],
            "record_id": row[4],
            "source_url": row[5],
            "record_date": row[6],
            "confidence": row[7],
            "amount_usd": row[8],
            "metadata": json.loads(row[9]) if row[9] else {},
        })
    return {"unit_id": unit_id, "edge_type_filter": edge_type, "edges": edges}


@app.get("/unit/{unit_id}/profile")
def unit_profile(unit_id: str) -> dict:
    """Return the topic profile and metadata for a UNC unit."""
    with store.connection(read_only=True) as conn:
        unit = conn.execute(
            "SELECT id, canonical_name, short_name, ror_id, parent_id, aliases, topic_keywords "
            "FROM nodes_unc_units WHERE id = ?",
            [unit_id],
        ).fetchone()
        profile = conn.execute(
            "SELECT keywords, concept_codes, updated_at FROM topic_profiles WHERE unit_id = ?",
            [unit_id],
        ).fetchone()

    if not unit:
        raise HTTPException(status_code=404, detail=f"Unit '{unit_id}' not found")

    import json
    return {
        "id": unit[0],
        "canonical_name": unit[1],
        "short_name": unit[2],
        "ror_id": unit[3],
        "parent_id": unit[4],
        "aliases": json.loads(unit[5]) if unit[5] else [],
        "topic_keywords": json.loads(unit[6]) if unit[6] else [],
        "topic_profile": {
            "keywords": json.loads(profile[0]) if profile else [],
            "concept_codes": json.loads(profile[1]) if profile else [],
            "updated_at": profile[2] if profile else None,
        } if profile else None,
    }


@app.get("/stats")
def stats() -> dict:
    """Graph statistics — record counts per table."""
    tables = [
        "raw_nih_grants", "raw_nsf_awards", "raw_usaspending",
        "raw_clinical_trials", "raw_crossref_papers",
        "nodes_unc_units", "nodes_faculty", "nodes_companies",
        "edges", "topic_profiles",
    ]
    counts = {}
    for table in tables:
        try:
            counts[table] = store.count_raw(table)
        except Exception:
            counts[table] = -1

    with store.connection(read_only=True) as conn:
        edge_by_type = conn.execute(
            "SELECT edge_type, COUNT(*) FROM edges GROUP BY edge_type"
        ).fetchall()

    return {
        "table_counts": counts,
        "edges_by_type": dict(edge_by_type),
    }
