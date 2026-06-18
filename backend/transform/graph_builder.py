"""
Transform raw records into typed graph nodes and edges.
One function per source type.  Each function calls store upserts directly.

Edge types: grant | paper | trial | contract | patent.
NOTE on 'patent': the spec lists patents as an edge type, and the schema/matcher
weights reserve it, but no patent edges are produced. The only patent data sources
(USPTO PatentsView Search API, EPO OPS) now require an API key, which violates the
hard "no API keys" constraint. Patents are therefore intentionally omitted until a
genuinely keyless source exists; the edge type remains defined for forward
compatibility (e.g. an OpenAlex/Lens snapshot import).
"""
from __future__ import annotations
import hashlib
import logging
from typing import Any

from ..graph import store
from ..extractors.base import now_iso
from .entity_resolver import (
    resolve_unc_unit,
    parse_nih_pi,
    faculty_id_from_orcid,
    faculty_id_from_name,
    normalize_company_name,
    company_id_from_name,
)

logger = logging.getLogger(__name__)


def _edge_id(*parts: str) -> str:
    return hashlib.sha256(":".join(parts).encode()).hexdigest()[:24]


# ------------------------------------------------------------------
# Org tree bootstrap
# ------------------------------------------------------------------

def load_unc_org_tree() -> None:
    """
    Upsert all UNC units from the curated JSON into nodes_unc_units.
    Safe to call multiple times.
    """
    from pathlib import Path
    import json
    tree_path = Path(__file__).parent.parent / "data" / "unc_org_tree.json"
    tree = json.loads(tree_path.read_text())
    for unit_id, u in tree.items():
        store.upsert_unc_unit(
            id_=unit_id,
            canonical_name=u["canonical_name"],
            short_name=u.get("short_name"),
            ror_id=u.get("ror_id"),
            parent_id=u.get("parent_id"),
            aliases=u.get("aliases", []),
            topic_keywords=[],
        )
    logger.info("Loaded %d UNC units into graph", len(tree))


# ------------------------------------------------------------------
# NIH RePORTER
# ------------------------------------------------------------------

def build_from_nih(record_id: str, source_url: str, raw: dict, fetched_at: str) -> int:
    """
    Process one NIH grant record → upsert faculty nodes + edges.
    Returns the number of edges created.
    """
    edges_created = 0

    org_block = raw.get("organization") or {}
    dept = org_block.get("dept_type", "") or ""
    org = org_block.get("org_name", "") or ""
    # Also try organization_type.name for school-level resolution
    org_type = (raw.get("organization_type") or {}).get("name", "") or ""

    # Resolve to a UNC unit — prefer dept, then org_type (e.g. "SCHOOLS OF MEDICINE").
    # Skip unresolved records instead of dumping them on unc:root, which would
    # otherwise accumulate the most edges and corrupt every ranking.
    unit_id = resolve_unc_unit(dept) or resolve_unc_unit(org_type)
    if not unit_id:
        logger.debug(
            "graph_builder: unresolved dept=%r org_type=%r for grant %s, skipping",
            dept, org_type, record_id,
        )
        return 0

    fiscal_year = str(raw.get("fiscal_year", "")) or None
    amount = _parse_amount(raw.get("award_amount"))
    title = raw.get("project_title", "")[:500]
    abstract_snippet = (raw.get("abstract_text", "") or "")[:300]
    agency = raw.get("agency_code", "")

    # PIs → faculty nodes + edges
    pis = raw.get("principal_investigators", []) or []
    for pi in pis:
        full_name, fac_id, orcid, confidence = parse_nih_pi(pi)
        store.upsert_faculty(
            id_=fac_id,
            full_name=full_name,
            orcid=orcid,
            unc_unit_id=unit_id,
            department=dept or None,
            confidence=confidence,
        )

        eid = _edge_id("grant", "unc:root", unit_id, record_id, fac_id)
        store.upsert_edge(
            id_=eid,
            edge_type="grant",
            source_node="unc:root",          # UNC-internal grant edge
            target_node=unit_id,
            faculty_id=fac_id,
            record_id=record_id,
            source_table="raw_nih_grants",
            source_url=source_url,
            fetched_at=fetched_at,
            record_date=fiscal_year,
            confidence="confirmed" if orcid else "probable",
            amount_usd=amount,
            metadata={
                "title": title,
                "abstract_snippet": abstract_snippet,
                "agency": agency,
                "pi_name": full_name,
            },
        )
        edges_created += 1

    # No PIs — still create a unit-level edge so grants are counted
    if not pis:
        eid = _edge_id("grant", "unc:root", unit_id, record_id)
        store.upsert_edge(
            id_=eid,
            edge_type="grant",
            source_node="unc:root",
            target_node=unit_id,
            faculty_id=None,
            record_id=record_id,
            source_table="raw_nih_grants",
            source_url=source_url,
            fetched_at=fetched_at,
            record_date=fiscal_year,
            confidence="probable",
            amount_usd=amount,
            metadata={"title": title, "agency": agency},
        )
        edges_created += 1

    return edges_created


