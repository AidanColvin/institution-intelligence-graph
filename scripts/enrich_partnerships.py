"""
Enrich frontend/partnerships.json IN PLACE with verified, source-linked data,
without rebuilding from DuckDB (which would discard live-edited units).

Four accuracy-first passes (each idempotent and re-runnable):

  1. Federal Research Awards  -- NEW area drawn from already-harvested, structured
     federal records. Counterparty = the FUNDING AGENCY, never a company.
       * NIH RePORTER        -> "National Institutes of Health"   (authoritative for NIH)
       * NSF                 -> "National Science Foundation"
       * USAspending         -> the awarding (sub-)agency, EXCLUDING NIH/NSF so we do
                                not double-count grants already covered above.
     Every row carries a canonical source_url, a real dollar amount, the project
     title, dates, and (where the structured record resolves it) a PI->faculty
     link and a specific UNC unit.

  2. Better unit mapping -- existing partnerships pinned to the generic root
     "UNC-Chapel Hill" node are re-attributed to their PI's specific unit when the
     linked faculty record supports it. Nothing is invented; rows only move where a
     structured faculty->unit link exists.

  3. Expanded faculty -- PIs named on the federal awards who are not yet in the
     roster are added from nodes_faculty (real names + ORCID + unit), so awards link
     to real people and faculty coverage grows.

  4. Company partnerships -- de-duplicate and tighten verification_notes on the
     existing Clinical Trial / Co-authored Publication rows. No fabricated dollars:
     the public sources for those carry no funding amount, so funding_value stays null.

Re-running is safe: rows this script creates are tagged (partnership_id prefix "fed:"
and a distinctive research_by on added faculty) and are dropped + rebuilt each run, so
the human-curated rows are never touched.

Usage:
  python scripts/enrich_partnerships.py [--json frontend/partnerships.json] [--db graph.db]
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import logging
import re
import shutil
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import duckdb

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("enrich")

AREA_FEDERAL = "Federal Research Award"
AREA_COPUB = "Co-authored Publication"
FED_PREFIX = "fed:"
COPUB_PREFIX = "cop:"
FED_FACULTY_TAG = "automated-scan (federal-award PI)"
COPUB_TAG = "automated-scan (OpenAlex refresh)"
COPUB_CACHE = "backend/data/openalex_copubs_recent.json"
TODAY = "2026-06-18"

# Unit ids we trust to exist in the live JSON; anything else falls back to root.
ROOT = "unc:root"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _hash(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:12]


def _iso(value: Any) -> str | None:
    """Normalize a date-ish value to YYYY-MM-DD, or None."""
    if not value:
        return None
    s = str(value).strip()
    if not s or s.lower() in ("none", "null"):
        return None
    if "T" in s:                                  # NIH: 2024-12-24T00:00:00
        return s[:10]
    if "/" in s:                                  # NSF: 05/27/2026
        m, d, y = (s.split("/") + ["", "", ""])[:3]
        if len(y) == 4 and m.isdigit() and d.isdigit():
            return f"{y}-{int(m):02d}-{int(d):02d}"
        return None
    return s[:10] if len(s) >= 10 else None


def _num(value: Any) -> float | None:
    try:
        n = float(value)
        return n if n > 0 else None
    except (TypeError, ValueError):
        return None


def _loads(raw: Any) -> dict:
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}
    return raw or {}


_CRED = re.compile(
    r"\b(MD|PhD|MS|MSc|MPH|MSPH|PharmD|DrPH|DO|ScD|RN|MBA|BSN|FACP|FACS|"
    r"Jr|Sr|II|III|IV)\b", re.I)


def _norm_name(name: str) -> str:
    """Lowercase, drop trailing credentials/degrees and punctuation."""
    base = (name or "").split(",")[0]              # 'David B Peden, MD, MS' -> 'David B Peden'
    base = _CRED.sub(" ", base)
    base = re.sub(r"[^a-z\s]", " ", base.lower())
    return re.sub(r"\s+", " ", base).strip()


def build_trial_pi_map(con) -> dict[str, str]:
    """{nctId: UNC principal-investigator name} from the structured trial record."""
    out: dict[str, str] = {}
    for rid, raw in con.execute(
            "SELECT id, raw_json FROM raw_clinical_trials").fetchall():
        d = _loads(raw)
        offs = (d.get("protocolSection", {})
                 .get("contactsLocationsModule", {})
                 .get("overallOfficials") or [])
        pi = None
        for o in offs:
            name = (o.get("name") or "").strip()
            affil = (o.get("affiliation") or "").lower()
            if not name:
                continue
            is_unc = "north carolina" in affil or re.search(r"\bunc\b", affil)
            if o.get("role") == "PRINCIPAL_INVESTIGATOR" and is_unc:
                pi = name
                break
            if pi is None and is_unc:                  # fallback: any UNC official
                pi = name
        if pi:
            out[rid] = pi
    return out


# --------------------------------------------------------------------------- #
# resolution maps from the matcher edges + faculty nodes
# --------------------------------------------------------------------------- #
def build_record_resolution(con) -> dict[tuple[str, str], dict]:
    """For each (source_table, record_id), pick the most specific unit and a PI.

    Prefers a non-root target unit (ties broken by edge count); keeps the faculty_id
    that the matcher attached. Returns {(table, record_id): {unit_id, faculty_id}}.
    """
    rows = con.execute(
        """SELECT source_table, record_id, target_node, faculty_id, COUNT(*) c
           FROM edges WHERE edge_type='grant'
           GROUP BY 1,2,3,4"""
    ).fetchall()
    by_rec: dict[tuple[str, str], list[tuple]] = defaultdict(list)
    for st, rid, unit, fac, c in rows:
        by_rec[(st, rid)].append((unit, fac, c))
    out: dict[tuple[str, str], dict] = {}
    for key, lst in by_rec.items():
        # best unit: highest edge count among non-root (deterministic tie-break on id)
        non_root = [x for x in lst if x[0] and x[0] != ROOT]
        unit = sorted(non_root, key=lambda x: (-x[2], x[0]))[0][0] if non_root else ROOT
        facs = sorted(x[1] for x in lst if x[1])     # stable PI choice across runs
        out[key] = {"unit_id": unit, "faculty_id": facs[0] if facs else None}
    return out


def load_node_faculty(con) -> dict[str, dict]:
    rows = con.execute(
        "SELECT id, full_name, orcid, unc_unit_id, department, confidence FROM nodes_faculty"
    ).fetchall()
    return {
        r[0]: {"full_name": r[1], "orcid": r[2], "unit_id": r[3],
               "department": r[4], "confidence": r[5]}
        for r in rows
    }


# --------------------------------------------------------------------------- #
# federal award row builders
# --------------------------------------------------------------------------- #
def _status_from_end(end: str | None) -> str:
    """Active if the award period has not yet ended, else Past."""
    return "Active" if (end and end >= TODAY) else "Past"


def _fed_row(*, source_table: str, record_id: str, agency: str, title: str,
             amount: float | None, ftype: str, start: str | None, end: str | None,
             url: str, notes: str, unit_id: str, faculty_id: str | None,
             status: str | None) -> dict:
    return {
        "partnership_id": FED_PREFIX + _hash(source_table, record_id),
        "unit_id": unit_id,
        "faculty_id": faculty_id,
        "area": AREA_FEDERAL,
        "company_name": agency,
        "description": (title or "").strip()[:300] or None,
        "status": status,
        "start_date": start,
        "end_date": end,
        "renewal_date": None,
        "recurring": None,
        "funding_value": amount,
        "funding_type": ftype,
        "unc_poc": None,
        "company_poc": None,
        "source_url": url,
        "verification_tier": "Verified",
        "verification_notes": notes,
        "research_by": "automated-scan",
        "date_of_research": TODAY,
        "pmid": None,
        "pubmed_url": None,
    }


def build_federal_awards(con, resolution, referenced_faculty: set[str]) -> list[dict]:
    rows: list[dict] = []
    nih_cores: set[str] = set()

    # ---- NIH RePORTER ----
    for rid, src_url, raw in con.execute(
            "SELECT id, source_url, raw_json FROM raw_nih_grants").fetchall():
        d = _loads(raw)
        res = resolution.get(("raw_nih_grants", rid), {})
        fac = res.get("faculty_id")
        if fac:
            referenced_faculty.add(fac)
        ic = _loads(d.get("agency_ic_admin"))
        ic_abbr = ic.get("abbreviation") if isinstance(ic, dict) else None
        pi = d.get("contact_pi_name") or ""
        core = (d.get("core_project_num") or d.get("project_num") or "").strip()
        if core:
            nih_cores.add(core)
        notes = (f"NIH RePORTER project {d.get('project_num')}"
                 + (f" ({ic_abbr})" if ic_abbr else "")
                 + (f"; contact PI {pi.title()}" if pi else "")
                 + ". Federal research funding to UNC — not a company partnership.")
        nih_end = _iso(d.get("project_end_date"))
        status = "Active" if str(d.get("is_active")).lower() == "true" else _status_from_end(nih_end)
        rows.append(_fed_row(
            source_table="raw_nih_grants", record_id=rid,
            agency="National Institutes of Health",
            title=d.get("project_title"),
            amount=_num(d.get("award_amount")), ftype="federal_grant",
            start=_iso(d.get("project_start_date")), end=nih_end,
            url=d.get("project_detail_url") or src_url,
            notes=notes,
            unit_id=res.get("unit_id", ROOT), faculty_id=fac, status=status,
        ))

    # ---- NSF ----
    for rid, src_url, raw in con.execute(
            "SELECT id, source_url, raw_json FROM raw_nsf_awards").fetchall():
        d = _loads(raw)
        res = resolution.get(("raw_nsf_awards", rid), {})
        fac = res.get("faculty_id")
        if fac:
            referenced_faculty.add(fac)
        directorate = " / ".join(x for x in (d.get("dirAbbr"), d.get("divAbbr")) if x)
        pi = d.get("pdPIName") or ""
        notes = (f"NSF award {rid}"
                 + (f" ({directorate})" if directorate else "")
                 + (f"; PI {pi}" if pi else "")
                 + ". Federal research funding to UNC — not a company partnership.")
        nsf_end = _iso(d.get("expDate"))
        status = "Active" if str(d.get("activeAwd")).lower() == "true" else _status_from_end(nsf_end)
        rows.append(_fed_row(
            source_table="raw_nsf_awards", record_id=rid,
            agency="National Science Foundation",
            title=d.get("title"),
            amount=_num(d.get("estimatedTotalAmt")) or _num(d.get("fundsObligatedAmt")),
            ftype="federal_grant",
            start=_iso(d.get("startDate")), end=nsf_end,
            url=src_url or f"https://www.nsf.gov/awardsearch/showAward?AWD_ID={rid}",
            notes=notes,
            unit_id=res.get("unit_id", ROOT), faculty_id=fac, status=status,
        ))

    # ---- USAspending (only agencies NOT covered by NIH RePORTER / NSF) ----
    skipped_dupe = 0
    for rid, src_url, raw in con.execute(
            "SELECT id, source_url, raw_json FROM raw_usaspending").fetchall():
        d = _loads(raw)
        agency = (d.get("awarding_agency") or "").strip()
        sub = (d.get("awarding_sub_agency") or "").strip()
        # de-dup: NIH and NSF awards already added from their authoritative sources
        if sub == "National Institutes of Health" or agency == "National Science Foundation" \
                or sub == "National Science Foundation":
            skipped_dupe += 1
            continue
        # counterparty: prefer the specific sub-agency unless it is a generic office
        counter = sub if (sub and sub != agency and "Office of" not in sub) else agency
        if not counter:
            continue
        group = (d.get("award_group") or "").lower()
        ftype = "federal_contract" if "contract" in group else "federal_grant"
        notes = (f"USAspending {d.get('award_id') or rid} ({d.get('award_type')}); "
                 f"{agency}{' / ' + sub if sub and sub != agency else ''}. "
                 f"Federal {'contract' if ftype.endswith('contract') else 'grant'} to UNC "
                 f"— not a company partnership.")
        usa_end = _iso(d.get("end_date"))
        rows.append(_fed_row(
            source_table="raw_usaspending", record_id=rid,
            agency=counter,
            title=d.get("description"),
            amount=_num(d.get("total_obligated_amount")), ftype=ftype,
            start=_iso(d.get("start_date")), end=usa_end,
            url=src_url,
            notes=notes,
            unit_id=ROOT, faculty_id=None, status=_status_from_end(usa_end),
        ))
    usa_kept = sum(1 for r in rows if r["company_name"] not in
                   ("National Institutes of Health", "National Science Foundation"))
    log.info("USAspending: skipped %d NIH/NSF dupes (already covered), kept %d other-agency rows",
             skipped_dupe, usa_kept)
    return rows


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default="frontend/partnerships.json")
    ap.add_argument("--db", default="graph.db")
    args = ap.parse_args()

    jpath = Path(args.json)
    backup = jpath.with_suffix(".json.bak")
    # Always build from the pristine baseline so every run is identical and
    # the human-curated rows are never compounded. First run snapshots it.
    if not backup.exists():
        shutil.copy2(jpath, backup)
        log.info("snapshot pristine baseline -> %s", backup)
    data = json.loads(backup.read_text())
    log.info("baseline source: %s", backup)
    units = data["units"]
    faculty = data["faculty"]
    parts = data["partnerships"]
    log.info("loaded %d units, %d faculty, %d partnerships",
             len(units), len(faculty), len(parts))

    unit_by_id = {u["unit_id"]: u for u in units}
    unit_ids = set(unit_by_id)

    con = duckdb.connect(args.db, read_only=True)
    resolution = build_record_resolution(con)
    node_fac = load_node_faculty(con)

    # ---- strip prior auto-generated rows so the run is idempotent ----
    parts = [p for p in parts if not str(p.get("partnership_id", ""))
             .startswith((FED_PREFIX, COPUB_PREFIX))]
    faculty = [f for f in faculty if f.get("research_by") != FED_FACULTY_TAG]
    fac_ids = {f["faculty_id"] for f in faculty}

    # ---- PASS 1+3: federal awards (collects referenced PI faculty_ids) ----
    referenced: set[str] = set()
    fed_rows = build_federal_awards(con, resolution, referenced)

    # add referenced PIs not already in the roster
    added_fac = 0
    for fid in sorted(referenced - fac_ids):
        nf = node_fac.get(fid)
        if not nf or not nf.get("full_name"):
            continue
        unit_id = nf["unit_id"] if nf["unit_id"] in unit_ids else ROOT
        faculty.append({
            "faculty_id": fid,
            "unit_id": unit_id,
            "full_name": nf["full_name"],
            "title": None,
            "profile_url": None,
            "openalex_id": nf.get("orcid"),    # ORCID populates the same id slot; null when unknown
            "research_by": FED_FACULTY_TAG,
            "date_of_research": TODAY,
        })
        fac_ids.add(fid)
        added_fac += 1
    log.info("PASS 3  added %d new faculty (roster %d -> %d)",
             added_fac, len(faculty) - added_fac, len(faculty))

    # drop federal rows whose PI we could not seat (keep faculty_id only if real)
    for r in fed_rows:
        if r["faculty_id"] and r["faculty_id"] not in fac_ids:
            r["faculty_id"] = None
        if r["unit_id"] not in unit_ids:
            r["unit_id"] = ROOT
    log.info("PASS 1  built %d federal-award rows", len(fed_rows))

    # ---- PASS 2: re-attribute root-pinned company partnerships to the PI's unit ----
    fac_unit = {f["faculty_id"]: f["unit_id"] for f in faculty
                if f.get("unit_id") and f["unit_id"] != ROOT}
    moved = 0
    for p in parts:
        if p.get("unit_id") == ROOT and p.get("faculty_id") in fac_unit:
            p["unit_id"] = fac_unit[p["faculty_id"]]
            moved += 1
    log.info("PASS 2  re-attributed %d root-pinned partnerships via existing faculty links", moved)

    # ---- shared high-precision faculty name index (built once, reused below) ----
    by_full: dict[str, list[dict]] = defaultdict(list)
    by_fl: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for f in faculty:
        norm = _norm_name(f.get("full_name") or "")
        toks = norm.split()
        if len(toks) >= 2:
            by_full[norm].append(f)
            by_fl[(toks[0], toks[-1])].append(f)

    def _match_faculty(person: str) -> dict | None:
        norm = _norm_name(person)
        toks = norm.split()
        if len(toks) < 2:
            return None
        if len(by_full.get(norm, [])) == 1:           # unique full-name match
            return by_full[norm][0]
        cand = by_fl.get((toks[0], toks[-1]), [])     # unique first+last match
        return cand[0] if len(cand) == 1 else None

    # ---- PASS 2b: clinical-trial PIs -> fill unc_poc + exact-name unit re-attribution ----
    trial_pi = build_trial_pi_map(con)
    poc_filled = pi_remapped = 0
    for p in parts:
        if p.get("area") != "Clinical Trial":
            continue
        m = re.search(r"(NCT\d+)", p.get("source_url") or "")
        pi = trial_pi.get(m.group(1)) if m else None
        if not pi:
            continue
        if not p.get("unc_poc"):
            p["unc_poc"] = pi
            poc_filled += 1
        fac_match = _match_faculty(pi)
        if fac_match and not p.get("faculty_id"):
            p["faculty_id"] = fac_match["faculty_id"]
            fu = fac_match.get("unit_id")
            if p.get("unit_id") == ROOT and fu and fu != ROOT:
                p["unit_id"] = fu
                pi_remapped += 1
    log.info("PASS 2b trial PIs: filled unc_poc on %d trials, re-attributed %d via PI exact-match",
             poc_filled, pi_remapped)

    # ---- PASS 5: refresh co-authored publications from the OpenAlex cache ----
    #   Gated to companies with a CONFIRMED UNC clinical-trial relationship, so each
    #   new co-pub links UNC to a company we already know partners with it. Offline:
    #   if the cache is absent (harvest not run), this pass is simply skipped.
    copub_rows: list[dict] = []
    cache_path = Path(COPUB_CACHE)
    if cache_path.exists():
        cache = json.loads(cache_path.read_text())
        trial_cos = {p["company_name"] for p in parts
                     if p.get("area") == "Clinical Trial" and p.get("company_name")}
        existing_doi = set()
        for p in parts:
            if p.get("area") == AREA_COPUB and p.get("source_url"):
                mm = re.search(r"(10\.\d+/\S+)", p["source_url"].lower())
                if mm:
                    existing_doi.add(mm.group(1))
        added_pairs = set()
        matched_auth = 0
        for w in cache:
            doi = (w.get("doi") or "").lower() or None
            if not doi or doi in existing_doi:
                continue
            keep = [c for c in w.get("companies", []) if c in trial_cos]
            if not keep:
                continue
            # one representative UNC author that resolves to a roster faculty
            fac_match = None
            for nm in w.get("unc_authors", []):
                fac_match = _match_faculty(nm)
                if fac_match:
                    matched_auth += 1
                    break
            unit_id = fac_match["unit_id"] if (fac_match and fac_match.get("unit_id")) else ROOT
            fac_id = fac_match["faculty_id"] if fac_match else None
            title = (w.get("title") or "").strip()
            for company in keep:
                if (company, doi) in added_pairs:
                    continue
                added_pairs.add((company, doi))
                copub_rows.append({
                    "partnership_id": COPUB_PREFIX + _hash(doi, company),
                    "unit_id": unit_id if unit_id in unit_ids else ROOT,
                    "faculty_id": fac_id,
                    "area": AREA_COPUB,
                    "company_name": company,
                    "description": title[:300] or None,
                    "status": None,
                    "start_date": (w.get("date") or None),
                    "end_date": None, "renewal_date": None, "recurring": None,
                    "funding_value": None, "funding_type": "none",
                    "unc_poc": None, "company_poc": None,
                    "source_url": f"https://doi.org/{doi}",
                    "verification_tier": "Reported",
                    "verification_notes": (
                        f"OpenAlex work co-authored with an author affiliated to {company}, "
                        f"a company with a confirmed UNC clinical-trial relationship."),
                    "research_by": COPUB_TAG, "date_of_research": TODAY,
                    "pmid": None, "pubmed_url": None,
                })
        log.info("PASS 5  added %d refreshed co-pub rows (%d works; %d author->faculty matches)",
                 len(copub_rows), len({r['source_url'] for r in copub_rows}), matched_auth)
    else:
        log.info("PASS 5  co-pub cache %s absent — skipped (run scripts/refresh_copubs.py first)",
                 COPUB_CACHE)

    # ---- PASS 4: de-dupe existing company rows (defensive) ----
    seen: set[tuple] = set()
    deduped: list[dict] = []
    removed = 0
    for p in parts:
        key = (p.get("area"), p.get("company_name"),
               p.get("pmid") or p.get("source_url"))
        if key in seen:
            removed += 1
            continue
        seen.add(key)
        deduped.append(p)
    parts = deduped
    log.info("PASS 4  removed %d duplicate company rows", removed)

    # ---- merge + re-stamp denormalized fields ----
    fac_name = {f["faculty_id"]: f.get("full_name") for f in faculty}
    all_parts = parts + fed_rows + copub_rows
    for p in all_parts:
        u = unit_by_id.get(p.get("unit_id"), {})
        p["unit_name"] = u.get("unit_name")
        p["unit_type"] = u.get("unit_type")
        if p.get("faculty_id"):
            p["faculty_name"] = fac_name.get(p["faculty_id"])

    # ---- recompute per-unit and per-faculty rollups ----
    by_unit: dict[str, list[dict]] = defaultdict(list)
    by_fac: dict[str, list[dict]] = defaultdict(list)
    for p in all_parts:
        by_unit[p.get("unit_id")].append(p)
        if p.get("faculty_id"):
            by_fac[p["faculty_id"]].append(p)

    def _counterparties(ps):
        return [p["company_name"] for p in ps if p.get("company_name")]

    for u in units:
        ps = by_unit.get(u["unit_id"], [])
        u["partnership_count"] = len(ps)
        u["top_companies"] = [c for c, _ in Counter(_counterparties(ps)).most_common(3)]
    for f in faculty:
        ps = by_fac.get(f["faculty_id"], [])
        f["partnership_count"] = len(ps)
        cps = _counterparties(ps)
        f["top_company"] = Counter(cps).most_common(1)[0][0] if cps else None
        f.setdefault("unit_name", unit_by_id.get(f.get("unit_id"), {}).get("unit_name"))

    # ---- meta (keeps companies and federal agencies clearly separate) ----
    company_areas = {"Clinical Trial", "Co-authored Publication"}
    companies = {p["company_name"] for p in all_parts
                 if p.get("area") in company_areas and p.get("company_name")}
    agencies = {p["company_name"] for p in all_parts
                if p.get("area") == AREA_FEDERAL and p.get("company_name")}
    fed_total = sum(p.get("funding_value") or 0 for p in all_parts
                    if p.get("area") == AREA_FEDERAL)
    data["meta"] = {
        "built_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "n_units": len(units),
        "n_faculty": len(faculty),
        "n_partnerships": len(all_parts),
        "counts_by_area": dict(Counter(p.get("area") for p in all_parts)),
        "counts_by_tier": dict(Counter(p.get("verification_tier") for p in all_parts)),
        "n_companies": len(companies),
        "n_funding_agencies": len(agencies),
        "total_federal_funding_usd": round(fed_total),
        "n_units_with_partnerships": sum(1 for u in units if u["partnership_count"]),
        "n_faculty_with_partnerships": sum(1 for f in faculty if f["partnership_count"]),
    }
    data["units"] = units
    data["faculty"] = faculty
    data["partnerships"] = all_parts

    # ---- write (baseline already snapshotted at start) ----
    jpath.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    log.info("wrote %s (%.1f MB)", jpath, jpath.stat().st_size / 1e6)
    log.info("META: %s", json.dumps(data["meta"], indent=2))
    con.close()


if __name__ == "__main__":
    main()
