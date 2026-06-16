"""
Crossref polite-pool extractor — papers with UNC-CH ROR affiliation.
Endpoint: https://api.crossref.org/works
No API key required. Polite pool: include email in User-Agent.
Uses cursor pagination for full harvest (~3,000–15,000 records expected).
"""
from __future__ import annotations
import logging
from typing import Iterator

from .base import BaseExtractor, now_iso

logger = logging.getLogger(__name__)

_API = "https://api.crossref.org/works"
_PAGE_SIZE = 1000
_ROR = "https://ror.org/0130frc33"
_DOI_BASE = "https://doi.org/{doi}"


class CrossrefExtractor(BaseExtractor):
    name = "crossref"
    min_interval = 1.1   # polite pool: ~1 req/s

    def extract(self) -> Iterator[tuple[str, str, dict]]:
        ck = self.load_checkpoint()
        cursor = ck.get("cursor", "*")
        logger.info("Crossref: starting with cursor=%s", cursor)

        while True:
            params = {
                "filter": f"has-ror-id:true,ror-id:{_ROR}",
                "rows": _PAGE_SIZE,
                "cursor": cursor,
                # Only fields valid for Crossref `select` (abstract/institution are not).
                "select": ",".join([
                    "DOI", "title", "author", "published", "container-title",
                    "type", "subject",
                ]),
            }

            try:
                data = self.get_json(_API, params)
            except Exception as exc:
                logger.error("Crossref fetch failed (cursor %s): %s", cursor, exc)
                self.save_checkpoint({"cursor": cursor})
                raise

            msg = data.get("message", {})
            items = msg.get("items", []) or []
            if not items:
                logger.info("Crossref: exhausted")
                self.clear_checkpoint()
                break

            fetched_at = now_iso()
            for item in items:
                doi = item.get("DOI", "")
                if not doi:
                    continue
                source_url = _DOI_BASE.format(doi=doi)
                yield doi, source_url, item

            next_cursor = msg.get("next-cursor")
            if not next_cursor or next_cursor == cursor:
                self.clear_checkpoint()
                break

            cursor = next_cursor
            self.save_checkpoint({"cursor": cursor})
            total = msg.get("total-results", "?")
            logger.info("Crossref: cursor advanced, total=%s", total)
