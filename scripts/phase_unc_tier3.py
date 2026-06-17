"""
Tier 3 of the UNC partnership scan: faculty.

Seeds unc_faculty from the already-collected, real UNC faculty roster (built
from ORCID + OpenAlex during the graph build), mapped to their unit. UNC faculty
directory pages bot-block the courteous scraper, so the collected roster is the
honest, reliable source of names. Reuses each faculty's existing stable id so
their grants/papers (edges) join straight through in the evidence pass.

A bounded subset of faculty with an ORCID is enriched with their OpenAlex author
id (1 req/sec). The per-faculty grant/paper partnership extraction itself runs in
phase_unc_evidence.py (single writer of unc_partnerships).

Usage:
  python scripts/phase_unc_tier3.py [--db graph.db] [--max-faculty 1000]
                                     [--max-openalex 15] [--include-root]
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.graph import store                                      # noqa: E402
from backend.partnerships import common                              # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("phase_unc_tier3")

ROOT_ID = "unc:root"
DEFAULT_MAX_FACULTY = 1000
DEFAULT_MAX_OPENALEX = 15


def known_unit_ids() -> set[str]:
    """Return the set of unit_ids present in unc_units (FK target for faculty)."""
    with store.connection(read_only=True) as conn:
        return {r[0] for r in conn.execute("SELECT unit_id FROM unc_units").fetchall()}


def select_faculty(include_root: bool, max_faculty: int) -> list[tuple]:
    """
    Pick real faculty to seed, most-active first.

    Args:
        include_root: include university-wide faculty (unc:root) too.
        max_faculty: cap on rows returned.
    Returns:
        List of (faculty_id, full_name, unc_unit_id, orcid) ordered by their
        public-record (edge) count, descending.
    """
    where = "" if include_root else "WHERE f.unc_unit_id IS NOT NULL AND f.unc_unit_id <> 'unc:root'"
    sql = f"""
        SELECT f.id, f.full_name, f.unc_unit_id, f.orcid, COUNT(e.id) AS n
        FROM nodes_faculty f
        LEFT JOIN edges e ON e.faculty_id = f.id
        {where}
        GROUP BY f.id, f.full_name, f.unc_unit_id, f.orcid
        ORDER BY n DESC, f.full_name
        LIMIT ?
    """
    with store.connection(read_only=True) as conn:
        return [(r[0], r[1], r[2], r[3]) for r in conn.execute(sql, [max_faculty]).fetchall()]


def seed_faculty(rows: list[tuple], valid_units: set[str]) -> int:
    """
    Write faculty to unc_faculty, mapping each to a real unit (root if unknown).

    Args:
        rows: (faculty_id, full_name, unc_unit_id, orcid) tuples.
        valid_units: unit_ids that exist in unc_units.
    Returns:
        Number of faculty written.
    """
    stamp = common.today()
    written = 0
    for faculty_id, full_name, unit_id, _orcid in rows:
        if not full_name:
            continue
        mapped = unit_id if unit_id in valid_units else ROOT_ID
        store.upsert_partnership_faculty(
            faculty_id=faculty_id, unit_id=mapped, full_name=full_name,
            title=None, profile_url=None, openalex_id=None,
            research_by=common.RESEARCH_BY, date_of_research=stamp,
        )
        written += 1
    return written


async def enrich_openalex(rows: list[tuple], max_openalex: int) -> int:
    """
    Resolve the OpenAlex author id for a bounded subset of faculty with an ORCID.

    Args:
        rows: (faculty_id, full_name, unit_id, orcid) tuples.
        max_openalex: cap on OpenAlex lookups.
    Returns:
        Number of faculty enriched with an openalex_id.
    """
    candidates = [(fid, orcid) for (fid, _n, _u, orcid) in rows if orcid][:max_openalex]
    if not candidates:
        return 0
    enriched = 0
    stamp = common.today()
    async with common.RateLimitedClient() as client:
        for faculty_id, orcid in candidates:
            data = await client.get_json(f"{common.OPENALEX_AUTHORS_URL}/https://orcid.org/{orcid}")
            if not data or not isinstance(data, dict):
                continue
            oa_id = (data.get("id") or "").rsplit("/", 1)[-1] or None
            if not oa_id:
                continue
            store.upsert_partnership_faculty(
                faculty_id=faculty_id, unit_id=None, full_name=data.get("display_name") or faculty_id,
                title=None, profile_url=None, openalex_id=oa_id,
                research_by=common.RESEARCH_BY, date_of_research=stamp,
            )
            enriched += 1
    return enriched


async def run(include_root: bool, max_faculty: int, max_openalex: int) -> None:
    """Seed the faculty roster and enrich a bounded subset via OpenAlex."""
    store.init_schema()
    valid_units = known_unit_ids()
    rows = select_faculty(include_root, max_faculty)
    n_written = seed_faculty(rows, valid_units)
    logger.info("Tier 3 seeded %d faculty from the collected roster", n_written)
    n_oa = await enrich_openalex(rows, max_openalex)
    logger.info("Tier 3 OpenAlex: resolved %d author ids", n_oa)
    logger.info("Tier 3 complete: %d faculty found", n_written)


def main() -> None:
    parser = argparse.ArgumentParser(description="UNC partnership scan — Tier 3 (faculty)")
    parser.add_argument("--db", default=None, help="DuckDB path (overrides GRAPH_DB_PATH)")
    parser.add_argument("--max-faculty", type=int, default=DEFAULT_MAX_FACULTY,
                        help="cap on faculty seeded (most-active first)")
    parser.add_argument("--max-openalex", type=int, default=DEFAULT_MAX_OPENALEX,
                        help="cap on OpenAlex author-id lookups")
    parser.add_argument("--include-root", action="store_true",
                        help="also seed university-wide (unc:root) faculty")
    args = parser.parse_args()
    if args.db:
        os.environ["GRAPH_DB_PATH"] = args.db
    asyncio.run(run(args.include_root, args.max_faculty, args.max_openalex))
    store.close()


if __name__ == "__main__":
    main()
