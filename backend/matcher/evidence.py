"""
Mode 1 — Evidence scorer.
For a company node ID, query all edges where source_node = company_id.
Returns weighted counts per UNC unit.
"""
from __future__ import annotations
from typing import Any

from ..graph import store

# Weights by edge type (tunable)
WEIGHTS: dict[str, float] = {
    "trial": 4.0,
    "grant": 3.0,
    "contract": 2.0,
    "paper": 2.0,
    "patent": 2.0,
}


def score_company(company_id: str) -> list[dict[str, Any]]:
    """
    Return a list of {unit_id, evidence_score, confidence, counts, faculty}
    for every UNC unit that shares an edge with company_id.
    Sorted descending by evidence_score.
    """
    sql = """
        SELECT
            e.target_node AS unit_id,
            e.edge_type,
            e.confidence,
            e.faculty_id,
            COUNT(*) AS cnt,
            SUM(COALESCE(e.amount_usd, 0)) AS total_usd
        FROM edges e
        WHERE e.source_node = ?
        GROUP BY e.target_node, e.edge_type, e.confidence, e.faculty_id
    """
    with store.connection(read_only=True) as conn:
        rows = conn.execute(sql, [company_id]).fetchall()

    # Aggregate per unit
    unit_data: dict[str, dict] = {}
    for unit_id, edge_type, confidence, faculty_id, cnt, total_usd in rows:
        if unit_id not in unit_data:
            unit_data[unit_id] = {
                "unit_id": unit_id,
                "evidence_score": 0.0,
                "confidence": "probable",
                "counts": {},
                "total_usd": 0,
                "faculty_ids": set(),
            }
        w = WEIGHTS.get(edge_type, 1.0)
        unit_data[unit_id]["evidence_score"] += w * cnt
        unit_data[unit_id]["counts"][edge_type] = (
            unit_data[unit_id]["counts"].get(edge_type, 0) + cnt
        )
        unit_data[unit_id]["total_usd"] += total_usd or 0
        if faculty_id:
            unit_data[unit_id]["faculty_ids"].add(faculty_id)
        if confidence == "confirmed":
            unit_data[unit_id]["confidence"] = "confirmed"

    results = []
    for u in unit_data.values():
        u["faculty_ids"] = list(u["faculty_ids"])
        results.append(u)

    results.sort(key=lambda x: -x["evidence_score"])
    return results
