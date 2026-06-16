"""
Combined scorer — merges evidence + topical modes into a ranked match list.

Formula:
  final_score = 0.7 * normalize(evidence_score) + 0.3 * topical_score

Confidence tiers:
  confirmed     → at least one confirmed edge exists
  probable      → only probable edges, but evidence_score > 0
  topical_only  → evidence_score == 0, topical overlap only
"""
from __future__ import annotations
import json
import logging
from typing import Any

from ..graph import store
from . import evidence as ev_mod
from . import topical as top_mod

logger = logging.getLogger(__name__)


def _normalize(scores: list[float]) -> list[float]:
    if not scores:
        return scores
    mn, mx = min(scores), max(scores)
    if mx == mn:
        return [1.0 if s > 0 else 0.0 for s in scores]
    return [(s - mn) / (mx - mn) for s in scores]


def _resolve_company_id(company_name: str) -> str | None:
    """Find a company node ID by normalized name."""
    from ..transform.entity_resolver import normalize_company_name, company_id_from_name
    norm = normalize_company_name(company_name)
    node_id = company_id_from_name(norm)

    with store.connection(read_only=True) as conn:
        row = conn.execute(
            "SELECT id FROM nodes_companies WHERE id = ? OR normalized_name = ? LIMIT 1",
            [node_id, norm],
        ).fetchone()
    return row[0] if row else None


def _get_unit_info(unit_id: str) -> dict:
    with store.connection(read_only=True) as conn:
        row = conn.execute(
            "SELECT canonical_name, short_name FROM nodes_unc_units WHERE id = ?",
            [unit_id],
        ).fetchone()
    if row:
        return {"canonical_name": row[0], "short_name": row[1]}
    return {"canonical_name": unit_id, "short_name": None}


def _get_top_faculty(faculty_ids: list[str], limit: int = 5) -> list[str]:
    if not faculty_ids:
        return []
    placeholders = ",".join("?" * len(faculty_ids[:20]))
    sql = f"SELECT full_name FROM nodes_faculty WHERE id IN ({placeholders}) LIMIT {limit}"
    with store.connection(read_only=True) as conn:
        rows = conn.execute(sql, faculty_ids[:20]).fetchall()
    return [r[0] for r in rows]


def match(company_name: str, sector_hint: str = "", top_n: int = 10) -> dict[str, Any]:
    """
    Main entry point.
    Returns a dict with company info and ranked UNC unit matches.
    """
    company_id = _resolve_company_id(company_name)

    # Mode 1: evidence
    ev_results = ev_mod.score_company(company_id) if company_id else []

    # Mode 2: topical
    top_results = top_mod.score_company(company_name, sector_hint)

    # Index topical scores by unit_id
    top_index = {r["unit_id"]: r["topical_score"] for r in top_results}

    # Collect all unit IDs from both modes
    unit_ids = set(r["unit_id"] for r in ev_results) | set(top_index.keys())

    # Build combined rows
    ev_index = {r["unit_id"]: r for r in ev_results}
    ev_scores_raw = [ev_index.get(u, {}).get("evidence_score", 0.0) for u in unit_ids]
    ev_norm = _normalize(ev_scores_raw)

    rows = []
    for uid, ev_norm_score in zip(unit_ids, ev_norm):
        ev = ev_index.get(uid, {})
        t_score = top_index.get(uid, 0.0)
        final = round(0.7 * ev_norm_score + 0.3 * t_score, 4)

        ev_score = ev.get("evidence_score", 0.0)
        if ev.get("confidence") == "confirmed":
            conf = "confirmed"
        elif ev_score > 0:
            conf = "probable"
        else:
            conf = "topical_only"

        unit_info = _get_unit_info(uid)
        top_faculty = _get_top_faculty(ev.get("faculty_ids", []))

        rows.append({
            "unc_unit": unit_info["canonical_name"],
            "unc_unit_id": uid,
            "short_name": unit_info["short_name"],
            "evidence_score": round(ev_score, 2),
            "evidence_score_normalized": round(ev_norm_score, 4),
            "topical_score": round(t_score, 4),
            "final_score": final,
            "confidence": conf,
            "top_faculty": top_faculty,
            "evidence_summary": ev.get("counts", {}),
            "total_usd": ev.get("total_usd", 0),
        })

    rows.sort(key=lambda x: -x["final_score"])
    top_rows = [r for r in rows if r["final_score"] > 0][:top_n]

    return {
        "company": company_name,
        "company_id": company_id,
        "has_evidence": bool(ev_results),
        "matches": top_rows,
    }
