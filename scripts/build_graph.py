"""
Phase-gated pipeline runner for the UNC research graph.
Usage:
  python scripts/build_graph.py [--phases 0,1,2,3,4,5,6,7,8] [--db path/to/graph.db]

Phases:
  0  - Faculty roster bootstrap (ORCID + curated JSON)
  1  - NIH RePORTER grants
  2  - NSF Awards
  3  - USAspending contracts
  4  - ClinicalTrials.gov
  5  - Crossref papers
  6  - Company nodes + entity resolution
  7  - Topic profiler
  8  - OpenAlex authors snapshot (ORCID enrichment)
"""
from __future__ import annotations
import argparse
import logging
import os
import sys
from pathlib import Path

# Allow running as: python scripts/build_graph.py from repo root
sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("build_graph")


def phase0_faculty() -> None:
    """Bootstrap faculty roster from ORCID public expanded-search API."""
    from backend.graph import store
    from backend.extractors.orcid import ORCIDExtractor
    from backend.transform.entity_resolver import faculty_id_from_orcid, resolve_unc_unit

    store.init_schema()
    extractor = ORCIDExtractor()
    count = 0
    for orcid, source_url, rec in extractor.extract():
        fac_id = faculty_id_from_orcid(orcid)
        full_name = rec.get("full_name", f"orcid:{orcid}")
        dept = rec.get("department")
        unit_id = resolve_unc_unit(dept) if dept else None

        store.upsert_faculty(
            id_=fac_id,
            full_name=full_name,
            orcid=orcid,
            unc_unit_id=unit_id,
            department=dept,
            confidence="confirmed",
        )
        count += 1

    logger.info("Phase 0: upserted %d faculty from ORCID", count)

    # Import existing unc_faculty.json from sibling repo if present
    _import_curated_faculty()


def _import_curated_faculty() -> None:
    sibling = Path(__file__).parent.parent.parent / "map-elt-research-graph-3" / "backend" / "aria_pi" / "data" / "unc_faculty.json"
    if not sibling.exists():
        logger.info("No sibling unc_faculty.json found at %s — skipping import", sibling)
        return

    import json
    from backend.graph import store
    from backend.transform.entity_resolver import (
        faculty_id_from_name, resolve_unc_unit
    )

    data = json.loads(sibling.read_text())
    count = 0
    # unc_faculty.json shape is assumed to be a list of {name, department, ...}
    # or a dict. Handle both.
    items = data if isinstance(data, list) else (data.get("faculty") or list(data.values()))
    for item in items:
        name = item.get("name") or item.get("full_name", "")
        dept = item.get("department", "")
        if not name:
            continue
        parts = name.split()
        last = parts[-1] if parts else name
        first_initial = parts[0][0] if parts else "x"
        unit_id = resolve_unc_unit(dept) or "unc:root"
        fac_id = faculty_id_from_name(last, first_initial, unit_id)
        store.upsert_faculty(
            id_=fac_id,
            full_name=name,
            orcid=item.get("orcid"),
            unc_unit_id=unit_id,
            department=dept or None,
            confidence="probable",
        )
        count += 1

    logger.info("Phase 0: imported %d faculty from curated unc_faculty.json", count)


def _run_source(phase: str, extractor, raw_table: str, builder, limit: int | None) -> None:
    """Shared extract→load→transform loop for a single source."""
    from backend.graph import store
    from backend.extractors.base import now_iso

    count = edge_count = 0
    for record_id, source_url, raw in extractor.extract():
        fetched_at = now_iso()
        store.upsert_raw(raw_table, record_id, source_url, fetched_at, raw)
        edge_count += builder(record_id, source_url, raw, fetched_at)
        count += 1
        if count % 500 == 0:
            logger.info("%s: %d records, %d edges", phase, count, edge_count)
        if limit and count >= limit:
            logger.info("%s: reached --limit %d, stopping", phase, limit)
            extractor.clear_checkpoint()
            break
    logger.info("%s complete: %d records, %d edges", phase, count, edge_count)


