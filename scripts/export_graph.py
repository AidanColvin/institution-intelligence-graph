"""
Export the DuckDB graph into a compact static graph.json for the browser frontend.

The frontend runs both matcher modes client-side against this file:
  - evidence mode  -> direct lookup in `companies[]`
  - topical mode   -> Jaccard of query tokens against `units[].keywords`

Usage:
  python scripts/export_graph.py [--db graph.db] [--out frontend/graph.json] [--built-at ISO]
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

EDGE_WEIGHTS = {"trial": 4.0, "grant": 3.0, "contract": 2.0, "paper": 2.0, "patent": 2.0}
SAMPLES_PER_UNIT = 3
TOP_FACULTY = 10
TOP_COMPANIES = 12


def export(db_path: str, out_path: str, built_at: str) -> dict:
    os.environ["GRAPH_DB_PATH"] = db_path
    from backend.graph import store

    with store.connection(read_only=True) as conn:
        units = _export_units(conn)
        companies = _export_companies(conn)
        counts = _table_counts(conn)

    payload = {
        "meta": {
            "built_at": built_at,
            "unc_ror": "0130frc33",
            "anchor": "University of North Carolina at Chapel Hill",
            "edge_weights": EDGE_WEIGHTS,
            "counts": counts,
            "n_units_with_data": len([u for u in units if u["footprint"]["total"] > 0]),
            "n_companies": len(companies),
        },
        "units": units,
        "companies": companies,
    }

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    size_kb = out.stat().st_size / 1024
    print(f"Wrote {out_path} ({size_kb:.0f} KB): "
          f"{len(units)} units, {len(companies)} companies, "
          f"{counts.get('edges', 0)} edges")
    return payload


def _table_counts(conn) -> dict:
    tables = [
        "raw_nih_grants", "raw_nsf_awards", "raw_usaspending",
        "raw_clinical_trials", "raw_crossref_papers",
        "nodes_unc_units", "nodes_faculty", "nodes_companies", "edges",
    ]
    out = {}
    for t in tables:
        try:
            out[t.replace("raw_", "").replace("nodes_", "")] = conn.execute(
                f"SELECT COUNT(*) FROM {t}"
            ).fetchone()[0]
        except Exception:
            out[t] = 0
    return out


def _export_units(conn) -> list[dict]:
    units = []
    rows = conn.execute(
        "SELECT id, canonical_name, short_name, parent_id FROM nodes_unc_units ORDER BY id"
    ).fetchall()

    for uid, name, short, parent in rows:
        # Footprint: UNC-internal edges by type + funding
        fp_rows = conn.execute(
            """SELECT edge_type, COUNT(*), COALESCE(SUM(amount_usd),0)
               FROM edges WHERE target_node = ? AND source_node = 'unc:root'
               GROUP BY edge_type""",
            [uid],
        ).fetchall()
        footprint = {"grant": 0, "paper": 0, "trial": 0, "contract": 0, "total": 0, "total_usd": 0}
        for etype, cnt, usd in fp_rows:
            footprint[etype] = cnt
            footprint["total"] += cnt
            footprint["total_usd"] += int(usd or 0)

        # Keywords from topic profile
        kw_row = conn.execute(
            "SELECT keywords FROM topic_profiles WHERE unit_id = ?", [uid]
        ).fetchone()
        keywords = json.loads(kw_row[0]) if kw_row and kw_row[0] else []

        # Top faculty by edge count
        fac = conn.execute(
            """SELECT f.full_name, COUNT(*) AS c
               FROM edges e JOIN nodes_faculty f ON e.faculty_id = f.id
               WHERE e.target_node = ? AND e.faculty_id IS NOT NULL
               GROUP BY f.full_name ORDER BY c DESC LIMIT ?""",
            [uid, TOP_FACULTY],
        ).fetchall()
        top_faculty = [{"name": n, "count": c} for n, c in fac]

        # Top partner companies by edge count
        co = conn.execute(
            """SELECT c.canonical_name, c.id, c.confidence, COUNT(*) AS cnt
               FROM edges e JOIN nodes_companies c ON e.source_node = c.id
               WHERE e.target_node = ?
               GROUP BY c.canonical_name, c.id, c.confidence ORDER BY cnt DESC LIMIT ?""",
            [uid, TOP_COMPANIES],
        ).fetchall()
        top_companies = [
            {"name": n, "id": cid, "confidence": conf, "count": cnt}
            for n, cid, conf, cnt in co
        ]

        units.append({
            "id": uid, "name": name, "short_name": short, "parent_id": parent,
            "footprint": footprint, "keywords": keywords,
            "top_faculty": top_faculty, "top_companies": top_companies,
        })
    return units


def _export_companies(conn) -> list[dict]:
    companies = []
    rows = conn.execute(
        """SELECT c.id, c.canonical_name, c.sec_cik, c.confidence, COUNT(e.id) AS n
           FROM nodes_companies c JOIN edges e ON e.source_node = c.id
           GROUP BY c.id, c.canonical_name, c.sec_cik, c.confidence
           HAVING n > 0 ORDER BY n DESC""",
    ).fetchall()

    for cid, name, cik, conf, n_edges in rows:
        # Per-unit aggregation for this company
        unit_rows = conn.execute(
            """SELECT target_node, edge_type, confidence, COUNT(*) AS cnt
               FROM edges WHERE source_node = ?
               GROUP BY target_node, edge_type, confidence""",
            [cid],
        ).fetchall()

        units_map: dict[str, dict] = {}
        for unit_id, etype, ec, cnt in unit_rows:
            u = units_map.setdefault(unit_id, {
                "unit_id": unit_id, "counts": {}, "score": 0.0, "confidence": "probable",
            })
            u["counts"][etype] = u["counts"].get(etype, 0) + cnt
            u["score"] += EDGE_WEIGHTS.get(etype, 1.0) * cnt
            if ec == "confirmed":
                u["confidence"] = "confirmed"

        # Attach up to N evidence samples (with source URLs) per unit
        for unit_id, u in units_map.items():
            samples = conn.execute(
                """SELECT edge_type, source_url, record_date, edge_metadata
                   FROM edges WHERE source_node = ? AND target_node = ?
                   ORDER BY record_date DESC NULLS LAST LIMIT ?""",
                [cid, unit_id, SAMPLES_PER_UNIT],
            ).fetchall()
            u["samples"] = [
                {
                    "type": etype,
                    "url": url,
                    "date": date,
                    "title": (json.loads(meta).get("title", "") if meta else "")[:160],
                }
                for etype, url, date, meta in samples
            ]

        unit_list = sorted(units_map.values(), key=lambda x: -x["score"])
        companies.append({
            "id": cid, "name": name, "cik": cik, "confidence": conf,
            "total_edges": n_edges, "units": unit_list,
        })
    return companies


def main() -> None:
    p = argparse.ArgumentParser(description="Export graph.json for the frontend")
    p.add_argument("--db", default="graph.db")
    p.add_argument("--out", default="frontend/graph.json")
    p.add_argument("--built-at", default="unknown",
                   help="ISO timestamp string (scripts can't call Date.now in workflows)")
    args = p.parse_args()
    export(args.db, args.out, args.built_at)


if __name__ == "__main__":
    main()
