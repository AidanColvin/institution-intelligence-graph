"""
ORCID public expanded-search extractor — UNC-affiliated researchers for faculty roster.
Endpoint: https://pub.orcid.org/v3.0/expanded-search/
No API key required. The expanded-search endpoint returns names + institution inline
(the basic /search/ endpoint returns only ORCID IDs).

There are ~13.5k UNC-affiliated ORCID records; a full crawl is slow, so MAX_RECORDS
bounds the roster bootstrap. Evidence-derived faculty (grant PIs, trial investigators,
paper authors) are added by the graph builders regardless of this cap.
"""
from __future__ import annotations
import logging
from typing import Iterator

from .base import BaseExtractor, now_iso

logger = logging.getLogger(__name__)

_SEARCH_API = "https://pub.orcid.org/v3.0/expanded-search/"
_ORCID_PAGE = "https://orcid.org/{orcid}"
_PAGE_SIZE = 200

# Bound the bootstrap. Set to None for a full (~13.5k, slow) crawl.
MAX_RECORDS: int | None = 2000

_QUERIES = [
    'affiliation-org-name:"University of North Carolina at Chapel Hill"',
    'affiliation-org-name:"University of North Carolina, Chapel Hill"',
]


class ORCIDExtractor(BaseExtractor):
    name = "orcid"
    min_interval = 1.5

    def __init__(self) -> None:
        super().__init__()
        self._session.headers.update({"Accept": "application/json"})

    def extract(self) -> Iterator[tuple[str, str, dict]]:
        seen: set[str] = set()
        for q in _QUERIES:
            if MAX_RECORDS and len(seen) >= MAX_RECORDS:
                break
            yield from self._search(q, seen)

    def _search(self, query: str, seen: set[str]) -> Iterator[tuple[str, str, dict]]:
        ck = self.load_checkpoint()
        offset = ck.get(f"q:{query}", 0)

        while True:
            if MAX_RECORDS and len(seen) >= MAX_RECORDS:
                break
            params = {"q": query, "start": offset, "rows": _PAGE_SIZE}
            try:
                data = self.get_json(_SEARCH_API, params)
            except Exception as exc:
                logger.error("ORCID search failed (q=%r, offset=%d): %s", query, offset, exc)
                ck[f"q:{query}"] = offset
                self.save_checkpoint(ck)
                raise

            results = data.get("expanded-result", []) or []
            if not results:
                break

            for r in results:
                orcid = r.get("orcid-id", "")
                if not orcid or orcid in seen:
                    continue
                seen.add(orcid)

                given = (r.get("given-names") or "").strip()
                family = (r.get("family-names") or "").strip()
                institutions = r.get("institution-name") or []
                record = {
                    "orcid": orcid,
                    "given_name": given,
                    "family_name": family,
                    "full_name": f"{given} {family}".strip() or f"orcid:{orcid}",
                    "institutions": institutions,
                    "department": None,
                }
                yield orcid, _ORCID_PAGE.format(orcid=orcid), record

            offset += len(results)
            ck[f"q:{query}"] = offset
            self.save_checkpoint(ck)
            logger.info("ORCID: q=%r fetched %d (seen=%d)", query[:40], offset, len(seen))

            if len(results) < _PAGE_SIZE:
                break
