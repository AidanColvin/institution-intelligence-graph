"""
NSF Awards API extractor — all UNC-CH awards.
Endpoint: https://api.nsf.gov/services/v1/awards.json
No API key required.
"""
from __future__ import annotations
import logging
from typing import Iterator

from .base import BaseExtractor, now_iso

logger = logging.getLogger(__name__)

_API = "https://api.nsf.gov/services/v1/awards.json"
_PAGE_SIZE = 25      # NSF API max per page
# UNC-CH UEI (exact filter — the awardeeName param matches loosely and returns other orgs)
_UNC_UEI = "D3LHU66KBLD5"
_DETAIL_BASE = "https://www.nsf.gov/awardsearch/showAward?AWD_ID={award_id}"

_FIELDS = ",".join([
    "id", "title", "awardeeName", "awardeeCity", "awardeeStateCode",
    "pdPIName", "piFirstName", "piLastName", "startDate", "expDate",
    "estimatedTotalAmt", "fundsObligatedAmt", "fundProgramName",
    "primaryProgram", "abstractText", "ueiNumber",
])


class NSFExtractor(BaseExtractor):
    name = "nsf"
    min_interval = 0.5

    def extract(self) -> Iterator[tuple[str, str, dict]]:
        ck = self.load_checkpoint()
        offset = ck.get("offset", 0)
        logger.info("NSF: starting from offset %d", offset)

        while True:
            params = {
                "ueiNumber": _UNC_UEI,    # exact match to UNC-CH
                "offset": offset + 1,     # NSF uses 1-based offsets
                "rpp": _PAGE_SIZE,
                "printFields": _FIELDS,
            }

            try:
                data = self.get_json(_API, params)
            except Exception as exc:
                logger.error("NSF fetch failed at offset %d: %s", offset, exc)
                self.save_checkpoint({"offset": offset})
                raise

            awards = data.get("response", {}).get("award", []) or []
            if not awards:
                logger.info("NSF: exhausted at offset %d", offset)
                self.clear_checkpoint()
                break

            for award in awards:
                award_id = award.get("id", "")
                if not award_id:
                    continue
                source_url = _DETAIL_BASE.format(award_id=award_id)
                yield award_id, source_url, award

            offset += len(awards)
            self.save_checkpoint({"offset": offset})
            logger.info("NSF: fetched %d awards so far", offset)

            if len(awards) < _PAGE_SIZE:
                self.clear_checkpoint()
                break