# ------------------------------------------------------------------
# NSF Awards
# ------------------------------------------------------------------

def build_from_nsf(record_id: str, source_url: str, raw: dict, fetched_at: str) -> int:
    # NSF fundProgramName is a research program name (e.g. "STATISTICS"); map via aliases.
    dept = raw.get("fundProgramName", "") or ""
    unit_id = resolve_unc_unit(dept)
    if not unit_id:
        logger.debug(
            "graph_builder: unresolved program=%r for NSF award %s, skipping",
            dept, record_id,
        )
        return 0

    amount = _parse_amount(raw.get("fundsObligatedAmt"))
    title = (raw.get("title", "") or "")[:500]
    first = (raw.get("piFirstName", "") or "").strip()
    last = (raw.get("piLastName", "") or "").strip()
    pi_name = f"{first} {last}".strip() or (raw.get("pdPIName", "") or "")
    start = raw.get("startDate", "") or ""
    start_date = start[-4:] if start else None   # NSF dates are MM/DD/YYYY

    fac_id = None
    if last:
        fac_id = faculty_id_from_name(last, first[0] if first else "x", unit_id)
        store.upsert_faculty(
            id_=fac_id,
            full_name=pi_name,
            orcid=None,
            unc_unit_id=unit_id,
            department=dept or None,
            confidence="probable",
        )

    eid = _edge_id("grant", "unc:root", unit_id, record_id)
    store.upsert_edge(
        id_=eid,
        edge_type="grant",
        source_node="unc:root",
        target_node=unit_id,
        faculty_id=fac_id,
        record_id=record_id,
        source_table="raw_nsf_awards",
        source_url=source_url,
        fetched_at=fetched_at,
        record_date=start_date,
        confidence="confirmed",     # NSF UEI structured-field match
        amount_usd=amount,
        metadata={"title": title, "pi": pi_name, "program": dept},
    )
    return 1


# ------------------------------------------------------------------
# USAspending contracts
# ------------------------------------------------------------------

def build_from_usaspending(record_id: str, source_url: str, raw: dict, fetched_at: str) -> int:
    description = raw.get("description", "") or ""
    unit_id = resolve_unc_unit(description)
    if not unit_id:
        logger.debug(
            "graph_builder: unresolved description=%r for USAspending award %s, skipping",
            description, record_id,
        )
        return 0
    amount = _parse_amount(raw.get("total_obligated_amount"))
    start = (raw.get("start_date", "") or "")[:4] or None
    # Grants map to edge_type 'grant'; contracts to 'contract'
    edge_type = "contract" if raw.get("award_group") == "contracts" else "grant"

    eid = _edge_id(edge_type, "unc:root", unit_id, record_id)
    store.upsert_edge(
        id_=eid,
        edge_type=edge_type,
        source_node="unc:root",
        target_node=unit_id,
        faculty_id=None,
        record_id=record_id,
        source_table="raw_usaspending",
        source_url=source_url,
        fetched_at=fetched_at,
        record_date=start,
        confidence="confirmed",
        amount_usd=amount,
        metadata={
            "description": description[:300],
            "agency": raw.get("awarding_agency", ""),
            "award_id": raw.get("award_id", ""),
        },
    )
    return 1


# ------------------------------------------------------------------
# ClinicalTrials.gov
# ------------------------------------------------------------------

