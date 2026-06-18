"""
Refresh co-authored publications from OpenAlex (keyless, public) to close the
2021->2026 recency gap.

Harvests works co-authored by UNC (ROR 0130frc33) AND a company-type institution,
restricted to companies ALREADY verified in our dataset — so this stays at exactly
the same confidence level as the existing co-pub rows (tier "Reported" = a UNC author
co-published with a company-affiliated author, matched by name). Writes a cache that
enrich_partnerships.py merges in offline; this script does all the network work.

Output cache: backend/data/openalex_copubs_recent.json
  [{doi, title, date, companies:[...], unc_authors:[...]}]

Usage:
  python scripts/refresh_copubs.py [--since 2021-01-01] [--out backend/data/openalex_copubs_recent.json]
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import time
import urllib.request
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("refresh_copubs")

UNC_ROR = "0130frc33"
MAILTO = "research-graph@example.com"        # OpenAlex polite-pool etiquette
SUFFIX = re.compile(
    r"\b(inc|llc|ltd|corp|corporation|co|company|plc|gmbh|ag|sa|nv|holdings|"
    r"pharmaceuticals?|pharma|therapeutics|biosciences?|laboratories|labs)\b")


def norm_company(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"\([^)]*\)", "", s)           # drop "(United States)" etc.
    s = SUFFIX.sub("", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def known_companies() -> dict[str, str]:
    """Normalized-name -> canonical name, from both verified artifacts."""
    root = Path(__file__).parent.parent / "frontend"
    names: set[str] = set()
    try:
        g = json.loads((root / "graph.json").read_text())
        names.update(c["name"] for c in g.get("companies", []))
    except FileNotFoundError:
        pass
    base = root / "partnerships.json.bak"
    pj = json.loads((base if base.exists() else root / "partnerships.json").read_text())
    for p in pj["partnerships"]:
        if p.get("company_name") and p.get("area") in ("Clinical Trial", "Co-authored Publication"):
            names.add(p["company_name"])
    out: dict[str, str] = {}
    for n in names:
        k = norm_company(n)
        if len(k) >= 3:                        # guard against empty/1-char keys
            out.setdefault(k, n)
    return out


def fetch_page(filt: str, cursor: str) -> dict:
    url = (f"https://api.openalex.org/works?filter={filt}&per_page=200&cursor={cursor}"
           f"&mailto={MAILTO}&select=id,doi,title,publication_date,authorships")
    req = urllib.request.Request(url, headers={"User-Agent": f"unc-research-graph ({MAILTO})"})
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.load(r)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2021-01-01")
    ap.add_argument("--out", default="backend/data/openalex_copubs_recent.json")
    ap.add_argument("--max-pages", type=int, default=60)
    args = ap.parse_args()

    known = known_companies()
    log.info("known verified companies: %d normalized keys", len(known))

    filt = (f"authorships.institutions.ror:{UNC_ROR},"
            f"authorships.institutions.type:company,"
            f"from_publication_date:{args.since}")
    cursor, page, scanned, kept = "*", 0, 0, []
    while page < args.max_pages:
        for attempt in range(4):
            try:
                d = fetch_page(filt, cursor)
                break
            except Exception as exc:                       # transient network/5xx
                log.warning("page %d attempt %d failed: %r", page, attempt, exc)
                time.sleep(2 * (attempt + 1))
        else:
            log.error("giving up at page %d after retries", page)
            break

        for w in d["results"]:
            scanned += 1
            comps, unc_auth = set(), set()
            for a in w.get("authorships", []):
                aname = (a.get("author") or {}).get("display_name")
                for inst in a.get("institutions", []):
                    if inst.get("type") == "company":
                        k = norm_company(inst.get("display_name"))
                        if k in known:
                            comps.add(known[k])
                    if (inst.get("ror") or "").endswith(UNC_ROR) and aname:
                        unc_auth.add(aname)
            if comps:
                doi = (w.get("doi") or "").replace("https://doi.org/", "").lower() or None
                kept.append({
                    "doi": doi,
                    "openalex_id": w.get("id"),
                    "title": w.get("title"),
                    "date": w.get("publication_date"),
                    "companies": sorted(comps),
                    "unc_authors": sorted(unc_auth),
                })
        cursor = d["meta"].get("next_cursor")
        page += 1
        if page % 5 == 0:
            log.info("page %d: scanned %d, kept %d", page, scanned, len(kept))
        if not cursor:
            break
        time.sleep(0.2)                                    # be polite

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(kept, ensure_ascii=False, indent=0))
    log.info("scanned %d works, kept %d company-matched works -> %s",
             scanned, len(kept), out)


if __name__ == "__main__":
    main()
