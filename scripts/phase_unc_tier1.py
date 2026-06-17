"""
Tier 1 of the UNC partnership scan: schools and departments.

Seeds unc_units from the curated UNC org tree (16 real, verified schools and
centers), derives their departments from the curated alias lists, then augments
by scraping the live UNC units directory. Writes top-level units with
parent_unit_id = null and departments linked to their parent school.

Usage:
  python scripts/phase_unc_tier1.py [--db graph.db] [--no-scrape]
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.graph import store                                      # noqa: E402
from backend.partnerships import common                              # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("phase_unc_tier1")

ROOT_ID = "unc:root"
MAX_SCRAPED_UNITS = 40            # cap augmentation so one page can't flood Tier 1
_DEPT_PREFIX_RE = re.compile(r"^(dept of|department of|div of|division of)\s+", re.I)


def classify_unit_type(name: str) -> str:
    """
    Classify a UNC unit by its name.

    Args:
        name: the unit's canonical or anchor name.
    Returns:
        One of the UNIT_* constants (School / Center / Institute / Department).
    """
    low = (name or "").lower()
    if "department" in low or low.startswith("dept") or low.startswith("div"):
        return common.UNIT_DEPARTMENT
    if "center" in low or "cancer center" in low:
        return common.UNIT_CENTER
    if "institute" in low or "program" in low or "affiliates" in low:
        return common.UNIT_INSTITUTE
    if "lab" in low or "laboratory" in low or "core" in low:
        return common.UNIT_LAB
    return common.UNIT_SCHOOL


def departments_from_aliases(aliases: list[str]) -> list[str]:
    """
    Extract distinct department names from a unit's curated alias list.

    Args:
        aliases: the unit's alias strings (some are "Dept of X").
    Returns:
        De-duplicated, title-cased department names (without the "Dept of" prefix).
    """
    depts: dict[str, str] = {}
    for alias in aliases or []:
        if not _DEPT_PREFIX_RE.match(alias):
            continue
        name = _DEPT_PREFIX_RE.sub("", alias).strip()
        if name:
            depts.setdefault(name.lower(), name)
    return list(depts.values())


def _slug(text: str) -> str:
    """Lowercase alphanumeric slug for building stable child unit ids."""
    return re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")


def seed_from_org_tree() -> tuple[int, int]:
    """
    Write the curated schools/centers and their departments to unc_units.

    Returns:
        (n_top_level_units, n_departments) written.
    """
    tree = common.load_org_tree()
    stamp = common.today()
    n_units = n_depts = 0

    for unit_id, node in tree.items():
        name = node.get("canonical_name", unit_id)
        is_root = unit_id == ROOT_ID
        unit_type = common.UNIT_INSTITUTE if is_root else classify_unit_type(name)
        aliases = node.get("aliases", [])
        depts = [] if is_root else departments_from_aliases(aliases)
        disciplines = "; ".join(depts) if depts else None

        store.upsert_partnership_unit(
            unit_id=unit_id,
            parent_unit_id=None if is_root else node.get("parent_id"),
            unit_name=name,
            unit_type=unit_type,
            description=f"{name}, University of North Carolina at Chapel Hill.",
            focus_areas=disciplines,
            disciplines=disciplines,
            faculty_count=None,            # never fabricated — left null unless found
            student_count=None,
            website_url=None,
            research_by=common.RESEARCH_BY,
            date_of_research=stamp,
            notes="curated UNC org tree (verified school/center)" if not is_root else "UNC-Chapel Hill (root)",
        )
        n_units += 1

        for dept in depts:
            dept_id = f"{unit_id}:{_slug(dept)}"
            store.upsert_partnership_unit(
                unit_id=dept_id,
                parent_unit_id=unit_id,
                unit_name=dept,
                unit_type=common.UNIT_DEPARTMENT,
                description=f"{dept}, {name}.",
                focus_areas=dept,
                disciplines=dept,
                faculty_count=None,
                student_count=None,
                website_url=None,
                research_by=common.RESEARCH_BY,
                date_of_research=stamp,
                notes="derived from curated department alias",
            )
            n_depts += 1

    return n_units, n_depts


def _looks_like_unit(anchor: str) -> bool:
    """True if anchor text reads like a school/center/institute/department."""
    low = (anchor or "").lower()
    return any(w in low for w in
               ("school", "college", "center", "institute", "department", "program"))


async def scrape_seed_units() -> int:
    """
    Augment Tier 1 by scraping the live UNC units directory pages.

    Discovers additional real units (links whose anchor text reads like a unit)
    and stores them with website_url set. Best-effort: dead seed pages are
    skipped, never fatal.

    Returns:
        Number of scraped units written.
    """
    stamp = common.today()
    written = 0
    seen: set[str] = set()
    async with common.RateLimitedClient() as client:
        for seed in common.UNC_SEED_URLS:
            html = await client.get_text(seed)
            if not html:
                logger.warning("Tier 1 seed page unavailable: %s", seed)
                continue
            _, links = common.parse_html(html, seed)
            for url, anchor in links:
                if written >= MAX_SCRAPED_UNITS:
                    break
                anchor = anchor.strip()
                if not anchor or len(anchor) < 6 or not _looks_like_unit(anchor):
                    continue
                if not common.same_host(url, "unc.edu"):
                    continue
                key = anchor.lower()
                if key in seen:
                    continue
                seen.add(key)
                store.upsert_partnership_unit(
                    unit_id=common.make_id("unc", anchor),
                    parent_unit_id=ROOT_ID,
                    unit_name=anchor,
                    unit_type=classify_unit_type(anchor),
                    description=None,
                    focus_areas=None,
                    disciplines=None,
                    faculty_count=None,
                    student_count=None,
                    website_url=url,
                    research_by=common.RESEARCH_BY,
                    date_of_research=stamp,
                    notes=f"discovered via UNC units directory ({seed})",
                )
                written += 1
            logger.info("Tier 1 scrape: %s -> %d units so far", seed, written)
    return written


async def run(scrape: bool) -> None:
    """Run Tier 1: curated seed, then optional live-page augmentation."""
    store.init_schema()
    n_units, n_depts = seed_from_org_tree()
    logger.info("Tier 1 curated seed: %d schools/centers, %d departments", n_units, n_depts)

    n_scraped = await scrape_seed_units() if scrape else 0
    logger.info("Tier 1 complete: %d schools/centers (+%d scraped), %d departments found",
                n_units, n_scraped, n_depts)


def main() -> None:
    parser = argparse.ArgumentParser(description="UNC partnership scan — Tier 1 (schools & departments)")
    parser.add_argument("--db", default=None, help="DuckDB path (overrides GRAPH_DB_PATH)")
    parser.add_argument("--no-scrape", action="store_true", help="skip live-page augmentation")
    args = parser.parse_args()
    if args.db:
        os.environ["GRAPH_DB_PATH"] = args.db
    asyncio.run(run(scrape=not args.no_scrape))
    store.close()


if __name__ == "__main__":
    main()
