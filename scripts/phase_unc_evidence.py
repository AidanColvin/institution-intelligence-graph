"""
Evidence collection for the UNC partnership scan.

Populates unc_partnerships from real, sourced signals only — nothing fabricated:

  Backbone (offline): every already-collected company<->unit edge (industry-
    sponsored clinical trials) becomes a Verified Clinical Trial partnership.

  Pass A  Web scrape  — known company names near partnership keywords on a unit's
                        own pages              -> Inferred / Reported
  Pass B  NIH RePORTER — UNC projects with an industry organization
                        -> Verified Research Grant
  Pass C  OpenAlex     — UNC works with a company co-author
                        -> Reported Research Grant (co-authored)
  Pass D  ClinicalTrials.gov — industry-sponsored trials at Chapel Hill
                        -> Verified Clinical Trial

Live passes are rate-limited (1 req/sec) and bounded by CLI flags so a default
run finishes quickly; raise the bounds for a deeper campus-wide sweep.

Usage:
  python scripts/phase_unc_evidence.py [--db graph.db]
      [--openalex-pages 2] [--openalex-per-page 200]
      [--max-web-units 12] [--max-nih-units 4] [--max-trial-units 2]
"""
from __future__ import annotations

import argparse
import asyncio
import json
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
logger = logging.getLogger("phase_unc_evidence")

ROOT_ID = "unc:root"
_COUNTRY_SUFFIX_RE = re.compile(r"\s*\([^)]*\)\s*$")
_NAME_PUNCT_RE = re.compile(r"[^a-z0-9\s]")


def _norm_name(name: str) -> str:
    """Normalize a person name to lowercase alphanumerics for matching."""
    return re.sub(r"\s+", " ", _NAME_PUNCT_RE.sub(" ", (name or "").lower())).strip()


def _clean_company(name: str) -> str:
    """Strip OpenAlex country suffixes, e.g. 'Google (United States)' -> 'Google'."""
    return _COUNTRY_SUFFIX_RE.sub("", name or "").strip()


def _valid_units() -> set[str]:
    with store.connection(read_only=True) as conn:
        return {r[0] for r in conn.execute("SELECT unit_id FROM unc_units").fetchall()}


def _faculty_index() -> dict[str, tuple[str, str | None]]:
    """{normalized_full_name: (faculty_id, unit_id)} for co-author attribution."""
    with store.connection(read_only=True) as conn:
        rows = conn.execute("SELECT faculty_id, full_name, unit_id FROM unc_faculty").fetchall()
    return {_norm_name(n): (fid, unit) for fid, n, unit in rows if n}


# ── backbone: existing company<->unit edges -> partnerships ──────────────────

def transform_edges(valid_units: set[str], valid_faculty: set[str]) -> int:
    """
    Turn every already-collected company<->unit edge into a partnership row.

    These are industry-sponsored clinical trials (real, sourced, Verified) that
    the graph build already harvested from ClinicalTrials.gov.

    Returns:
        Number of partnership rows written.
    """
    sql = """
        SELECT e.id, e.edge_type, e.target_node, e.faculty_id, e.source_url,
               e.record_date, e.amount_usd, e.edge_metadata, c.canonical_name
        FROM edges e
        JOIN nodes_companies c ON e.source_node = c.id
        WHERE e.source_node LIKE 'co:%' OR e.source_node LIKE 'cik:%'
    """
    stamp = common.today()
    written = 0
    with store.connection(read_only=True) as conn:
        rows = conn.execute(sql).fetchall()
    for (edge_id, etype, target, faculty_id, source_url, rec_date,
         amount, meta_json, company) in rows:
        meta = json.loads(meta_json) if meta_json else {}
        unit_id = target if target in valid_units else ROOT_ID
        fid = faculty_id if faculty_id in valid_faculty else None
        store.upsert_partnership({
            "partnership_id": common.make_id("pp", edge_id),
            "unit_id": unit_id,
            "faculty_id": fid,
            "area": common.EDGE_TYPE_AREA.get(etype, common.AREA_RESEARCH_GRANT),
            "company_name": company,
            "description": (meta.get("title") or "")[:240] or None,
            "status": _trial_status(meta.get("overallStatus") or meta.get("status")),
            "start_date": rec_date,
            "recurring": common.RECURRING_ONE_TIME,
            "funding_value": float(amount) if amount else None,
            "funding_type": common.EDGE_TYPE_FUNDING.get(etype, common.FUNDING_NONE),
            "source_url": source_url,
            "verification_tier": common.EDGE_TYPE_TIER.get(etype, common.TIER_REPORTED),
            "verification_notes": "from collected ClinicalTrials.gov sponsor edge",
            "research_by": common.RESEARCH_BY,
            "date_of_research": stamp,
        })
        written += 1
    logger.info("Backbone: %d partnerships from collected company<->unit edges", written)
    return written