def _load_ror_anchor() -> None:
    """
    Fetch UNC-CH's ROR record (anchor 0130frc33) and store it raw.
    The org tree itself is curated, but this verifies the anchor against the
    live ROR registry and records it (with source_url + fetched_at) like every
    other source. Best-effort: a ROR outage must not block the rest of the build.
    """
    from backend.graph import store
    from backend.extractors.ror import RORExtractor
    from backend.extractors.base import now_iso
    try:
        ror = RORExtractor()
        n = 0
        for rid, source_url, raw in ror.extract():
            store.upsert_raw("raw_ror_orgs", rid, source_url, now_iso(), raw)
            n += 1
        logger.info("ROR anchor: stored %d org record(s)", n)
    except Exception as exc:
        logger.warning("ROR anchor fetch failed (non-fatal): %s", exc)


def phase1_nih(limit: int | None = None) -> None:
    """Extract the ROR anchor + NIH grants and build graph edges."""
    from backend.graph import store
    from backend.extractors.nih import NIHExtractor
    from backend.transform.graph_builder import load_unc_org_tree, build_from_nih

    store.init_schema()
    load_unc_org_tree()
    _load_ror_anchor()
    _run_source("Phase 1 (NIH)", NIHExtractor(), "raw_nih_grants", build_from_nih, limit)


def phase2_nsf(limit: int | None = None) -> None:
    from backend.extractors.nsf import NSFExtractor
    from backend.transform.graph_builder import build_from_nsf
    _run_source("Phase 2 (NSF)", NSFExtractor(), "raw_nsf_awards", build_from_nsf, limit)


def phase3_usaspending(limit: int | None = None) -> None:
    from backend.extractors.usaspending import USAspendingExtractor
    from backend.transform.graph_builder import build_from_usaspending
    _run_source("Phase 3 (USAspending)", USAspendingExtractor(), "raw_usaspending",
                build_from_usaspending, limit)


def phase4_clinical(limit: int | None = None) -> None:
    from backend.extractors.clinicaltrials import ClinicalTrialsExtractor
    from backend.transform.graph_builder import build_from_clinical_trial
    _run_source("Phase 4 (ClinicalTrials)", ClinicalTrialsExtractor(), "raw_clinical_trials",
                build_from_clinical_trial, limit)


def phase5_crossref(limit: int | None = None) -> None:
    from backend.extractors.crossref import CrossrefExtractor
    from backend.transform.graph_builder import build_from_crossref
    _run_source("Phase 5 (Crossref)", CrossrefExtractor(), "raw_crossref_papers",
                build_from_crossref, limit)


def phase6_companies() -> None:
    """
    Enrich company nodes (created by the trials builder) with SEC CIKs.
    Uses the public SEC company_tickers.json (10k+ public companies) to match
    normalized company names → CIK. Matched companies are upgraded to 'confirmed'.
    """
    from backend.graph import store
    from backend.transform.entity_resolver import normalize_company_name
    import requests

    H = {"User-Agent": "UNC-Research-Graph/1.0 mailto:aidanacolvin@gmail.com"}
    try:
        tickers = requests.get(
            "https://www.sec.gov/files/company_tickers.json", headers=H, timeout=30
        ).json()
    except Exception as exc:
        logger.warning("Phase 6: could not fetch SEC company_tickers.json: %s", exc)
        return

    # Build normalized-name -> (cik, title) index
    sec_index: dict[str, tuple[str, str]] = {}
    for entry in tickers.values():
        title = entry.get("title", "")
        cik = str(entry.get("cik_str", "")).zfill(10)
        norm = normalize_company_name(title)
        if norm:
            sec_index[norm] = (cik, title)

    # Match existing company nodes
    with store.connection(read_only=True) as conn:
        companies = conn.execute(
            "SELECT id, canonical_name, normalized_name FROM nodes_companies"
        ).fetchall()

    logger.info("Phase 6: matching %d company nodes against %d SEC entries",
                len(companies), len(sec_index))
    matched = 0
    for co_id, canonical, norm in companies:
        hit = sec_index.get(norm)
        # Also try a prefix match (e.g. "merck sharp & dohme" contains a SEC name token)
        if not hit:
            for sec_norm, (cik, title) in sec_index.items():
                if len(sec_norm) >= 5 and (sec_norm in norm or norm in sec_norm):
                    hit = (cik, title)
                    break
        if hit:
            cik, title = hit
            store.upsert_company(
                id_=co_id, canonical_name=canonical, normalized_name=norm,
                sec_cik=cik, ror_id=None, confidence="confirmed",
            )
            matched += 1

    logger.info("Phase 6 complete: %d/%d companies matched to SEC CIK", matched, len(companies))


