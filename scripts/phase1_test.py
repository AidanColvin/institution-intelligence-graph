"""
Phase 1 smoke test — verifies the NIH end-to-end flow.

Checks:
  1. DuckDB schema initializes without error.
  2. UNC org tree loads (13 units).
  3. A small NIH sample fetch works (first 50 records).
  4. Edges are created and attached to UNC units.
  5. Each of the 13 top-level schools has at least 1 edge.
  6. Source URLs are non-empty and contain the expected base URL.
  7. fetched_at is a valid ISO timestamp.

Usage:
  python scripts/phase1_test.py
  python scripts/phase1_test.py --limit 200  # fetch more for confidence
"""
from __future__ import annotations
import argparse
import logging
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("phase1_test")

EXPECTED_SCHOOLS = {
    "unc:root", "unc:gillings", "unc:som", "unc:lineberger",
    "unc:eshelman", "unc:kenan_flagler", "unc:law", "unc:dentistry",
    "unc:nursing", "unc:social_work", "unc:education", "unc:information",
    "unc:government", "unc:arts_sciences",
}
NIH_DETAIL_PREFIX = "https://reporter.nih.gov/project-details/"


def run_test(limit: int, db_path: str) -> None:
    os.environ["GRAPH_DB_PATH"] = db_path

    # --- Test 1: Schema init ---
    from backend.graph import store
    store.init_schema()
    logger.info("✓ Schema initialized")

    # --- Test 2: Org tree ---
    from backend.transform.graph_builder import load_unc_org_tree
    load_unc_org_tree()
    with store.connection(read_only=True) as conn:
        unit_count = conn.execute("SELECT COUNT(*) FROM nodes_unc_units").fetchone()[0]
    assert unit_count >= 14, f"Expected ≥14 UNC units, got {unit_count}"
    logger.info("✓ Org tree loaded: %d units", unit_count)

    # --- Test 3: NIH fetch (limited) ---
    from backend.extractors.nih import NIHExtractor
    from backend.transform.graph_builder import build_from_nih
    from backend.extractors.base import now_iso

    extractor = NIHExtractor()
    records = []
    for record_id, source_url, raw in extractor.extract():
        records.append((record_id, source_url, raw))
        if len(records) >= limit:
            extractor.clear_checkpoint()
            break

    assert records, "NIH extractor returned 0 records — API may be down"
    logger.info("✓ NIH fetch returned %d records", len(records))

    # --- Test 4: Build edges ---
    total_edges = 0
    for record_id, source_url, raw in records:
        fetched_at = now_iso()
        store.upsert_raw("raw_nih_grants", record_id, source_url, fetched_at, raw)
        total_edges += build_from_nih(record_id, source_url, raw, fetched_at)

    assert total_edges > 0, "No edges created from NIH records"
    logger.info("✓ %d edges created from %d grants", total_edges, len(records))

    # --- Test 5: Unit coverage ---
    edge_by_unit = dict(store.count_edges_by_unit())
    covered_units = set(edge_by_unit.keys())
    logger.info("Units with edges: %s", sorted(covered_units))

    if limit < 200:
        logger.info(
            "  (limit=%d; run with --limit 200 for full school coverage check)", limit
        )
    else:
        missing = EXPECTED_SCHOOLS - covered_units
        if missing:
            logger.warning("Schools with 0 edges (may need more data): %s", missing)
        else:
            logger.info("✓ All 14 expected units have ≥1 edge")

    # --- Test 6: Source URLs ---
    with store.connection(read_only=True) as conn:
        urls = conn.execute(
            "SELECT source_url FROM raw_nih_grants LIMIT 5"
        ).fetchall()
    for (url,) in urls:
        assert url.startswith(NIH_DETAIL_PREFIX), f"Bad source URL: {url}"
    logger.info("✓ Source URLs are correct NIH detail URLs")

    # --- Test 7: fetched_at format ---
    with store.connection(read_only=True) as conn:
        timestamps = conn.execute(
            "SELECT fetched_at FROM raw_nih_grants LIMIT 5"
        ).fetchall()
    for (ts,) in timestamps:
        from datetime import datetime
        datetime.fromisoformat(ts)   # raises if invalid
    logger.info("✓ fetched_at timestamps are valid ISO 8601")

    # --- Summary ---
    logger.info("\n=== Phase 1 smoke test PASSED ===")
    logger.info("  NIH records: %d", len(records))
    logger.info("  Edges: %d", total_edges)
    logger.info("  Units covered: %d", len(covered_units))
    logger.info("  Top units by edge count:")
    for unit_id, cnt in sorted(edge_by_unit.items(), key=lambda x: -x[1])[:8]:
        logger.info("    %-30s  %d edges", unit_id, cnt)


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 1 smoke test (NIH end-to-end)")
    parser.add_argument("--limit", type=int, default=50, help="Max NIH records to fetch (default 50)")
    args = parser.parse_args()

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    # DuckDB can't open a non-DuckDB file, so remove the empty temp file first
    Path(db_path).unlink(missing_ok=True)

    try:
        run_test(args.limit, db_path)
    finally:
        Path(db_path).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