def _trial_status(raw: str | None) -> str | None:
    """Map a ClinicalTrials status to the inventory's status vocabulary."""
    if not raw:
        return None
    low = raw.lower()
    if any(w in low for w in ("recruiting", "active", "enrolling", "not yet")):
        return common.STATUS_ACTIVE
    if any(w in low for w in ("completed", "terminated", "withdrawn", "suspended")):
        return common.STATUS_PAST
    return None


# ── Pass A: web scrape unit pages ────────────────────────────────────────────

async def pass_a_web(client: common.RateLimitedClient, companies: dict[str, str],
                     max_units: int) -> int:
    """Scrape unit homepages for known-company partnership signals."""
    with store.connection(read_only=True) as conn:
        units = conn.execute(
            "SELECT unit_id, unit_name, website_url FROM unc_units "
            "WHERE website_url IS NOT NULL LIMIT ?", [max_units]
        ).fetchall()
    stamp = common.today()
    written = 0
    for unit_id, unit_name, website in units:
        html = await client.get_text(website)
        if not html:
            continue
        text, _ = common.parse_html(html, website)
        signals = common.scan_for_partnerships(text, companies, website)
        for sig in signals:
            store.upsert_partnership({
                "partnership_id": common.make_id("pp", "web", unit_id, sig["company_name"], sig["area"]),
                "unit_id": unit_id,
                "area": sig["area"],
                "company_name": sig["company_name"],
                "description": sig["description"],
                "source_url": sig["source_url"],
                "verification_tier": sig["verification_tier"],
                "verification_notes": "web-scrape signal (company name near partnership keyword)",
                "research_by": common.RESEARCH_BY,
                "date_of_research": stamp,
            })
            written += 1
        logger.info("Unit: %s — web: %d", unit_name, len(signals))
    logger.info("Pass A complete: %d web-signal partnerships", written)
    return written


# ── Pass B: NIH RePORTER ─────────────────────────────────────────────────────

async def pass_b_nih(client: common.RateLimitedClient, companies: dict[str, str],
                     max_units: int) -> int:
    """
    Query NIH RePORTER for UNC projects and emit a Research Grant partnership
    when an industry organization (matching a known company) is on the project.
    NIH awards are federal, so industry co-funders are rare — honest low counts.
    """
    with store.connection(read_only=True) as conn:
        units = conn.execute(
            "SELECT unit_id, unit_name FROM unc_units WHERE unit_type IN ('School','Center') LIMIT ?",
            [max_units],
        ).fetchall()
    stamp = common.today()
    written = 0
    for unit_id, unit_name in units:
        body = {
            "criteria": {
                "org_names": ["UNIV OF NORTH CAROLINA CHAPEL HILL"],
                "advanced_text_search": {"operator": "and", "search_field": "projecttitle",
                                          "search_text": unit_name.split()[0]},
            },
            "limit": 25,
        }
        data = await client.post_json(common.NIH_SEARCH_URL, body)
        found = 0
        for proj in (data or {}).get("results", []) or []:
            org = (proj.get("organization") or {}).get("org_name", "")
            match = companies.get(common.norm_company(org))
            if not match:
                continue
            pid = proj.get("project_num") or proj.get("core_project_num") or org
            store.upsert_partnership({
                "partnership_id": common.make_id("pp", "nih", unit_id, pid),
                "unit_id": unit_id,
                "area": common.AREA_RESEARCH_GRANT,
                "company_name": match,
                "description": (proj.get("project_title") or "")[:240] or None,
                "status": common.STATUS_ACTIVE if proj.get("is_active") else common.STATUS_PAST,
                "funding_value": float(proj.get("award_amount") or 0) or None,
                "funding_type": common.FUNDING_GRANT,
                "source_url": f"{common.NIH_PROJECT_URL}{pid}",
                "verification_tier": common.TIER_VERIFIED,
                "verification_notes": "NIH RePORTER project with industry organization",
                "research_by": common.RESEARCH_BY,
                "date_of_research": stamp,
            })
            found += 1
            written += 1
        logger.info("Unit: %s — NIH: %d", unit_name, found)
    logger.info("Pass B complete: %d NIH industry-grant partnerships", written)
    return written


