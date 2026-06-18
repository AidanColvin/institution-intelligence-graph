"""
Build a DOI -> PMID index from PubMed (NCBI E-utilities, keyless) for every
co-authored-publication DOI in partnerships.json. This is the independent SECOND
source for the co-pub rows (PubMed is a separate index from OpenAlex/Crossref).

Cached + resumable: existing answers in the cache are not re-queried, so re-runs
only fetch new DOIs. Network failures are retried then left as unknown (skipped),
never guessed.

Output: backend/data/pubmed_doi_index.json   { "<doi>": "<pmid>" | null }

Usage:
  python scripts/verify_pubmed.py [--json frontend/partnerships.json]
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("verify_pubmed")

ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
TOOL = "unc-research-graph"
EMAIL = "research-graph@example.com"


def doi_of(url: str) -> str | None:
    m = re.search(r"(10\.\d{4,9}/\S+)", (url or "").lower())
    return m.group(1).rstrip("/.") if m else None


def pmid_for(doi: str) -> str | None | bool:
    """Return PMID string, None if PubMed has no record, or False on network error."""
    term = urllib.parse.quote(f"{doi}[DOI]")
    url = (f"{ESEARCH}?db=pubmed&term={term}&retmode=json"
           f"&tool={TOOL}&email={EMAIL}")
    req = urllib.request.Request(url, headers={"User-Agent": f"{TOOL} ({EMAIL})"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            ids = json.load(r).get("esearchresult", {}).get("idlist", [])
        return ids[0] if ids else None
    except Exception:
        return False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default="frontend/partnerships.json")
    ap.add_argument("--out", default="backend/data/pubmed_doi_index.json")
    args = ap.parse_args()

    data = json.loads(Path(args.json).read_text())
    dois = sorted({doi_of(p.get("source_url"))
                   for p in data["partnerships"]
                   if p.get("area") == "Co-authored Publication"} - {None})
    log.info("co-pub DOIs to confirm: %d", len(dois))

    out = Path(args.out)
    cache: dict[str, str | None] = json.loads(out.read_text()) if out.exists() else {}
    todo = [d for d in dois if d not in cache]
    log.info("cached: %d, to fetch: %d", len(cache), len(todo))

    found = 0
    for i, doi in enumerate(todo, 1):
        res = pmid_for(doi)
        if res is False:                       # transient error: one retry, else skip
            time.sleep(1.0)
            res = pmid_for(doi)
            if res is False:
                continue
        cache[doi] = res
        if res:
            found += 1
        if i % 200 == 0:
            log.info("  %d/%d fetched (%d indexed); checkpoint", i, len(todo), found)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(cache, ensure_ascii=False, indent=0))
        time.sleep(0.34)                       # NCBI: <=3 req/s without an API key

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(cache, ensure_ascii=False, indent=0))
    indexed = sum(1 for v in cache.values() if v)
    log.info("done: %d DOIs in index, %d PubMed-indexed -> %s", len(cache), indexed, out)


if __name__ == "__main__":
    main()
