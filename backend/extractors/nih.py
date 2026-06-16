"""
NIH RePORTER v2 extractor — all UNC-CH grants, all years.
Endpoint: https://api.reporter.nih.gov/v2/projects/search
No API key required. Results paginated at 500/page.
"""
from __future__ import annotations
import logging
from typing import Iterator

from .base import BaseExtractor, now_iso

logger = logging.getLogger(__name__)

_API = "https://api.reporter.nih.gov/v2/projects/search"
_PAGE_SIZE = 500

# NIH uses "UNIV OF NORTH CAROLINA CHAPEL HILL" as the canonical org_name for UNC-CH.
# The ROR filter is supplementary — not all NIH records include ROR.
_ORG_NAME = "UNIV OF NORTH CAROLINA CHAPEL HILL"
_DETAIL_BASE = "https://reporter.nih.gov/project-details"


class NIHExtractor(BaseExtractor):
    name = "nih"
    min_interval = 0.5   # NIH reporter recommends < 2 req/s keyless

    def extract(self) -> Iterator[tuple[str, str, dict]]:
        ck = self.load_checkpoint()
        offset = ck.get("offset", 0)
        logger.info("NIH: starting from offset %d", offset)

        while True:
            body = {
                "criteria": {
                    "org_names": [_ORG_NAME],
                    "date_start": "2000-01-01",
                    "date_end": "2099-12-31",
                },
                # No include_fields — return all fields so nested organization object is present.
                # Filtering to specific include_fields strips the nested organization dict.
                "offset": offset,
                "limit": _PAGE_SIZE,
                "sort_field": "fiscal_year",
                "sort_order": "desc",
            }

            try:
                data = self.post_json(_API, body)
            except Exception as exc:
                logger.error("NIH fetch failed at offset %d: %s", offset, exc)
                self.save_checkpoint({"offset": offset})
                raise

            results = data.get("results", [])
            if not results:
                logger.info("NIH: exhausted at offset %d", offset)
                self.clear_checkpoint()
                break

            fetched_at = now_iso()
            for rec in results:
                project_num = rec.get("project_num", "")
                if not project_num:
                    continue
                source_url = f"{_DETAIL_BASE}/{project_num}"
                yield project_num, source_url, rec

            offset += len(results)
            self.save_checkpoint({"offset": offset})

            total = data.get("meta", {}).get("total", "?")
            logger.info("NIH: fetched %d/%s", offset, total)

            if len(results) < _PAGE_SIZE:
                self.clear_checkpoint()
                break