# ── Pass C: OpenAlex company co-authors ──────────────────────────────────────

async def pass_c_openalex(client: common.RateLimitedClient,
                          faculty_idx: dict[str, tuple[str, str | None]],
                          pages: int, per_page: int) -> int:
    """
    Find UNC works with a company co-author and emit Research Grant (co-authored)
    partnerships. Attributes to a faculty/unit when an author name matches the
    seeded roster, else to the university (unc:root). Real, sourced by DOI.
    """
    stamp = common.today()
    written = 0
    select = "id,title,publication_year,doi,authorships"
    base = (f"{common.OPENALEX_WORKS_URL}?filter=institutions.ror:{common.UNC_ROR_ID},"
            f"authorships.institutions.type:company&select={select}&per-page={per_page}")
    for page in range(1, pages + 1):
        data = await client.get_json(f"{base}&page={page}")
        results = (data or {}).get("results", []) or []
        if not results:
            break
        for work in results:
            companies_here = _companies_in_work(work)
            if not companies_here:
                continue
            faculty_id, unit_id = _attribute_work(work, faculty_idx)
            source_url = work.get("doi") or work.get("id") or ""
            year = work.get("publication_year")
            for company in companies_here:
                store.upsert_partnership({
                    "partnership_id": common.make_id("pp", "oa", work.get("id"), company),
                    "unit_id": unit_id,
                    "faculty_id": faculty_id,
                    "area": common.AREA_RESEARCH_GRANT,
                    "company_name": company,
                    "description": f"Co-authored research: {(work.get('title') or '')[:200]}",
                    "start_date": f"{year}-01-01" if year else None,
                    "funding_type": common.FUNDING_NONE,
                    "source_url": source_url,
                    "verification_tier": common.TIER_REPORTED,
                    "verification_notes": "OpenAlex work with a company-affiliated co-author",
                    "research_by": common.RESEARCH_BY,
                    "date_of_research": stamp,
                })
                written += 1
        logger.info("Pass C: OpenAlex page %d -> %d partnerships so far", page, written)
    logger.info("Pass C complete: %d co-author partnerships", written)
    return written


def _companies_in_work(work: dict) -> list[str]:
    """Distinct cleaned company names appearing as co-author institutions."""
    out: set[str] = set()
    for auth in work.get("authorships", []) or []:
        for inst in auth.get("institutions", []) or []:
            if inst.get("type") == "company" and inst.get("display_name"):
                out.add(_clean_company(inst["display_name"]))
    return sorted(n for n in out if n)


def _attribute_work(work: dict, faculty_idx: dict[str, tuple[str, str | None]]
                    ) -> tuple[str | None, str]:
    """Return (faculty_id, unit_id) for a work by matching a UNC author name."""
    for auth in work.get("authorships", []) or []:
        name = (auth.get("author") or {}).get("display_name", "")
        hit = faculty_idx.get(_norm_name(name))
        if hit:
            return hit[0], (hit[1] or ROOT_ID)
    return None, ROOT_ID