def build_from_clinical_trial(record_id: str, source_url: str, raw: dict, fetched_at: str) -> int:
    """
    raw is the ClinicalTrials v2 study JSON.  record_id is the NCT ID.

    Creates:
      - a UNC-internal trial edge (unc:root -> unit)
      - a faculty node from the responsible party investigator
      - for each INDUSTRY sponsor/collaborator: a company node + a company -> unit edge
        (this is the primary company<->UNC partnership signal)
    """
    edges_created = 0
    protocol = raw.get("protocolSection", {})
    id_mod = protocol.get("identificationModule", {})
    conditions = protocol.get("conditionsModule", {}).get("conditions", []) or []
    spon_mod = protocol.get("sponsorCollaboratorsModule", {})
    responsible = spon_mod.get("responsibleParty", {}) or {}
    affiliation = responsible.get("investigatorAffiliation", "") or responsible.get("affiliation", "")
    start = (protocol.get("statusModule", {}).get("startDateStruct", {}).get("date", "") or "")[:7]

    unit_id = resolve_unc_unit(affiliation) or _route_trial_by_conditions(conditions)
    if not unit_id:
        logger.debug(
            "graph_builder: unresolved affiliation=%r for trial %s, skipping",
            affiliation, record_id,
        )
        return 0
    title = (id_mod.get("briefTitle", "") or "")[:300]
    nct_id = id_mod.get("nctId", record_id)

    # Faculty from responsible-party investigator
    fac_id = None
    investigator = (responsible.get("investigatorFullName", "") or "").strip()
    if investigator:
        parts = investigator.replace(",", " ").split()
        last = parts[-1] if parts else investigator
        first_initial = parts[0][0] if parts else "x"
        fac_id = faculty_id_from_name(last, first_initial, unit_id)
        store.upsert_faculty(
            id_=fac_id, full_name=investigator, orcid=None,
            unc_unit_id=unit_id, department=affiliation[:200] or None,
            confidence="probable",
        )

    # UNC-internal trial edge
    eid = _edge_id("trial", "unc:root", unit_id, record_id)
    store.upsert_edge(
        id_=eid, edge_type="trial", source_node="unc:root", target_node=unit_id,
        faculty_id=fac_id, record_id=record_id, source_table="raw_clinical_trials",
        source_url=source_url, fetched_at=fetched_at, record_date=start or None,
        confidence="confirmed", amount_usd=None,
        metadata={"nct_id": nct_id, "title": title, "conditions": conditions[:5]},
    )
    edges_created += 1

    # Industry sponsors/collaborators -> company nodes + company->unit edges
    industry_orgs: list[str] = []
    lead = spon_mod.get("leadSponsor", {}) or {}
    if lead.get("class") == "INDUSTRY" and lead.get("name"):
        industry_orgs.append(lead["name"])
    for collab in spon_mod.get("collaborators", []) or []:
        if collab.get("class") == "INDUSTRY" and collab.get("name"):
            industry_orgs.append(collab["name"])

    for org_name in industry_orgs:
        norm = normalize_company_name(org_name)
        if not norm or len(norm) < 2:
            continue
        co_id = company_id_from_name(norm)
        store.upsert_company(
            id_=co_id, canonical_name=org_name, normalized_name=norm,
            sec_cik=None, ror_id=None, confidence="probable",
        )
        ceid = _edge_id("trial", co_id, unit_id, record_id)
        store.upsert_edge(
            id_=ceid, edge_type="trial", source_node=co_id, target_node=unit_id,
            faculty_id=fac_id, record_id=record_id, source_table="raw_clinical_trials",
            source_url=source_url, fetched_at=fetched_at, record_date=start or None,
            confidence="confirmed", amount_usd=None,
            metadata={"nct_id": nct_id, "title": title, "company": org_name,
                      "conditions": conditions[:5]},
        )
        edges_created += 1

    return edges_created


# ------------------------------------------------------------------
# Crossref papers
# ------------------------------------------------------------------

