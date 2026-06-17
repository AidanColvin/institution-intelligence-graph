"""
Tier 2 of the UNC partnership scan: labs and centers.

For each Tier 1 school/center with a known website, scrape one level deep for
links to labs, centers, institutes, and cores, and store each as a child unit
linked to its parent. Best-effort: unreachable sites are skipped, never fatal.

Usage:
  python scripts/phase_unc_tier2.py [--db graph.db] [--max-per-unit 25]
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.graph import store                                      # noqa: E402
from backend.partnerships import common                              # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("phase_unc_tier2")

DEFAULT_MAX_PER_UNIT = 25
_CHILD_WORDS = ("lab", "laboratory", "center", "centre", "institute", "core", "clinic")
_CHILD_TYPE_BY_WORD = {
    "lab": common.UNIT_LAB, "laboratory": common.UNIT_LAB, "core": common.UNIT_LAB,
    "center": common.UNIT_CENTER, "centre": common.UNIT_CENTER, "clinic": common.UNIT_CENTER,
    "institute": common.UNIT_INSTITUTE,
}

# Curated real homepages for the UNC schools/centers (public, stable). These are
# the Tier 2 crawl seeds; the parent unit's website_url is enriched from here too.
SCHOOL_WEBSITES = {
    "unc:gillings": "https://sph.unc.edu/",
    "unc:som": "https://www.med.unc.edu/",
    "unc:lineberger": "https://unclineberger.org/",
    "unc:eshelman": "https://pharmacy.unc.edu/",
    "unc:kenan_flagler": "https://www.kenan-flagler.unc.edu/",
    "unc:law": "https://law.unc.edu/",
    "unc:dentistry": "https://www.dentistry.unc.edu/",
    "unc:nursing": "https://nursing.unc.edu/",
    "unc:social_work": "https://ssw.unc.edu/",
    "unc:education": "https://ed.unc.edu/",
    "unc:information": "https://sils.unc.edu/",
    "unc:government": "https://www.sog.unc.edu/",
    "unc:arts_sciences": "https://college.unc.edu/",
    "unc:chip": "https://chip.unc.edu/",
}


def _child_type(anchor: str) -> str | None:
    """Return the child unit type for an anchor, or None if it isn't a child unit."""
    low = (anchor or "").lower()
    for word in _CHILD_WORDS:
        if word in low:
            return _CHILD_TYPE_BY_WORD.get(word, common.UNIT_CENTER)
    return None


def seed_websites() -> dict[str, str]:
    """
    Build {unit_id: website} to crawl: curated school homepages plus any unit
    that already has a website_url (e.g. discovered in Tier 1).

    Returns:
        Mapping of unit_id -> website URL.
    """
    seeds = dict(SCHOOL_WEBSITES)
    with store.connection(read_only=True) as conn:
        rows = conn.execute(
            "SELECT unit_id, website_url FROM unc_units WHERE website_url IS NOT NULL"
        ).fetchall()
    for unit_id, website in rows:
        seeds.setdefault(unit_id, website)
    return seeds


async def crawl_unit(client: common.RateLimitedClient, unit_id: str,
                     website: str, max_per_unit: int) -> tuple[int, int]:
    """
    Scrape one unit's homepage for child labs/centers and store them.

    Args:
        client: shared rate-limited HTTP client.
        unit_id: parent unit id.
        website: parent homepage URL.
        max_per_unit: cap on children stored for this unit.
    Returns:
        (n_labs, n_centers_or_institutes) written for this unit.
    """
    # Enrich the parent with its homepage URL while we're here.
    store.upsert_partnership_unit(
        unit_id=unit_id, parent_unit_id=None, unit_name=_unit_name(unit_id),
        unit_type=None, description=None, focus_areas=None, disciplines=None,
        faculty_count=None, student_count=None, website_url=website,
        research_by=common.RESEARCH_BY, date_of_research=common.today(), notes=None,
    )

    html = await client.get_text(website)
    if not html:
        logger.warning("Tier 2: %s homepage unreachable (%s)", unit_id, website)
        return 0, 0

    page_host = urlparse(website).netloc.lower()
    _, links = common.parse_html(html, website)
    stamp = common.today()
    n_labs = n_centers = 0
    seen: set[str] = set()

    for url, anchor in links:
        if n_labs + n_centers >= max_per_unit:
            break
        anchor = anchor.strip()
        if not anchor or len(anchor) < 6:
            continue
        child_type = _child_type(anchor)
        if child_type is None:
            continue
        if not (common.same_host(url, page_host) or common.same_host(url, "unc.edu")):
            continue
        key = anchor.lower()
        if key in seen:
            continue
        seen.add(key)
        store.upsert_partnership_unit(
            unit_id=f"{unit_id}:{common.make_id('child', anchor).split(':')[1]}",
            parent_unit_id=unit_id,
            unit_name=anchor,
            unit_type=child_type,
            description=None,
            focus_areas=None,
            disciplines=None,
            faculty_count=None,
            student_count=None,
            website_url=url,
            research_by=common.RESEARCH_BY,
            date_of_research=stamp,
            notes=f"discovered under {unit_id} homepage",
        )
        if child_type == common.UNIT_LAB:
            n_labs += 1
        else:
            n_centers += 1
    logger.info("Tier 2: %s -> %d labs, %d centers/institutes", unit_id, n_labs, n_centers)
    return n_labs, n_centers


def _unit_name(unit_id: str) -> str:
    """Look up a unit's current name (so enrichment upserts keep it intact)."""
    with store.connection(read_only=True) as conn:
        row = conn.execute(
            "SELECT unit_name FROM unc_units WHERE unit_id = ?", [unit_id]
        ).fetchone()
    return row[0] if row else unit_id


async def run(max_per_unit: int) -> None:
    """Crawl every seeded unit homepage for child labs/centers."""
    store.init_schema()
    seeds = seed_websites()
    total_labs = total_centers = 0
    async with common.RateLimitedClient() as client:
        for unit_id, website in seeds.items():
            labs, centers = await crawl_unit(client, unit_id, website, max_per_unit)
            total_labs += labs
            total_centers += centers
    logger.info("Tier 2 complete: %d labs, %d centers found", total_labs, total_centers)


def main() -> None:
    parser = argparse.ArgumentParser(description="UNC partnership scan — Tier 2 (labs & centers)")
    parser.add_argument("--db", default=None, help="DuckDB path (overrides GRAPH_DB_PATH)")
    parser.add_argument("--max-per-unit", type=int, default=DEFAULT_MAX_PER_UNIT,
                        help="cap on child units stored per parent")
    args = parser.parse_args()
    if args.db:
        os.environ["GRAPH_DB_PATH"] = args.db
    asyncio.run(run(args.max_per_unit))
    store.close()


if __name__ == "__main__":
    main()