# ── Pass D: ClinicalTrials.gov live ──────────────────────────────────────────

async def pass_d_trials(max_units: int) -> int:
    """
    Live ClinicalTrials.gov check for industry-sponsored trials at Chapel Hill.
    Complements the offline backbone with the freshest sponsor records. Uses a
    browser UA because the public API rejects the mailto: UA the other passes use.
    """
    async with common.RateLimitedClient(
        headers={"User-Agent": common.BROWSER_USER_AGENT}
    ) as client:
        data = await client.get_json(
            f"{common.CLINICALTRIALS_URL}?query.locn=Chapel+Hill&pageSize={max_units * 50}"
        )
    studies = (data or {}).get("studies", []) or []
    stamp = common.today()
    written = 0
    for study in studies:
        proto = study.get("protocolSection", {})
        ident = proto.get("identificationModule", {})
        nct = ident.get("nctId")
        lead = proto.get("sponsorCollaboratorsModule", {}).get("leadSponsor", {})
        sponsor = lead.get("name")
        # Industry lead sponsors only — that's the company<->UNC partnership signal.
        if not nct or not sponsor or lead.get("class") != "INDUSTRY":
            continue
        status = proto.get("statusModule", {}).get("overallStatus")
        store.upsert_partnership({
            "partnership_id": common.make_id("pp", "ct", nct, sponsor),
            "unit_id": ROOT_ID,
            "area": common.AREA_CLINICAL_TRIAL,
            "company_name": _clean_company(sponsor),
            "description": (ident.get("briefTitle") or "")[:240] or None,
            "status": _trial_status(status),
            "recurring": common.RECURRING_ONE_TIME,
            "funding_type": common.FUNDING_SPONSORSHIP,
            "source_url": f"{common.CLINICALTRIALS_VIEW}{nct}",
            "verification_tier": common.TIER_VERIFIED,
            "verification_notes": "ClinicalTrials.gov industry lead sponsor at Chapel Hill",
            "research_by": common.RESEARCH_BY,
            "date_of_research": stamp,
        })
        written += 1
    logger.info("Pass D complete: %d live industry-trial partnerships", written)
    return written


async def run(args: argparse.Namespace) -> None:
    """Run the backbone transform plus the four bounded live evidence passes."""
    store.init_schema()
    valid_units = _valid_units()
    companies = common.known_company_names()
    with store.connection(read_only=True) as conn:
        valid_faculty = {r[0] for r in conn.execute("SELECT faculty_id FROM unc_faculty").fetchall()}
    faculty_idx = _faculty_index()
    logger.info("Evidence: %d units, %d faculty, %d known companies",
                len(valid_units), len(valid_faculty), len(companies))

    total = transform_edges(valid_units, valid_faculty)
    async with common.RateLimitedClient() as client:
        total += await pass_c_openalex(client, faculty_idx, args.openalex_pages, args.openalex_per_page)
        total += await pass_b_nih(client, companies, args.max_nih_units)
    total += await pass_d_trials(args.max_trial_units)
    async with common.RateLimitedClient() as client:
        total += await pass_a_web(client, companies, args.max_web_units)
    logger.info("Evidence complete: %d total partnerships in unc_partnerships", total)


def main() -> None:
    parser = argparse.ArgumentParser(description="UNC partnership scan — evidence passes A/B/C/D")
    parser.add_argument("--db", default=None, help="DuckDB path (overrides GRAPH_DB_PATH)")
    parser.add_argument("--openalex-pages", type=int, default=2)
    parser.add_argument("--openalex-per-page", type=int, default=200)
    parser.add_argument("--max-web-units", type=int, default=12)
    parser.add_argument("--max-nih-units", type=int, default=4)
    parser.add_argument("--max-trial-units", type=int, default=2)
    args = parser.parse_args()
    if args.db:
        os.environ["GRAPH_DB_PATH"] = args.db
    asyncio.run(run(args))
    store.close()


if __name__ == "__main__":
    main()
