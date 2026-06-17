"""
Master runner for the UNC partnership sector scan.

Runs every phase in order, in one process (so the DuckDB connection is shared):
  Tier 1 (schools & departments) -> Tier 2 (labs & centers) ->
  Tier 3 (faculty) -> evidence (A/B/C/D) -> export (xlsx + json)

Logs record counts after each phase and total wall-clock time at the end.

Usage:
  python scripts/build_unc.py [--db graph.db] [--no-scrape]
      [--max-faculty 1000] [--openalex-pages 2]
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import time
from argparse import Namespace
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from backend.graph import store                                      # noqa: E402
import phase_unc_tier1 as tier1                                      # noqa: E402
import phase_unc_tier2 as tier2                                      # noqa: E402
import phase_unc_tier3 as tier3                                      # noqa: E402
import phase_unc_evidence as evidence                                # noqa: E402
import export_partnerships as exporter                               # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("build_unc")


def _counts() -> dict[str, int]:
    """Return row counts for the three inventory tables."""
    with store.connection(read_only=True) as conn:
        return {
            "units": conn.execute("SELECT COUNT(*) FROM unc_units").fetchone()[0],
            "faculty": conn.execute("SELECT COUNT(*) FROM unc_faculty").fetchone()[0],
            "partnerships": conn.execute("SELECT COUNT(*) FROM unc_partnerships").fetchone()[0],
        }


def run(args: Namespace) -> None:
    """Execute every phase in sequence and export the inventory."""
    if args.db:
        os.environ["GRAPH_DB_PATH"] = args.db
    started = time.monotonic()
    store.init_schema()

    logger.info("=== Tier 1: schools & departments ===")
    asyncio.run(tier1.run(scrape=not args.no_scrape))

    logger.info("=== Tier 2: labs & centers ===")
    asyncio.run(tier2.run(args.max_per_unit))

    logger.info("=== Tier 3: faculty ===")
    asyncio.run(tier3.run(args.include_root, args.max_faculty, args.max_openalex))

    logger.info("=== Evidence: passes A/B/C/D ===")
    ev_args = Namespace(
        db=None, openalex_pages=args.openalex_pages, openalex_per_page=args.openalex_per_page,
        max_web_units=args.max_web_units, max_nih_units=args.max_nih_units,
        max_trial_units=args.max_trial_units,
    )
    asyncio.run(evidence.run(ev_args))

    logger.info("=== Export: xlsx + json ===")
    exporter.export(None, args.xlsx, args.json, args.built_at)

    counts = _counts()
    elapsed = time.monotonic() - started
    logger.info("Build complete in %.1fs — %d units, %d faculty, %d partnerships",
                elapsed, counts["units"], counts["faculty"], counts["partnerships"])
    store.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the UNC partnership inventory end to end")
    parser.add_argument("--db", default=None, help="DuckDB path (overrides GRAPH_DB_PATH)")
    parser.add_argument("--no-scrape", action="store_true", help="skip Tier 1 live-page augmentation")
    parser.add_argument("--max-per-unit", type=int, default=tier2.DEFAULT_MAX_PER_UNIT)
    parser.add_argument("--max-faculty", type=int, default=tier3.DEFAULT_MAX_FACULTY)
    parser.add_argument("--max-openalex", type=int, default=tier3.DEFAULT_MAX_OPENALEX)
    parser.add_argument("--include-root", action="store_true")
    parser.add_argument("--openalex-pages", type=int, default=2)
    parser.add_argument("--openalex-per-page", type=int, default=200)
    parser.add_argument("--max-web-units", type=int, default=12)
    parser.add_argument("--max-nih-units", type=int, default=4)
    parser.add_argument("--max-trial-units", type=int, default=2)
    parser.add_argument("--xlsx", default="UNC_Partnership_Inventory.xlsx")
    parser.add_argument("--json", default="frontend/partnerships.json")
    from backend.partnerships import common
    parser.add_argument("--built-at", default=common.iso_now())
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()