def build_from_crossref(record_id: str, source_url: str, raw: dict, fetched_at: str) -> int:
    """record_id is the DOI."""
    edges_created = 0
    year = _crossref_year(raw)
    title_list = raw.get("title") or [""]
    title = (title_list[0] if title_list else "")[:300]
    journal_list = raw.get("container-title") or [""]
    journal = journal_list[0] if journal_list else ""

    authors = raw.get("author", []) or []
    for author in authors:
        affils = author.get("affiliation", []) or []
        # Only process authors with a UNC affiliation signal
        unc_affil = next(
            (a for a in affils if "north carolina" in (a.get("name", "") or "").lower()), None
        )
        if not unc_affil:
            continue

        dept_text = unc_affil.get("name", "")
        unit_id = resolve_unc_unit(dept_text)
        if not unit_id:
            logger.debug(
                "graph_builder: unresolved affiliation=%r for paper %s author, skipping",
                dept_text, record_id,
            )
            continue

        given = author.get("given", "").strip()
        family = author.get("family", "").strip()
        full_name = f"{given} {family}".strip() or "Unknown Author"
        orcid_raw = author.get("ORCID", "") or ""
        orcid = orcid_raw.replace("http://orcid.org/", "").replace("https://orcid.org/", "") or None

        if orcid:
            fac_id = faculty_id_from_orcid(orcid)
            confidence = "confirmed"
        else:
            first_initial = given[0] if given else "x"
            fac_id = faculty_id_from_name(family, first_initial, unit_id)
            confidence = "probable"

        store.upsert_faculty(
            id_=fac_id,
            full_name=full_name,
            orcid=orcid,
            unc_unit_id=unit_id,
            department=dept_text[:200] or None,
            confidence=confidence,
        )

        eid = _edge_id("paper", "unc:root", unit_id, record_id, fac_id)
        store.upsert_edge(
            id_=eid,
            edge_type="paper",
            source_node="unc:root",
            target_node=unit_id,
            faculty_id=fac_id,
            record_id=record_id,
            source_table="raw_crossref_papers",
            source_url=source_url,
            fetched_at=fetched_at,
            record_date=year,
            confidence=confidence,
            amount_usd=None,
            metadata={"title": title, "journal": journal, "doi": record_id},
        )
        edges_created += 1

    # The ROR filter already confirms UNC involvement. If no author affiliation
    # text was parseable (common in Crossref), still record a paper->root edge so
    # the paper is counted and the source_url is preserved.
    if edges_created == 0:
        eid = _edge_id("paper", "unc:root", "unc:root", record_id)
        store.upsert_edge(
            id_=eid,
            edge_type="paper",
            source_node="unc:root",
            target_node="unc:root",
            faculty_id=None,
            record_id=record_id,
            source_table="raw_crossref_papers",
            source_url=source_url,
            fetched_at=fetched_at,
            record_date=year,
            confidence="confirmed",   # ROR structured-field match
            amount_usd=None,
            metadata={"title": title, "journal": journal, "doi": record_id},
        )
        edges_created += 1

    return edges_created


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

# Conservative structured-field routing: ClinicalTrials `conditions` are MeSH-style
# disease terms. We only route when the mapping is unambiguous (e.g. any cancer term →
# the Lineberger Comprehensive Cancer Center, which is UNC's designated cancer center).
_CONDITION_ROUTES = [
    (("cancer", "tumor", "tumour", "carcinoma", "oncolog", "leukemia", "leukaemia",
      "lymphoma", "melanoma", "myeloma", "sarcoma", "neoplasm", "glioma"), "unc:lineberger"),
]


def _route_trial_by_conditions(conditions: list) -> str | None:
    text = " ".join(c.lower() for c in (conditions or []) if isinstance(c, str))
    if not text:
        return None
    for keywords, unit_id in _CONDITION_ROUTES:
        if any(k in text for k in keywords):
            return unit_id
    return None


def _parse_amount(v: Any) -> int | None:
    try:
        if v is None:
            return None
        return int(float(str(v).replace(",", "").replace("$", "")))
    except (ValueError, TypeError):
        return None


def _crossref_year(raw: dict) -> str | None:
    """Safely pull a 4-digit year from a Crossref work's date fields."""
    for key in ("published", "published-online", "published-print", "issued", "created"):
        dp = (raw.get(key) or {}).get("date-parts") or []
        if dp and dp[0] and dp[0][0]:
            return str(dp[0][0])[:4]
    return None