def phase7_topics() -> None:
    from backend.topic.profiler import build_profiles
    build_profiles()
    logger.info("Phase 7 complete: topic profiles built")


def phase8_openalex() -> None:
    from backend.graph import store
    from backend.extractors.openalex import OpenAlexAuthorsExtractor
    from backend.transform.entity_resolver import (
        faculty_id_from_orcid, resolve_unc_unit
    )

    extractor = OpenAlexAuthorsExtractor()
    count = 0
    for record_id, source_url, rec in extractor.extract():
        orcid_raw = rec.get("orcid", "") or ""
        orcid = orcid_raw.replace("https://orcid.org/", "").strip() or None
        if not orcid:
            continue

        given = rec.get("display_name", "").strip()
        institutions = rec.get("last_known_institutions", []) or []
        dept = institutions[0].get("display_name", "") if institutions else ""
        unit_id = resolve_unc_unit(dept) or "unc:root"

        fac_id = faculty_id_from_orcid(orcid)
        store.upsert_faculty(
            id_=fac_id,
            full_name=given or f"orcid:{orcid}",
            orcid=orcid,
            unc_unit_id=unit_id,
            department=dept or None,
            confidence="confirmed",
        )
        count += 1

    logger.info("Phase 8 complete: %d faculty ORCID records from OpenAlex", count)


# Phases that accept a per-source --limit (the source-harvest phases)
_LIMITED_PHASES = {1, 2, 3, 4, 5}
_PHASES = {
    0: phase0_faculty,
    1: phase1_nih,
    2: phase2_nsf,
    3: phase3_usaspending,
    4: phase4_clinical,
    5: phase5_crossref,
    6: phase6_companies,
    7: phase7_topics,
    8: phase8_openalex,
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Build UNC research graph")
    parser.add_argument(
        "--phases",
        default="0,1,2,3,4,5,6,7",
        help="Comma-separated phase numbers to run (default: 0-7; add 8 for OpenAlex)",
    )
    parser.add_argument("--db", default=None, help="Path to DuckDB file (overrides GRAPH_DB_PATH)")
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Max records per source-harvest phase (1-5). Useful for fast test builds.",
    )
    args = parser.parse_args()

    if args.db:
        os.environ["GRAPH_DB_PATH"] = args.db

    phases = [int(p.strip()) for p in args.phases.split(",")]
    logger.info("Running phases: %s (limit=%s)", phases, args.limit)

    for phase_num in phases:
        if phase_num not in _PHASES:
            logger.warning("Unknown phase %d — skipping", phase_num)
            continue
        logger.info("=== Starting Phase %d ===", phase_num)
        if phase_num in _LIMITED_PHASES:
            _PHASES[phase_num](limit=args.limit)
        else:
            _PHASES[phase_num]()
        logger.info("=== Phase %d done ===", phase_num)

    from backend.graph import store
    store.close()


if __name__ == "__main__":
    main()
