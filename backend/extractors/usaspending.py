"""
USAspending.gov extractor — UNC-CH as prime award recipient.
Endpoint: https://api.usaspending.gov/api/v2/search/spending_by_award/
No API key required.

The API matches recipients by name text (UEI filtering via recipient_search_text
does not work reliably). The earliest queryable date is 2007-10-01.
Award detail URLs use the `generated_internal_id` field.
"""
from __future__ import annotations
import logging
from typing import Iterator

from .base import BaseExtractor, now_iso

logger = logging.getLogger(__name__)

_API = "https://api.usaspending.gov/api/v2/search/spending_by_award/"
_PAGE_SIZE = 100

_RECIPIENT_NAMES = ["UNIVERSITY OF NORTH CAROLINA AT CHAPEL HILL"]
_AWARD_DETAIL = "https://www.usaspending.gov/award/{internal_id}/"

# Grant + cooperative-agreement types (02-05) and contract types (A-D)
_GRANT_TYPES = ["02", "03", "04", "05"]
_CONTRACT_TYPES = ["A", "B", "C", "D"]
# API floor is 2007-10-01
_START_DATE = "2007-10-01"


class USAspendingExtractor(BaseExtractor):
    name = "usaspending"
    min_interval = 0.5

    def extract(self) -> Iterator[tuple[str, str, dict]]:
        # Grants and contracts must be queried separately (API rejects mixing the two groups)
        yield from self._extract_group("grants", _GRANT_TYPES)
        yield from self._extract_group("contracts", _CONTRACT_TYPES)

    def _extract_group(self, group: str, award_types: list[str]) -> Iterator[tuple[str, str, dict]]:
        ck = self.load_checkpoint()
        page = ck.get(f"page:{group}", 1)
        logger.info("USAspending[%s]: starting from page %d", group, page)

        while True:
            body = {
                "filters": {
                    "recipient_search_text": _RECIPIENT_NAMES,
                    "award_type_codes": award_types,
                    "time_period": [{"start_date": _START_DATE, "end_date": "2099-12-31"}],
                },
                "fields": [
                    "Award ID", "Recipient Name", "Start Date", "End Date",
                    "Award Amount", "Awarding Agency", "Awarding Sub Agency",
                    "Award Type", "Description",
                ],
                "page": page,
                "limit": _PAGE_SIZE,
                "sort": "Award Amount",
                "order": "desc",
            }

            try:
                data = self.post_json(_API, body)
            except Exception as exc:
                logger.error("USAspending[%s] fetch failed at page %d: %s", group, page, exc)
                ck[f"page:{group}"] = page
                self.save_checkpoint(ck)
                raise

            results = data.get("results", []) or []
            if not results:
                logger.info("USAspending[%s]: exhausted at page %d", group, page)
                break

            for rec in results:
                # Only keep records whose recipient actually is UNC-CH
                recipient = (rec.get("Recipient Name") or "").upper()
                if "NORTH CAROLINA" not in recipient or "CHAPEL HILL" not in recipient:
                    continue
                internal_id = rec.get("generated_internal_id") or rec.get("Award ID", "")
                if not internal_id:
                    continue
                source_url = _AWARD_DETAIL.format(internal_id=internal_id)
                flat = {
                    "award_id": rec.get("Award ID", ""),
                    "internal_id": internal_id,
                    "recipient_name": rec.get("Recipient Name", ""),
                    "start_date": rec.get("Start Date", ""),
                    "end_date": rec.get("End Date", ""),
                    "total_obligated_amount": rec.get("Award Amount"),
                    "awarding_agency": rec.get("Awarding Agency", ""),
                    "awarding_sub_agency": rec.get("Awarding Sub Agency", ""),
                    "award_type": rec.get("Award Type", ""),
                    "award_group": group,
                    "description": rec.get("Description", ""),
                }
                yield str(internal_id), source_url, flat

            page += 1
            ck[f"page:{group}"] = page
            self.save_checkpoint(ck)
            logger.info("USAspending[%s]: fetched ~%d records", group, (page - 1) * _PAGE_SIZE)

            if not data.get("page_metadata", {}).get("hasNext", False):
                break
